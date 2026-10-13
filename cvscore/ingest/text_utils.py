"""Text normalisation and section-heading detection shared by all extractors."""

from __future__ import annotations

import re
import unicodedata

# Characters CV templates use as bullets. Symbol bullets may touch the text ("•Developed");
# "-", "*", dashes and "o" only count when followed by a space ("-5% churn" is not a bullet).
# U+E000-U+F8FF: Symbol/Wingdings glyphs, e.g. Word's default bullet exported to PDF (U+F0B7).
_BULLET_RE = re.compile(r"^\s*(?:[•●○◦▪▫■□►▶➢➤✓✔→·‣⁃\ue000-\uf8ff]\s*|[*\u2013\u2014\-o]\s+)")
_WS_RE = re.compile(r"[ \t\u00a0\u2000-\u200b\u202f\u205f\u3000]+")
_ZERO_WIDTH_RE = re.compile(r"[\u200c\u200d\u2060\ufeff\u00ad]")  # incl. soft hyphen

# Section headings commonly found in CVs and cover letters. Matched case-insensitively against
# the whole line (ignoring a trailing colon), so ordinary sentences never match.
# Extend this list for further languages.
SECTION_KEYWORDS: frozenset[str] = frozenset(
    {
        # English
        "profile",
        "summary",
        "professional summary",
        "personal statement",
        "about me",
        "objective",
        "career objective",
        "experience",
        "work experience",
        "professional experience",
        "employment",
        "employment history",
        "work history",
        "career history",
        "relevant experience",
        "education",
        "education and training",
        "academic background",
        "qualifications",
        "skills",
        "key skills",
        "technical skills",
        "core competencies",
        "competencies",
        "expertise",
        "achievements",
        "key achievements",
        "accomplishments",
        "projects",
        "selected projects",
        "certifications",
        "certificates",
        "licenses and certifications",
        "courses",
        "training",
        "languages",
        "publications",
        "awards",
        "honours",
        "honors",
        "volunteering",
        "volunteer experience",
        "interests",
        "hobbies",
        "references",
        "contact",
        "contact details",
        "personal details",
        "personal information",
        # German
        "profil",
        "kurzprofil",
        "berufserfahrung",
        "berufliche erfahrung",
        "beruflicher werdegang",
        "werdegang",
        "ausbildung",
        "bildung",
        "studium",
        "kenntnisse",
        "fähigkeiten",
        "fachkenntnisse",
        "kompetenzen",
        "sprachen",
        "sprachkenntnisse",
        "projekte",
        "zertifikate",
        "weiterbildung",
        "erfolge",
        "interessen",
        "hobbys",
        "persönliche daten",
        "kontakt",
        # Norwegian / Danish / Swedish
        "sammendrag",
        "nøkkelkvalifikasjoner",
        "arbeidserfaring",
        "erfaring",
        "yrkeserfaring",
        "erhvervserfaring",
        "arbetslivserfarenhet",
        "erfarenhet",
        "utdanning",
        "utdannelse",
        "uddannelse",
        "utbildning",
        "kompetanse",
        "kompetencer",
        "kompetenser",
        "ferdigheter",
        "færdigheder",
        "färdigheter",
        "språk",
        "sprog",
        "prosjekter",
        "projekter",
        "kurs",
        "sertifiseringer",
        "sertifikater",
        "certifieringar",
        "publikasjoner",
        "referanser",
        "referencer",
        "referenser",
        "personalia",
        "personlige opplysninger",
    }
)


def normalize_text(text: str) -> str:
    """Unicode-normalise and tidy whitespace on a single line or paragraph."""
    text = unicodedata.normalize("NFKC", text)  # also expands ligatures such as "ﬁ"
    text = _ZERO_WIDTH_RE.sub("", text)
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    return _WS_RE.sub(" ", text).strip()


def strip_bullet(line: str) -> tuple[bool, str]:
    """Return (is_bullet, text without the bullet marker)."""
    m = _BULLET_RE.match(line)
    if m:
        return True, line[m.end() :].strip()
    return False, line.strip()


def join_lines(lines: list[str]) -> str:
    """Join wrapped lines into one paragraph, undoing end-of-line hyphenation."""
    out = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if not out:
            out = line
        elif re.search(r"[^\W\d_]-$", out) and line[:1].islower():
            out = out[:-1] + line  # "manage-" + "ment" -> "management"
        else:
            out = f"{out} {line}"
    return out


def is_section_keyword(line: str) -> bool:
    key = normalize_text(line).rstrip(":").strip().lower()
    return key in SECTION_KEYWORDS
