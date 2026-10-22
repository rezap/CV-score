"""Head-to-head comparison of near-tied candidates by the judge model.

Absolute scores of two similar CVs, each read on its own, are noisy: one step on a rubric
moves a total by several points. For neighbours in the ranking that are too close to call
(the same tier), the judge reads both candidates' redacted documents side by side and says
which fits the role better. Each pair is judged twice, with the candidates in both orders,
to cancel position bias; the order changes only when both readings agree. Totals are never
changed: the judge's view is recorded in the notes, so a reviewer sees why two candidates
were swapped.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel

from cvscore.config import ModelRole
from cvscore.extract import documents_prompt
from cvscore.llm.structured import LLMProvider
from cvscore.models import DocType, Flag, JobProfile, ScoreCard
from cvscore.prompts import PAIRWISE_SYSTEM
from cvscore.score import job_summary

Verdict = Literal["A", "B", "tie"]


class PairwiseDraft(BaseModel):
    better: Verdict
    rationale: str


def compare(
    job: JobProfile,
    a: dict[DocType, str],
    b: dict[DocType, str],
    llm: LLMProvider,
    config: RunnableConfig | None = None,
) -> PairwiseDraft:
    """Which of two candidates (redacted documents ``a`` and ``b``) fits the role better."""
    prompt = (
        f"<hiring_profile>\n{job_summary(job)}\n</hiring_profile>\n\n"
        f'<candidate id="A">\n{documents_prompt(a)}\n</candidate>\n\n'
        f'<candidate id="B">\n{documents_prompt(b)}\n</candidate>'
    )
    return llm(ModelRole.JUDGE).generate(
        PairwiseDraft, [SystemMessage(PAIRWISE_SYSTEM), HumanMessage(prompt)], config
    )


def _eligible(card: ScoreCard) -> bool:
    return (
        card.total is not None and card.tier is not None and Flag.KNOCKOUT_FAILED not in card.flags
    )


def judge_ties(
    ordered: list[ScoreCard],
    job: JobProfile,
    docs: dict[str, dict[DocType, str]],
    llm: LLMProvider,
    top: int,
    config: RunnableConfig | None = None,
    max_workers: int = 4,
) -> list[ScoreCard]:
    """Compare same-tier neighbours among the best ``top``; swap a pair when the judge
    prefers the lower one in both orders. Returns the new order (ranks renumbered)."""
    head = ordered[:top]
    pairs = [
        i
        for i in range(len(head) - 1)
        if _eligible(head[i])
        and _eligible(head[i + 1])
        and head[i].tier == head[i + 1].tier
        and head[i].candidate_id in docs
        and head[i + 1].candidate_id in docs
    ]
    if not pairs:
        return ordered

    def both_orders(i: int) -> tuple[PairwiseDraft, PairwiseDraft]:
        upper, lower = docs[head[i].candidate_id], docs[head[i + 1].candidate_id]
        return (
            compare(job, upper, lower, llm, config),  # A = upper
            compare(job, lower, upper, llm, config),  # A = lower
        )

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        verdicts = dict(zip(pairs, pool.map(both_orders, pairs), strict=True))

    result = list(ordered)
    swapped_at: set[int] = set()
    for i in pairs:
        first, second = verdicts[i]
        upper, lower = head[i], head[i + 1]
        if first.better == "B" and second.better == "A":
            if i in swapped_at:  # the upper one just moved down from the pair above
                continue
            a, b = result.index(upper), result.index(lower)
            result[a], result[b] = lower, upper
            swapped_at.update({i, i + 1})
            note = f"Judge preferred {lower.candidate_id} over {upper.candidate_id} in both orders"
            lower.notes.append(f"{note}, moved up: {first.rationale}")
            upper.notes.append(f"{note}, moved down: {first.rationale}")
        elif first.better == "A" and second.better == "B":
            upper.notes.append(
                f"Judge confirmed this candidate over {lower.candidate_id}: {first.rationale}"
            )
        else:
            upper.notes.append(
                f"Judge could not separate this candidate from {lower.candidate_id} "
                "(the two orders disagreed or called a tie)."
            )
    for n, card in enumerate(result, 1):
        card.rank = n
    return result
