"""On-disk cache of extracted documents, so a folder is parsed once.

The key is the file's content (SHA-256), the document type it was extracted as, and a
fingerprint of the extraction code and libraries: editing or replacing a file, or
upgrading the extractors, reads the file again. Files that could not be read are cached
too (with their error). OCR is not cached here; its model calls have their own cache.

The cache holds the full, unredacted text of every document: treat it as personal data,
like the rest of the output folder.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from importlib import metadata
from pathlib import Path

from cvscore.ingest.discovery import Extractor
from cvscore.ingest.document import ExtractionError, sha256_file
from cvscore.models import DocType, SourceDocument


@lru_cache(maxsize=1)
def extraction_fingerprint() -> str:
    """Changes whenever the code or libraries that turn files into documents change."""
    package = Path(__file__).resolve().parent.parent
    h = hashlib.sha256()
    for path in sorted((package / "ingest").glob("*.py")) + [
        package / "pii.py",
        package / "models.py",
    ]:
        h.update(path.name.encode())
        h.update(path.read_bytes())
    for dist in ("pdfplumber", "pdfminer.six", "python-docx", "pypdfium2"):
        try:
            h.update(f"{dist}={metadata.version(dist)}".encode())
        except metadata.PackageNotFoundError:
            h.update(dist.encode())
    return h.hexdigest()[:16]


def cached_extractor(extract: Extractor, cache_dir: Path | None) -> Extractor:
    """``extract``, reusing earlier results stored under ``cache_dir/extract``."""
    if cache_dir is None:
        return extract
    folder = cache_dir / "extract" / extraction_fingerprint()

    def cached(path: Path, doc_type: DocType, display_path: str) -> SourceDocument:
        try:
            digest = sha256_file(path)
        except OSError:
            return extract(path, doc_type, display_path)  # let the extractor report it
        entry = folder / f"{digest}-{doc_type.value}{path.suffix.lower()}.json"
        if entry.is_file():
            try:
                data = json.loads(entry.read_text(encoding="utf-8"))
                if "error" in data:
                    raise ExtractionError(data["error"])
                doc = SourceDocument.model_validate(data)
                doc.path = display_path  # the same file may sit in another folder
                return doc
            except (ValueError, KeyError):
                pass  # a damaged entry: extract again
        try:
            doc = extract(path, doc_type, display_path)
        except ExtractionError as e:
            _write(entry, {"error": str(e)})
            raise
        _write(entry, doc.model_dump(mode="json"))
        return doc

    return cached


def _write(entry: Path, data: object) -> None:
    try:
        entry.parent.mkdir(parents=True, exist_ok=True)
        tmp = entry.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(entry)
    except OSError:
        pass  # a read-only or full disk only costs speed
