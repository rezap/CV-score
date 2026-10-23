"""Where a word appears in a CV: in described work, or only in a list of skills.

Used to judge evidence levels without a model: "Terraform" in a skills section or a
summary is *mentioned*; in an experience or project entry it was *used* in work. The
sections come from the CV's headings (see ``ingest``), so this is a heuristic: the scoring
model makes the final call, and this only informs it and screening.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from functools import lru_cache
from typing import Literal

from cvscore.models import BlockKind, TextBlock

Context = Literal["work", "skills list"]

# Sections that list rather than describe: skills, summaries, education, languages, ...
_LISTING_SECTION = re.compile(
    r"skill|competen|kompetan|ferdighet|færdighed|technolog|teknolog|tools|verktøy|"
    r"expertise|summary|profile|profil|about|om meg|keyword|qualification|kvalifikasjon|"
    r"kenntnisse|fähigkeit|languages|språk|sprog|certific|sertifi|courses|kurs|"
    r"educat|utdann|uddann|ausbildung|interest|interesse|hobb|contact",
    re.I,
)


@lru_cache(maxsize=8192)  # the same blocks are folded for every term of every check
def fold(text: str) -> str:
    """Lower case without accents: "Statistikk"/"statistikk", "Résumé"/"resume"."""
    if text.isascii():
        return text  # nothing to decompose
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def term_pattern(term: str) -> re.Pattern[str] | None:
    """Whole-word pattern for a term, on accent-folded text. Spaces and hyphens inside it
    match either ("time series" = "time-series"). One- and two-letter terms ("R", "C",
    "Go", "ML") match only in the given case, and "R" not in "R&D"."""
    term = term.strip()
    if not term:
        return None
    if len(term) <= 2:
        return re.compile(rf"(?<![\w+#.\[]){re.escape(fold(term))}(?![\w+#&])")
    body = r"[\s\-]*".join(re.escape(fold(p)) for p in re.split(r"[\s\-]+", term))
    return re.compile(rf"(?<![\w+#]){body}(?![\w+#])", re.I)


def block_context(block: TextBlock) -> Context:
    """'skills list' for text in a listing section (or before any section), else 'work'."""
    if block.section is None or _LISTING_SECTION.search(block.section):
        return "skills list"
    return "work"


def placed_blocks(blocks: Iterable[TextBlock]) -> list[tuple[str, Context]]:
    """(folded text, context) for each non-heading block. A document without any
    recognised section is read as work throughout: with no structure to go by, placement
    must not cost a candidate credit."""
    content = [b for b in blocks if b.kind is not BlockKind.HEADING]
    structured = any(b.section for b in content)
    return [(fold(b.text), block_context(b) if structured else "work") for b in content]


def term_contexts(terms: Iterable[str], blocks: Iterable[TextBlock]) -> set[Context]:
    """The contexts in which any of ``terms`` occurs (headings are ignored)."""
    patterns = [p for t in terms if (p := term_pattern(t)) is not None]
    found: set[Context] = set()
    if not patterns:
        return found
    for text, context in placed_blocks(blocks):
        if any(p.search(text) for p in patterns):
            found.add(context)
            if len(found) == 2:
                break
    return found


def context_level(contexts: Iterable[str]) -> str:
    """The evidence level the word's placement alone supports: applied / mentioned / none."""
    found = set(contexts)
    if "work" in found:
        return "applied"
    return "mentioned" if found else "none"
