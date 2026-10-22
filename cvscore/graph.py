"""The assessment pipeline as a LangGraph graph.

    START --(Send per candidate)--> assess --> rank --(Send per top N)--> rescore --+
                                                  |                                  |
                                                  +-------------------------> finalize --> END

``assess`` redacts a candidate's documents, extracts a structured profile and scores it.
A candidate whose documents could not be read (no text, e.g. a scanned PDF) is not sent
to the model; it gets an empty score card flagged for review instead. ``rank`` combines
scores in plain Python. The best ``ranking.repeat_top`` are then scored a second time
(``rescore``, in parallel), and ``finalize`` averages the readings, groups totals too
close to call into tiers, and lets the judge model compare same-tier neighbours
(``ranking.pairwise_top``). LLM, tracing and concurrency come in from the caller.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from cvscore.config import RankingConfig
from cvscore.extract import extract_profile
from cvscore.judge import judge_ties
from cvscore.llm.structured import LLMProvider
from cvscore.models import (
    CandidateDocuments,
    CandidateProfile,
    Flag,
    JobProfile,
    ScoreCard,
)
from cvscore.prompts import PROMPT_VERSIONS
from cvscore.rank import combine_passes, rank
from cvscore.redact import redact
from cvscore.score import score_candidate

LOW_QUALITY = 0.5  # documents below this extraction quality are flagged for review


class Assessment(TypedDict):
    profile: CandidateProfile
    card: ScoreCard


class PipelineState(TypedDict, total=False):
    job: JobProfile
    candidates: list[CandidateDocuments]
    identity: dict[str, str]
    names: dict[str, list[str]]  # candidate_id -> names to redact (default: the identity)
    assessments: Annotated[list[Assessment], operator.add]
    first_ranking: list[ScoreCard]
    rescored: Annotated[list[ScoreCard], operator.add]
    ranked: list[ScoreCard]


class CandidateTask(TypedDict):
    job: JobProfile
    candidate: CandidateDocuments
    names: list[str]


class RescoreTask(TypedDict):
    job: JobProfile
    candidate: CandidateDocuments
    names: list[str]
    profile: CandidateProfile


def assess_candidate(
    job: JobProfile,
    candidate: CandidateDocuments,
    names: list[str],
    llm: LLMProvider,
    config: RunnableConfig | None = None,
) -> Assessment:
    docs = redact(candidate, names)
    notes = [f"Could not read {e}" for e in candidate.errors]
    low_quality = [d.path for d in candidate.documents if d.quality < LOW_QUALITY]
    if low_quality:
        notes.append(f"Poor text extraction (scanned?): {', '.join(low_quality)}.")
    if not docs:
        card = ScoreCard(
            candidate_id=candidate.candidate_id,
            job_id=job.id,
            flags=[Flag.NEEDS_REVIEW],
            notes=[*notes, "No readable text; not assessed."],
            prompt_versions=dict(PROMPT_VERSIONS),
        )
        return {"profile": CandidateProfile(candidate_id=candidate.candidate_id), "card": card}
    profile = extract_profile(candidate.candidate_id, docs, llm, config)
    card = score_candidate(job, profile, docs, llm, config, sources=candidate.documents)
    card.notes = [*notes, *card.notes]
    if notes:
        card.flags = sorted({*card.flags, Flag.NEEDS_REVIEW})
    return {"profile": profile, "card": card}


def build_graph(llm: LLMProvider, ranking: RankingConfig) -> CompiledStateGraph[Any, Any, Any, Any]:
    def names_for(state: PipelineState, candidate_id: str) -> list[str]:
        identity = state.get("identity", {})
        return state.get("names", {}).get(candidate_id) or [
            n for n in [identity.get(candidate_id)] if n
        ]

    def fan_out(state: PipelineState) -> list[Send]:
        return [
            Send(
                "assess",
                CandidateTask(
                    job=state["job"], candidate=c, names=names_for(state, c.candidate_id)
                ),
            )
            for c in state["candidates"]
        ]

    def assess(task: CandidateTask, config: RunnableConfig) -> dict[str, list[Assessment]]:
        result = assess_candidate(task["job"], task["candidate"], task["names"], llm, config)
        return {"assessments": [result]}

    def rank_node(state: PipelineState) -> dict[str, list[ScoreCard]]:
        # Rank copies: finalize ranks the original cards again with the second readings.
        cards = [a["card"].model_copy(deep=True) for a in state.get("assessments", [])]
        return {"first_ranking": rank(cards, state["job"], ranking)}

    def to_rescore(state: PipelineState) -> list[Send] | str:
        top = [
            c
            for c in state.get("first_ranking", [])
            if c.total is not None and Flag.KNOCKOUT_FAILED not in c.flags
        ][: ranking.repeat_top]
        if not top:
            return "finalize"
        profiles = {a["profile"].candidate_id: a["profile"] for a in state.get("assessments", [])}
        candidates = {c.candidate_id: c for c in state["candidates"]}
        return [
            Send(
                "rescore",
                RescoreTask(
                    job=state["job"],
                    candidate=candidates[c.candidate_id],
                    names=names_for(state, c.candidate_id),
                    profile=profiles[c.candidate_id],
                ),
            )
            for c in top
        ]

    def rescore(task: RescoreTask, config: RunnableConfig) -> dict[str, list[ScoreCard]]:
        docs = redact(task["candidate"], task["names"])
        card = score_candidate(
            task["job"],
            task["profile"],
            docs,
            llm,
            config,
            sources=task["candidate"].documents,
            variant=1,
        )
        return {"rescored": [card]}

    def finalize(state: PipelineState, config: RunnableConfig) -> dict[str, list[ScoreCard]]:
        job = state["job"]
        again: dict[str, list[ScoreCard]] = {}
        for card in state.get("rescored", []):
            again.setdefault(card.candidate_id, []).append(card)
        cards = []
        for a in state.get("assessments", []):
            card = a["card"]
            extra = again.get(card.candidate_id, [])
            if extra:
                # Notes from assess (unreadable files etc.) are kept on the combined card.
                combined = combine_passes([card, *extra], job)
                cards.append(combined)
            else:
                cards.append(card.model_copy(deep=True))
        ordered = rank(cards, job, ranking)
        if ranking.pairwise_top > 1:
            candidates = {c.candidate_id: c for c in state["candidates"]}
            head = {c.candidate_id for c in ordered[: ranking.pairwise_top]}
            docs = {
                cid: redact(candidates[cid], names_for(state, cid))
                for cid in head
                if cid in candidates
            }
            ordered = judge_ties(ordered, job, docs, llm, ranking.pairwise_top, config)
        return {"ranked": ordered}

    graph = StateGraph(PipelineState)
    graph.add_node("assess", assess)  # type: ignore[call-overload]
    graph.add_node("rank", rank_node)
    graph.add_node("rescore", rescore)  # type: ignore[arg-type]
    graph.add_node("finalize", finalize)
    graph.add_conditional_edges(START, fan_out, ["assess"])
    graph.add_edge("assess", "rank")
    graph.add_conditional_edges("rank", to_rescore, ["rescore", "finalize"])
    graph.add_edge("rescore", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()
