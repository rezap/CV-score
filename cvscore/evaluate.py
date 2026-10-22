"""Evaluates the ranking on a set of fictional candidates with known answers.

An evaluation set is a folder with ``job.yaml``, ``candidates/`` (the documents) and
``expected.yaml``:

    tiers: {Candidate Name: 1, ...}        # 1 = strong fit, 2 = adjacent, 3 = unrelated
    better: [[A, B, why], ...]             # A must score higher than B
    same: [[A, B, why], ...]               # A and B must score about the same
    tolerance: 0.05                        # for "same"

The whole assessment runs ``repeats`` times without the model cache, so every run is a
fresh sample. The report says how well the ranking matches the expected tiers (share of
correctly ordered cross-tier pairs, Kendall's tau), whether each controlled pair holds,
and how much each candidate's total moves between runs.
"""

from __future__ import annotations

from collections.abc import Callable
from itertools import combinations
from pathlib import Path
from statistics import mean

import yaml
from pydantic import BaseModel, Field

from cvscore.config import Settings
from cvscore.ingest import ingest
from cvscore.job import load_job_profile
from cvscore.llm.structured import LLMProvider

ProviderFactory = Callable[[Settings], LLMProvider]


class Expected(BaseModel):
    tiers: dict[str, int]
    better: list[tuple[str, str, str]] = Field(default_factory=list)
    same: list[tuple[str, str, str]] = Field(default_factory=list)
    tolerance: float = 0.05


class CandidateResult(BaseModel):
    expected_tier: int | None
    totals: list[float | None]
    mean: float | None
    spread: float | None = Field(description="Max minus min total across runs.")
    ranks: list[int | None]


class PairResult(BaseModel):
    a: str
    b: str
    why: str
    difference: float | None = Field(description="Mean total of a minus b.")
    ok: bool


class EvalReport(BaseModel):
    job: str
    repeats: int
    candidates: dict[str, CandidateResult]
    tier_order_accuracy: float | None = Field(
        description="Share of pairs from different tiers whose mean totals are in order."
    )
    kendall_tau: float | None
    better: list[PairResult]
    same: list[PairResult]
    mean_spread: float | None
    max_spread: float | None

    def summary(self) -> str:
        def pct(v: float | None) -> str:
            return "n/a" if v is None else f"{round(v * 100)}"

        lines = [
            f"Evaluation: {self.job}, {self.repeats} run(s)",
            f"  tier order accuracy {pct(self.tier_order_accuracy)}%, "
            f"Kendall tau {'n/a' if self.kendall_tau is None else f'{self.kendall_tau:.2f}'}",
            f"  spread between runs: mean {pct(self.mean_spread)}, "
            f"max {pct(self.max_spread)} points",
        ]
        for kind, pairs in (("better", self.better), ("same", self.same)):
            for p in pairs:
                mark = "ok  " if p.ok else "FAIL"
                diff = "n/a" if p.difference is None else f"{p.difference * 100:+.0f}"
                lines.append(f"  {mark} {kind}: {p.a} vs {p.b} ({diff}) - {p.why}")
        lines.append("  candidates (tier, mean, totals):")
        order = sorted(self.candidates.items(), key=lambda kv: -(kv[1].mean or -1))
        for name, c in order:
            totals = ", ".join(pct(t) for t in c.totals)
            lines.append(f"    {name:<22} tier {c.expected_tier}  {pct(c.mean):>4}  [{totals}]")
        return "\n".join(lines)


def kendall_tau(xs: list[float], ys: list[float]) -> float | None:
    """Kendall's tau-b between two equally long lists (ties allowed)."""
    concordant = discordant = ties_x = ties_y = 0
    for i, j in combinations(range(len(xs)), 2):
        dx, dy = xs[i] - xs[j], ys[i] - ys[j]
        if dx == 0 and dy == 0:
            continue
        if dx == 0:
            ties_x += 1
        elif dy == 0:
            ties_y += 1
        elif (dx > 0) == (dy > 0):
            concordant += 1
        else:
            discordant += 1
    n0 = concordant + discordant
    denominator = ((n0 + ties_x) * (n0 + ties_y)) ** 0.5
    return round((concordant - discordant) / denominator, 3) if denominator else None


def score_report(
    job_title: str,
    totals: dict[str, list[float | None]],
    ranks: dict[str, list[int | None]],
    expected: Expected,
) -> EvalReport:
    """Metrics from each candidate's totals over the runs."""
    candidates: dict[str, CandidateResult] = {}
    for name, ts in totals.items():
        values = [t for t in ts if t is not None]
        candidates[name] = CandidateResult(
            expected_tier=expected.tiers.get(name),
            totals=ts,
            mean=round(mean(values), 3) if values else None,
            spread=round(max(values) - min(values), 3) if len(values) > 1 else None,
            ranks=ranks.get(name, []),
        )
    scored = [
        (name, c.mean, c.expected_tier)
        for name, c in candidates.items()
        if c.mean is not None and c.expected_tier is not None
    ]
    cross = [(a, b) for a, b in combinations(scored, 2) if a[2] != b[2]]
    in_order = sum((a[1] > b[1]) == (a[2] < b[2]) and a[1] != b[1] for a, b in cross)
    tau = kendall_tau([m for _, m, _ in scored], [-t for _, _, t in scored]) if scored else None

    def pair(a: str, b: str, why: str, same: bool) -> PairResult:
        ma, mb = (candidates[x].mean if x in candidates else None for x in (a, b))
        diff = None if ma is None or mb is None else round(ma - mb, 3)
        ok = diff is not None and (abs(diff) <= expected.tolerance if same else diff > 0)
        return PairResult(a=a, b=b, why=why, difference=diff, ok=ok)

    spreads = [c.spread for c in candidates.values() if c.spread is not None]
    return EvalReport(
        job=job_title,
        repeats=max((len(t) for t in totals.values()), default=0),
        candidates=candidates,
        tier_order_accuracy=round(in_order / len(cross), 3) if cross else None,
        kendall_tau=tau,
        better=[pair(a, b, why, same=False) for a, b, why in expected.better],
        same=[pair(a, b, why, same=True) for a, b, why in expected.same],
        mean_spread=round(mean(spreads), 3) if spreads else None,
        max_spread=round(max(spreads), 3) if spreads else None,
    )


def run_eval(
    set_dir: Path,
    settings: Settings,
    provider: ProviderFactory,
    *,
    repeats: int = 3,
) -> EvalReport:
    """Assess the set ``repeats`` times (fresh model calls each time) and score it."""
    from cvscore.service import assess_all

    job = load_job_profile(set_dir / "job.yaml")
    expected = Expected.model_validate(
        yaml.safe_load((set_dir / "expected.yaml").read_text("utf-8"))
    )
    ingested = ingest(set_dir / "candidates")
    totals: dict[str, list[float | None]] = {name: [] for name in ingested.identity.values()}
    ranks: dict[str, list[int | None]] = {name: [] for name in ingested.identity.values()}
    for _ in range(max(1, repeats)):
        outcome = assess_all(job, ingested, provider(settings), settings)
        for card in outcome.ranked:
            name = ingested.identity.get(card.candidate_id, card.candidate_id)
            totals.setdefault(name, []).append(card.total)
            ranks.setdefault(name, []).append(card.rank)
    return score_report(job.title, totals, ranks, expected)
