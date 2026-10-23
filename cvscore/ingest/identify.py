"""Works out what a document is and whose it is, from its file name and its content.

- ``doc_type_of``: CV, cover letter or other. The file name is one signal; the content is
  another (section headings and date ranges for a CV; a salutation, a closing and
  paragraphs of prose for a letter), so ``Jane Doe.pdf`` or ``application.pdf`` is still
  recognised, and a file named "cv" that is really a letter is caught.
- ``names_in``: the candidate's name, from the top of a CV, the signature of a letter, a
  "Name" answer in a form, or the file name.
- ``emails_in``: the candidate's own email addresses (not recruiters' role addresses).

Everything here is heuristic: the results come with reasons, and callers show them so a
person can correct them.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from cvscore.ingest.text_utils import is_section_keyword, normalize_text
from cvscore.models import BlockKind, DocType, SourceDocument
from cvscore.pii import EMAIL_RE, has_contact_details

# --- File names ----------------------------------------------------------------------

_LETTER_TOKENS = frozenset(
    {
        "cover",
        "letter",
        "coverletter",
        "motivation",
        "motivational",
        "motivationsschreiben",
        "anschreiben",
        "lettre",
        "carta",
        "søknad",
        "soknad",
        "søknadsbrev",
        "soknadsbrev",
        "følgebrev",
        "folgebrev",
        "ansøgning",
        "ansogning",
        "ansökan",
        "ansokan",
        "personligt",
        "personlig",
        "brev",
    }
)
_CV_TOKENS = frozenset(
    {
        "cv",
        "resume",
        "résumé",
        "resumé",
        "lebenslauf",
        "curriculum",
        "vitae",
        "vita",
        "merittliste",
    }
)
_FORM_TOKENS = frozenset(
    {"form", "questionnaire", "answers", "bewerbungsformular", "fragebogen", "skjema"}
)
# Words in file names that say nothing about the person.
_NOISE_TOKENS = frozenset(
    {
        "application",
        "bewerbung",
        "final",
        "new",
        "updated",
        "update",
        "latest",
        "draft",
        "copy",
        "kopi",
        "version",
        "ver",
        "signed",
        "english",
        "eng",
        "en",
        "norsk",
        "no",
        "nb",
        "de",
        "deutsch",
        "pdf",
        "doc",
        "docx",
        "my",
        "for",
        "to",
        "the",
        "and",
        "og",
        "på",
        "til",
        "job",
        "position",
        "stilling",
    }
)
# Other documents candidates send along.
_OTHER_DOC_TOKENS = frozenset(
    {
        "notes",
        "interview",
        "portfolio",
        "certificate",
        "certificates",
        "diploma",
        "transcript",
        "references",
        "reference",
        "attest",
        "attester",
        "vitnemål",
        "vitnemal",
        "karakterer",
        "zeugnis",
        "zeugnisse",
    }
)
TYPE_TOKENS = _LETTER_TOKENS | _CV_TOKENS | _FORM_TOKENS | _OTHER_DOC_TOKENS
_TOKEN_SPLIT = re.compile(r"[\s_\-.()\[\],+&]+")
_CAMEL = re.compile(r"(?<=[a-zà-ÿ])(?=[A-ZÀ-Þ])")


def file_tokens(stem: str) -> list[str]:
    """Lower-case tokens of a file name.

    ``JaneDoe_CoverLetter-v2`` -> jane, doe, cover, letter, v2."""
    return [t for t in _TOKEN_SPLIT.split(_CAMEL.sub(" ", stem).lower()) if t]


def type_from_filename(path: Path) -> DocType | None:
    tokens = set(file_tokens(path.stem))
    if tokens & _LETTER_TOKENS:
        return DocType.COVER_LETTER
    if tokens & _CV_TOKENS:
        return DocType.CV
    if tokens & _FORM_TOKENS:
        return DocType.APPLICATION_FORM
    if tokens & _OTHER_DOC_TOKENS:
        return DocType.OTHER
    return None


def filename_key(path: Path) -> str:
    """The file name without document-type words, versions and dates: what is left usually
    names the candidate (``Jane_Doe_CV_v2.pdf`` -> ``jane doe``) or an applicant number."""
    return _stem_key(path.stem)


@lru_cache(maxsize=8192)  # grouping a folder compares every file name with every other
def _stem_key(stem: str) -> str:
    kept = [
        t
        for t in file_tokens(stem)
        if t not in TYPE_TOKENS
        and t not in _NOISE_TOKENS
        and not re.fullmatch(r"v\d+|\d{1,2}|(19|20)\d\d(\d{2,4})?", t)
    ]
    return " ".join(kept)


def name_from_filename(path: Path) -> str | None:
    tokens = filename_key(path).split()
    if (
        2 <= len(tokens) <= 4
        and all(t.isalpha() and len(t) >= 2 for t in tokens)
        and not any(t in _NOT_NAME_WORDS for t in tokens)
    ):
        return " ".join(t.capitalize() for t in tokens)
    return None


# --- Content: document type ----------------------------------------------------------

_SALUTATION_RE = re.compile(
    r"^(dear|hi|hello|to whom it may concern|hei|hallo|kjære|god dag|hej|kære|bästa|"
    r"sehr geehrte|liebe|lieber|guten tag|bonjour|madame|monsieur|estimad[oa]s?|"
    r"buongiorno|gentile|geachte|beste)\b",
    re.I,
)
_CLOSING_RE = re.compile(
    r"^(yours sincerely|sincerely|kind regards|best regards|warm regards|warmest regards|"
    r"regards|best wishes|yours faithfully|yours truly|respectfully|thank you|"
    r"med vennlig hilsen|vennlig hilsen|mvh|med venlig hilsen|venlig hilsen|"
    r"med vänliga hälsningar|vänliga hälsningar|mvh|mit freundlichen grüßen|"
    r"mit freundlichen gruessen|freundliche grüße|beste grüße|cordialement|bien à vous|"
    r"atentamente|cordiali saluti|met vriendelijke groet)\b[\s,.!]*",
    re.I,
)
_APPLY_RE = re.compile(
    r"\b(i am writing|i'm writing|i would like to apply|i wish to apply|i am applying|"
    r"apply for the (position|role|job)|application for|interested in the (position|role)|"
    r"your (company|team|organisation|organization)|hereby apply|jeg søker|søker (herved )?"
    r"(på )?stillingen|søknad på|bewerbe mich|ich bewerbe|jag söker|ansøger hermed)\b",
    re.I,
)
_DATE_RANGE_RE = re.compile(
    r"\b(?:\d{1,2}[./]\s*)?(?:19|20)\d\d\s*(?:-|–|—|to|til|bis)\s*"
    r"(?:(?:\d{1,2}[./]\s*)?(?:19|20)\d\d|present|now|current|today|nå|dags dato|heute|i dag)\b",
    re.I,
)
_FIRST_PERSON_RE = re.compile(r"\b(i|my|me|jeg|min|mitt|mine|meg|ich|mein|meine|jag|mig)\b", re.I)


@dataclass
class TypeGuess:
    doc_type: DocType
    reason: str
    warnings: list[str] = field(default_factory=list)


def _content_scores(doc: SourceDocument) -> tuple[float, float, list[str], list[str]]:
    """(cv score, letter score, cv reasons, letter reasons) from the text alone."""
    blocks = [b for b in doc.blocks if b.text.strip()]
    if not blocks:
        return 0.0, 0.0, [], []
    texts = [normalize_text(b.text) for b in blocks]
    cv, letter = 0.0, 0.0
    cv_why: list[str] = []
    letter_why: list[str] = []

    sections = sum(1 for t in texts if len(t) <= 40 and is_section_keyword(t))
    if sections >= 2:
        cv += 2 + min(sections - 2, 4) * 0.5
        cv_why.append(f"{sections} CV section headings")
    dates = sum(len(_DATE_RANGE_RE.findall(t)) for t in texts)
    if dates >= 2:
        cv += 1.5 + min(dates - 2, 4) * 0.25
        cv_why.append(f"{dates} date ranges")
    items = sum(1 for b in blocks if b.kind in (BlockKind.LIST_ITEM, BlockKind.TABLE_ROW))
    if len(blocks) >= 8 and items / len(blocks) >= 0.25:
        cv += 1
        cv_why.append("mostly lists")

    if any(_SALUTATION_RE.match(t) for t in texts[:10]):
        letter += 3
        letter_why.append("salutation")
    if any(_CLOSING_RE.match(t) for t in texts[-12:]):
        letter += 3
        letter_why.append("closing")
    if any(_APPLY_RE.search(t) for t in texts):
        letter += 2
        letter_why.append("applies for a position")
    words = sum(len(t.split()) for t in texts)
    prose = sum(len(t.split()) for t in texts if len(t.split()) >= 25)
    if words >= 80 and prose / words >= 0.6:
        letter += 1.5
        letter_why.append("written as prose")
        first_person = sum(len(_FIRST_PERSON_RE.findall(t)) for t in texts)
        if first_person / words >= 0.03:
            letter += 1
            letter_why.append("first person")
    return cv, letter, cv_why, letter_why


def doc_type_of(path: Path, doc: SourceDocument | None) -> TypeGuess:
    """Best guess of a document's type from its file name and content."""
    by_name = type_from_filename(path)
    if doc is None or doc.doc_type is DocType.APPLICATION_FORM:
        if doc is not None:
            return TypeGuess(DocType.APPLICATION_FORM, "structured form file")
        return TypeGuess(by_name or DocType.OTHER, "file name" if by_name else "unknown")
    cv, letter, cv_why, letter_why = _content_scores(doc)
    if by_name in (DocType.APPLICATION_FORM, DocType.OTHER):
        return TypeGuess(by_name, "file name")
    name_bonus = 4.0
    if by_name is DocType.CV:
        cv += name_bonus
    elif by_name is DocType.COVER_LETTER:
        letter += name_bonus

    def reason(kind: DocType, why: list[str]) -> str:
        parts = (["file name"] if by_name is kind else []) + why
        return ", ".join(parts)

    warnings: list[str] = []
    if by_name is DocType.CV and letter - name_bonus >= 6 and letter > cv:
        warnings.append("Named as a CV but reads like a cover letter.")
    if by_name is DocType.COVER_LETTER and cv - name_bonus >= 5 and cv > letter:
        warnings.append("Named as a cover letter but reads like a CV.")
    raw_letter = letter - (name_bonus if by_name is DocType.COVER_LETTER else 0)
    raw_cv = cv - (name_bonus if by_name is DocType.CV else 0)
    if raw_letter >= 6 and raw_cv >= 4:
        warnings.append("Looks like a cover letter and a CV in one file; treated as a CV.")
        return TypeGuess(DocType.CV, reason(DocType.CV, cv_why), warnings)
    if max(cv, letter) < 2:
        return TypeGuess(DocType.OTHER, "no clear CV or letter features", warnings)
    if cv >= letter:
        return TypeGuess(DocType.CV, reason(DocType.CV, cv_why), warnings)
    return TypeGuess(DocType.COVER_LETTER, reason(DocType.COVER_LETTER, letter_why), warnings)


# --- Content: the candidate's name ---------------------------------------------------

# Words that make a short capitalised line a job title, heading or place, not a name.
_NOT_NAME_WORDS = frozenset(
    {
        "curriculum",
        "vitae",
        "resume",
        "résumé",
        "cv",
        "cover",
        "letter",
        "application",
        "page",
        "side",
        "contact",
        "profile",
        "summary",
        "personal",
        "senior",
        "junior",
        "lead",
        "principal",
        "staff",
        "chief",
        "head",
        "director",
        "manager",
        "engineer",
        "engineering",
        "developer",
        "scientist",
        "science",
        "data",
        "software",
        "consultant",
        "analyst",
        "architect",
        "designer",
        "specialist",
        "officer",
        "assistant",
        "associate",
        "intern",
        "student",
        "researcher",
        "professor",
        "teacher",
        "nurse",
        "accountant",
        "advisor",
        "adviser",
        "coordinator",
        "administrator",
        "technician",
        "executive",
        "president",
        "founder",
        "owner",
        "team",
        "group",
        "project",
        "product",
        "marketing",
        "sales",
        "finance",
        "learning",
        "machine",
        "ai",
        "ml",
        "it",
        "phd",
        "msc",
        "bsc",
        "mba",
        "dr",
        "university",
        "universitet",
        "college",
        "school",
        "company",
        "ltd",
        "inc",
        "gmbh",
        "as",
        "asa",
        "ab",
        "references",
        "reference",
        "notes",
        "interview",
        "portfolio",
        "certificate",
        "diploma",
        "transcript",
        "attest",
        "vitnemål",
        "karakterer",
        "dear",
        "regards",
        "sincerely",
        "hilsen",
        "norway",
        "norge",
        "sweden",
        "denmark",
        "germany",
        "oslo",
        "bergen",
        "stavanger",
        "trondheim",
        "london",
        # places that look like names ("United Kingdom", "New York")
        "united",
        "kingdom",
        "states",
        "america",
        "republic",
        "new",
        "york",
        "san",
        "francisco",
        "street",
        "road",
        "avenue",
        "india",
        "china",
        "korea",
        "japan",
        "france",
        "spain",
        "italy",
        "canada",
        "australia",
        "netherlands",
        "switzerland",
        "poland",
        "finland",
        "iceland",
        "europe",
        "remote",
    }
)
_PARTICLES = frozenset(
    {"van", "von", "de", "der", "den", "da", "di", "du", "la", "le", "bin", "al", "el"}
)
_NAME_LABEL_RE = re.compile(
    r"^(full name|name|navn|fullt navn|namn|name des bewerbers)\s*[:\-]\s*(.+)$", re.I
)


def looks_like_name(text: str) -> bool:
    """2-4 capitalised words (particles and initials allowed), none a title word."""
    text = normalize_text(text)
    if re.search(r"[\d,:;|/@!?()]", text):
        return False
    tokens = text.split()
    if not 2 <= len(tokens) <= 4:
        return False
    real = initials = 0
    for t in tokens:
        bare = t.strip(".'’")
        if t.lower() in _PARTICLES:
            continue
        if re.fullmatch(r"[A-ZÀ-Þ]\.", t):  # an initial
            initials += 1
            continue
        if not bare or not all(ch.isalpha() or ch in "-'’" for ch in bare):
            return False
        if not bare[0].isupper() or bare.lower() in _NOT_NAME_WORDS:
            return False
        if is_section_keyword(bare):
            return False
        real += 1
    return real >= 2 or (real == 1 and initials >= 1)


def pretty_name(text: str) -> str:
    """``JANE DOE`` or ``jane doe`` -> ``Jane Doe``; mixed case is kept."""
    text = normalize_text(text)
    if text.isupper() or text.islower():
        return " ".join(
            w if w.lower() in _PARTICLES else w.capitalize() for w in text.lower().split()
        )
    return text


def _top_area(doc: SourceDocument, limit: int = 12) -> list[str]:
    """Lines before the first section heading, large-type lines (headings) first."""
    blocks = []
    for b in doc.blocks[:limit]:
        if b.kind is BlockKind.HEADING and is_section_keyword(b.text):
            break
        blocks.append(b)
    headings = [b.text for b in blocks if b.kind is BlockKind.HEADING]
    # A large name wraps in a narrow sidebar: "Ingrid" / "Solberg", "SOFIE" / "HANSEN".
    pairs = [
        f"{a.text} {b.text}"
        for a, b in zip(blocks, blocks[1:], strict=False)
        if a.kind is b.kind is BlockKind.HEADING and len(a.text.split()) == len(b.text.split()) == 1
    ]
    return headings + pairs + [b.text for b in blocks if b.kind is not BlockKind.HEADING]


def _name_at_start(text: str) -> str | None:
    """The name at the start of a top line: "Jane Doe · Data Scientist", "Jane Doe | Oslo",
    or "Jane Doe Email: jane@x.com" (name and contact details on one row)."""
    text = normalize_text(text)
    first = re.split(r"\s+[|·•–—-]\s+", text)[0]
    if looks_like_name(first):
        return first
    tokens = text.split()
    for n in (4, 3, 2):
        rest = " ".join(tokens[n:])
        if (
            len(tokens) > n
            and looks_like_name(" ".join(tokens[:n]))
            and (has_contact_details(rest) or re.match(r"(e-?mail|phone|mobile|tel)\b", rest, re.I))
        ):
            return " ".join(tokens[:n])
    return None


def _signature(doc: SourceDocument) -> str | None:
    texts = [normalize_text(b.text) for b in doc.blocks if b.text.strip()]
    for i in range(len(texts) - 1, -1, -1):
        m = _CLOSING_RE.match(texts[i])
        if not m:
            continue
        rest = texts[i][m.end() :].strip()
        for cand in [rest, *texts[i + 1 : i + 4]]:
            # A signature line may continue with a title or phone: "Jane Doe | +47 ..."
            first = re.split(r"\s*[|,·•]\s*", cand)[0] if cand else ""
            if first and looks_like_name(first):
                return pretty_name(first)
        return None
    return None


def names_in(doc: SourceDocument | None, path: Path) -> list[tuple[str, str]]:
    """Candidate names found in a document, best first, as (name, where it was found)."""
    found: list[tuple[str, str]] = []
    if doc is not None:
        for b in doc.blocks:
            m = _NAME_LABEL_RE.match(normalize_text(b.text))
            if m and looks_like_name(m.group(2)):
                found.append((pretty_name(m.group(2)), "'Name' field"))
        if doc.doc_type is DocType.APPLICATION_FORM:
            blocks = doc.blocks
            for q, a in zip(blocks, blocks[1:], strict=False):
                if (
                    q.kind is BlockKind.HEADING
                    and re.search(r"\bname\b|\bnavn\b", q.text, re.I)
                    and looks_like_name(a.text)
                ):
                    found.append((pretty_name(a.text), "form answer"))
        elif doc.doc_type is DocType.COVER_LETTER:
            sig = _signature(doc)
            if sig:
                found.append((sig, "letter signature"))
        else:
            for text in _top_area(doc):
                name = _name_at_start(text)
                if name:
                    found.append((pretty_name(name), "top of the CV"))
                    break
            if not found:
                sig = _signature(doc)
                if sig:
                    found.append((sig, "signature"))
    from_file = name_from_filename(path)
    if from_file:
        found.append((from_file, "file name"))
    unique: list[tuple[str, str]] = []
    for name, where in found:
        if not any(same_person(name, n) for n, _ in unique):
            unique.append((name, where))
    return unique


@lru_cache(maxsize=8192)  # grouping a folder compares every name with every other
def name_tokens(name: str) -> tuple[str, ...]:
    plain = unicodedata.normalize("NFKD", name.lower())
    plain = "".join(ch for ch in plain if not unicodedata.combining(ch))
    plain = plain.replace("ø", "o").replace("æ", "ae").replace("ß", "ss")
    return tuple(t for t in re.split(r"[^a-z]+", plain) if len(t) >= 2 and t not in _PARTICLES)


def same_person(a: str, b: str) -> bool:
    """Names match if they share first and last name, or one contains the other (2+ words)."""
    ta, tb = name_tokens(a), name_tokens(b)
    if len(ta) < 2 or len(tb) < 2:
        return False
    if ta == tb or (ta[0] == tb[0] and ta[-1] == tb[-1]):
        return True
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return set(small) <= set(big)


# --- Content: the candidate's email --------------------------------------------------

_ROLE_ADDRESS_RE = re.compile(
    r"^(hr|jobs?|careers?|recruit\w*|rekrutt\w*|talent|post|postmottak|info|contact|kontakt|"
    r"hello|hei|noreply|no-reply|admin|office|apply|applications?|soknad|stilling)\b",
    re.I,
)


def emails_in(doc: SourceDocument | None) -> set[str]:
    """The candidate's own email addresses: in a CV's or form's contact details, or under a
    letter's closing (a letter's top may hold the recipient's address). Role addresses such
    as hr@ or jobs@ are ignored."""
    if doc is None:
        return set()
    if doc.doc_type is DocType.COVER_LETTER:
        texts = [b.text for b in doc.blocks]
        closing = next(
            (
                i
                for i in range(len(texts) - 1, -1, -1)
                if _CLOSING_RE.match(normalize_text(texts[i]))
            ),
            None,
        )
        texts = texts[closing:] if closing is not None else []
    else:
        texts = [b.text for b in doc.blocks if b.is_contact]
    out: set[str] = set()
    for text in texts:
        for e in EMAIL_RE.findall(text):
            if not _ROLE_ADDRESS_RE.match(e.split("@", 1)[0]):
                out.add(e.lower())
    return out
