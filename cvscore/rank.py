"""Combines dimension scores into a total and orders candidates. Plain Python, no model."""

from __future__ import annotations

from statistics import mean

from cvscore.config import RankingConfig
from cvscore.models import Dimension, DimensionScore, Flag, JobProfile, RequirementStatus, ScoreCard


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
    assign_tiers(ordered, config.tie_margin)
    return ordered


def assign_tiers(ordered: list[ScoreCard], margin: float) -> None:
    """Number tiers down the ranking: a candidate joins the tier above when the gap to
    the previous one is within ``margin`` or within their spreads. Knocked-out and
    unscored candidates get no tier."""
    tier = 0
    previous: ScoreCard | None = None
    for card in ordered:
        if card.total is None or Flag.KNOCKOUT_FAILED in card.flags:
            card.tier = None
            continue
        if previous is None or previous.total is None:
            tier += 1
        else:
            allowed = max(margin, ((previous.spread or 0) + (card.spread or 0)) / 2)
            if previous.total - card.total > allowed + 1e-9:
                tier += 1
        card.tier = tier
        previous = card


def combine_passes(passes: list[ScoreCard], job: JobProfile) -> ScoreCard:
    """One card from repeated readings: dimension scores averaged, the spread of their
    totals recorded, and a must-have the readings disagree on failing made "unclear"."""
    first = passes[0].model_copy(deep=True)
    if len(passes) == 1:
        return first
    totals = [t for p in passes if (t := total_score(p, job.weights)) is not None]
    by_dim: dict[Dimension, list[DimensionScore]] = {}
    for p in passes:
        for s in p.scores:
            by_dim.setdefault(s.dimension, []).append(s)
    for s in first.scores:
        values = [x.score for x in by_dim.get(s.dimension, []) if x.score is not None]
        if values:
            s.score = round(mean(values), 3)
        s.confidence = round(mean(x.confidence for x in by_dim.get(s.dimension, [s])), 2)
    for check in first.requirement_checks:
        statuses = {
            c.status
            for p in passes
            for c in p.requirement_checks
            if c.requirement_id == check.requirement_id
        }
        if check.must_have and RequirementStatus.FAIL in statuses and len(statuses) > 1:
            check.status = RequirementStatus.UNCLEAR
            check.rationale += " [The readings disagreed on whether this is met.]"
    if len(totals) > 1:
        first.spread = round(max(totals) - min(totals), 3)
        first.notes.append(
            f"Scored {len(passes)} times: totals "
            + ", ".join(f"{round(t * 100)}" for t in totals)
            + "."
        )
    return first
