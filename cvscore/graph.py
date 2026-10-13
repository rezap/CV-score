"""The assessment pipeline as a LangGraph graph.

    START --(one Send per candidate, run in parallel)--> assess --> rank --> END

``assess`` redacts a candidate's documents, extracts a structured profile and scores it.
A candidate whose documents could not be read (no text, e.g. a scanned PDF) is not sent
to the model; it gets an empty score card flagged for review instead. ``rank`` combines
scores in plain Python. LLM, tracing and concurrency come in from the caller.
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
from cvscore.llm.structured import LLMProvider
from cvscore.models import (
    CandidateDocuments,
    CandidateProfile,
    Flag,
    JobProfile,
    ScoreCard,
)
from cvscore.prompts import PROMPT_VERSIONS
from cvscore.rank import rank
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
    ranked: list[ScoreCard]


class CandidateTask(TypedDict):
    job: JobProfile
    candidate: CandidateDocuments
    names: list[str]


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
    card = score_candidate(job, profile, docs, llm, config)
    card.notes = [*notes, *card.notes]
    if notes:
        card.flags = sorted({*card.flags, Flag.NEEDS_REVIEW})
    return {"profile": profile, "card": card}


def build_graph(llm: LLMProvider, ranking: RankingConfig) -> CompiledStateGraph[Any, Any, Any, Any]:
    def fan_out(state: PipelineState) -> list[Send]:
        identity = state.get("identity", {})
        names = state.get("names", {})
        return [
            Send(
                "assess",
                CandidateTask(
                    job=state["job"],
                    candidate=c,
                    names=names.get(c.candidate_id)
                    or [n for n in [identity.get(c.candidate_id)] if n],
                ),
            )
            for c in state["candidates"]
        ]

    def assess(task: CandidateTask, config: RunnableConfig) -> dict[str, list[Assessment]]:
        result = assess_candidate(task["job"], task["candidate"], task["names"], llm, config)
        return {"assessments": [result]}

    def rank_node(state: PipelineState) -> dict[str, list[ScoreCard]]:
        cards = [a["card"] for a in state.get("assessments", [])]
        return {"ranked": rank(cards, state["job"], ranking)}

    graph = StateGraph(PipelineState)
    graph.add_node("assess", assess)  # type: ignore[call-overload]
    graph.add_node("rank", rank_node)
    graph.add_conditional_edges(START, fan_out, ["assess"])
    graph.add_edge("assess", "rank")
    graph.add_edge("rank", END)
    return graph.compile()
