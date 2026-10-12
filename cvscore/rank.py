"""Combines dimension scores into a total and orders candidates. Plain Python, no model."""

from __future__ import annotations

from cvscore.config import RankingConfig
from cvscore.models import Dimension, Flag, JobProfile, RequirementStatus, ScoreCard


def total_score(card: ScoreCard, weights: dict[Dimension, float]) -> float | None:
    """Weighted mean of the scored dimensions; dimensions without a score are left out
    and the remaining weights renormalised."""
    parts = [
        (s.score, weights.get(s.dimension, 0.0))
        for s in card.scores
        if s.score is not None and weights.get(s.dimension, 0.0) > 0
    ]
    weight = sum(w for _, w in parts)
    if not parts or weight == 0:
        return None
    return round(sum(score * w for score, w in parts) / weight, 3)


def apply_flags(card: ScoreCard, job: JobProfile, config: RankingConfig) -> None:
    flags = set(card.flags)
    must = [c for c in card.requirement_checks if c.must_have]
    failed = [c.requirement_id for c in must if c.status is RequirementStatus.FAIL]
    unclear = [c.requirement_id for c in must if c.status is RequirementStatus.UNCLEAR]
    if failed:
        flags.add(Flag.KNOCKOUT_FAILED)
        card.notes.append(f"Must-have not met: {', '.join(failed)}.")
    if unclear:
        flags.add(Flag.NEEDS_REVIEW)
        card.notes.append(f"Must-have unclear from the documents: {', '.join(unclear)}.")
    weighted = [s for s in card.scores if job.weights.get(s.dimension, 0) > 0]
    low = [s.dimension.value for s in weighted if s.confidence < config.low_confidence]
    if low:
        flags.add(Flag.LOW_CONFIDENCE)
        card.notes.append(f"Low confidence: {', '.join(low)}.")
    if (card.features.get("dropped_quotes") or 0) >= config.max_dropped_quotes:
        flags.add(Flag.INCONSISTENCY)
    card.flags = sorted(flags)


def rank(cards: list[ScoreCard], job: JobProfile, config: RankingConfig) -> list[ScoreCard]:
    """Set total, flags and rank on each card; return them best first.

    Knocked-out candidates (a must-have clearly not met) are ranked after everyone else
    (``knockout: flag``) or get no total at all (``knockout: exclude``). Either way they are
    still shown, so a person can overrule the call.
    """
    for card in cards:
        apply_flags(card, job, config)
        card.total = total_score(card, job.weights)
        if Flag.KNOCKOUT_FAILED in card.flags and config.knockout == "exclude":
            card.total = None

    def key(card: ScoreCard) -> tuple[int, int, float]:
        knocked_out = Flag.KNOCKOUT_FAILED in card.flags
        return (card.total is None, knocked_out, -(card.total or 0.0))

    ordered = sorted(cards, key=key)
    for i, card in enumerate(ordered, 1):
        card.rank = i
    return ordered
