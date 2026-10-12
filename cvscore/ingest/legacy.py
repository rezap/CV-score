"""Converts legacy formats (.doc, .rtf, .odt) to .docx with LibreOffice, if it is installed."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from cvscore.ingest.document import ExtractionError, sha256_file
from cvscore.ingest.word import extract_docx
from cvscore.models import DocType, SourceDocument

LEGACY_SUFFIXES = frozenset({".doc", ".rtf", ".odt"})
CONVERT_TIMEOUT_S = 120


def find_soffice() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


def extract_legacy(path: Path, *, doc_type: DocType, display_path: str) -> SourceDocument:
    soffice = find_soffice()
    if soffice is None:
        raise ExtractionError(
            f"{path.suffix} files need LibreOffice ('soffice' on PATH) to convert them. "
            "Install it, or save the file as .docx or .pdf."
        )
    with tempfile.TemporaryDirectory() as tmp:
        try:
            # A private profile: soffice silently does nothing if another LibreOffice
            # instance (e.g. the desktop app) is using the default one.
            profile = (Path(tmp) / "profile").as_uri()
            subprocess.run(
                [
                    soffice,
                    f"-env:UserInstallation={profile}",
                    "--headless",
                    "--convert-to",
                    "docx",
                    "--outdir",
                    tmp,
                    str(path),
                ],
                check=True,
                capture_output=True,
                timeout=CONVERT_TIMEOUT_S,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            raise ExtractionError(f"LibreOffice could not convert the file: {e}") from e
        converted = Path(tmp) / f"{path.stem}.docx"
        if not converted.is_file():
            raise ExtractionError("LibreOffice did not produce a .docx file.")
        doc = extract_docx(converted, doc_type=doc_type, display_path=display_path)
    # Identify the document by the original file, not the temporary conversion.
    doc.sha256 = sha256_file(path)
    doc.extractor = f"libreoffice+{doc.extractor}"
    return doc
