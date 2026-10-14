"""Fast, cheap screening: keyword and semantic search of the redacted text, no chat model.

The job profile's screening criteria (drafted once with the job profile, editable) each
list terms a CV would use to show a competence: tools, synonyms, abbreviations, other
languages. For every candidate:

- **Keywords:** whole-word, case-insensitive matches of each criterion's terms in the
  redacted documents (one- and two-letter terms such as "R" or "Go" match case-sensitively).
  More distinct terms found means a higher score: 1 term 0.6, 2 terms 0.84, 3 terms 0.94.
- **Semantic** (optional, when an embeddings model is configured): the similarity of the
  criterion to the closest passage of the CV, for competences described in other words.
  It can raise a criterion's score to at most 0.8; keywords are needed for full credit.
- **Gate:** a criterion marked ``required`` with no keyword match and no clear semantic
  match screens the CV out. Screened-out candidates are ranked last, never removed.
- **Years:** a rough span of dated entries outside education, as a hint.

The score is the weighted mean of the criteria. It gives an impression and decides which
candidates get the full assessment (``cvscore run --shortlist N``); it is not a verdict.
Keyword screens favour CVs that use the expected words, so check the screened-out list.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Callable, Sequence
from datetime import date

from pydantic import BaseModel, Field

from cvscore.config import ScreeningConfig
from cvscore.extract import parse_date, years_of_experience
from cvscore.ingest.pipeline import IngestResult
from cvscore.job import screening_from_skills
from cvscore.models import (
    BlockKind,
    CandidateDocuments,
    ExperienceEntry,
    JobProfile,
    ScreeningCriterion,
    TextBlock,
)
from cvscore.pii import REDACTED_RE
from cvscore.redact import redact_blocks

# Embeds texts into vectors (an embeddings model, usually cached); see llm.embeddings.
Embedder = Callable[[list[str]], list[list[float]]]

MAX_SNIPPETS = 3
SEMANTIC_CAP = 0.8  # semantic similarity alone never gives full credit
_EDUCATION = re.compile(
    r"educat|academic|qualificat|utdann|uddann|utbild|ausbildung|studium|bildung|course|kurs",
    re.I,
)
_RANGE_RE = re.compile(
    r"((?:[a-zæøåäöü]{3,9}\.?\s+|\d{1,2}[./]\s?)?(?:19|20)\d\d)\s*(?:-|–|—|to|til|bis)\s*"
    r"((?:[a-zæøåäöü]{3,9}\.?\s+|\d{1,2}[./]\s?)?(?:19|20)\d\d|present|now|current|today|"
    r"ongoing|nå|dags dato|i dag|heute|idag)",
    re.I,
)
_PRESENT = re.compile(r"present|now|current|today|ongoing|nå|dags dato|i dag|heute|idag", re.I)


class CriterionResult(BaseModel):
    id: str
    name: str
    required: bool
    weight: float
    score: float = Field(description="0-1: the keyword score, or a capped semantic score.")
    keyword_score: float
    semantic_score: float | None = None
    matched: list[str] = Field(default_factory=list, description="Terms found in the CV.")
    snippets: list[str] = Field(default_factory=list, description="Where they were found.")


class ScreenResult(BaseModel):
    candidate_id: str
    score: float | None = Field(description="Weighted mean of the criteria; None if no text.")
    rank: int | None = None
    screened_out: bool = False
    criteria: list[CriterionResult] = Field(default_factory=list)
    years_hint: float | None = Field(
        default=None, description="Span of dated entries outside education, in years."
    )
    notes: list[str] = Field(default_factory=list)


# --- Keywords ----------------------------------------------------------------------------


def _fold(text: str) -> str:
    """Lower case without accents: "Statistikk"/"statistikk", "Résumé"/"resume"."""
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def term_pattern(term: str) -> re.Pattern[str] | None:
    """Whole-word pattern for a term. Spaces and hyphens inside it match either ("time
    series" = "time-series"). One- and two-letter terms ("R", "C", "Go", "ML") match only
    in the given case, and "R" not in "R&D"."""
    term = term.strip()
    if not term:
        return None
    parts = [re.escape(_fold(p)) for p in re.split(r"[\s\-]+", term)]
    body = r"[\s\-]*".join(parts)
    if len(term) <= 2:
        return re.compile(rf"(?<![\w+#.\[]){re.escape(_fold(term))}(?![\w+#&])")
    return re.compile(rf"(?<![\w+#]){body}(?![\w+#])", re.I)


def _snippet(text: str, start: int, end: int, width: int = 60) -> str:
    a, b = max(0, start - width), min(len(text), end + width)
    return (
        ("…" if a else "") + text[a:b].replace("\n", " ").strip() + ("…" if b < len(text) else "")
    )


def keyword_matches(
    criterion: ScreeningCriterion, text: str, original: str | None = None
) -> tuple[list[str], list[str]]:
    """(terms found, snippets) for one criterion in ``text`` (folded for accents);
    snippets come from ``original`` when it lines up with ``text``."""
    shown = original if original is not None and len(original) == len(text) else text
    matched: list[str] = []
    snippets: list[str] = []
    for term in criterion.terms:
        pattern = term_pattern(term)
        if pattern is None or term in matched:
            continue
        m = pattern.search(text)
        if m:
            matched.append(term)
            if len(snippets) < MAX_SNIPPETS:
                snippets.append(_snippet(shown, m.start(), m.end()))
    return matched, snippets


def keyword_score(found: int) -> float:
    return round(1 - 0.4**found, 2) if found else 0.0


# --- Semantic ----------------------------------------------------------------------------


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def passages(blocks: list[TextBlock], limit: int = 200) -> list[str]:
    """CV passages for semantic search: a heading's items joined with it, 4+ words each."""
    out: list[str] = []
    for b in blocks:
        text = REDACTED_RE.sub("", b.text).strip()
        if b.kind is BlockKind.HEADING or len(text.split()) < 4:
            continue
        out.append(f"{b.section}: {text}" if b.section else text)
    return out[:limit]


def criterion_query(criterion: ScreeningCriterion) -> str:
    return f"{criterion.name}: {', '.join(criterion.terms[:15])}"


def semantic_scores(
    criteria: list[ScreeningCriterion],
    chunks: list[str],
    embed: Embedder,
    config: ScreeningConfig,
) -> list[float]:
    """Per criterion: best passage similarity, scaled from ``similarity_floor`` (0) to
    ``similarity_full`` (1)."""
    if not chunks:
        return [0.0 for _ in criteria]
    vectors = embed([criterion_query(c) for c in criteria] + chunks)
    queries, passages_ = vectors[: len(criteria)], vectors[len(criteria) :]
    span = max(1e-6, config.similarity_full - config.similarity_floor)
    out = []
    for q in queries:
        best = max(_cosine(q, p) for p in passages_)
        out.append(round(min(1.0, max(0.0, (best - config.similarity_floor) / span)), 2))
    return out


# --- Years -------------------------------------------------------------------------------


def years_hint(blocks: list[TextBlock], today: date | None = None) -> float | None:
    """Span of the dated entries outside education sections, overlaps counted once."""
    today = today or date.today()
    entries = []
    for b in blocks:
        if b.section and _EDUCATION.search(b.section):
            continue
        for m in _RANGE_RE.finditer(b.text):
            start = parse_date(m.group(1))
            current = bool(_PRESENT.fullmatch(m.group(2).strip()))
            end = None if current else parse_date(m.group(2))
            if start is not None:
                entries.append(ExperienceEntry(title="", start=start, end=end, is_current=current))
    return years_of_experience(entries, today=today) if entries else None


# --- Screening ---------------------------------------------------------------------------


def screen_candidate(
    job: JobProfile,
    candidate: CandidateDocuments,
    names: list[str],
    config: ScreeningConfig,
    embed: Embedder | None = None,
) -> ScreenResult:
    criteria = job.screening or screening_from_skills(job.key_skills)
    blocks = [b for doc in candidate.documents for b in redact_blocks(doc.blocks, names)]
    original = "\n".join(b.text for b in blocks)
    text = _fold(original)
    result = ScreenResult(candidate_id=candidate.candidate_id, score=None)
    content = [b for b in blocks if b.kind is not BlockKind.HEADING]
    if not REDACTED_RE.sub("", " ".join(b.text for b in content)).strip():
        result.notes.append("No readable text; not screened.")
        return result
    semantic: list[float | None] = [None] * len(criteria)
    if embed is not None and criteria:
        try:
            semantic = list(semantic_scores(criteria, passages(blocks), embed, config))
        except Exception as e:  # noqa: BLE001 - keywords still work without embeddings
            result.notes.append(f"Semantic search failed ({type(e).__name__}); keywords only.")
    for c, sem in zip(criteria, semantic, strict=True):
        matched, snippets = keyword_matches(c, text, original)
        kw = keyword_score(len(matched))
        score = max(kw, SEMANTIC_CAP * sem) if sem is not None else kw
        result.criteria.append(
            CriterionResult(
                id=c.id,
                name=c.name,
                required=c.required,
                weight=c.weight,
                score=round(score, 2),
                keyword_score=kw,
                semantic_score=sem,
                matched=matched,
                snippets=snippets,
            )
        )
        if c.required and not matched and (sem is None or sem < config.required_semantic):
            result.screened_out = True
            shown = ", ".join(c.terms[:8]) + (", …" if len(c.terms) > 8 else "")
            result.notes.append(f"No mention of required '{c.name}' (searched: {shown}).")
    weight = sum(r.weight for r in result.criteria)
    if weight:
        result.score = round(sum(r.score * r.weight for r in result.criteria) / weight, 3)
    result.years_hint = years_hint(blocks)
    return result


def screen_all(
    job: JobProfile,
    ingested: IngestResult,
    config: ScreeningConfig,
    embed: Embedder | None = None,
) -> list[ScreenResult]:
    """Screen every candidate; return them best first, with ranks."""
    results = [
        screen_candidate(job, c, ingested.names.get(c.candidate_id, []), config, embed)
        for c in ingested.candidates
    ]
    results.sort(key=lambda r: (r.score is None, r.screened_out, -(r.score or 0.0)))
    for i, r in enumerate(results, 1):
        r.rank = i
    return results
