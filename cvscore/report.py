"""Writes the results: ranking.csv, results.json and a self-contained report.html."""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from typing import TYPE_CHECKING

from cvscore.models import (
    CandidateProfile,
    Dimension,
    Evidence,
    JobProfile,
    RequirementStatus,
    ScoreCard,
)

if TYPE_CHECKING:
    from cvscore.screen import ScreenResult

DIMENSIONS = [
    Dimension.REQUIREMENTS,
    Dimension.EXPERIENCE,
    Dimension.ACHIEVEMENTS,
    Dimension.SKILLS,
    Dimension.TRAITS,
]


def _pct(value: float | None) -> str:
    return "" if value is None else f"{round(value * 100)}"


def _label(card: ScoreCard, names: dict[str, str] | None) -> str:
    if names and card.candidate_id in names:
        return f"{names[card.candidate_id]} ({card.candidate_id})"
    return card.candidate_id


def write_csv(cards: list[ScoreCard], path: Path, names: dict[str, str] | None) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "rank",
                "candidate",
                "total",
                *[d.value for d in DIMENSIONS],
                "years_experience",
                "skill_coverage",
                "flags",
                "notes",
            ]
        )
        for c in cards:
            dims = []
            for d in DIMENSIONS:
                s = c.score(d)
                dims.append(_pct(s.score) if s else "")
            w.writerow(
                [
                    c.rank,
                    _label(c, names),
                    _pct(c.total),
                    *dims,
                    c.features.get("years_experience") or "",
                    _pct(c.features.get("skill_coverage")),
                    " ".join(c.flags),
                    " | ".join(c.notes),
                ]
            )


def write_json(
    job: JobProfile, cards: list[ScoreCard], profiles: list[CandidateProfile], path: Path
) -> None:
    by_id = {p.candidate_id: p for p in profiles}
    data = {
        "job": job.model_dump(mode="json"),
        "candidates": [
            {
                "score_card": c.model_dump(mode="json"),
                "profile": by_id[c.candidate_id].model_dump(mode="json")
                if c.candidate_id in by_id
                else None,
            }
            for c in cards
        ],
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


_CSS = """
:root{
  --bg:#fff;
  --fg:#1a1a1a;
  --muted:#666;
  --line:#ddd;
  --card:#f7f7f8;
  --good:#1b7f3b;
  --bad:#b42318;
  --warn:#a15c07;
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#141414;
    --fg:#eee;
    --muted:#aaa;
    --line:#333;
    --card:#1e1e1e;
    --good:#5fd38a;
    --bad:#ff7b72;
    --warn:#e3b341;
  }
}
body{
  font:15px/1.5 system-ui,sans-serif;
  background:var(--bg);
  color:var(--fg);
  margin:0 auto;
  max-width:1100px;
  padding:16px;
}
h1{font-size:1.5em;margin:.2em 0}
h2{font-size:1.15em;margin:1.4em 0 .4em}
h3{font-size:1em;margin:1em 0 .3em}
.muted{color:var(--muted)}
table{border-collapse:collapse;width:100%;margin:.5em 0}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{font-weight:600}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
td.id{white-space:nowrap}
.wrap{overflow-x:auto}
.flag{
  display:inline-block;
  border:1px solid var(--warn);
  color:var(--warn);
  border-radius:4px;
  padding:0 5px;
  margin:1px;
  font-size:.8em;
}
details{
  background:var(--card);
  border:1px solid var(--line);
  border-radius:8px;
  padding:8px 12px;
  margin:10px 0;
}
summary{cursor:pointer;font-weight:600}
blockquote{
  margin:4px 0 4px 8px;
  padding-left:8px;
  border-left:3px solid var(--line);
  color:var(--muted);
}
.pass{color:var(--good)}
.fail{color:var(--bad)}
.unclear{color:var(--warn)}
.note{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 12px}
"""


def _e(text: object) -> str:
    return html.escape(str(text))


def _quotes(evidence: list[Evidence]) -> str:
    return "".join(
        f"<blockquote>{_e(ev.quote)} <span class='muted'>({_e(ev.doc_type)})</span></blockquote>"
        for ev in evidence
    )


def _candidate_section(card: ScoreCard, label: str) -> str:
    parts = [
        f"<details><summary>#{card.rank} {_e(label)} - total {_pct(card.total) or 'n/a'}"
        f"{''.join(f' <span class=flag>{_e(f)}</span>' for f in card.flags)}</summary>"
    ]
    if card.notes:
        parts.append("<ul>" + "".join(f"<li>{_e(n)}</li>" for n in card.notes) + "</ul>")
    feats = card.features
    parts.append(
        f"<p class=muted>Years of experience: {_e(feats.get('years_experience') or 'unknown')}"
        f" · Key-skill coverage: {_pct(feats.get('skill_coverage')) or 'n/a'}%</p>"
    )
    if card.requirement_checks:
        parts.append(
            "<h3>Requirements</h3><div class=wrap><table><tr><th>Requirement</th>"
            "<th>Result</th><th>Why</th></tr>"
        )
        for rc in card.requirement_checks:
            kind = "must" if rc.must_have else "nice"
            css = {RequirementStatus.PASS: "pass", RequirementStatus.FAIL: "fail"}.get(
                rc.status, "unclear"
            )
            parts.append(
                f"<tr><td>{_e(rc.requirement_id)} ({kind}): {_e(rc.description)}</td>"
                f"<td class={css}>{_e(rc.status)}</td><td>{_e(rc.rationale)}"
                f"{_quotes(rc.evidence)}</td></tr>"
            )
        parts.append("</table></div>")
    for s in card.scores:
        if s.dimension is Dimension.REQUIREMENTS:
            continue
        parts.append(
            f"<h3>{_e(s.dimension.value.title())}: {_pct(s.score) or 'n/a'}"
            f" <span class=muted>(confidence {round(s.confidence * 100)}%)</span></h3>"
            f"<p>{_e(s.rationale)}</p>{_quotes(s.evidence)}"
        )
        if s.dimension is Dimension.TRAITS:
            for t in card.trait_scores:
                parts.append(
                    f"<p><b>{_e(t.name)}</b>: {_pct(t.score) or 'insufficient evidence'}. "
                    f"{_e(t.rationale)}</p>{_quotes(t.evidence)}"
                )
    parts.append("</details>")
    return "".join(parts)


def write_html(
    job: JobProfile, cards: list[ScoreCard], path: Path, names: dict[str, str] | None
) -> None:
    weights = ", ".join(f"{d.value} {round(w * 100)}%" for d, w in job.weights.items() if w)
    rows = []
    for c in cards:
        cells = "".join(
            f"<td class=num>{_pct(s.score) if (s := c.score(d)) else ''}</td>" for d in DIMENSIONS
        )
        flags = "".join(f"<span class=flag>{_e(f)}</span>" for f in c.flags)
        rows.append(
            f"<tr><td class=num>{c.rank}</td><td class=id>{_e(_label(c, names))}</td>"
            f"<td class=num><b>{_pct(c.total)}</b></td>{cells}<td>{flags}</td></tr>"
        )
    head = "".join(f"<th class=num>{d.value.title()}</th>" for d in DIMENSIONS)
    body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Candidate ranking</title><style>{_CSS}</style></head><body>
<h1>{_e(job.title)}</h1>
<p class=muted>Weights: {_e(weights)}. Scores are 0-100. Traits are shown for information
unless given a weight in the job profile.</p>
<p class=note>Decision support only. Scores come from a language model applying a rubric to
redacted documents; every judgement cites quotes that were checked against the documents.
Read the evidence, and treat flagged candidates with care, before deciding.</p>
<h2>Ranking</h2><div class=wrap><table><tr><th class=num>#</th><th>Candidate</th>
<th class=num>Total</th>{head}
<th>Flags</th></tr>{"".join(rows)}</table></div>
<h2>Details</h2>{"".join(_candidate_section(c, _label(c, names)) for c in cards)}
</body></html>"""
    path.write_text(body, encoding="utf-8")


def write_report(
    out: Path,
    job: JobProfile,
    cards: list[ScoreCard],
    profiles: list[CandidateProfile],
    names: dict[str, str] | None = None,
) -> None:
    out.mkdir(parents=True, exist_ok=True)
    write_csv(cards, out / "ranking.csv", names)
    write_json(job, cards, profiles, out / "results.json")
    write_html(job, cards, out / "report.html", names)


# --- Screening -------------------------------------------------------------------------


def write_screening(
    out: Path, job: JobProfile, results: list[ScreenResult], names: dict[str, str] | None = None
) -> None:
    """screening.csv, screening.json and screening.html for a fast screen."""
    out.mkdir(parents=True, exist_ok=True)

    def label(cid: str) -> str:
        return f"{names[cid]} ({cid})" if names and cid in names else cid

    criteria = job.screening or (results[0].criteria if results else [])
    crit_ids = [c.id for c in criteria]
    crit_names = {c.id: c.name for c in criteria}
    with (out / "screening.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            ["rank", "candidate", "score", "screened_out", "years_hint",
             *[f"{i} {crit_names[i]}" for i in crit_ids], "matched", "notes"]
        )  # fmt: skip
        for r in results:
            by_id = {c.id: c for c in r.criteria}
            w.writerow(
                [
                    r.rank,
                    label(r.candidate_id),
                    _pct(r.score),
                    "yes" if r.screened_out else "",
                    r.years_hint if r.years_hint is not None else "",
                    *[_pct(by_id[i].score) if i in by_id else "" for i in crit_ids],
                    "; ".join(t for c in r.criteria for t in c.matched),
                    " | ".join(r.notes),
                ]
            )
    (out / "screening.json").write_text(
        json.dumps(
            {
                "job": {"id": job.id, "title": job.title},
                "criteria": [c.model_dump(mode="json") for c in job.screening],
                "candidates": [r.model_dump(mode="json") for r in results],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    head = "".join(f"<th class=num title='{_e(crit_names[i])}'>{_e(i)}</th>" for i in crit_ids)
    rows = []
    for r in results:
        by_id = {c.id: c for c in r.criteria}
        cells = "".join(
            f"<td class=num title='{_e(', '.join(by_id[i].matched))}'>{_pct(by_id[i].score)}</td>"
            if i in by_id
            else "<td></td>"
            for i in crit_ids
        )
        out_flag = "<span class=flag>screened out</span>" if r.screened_out else ""
        years = "" if r.years_hint is None else f"{r.years_hint:g}"
        rows.append(
            f"<tr><td class=num>{r.rank}</td><td class=id>{_e(label(r.candidate_id))}</td>"
            f"<td class=num><b>{_pct(r.score)}</b></td>{cells}<td class=num>{years}</td>"
            f"<td>{out_flag} {_e(' '.join(r.notes))}</td></tr>"
        )
    legend = "".join(
        f"<li><b>{_e(c.id)}</b> {_e(c.name)}{' (required)' if c.required else ''}: "
        f"<span class=muted>{_e(', '.join(c.terms))}</span></li>"
        for c in job.screening
    )
    body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Candidate screening</title><style>{_CSS}</style></head><body>
<h1>{_e(job.title)}: screening</h1>
<p class=note>A fast keyword (and semantic) search of the redacted documents, without a
language model reading them. It gives an impression and picks whom to assess in depth; it
is not a verdict. CVs that describe a skill in other words can be missed: check the
screened-out candidates.</p>
<h2>Criteria</h2><ul>{legend}</ul>
<h2>Ranking</h2><div class=wrap><table><tr><th class=num>#</th><th>Candidate</th>
<th class=num>Score</th>{head}<th class=num>Years</th><th>Notes</th></tr>{"".join(rows)}
</table></div></body></html>"""
    (out / "screening.html").write_text(body, encoding="utf-8")
