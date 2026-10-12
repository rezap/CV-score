"""Application forms: generic adapters until the real form format is known.

A form becomes a list of question/answer pairs, then a ``SourceDocument`` where each
question is a heading and its answer a paragraph. Supported shapes:

Per candidate (a file in the candidate's folder):
- JSON / YAML mapping: ``{"Why this role?": "...", "Years of experience": 5}``
- JSON / YAML list: ``[{"question": "...", "answer": "..."}, ...]``
- CSV with ``question`` and ``answer`` columns, or a header row plus one answer row.

Many candidates in one table (an ATS or survey export): see ``read_form_table``.

Answers to personal-data questions (name, email, date of birth, ...) are flagged as
contact text so the redaction step removes them before scoring.
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from cvscore.ingest.document import ExtractionError, build_document
from cvscore.ingest.text_utils import normalize_text
from cvscore.models import BlockKind, DocType, SourceDocument, TextBlock

FORM_SUFFIXES = frozenset({".json", ".yaml", ".yml", ".csv"})

_PERSONAL_FIELD_RE = re.compile(
    r"\b(name|first name|last name|surname|e-?mail|phone|mobile|telephone|address|street|"
    r"city|post ?code|zip|date of birth|birth ?date|dob|age|gender|sex|nationality|"
    r"citizenship|marital|photo|linkedin|vorname|nachname|geburtsdatum|geschlecht|"
    r"staatsangehörigkeit|anschrift|telefon|adresse)\b",
    re.I,
)


class FormAnswer(BaseModel):
    question: str
    answer: str


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, Mapping):
        return "; ".join(f"{k}: {_stringify(v)}" for k, v in value.items())
    if isinstance(value, Sequence) and not isinstance(value, str):
        return "; ".join(_stringify(v) for v in value)
    return str(value)


def answers_from_data(data: Any) -> list[FormAnswer]:
    if isinstance(data, Mapping):
        pairs = [(str(k), _stringify(v)) for k, v in data.items()]
    elif isinstance(data, list) and all(isinstance(x, Mapping) for x in data):
        pairs = []
        for item in data:
            keys = {k.lower(): k for k in item}
            q, a = keys.get("question"), keys.get("answer")
            if q is None or a is None:
                raise ExtractionError("Form list items need 'question' and 'answer' keys.")
            pairs.append((str(item[q]), _stringify(item[a])))
    else:
        raise ExtractionError("Form must be a mapping or a list of question/answer objects.")
    answers = [FormAnswer(question=normalize_text(q), answer=a.strip()) for q, a in pairs]
    return [x for x in answers if x.question and x.answer]


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return [{k.strip(): (v or "") for k, v in row.items() if k} for row in csv.DictReader(f)]


def read_form_file(path: Path) -> list[FormAnswer]:
    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            return answers_from_data(json.loads(path.read_text(encoding="utf-8-sig")))
        if suffix in (".yaml", ".yml"):
            return answers_from_data(yaml.safe_load(path.read_text(encoding="utf-8-sig")))
        if suffix == ".csv":
            rows = _read_csv_rows(path)
            if rows and {k.lower() for k in rows[0]} >= {"question", "answer"}:
                return answers_from_data(rows)
            if len(rows) == 1:
                return answers_from_data(rows[0])
            raise ExtractionError(
                f"Form CSV has {len(rows)} rows; expected question/answer columns or a single "
                "answer row. For a multi-candidate export use --forms with --form-key."
            )
    except (json.JSONDecodeError, yaml.YAMLError, csv.Error, UnicodeDecodeError) as e:
        raise ExtractionError(f"Cannot parse form: {e}") from e
    raise ExtractionError(f"Unsupported form format: {suffix}")


def read_form_table(path: Path, key_column: str) -> dict[str, list[FormAnswer]]:
    """Multi-candidate CSV (one row per candidate) keyed by ``key_column``.

    The key must match the candidate's folder name (or manifest label). The key column
    itself is not included in the answers.
    """
    rows = _read_csv_rows(path)
    if rows and key_column not in rows[0]:
        raise ExtractionError(
            f"Column '{key_column}' not found in {path.name}; columns: {', '.join(rows[0])}"
        )
    table: dict[str, list[FormAnswer]] = {}
    for row in rows:
        key = row.pop(key_column, "").strip()
        if key:
            table[key] = answers_from_data(row)
    return table


def form_document(
    answers: list[FormAnswer], *, path: Path, display_path: str, extractor: str
) -> SourceDocument:
    blocks: list[TextBlock] = []
    for a in answers:
        personal = bool(_PERSONAL_FIELD_RE.search(a.question))
        blocks.append(TextBlock(kind=BlockKind.HEADING, text=a.question, is_contact=personal))
        blocks.append(
            TextBlock(kind=BlockKind.PARAGRAPH, text=normalize_text(a.answer), is_contact=personal)
        )
    return build_document(
        doc_type=DocType.APPLICATION_FORM,
        path=path,
        display_path=display_path,
        blocks=blocks,
        extractor=extractor,
        detect_contact=False,
        check_length=False,  # short forms are normal; the length check is meant for CVs
    )


def extract_form(path: Path, *, display_path: str) -> SourceDocument:
    return form_document(
        read_form_file(path),
        path=path,
        display_path=display_path,
        extractor=f"form:{path.suffix.lower().lstrip('.')}",
    )
