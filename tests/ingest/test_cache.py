"""The extraction cache: files are parsed once, and again only when they or the
extractors change."""

import shutil
from pathlib import Path
from typing import Any

import pytest

from cvscore.ingest import cache, ingest, pipeline
from cvscore.ingest.cache import cached_extractor
from cvscore.ingest.document import ExtractionError
from cvscore.models import DocType, SourceDocument


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Counts real PDF extractions."""
    seen: list[str] = []
    real = pipeline.extract_pdf

    def counting(path: Path, **kw: Any) -> SourceDocument:
        seen.append(path.name)
        return real(path, **kw)

    monkeypatch.setattr(pipeline, "extract_pdf", counting)
    return seen


def make_folder(root: Path, files: dict[str, Path]) -> Path:
    (root / "Jane Doe").mkdir(parents=True)
    shutil.copy(files["two_column_pdf"], root / "Jane Doe" / "cv.pdf")
    shutil.copy(files["letter_pdf"], root / "Jane Doe" / "cover_letter.pdf")
    (root / "Sam Roe").mkdir()
    (root / "Sam Roe" / "broken.pdf").write_bytes(b"garbage")
    return root


def dump(result: pipeline.IngestResult) -> list[str]:
    return [c.model_dump_json() for c in result.candidates]


def test_second_ingest_reads_from_the_cache(
    tmp_path: Path, files: dict[str, Path], calls: list[str]
) -> None:
    root = make_folder(tmp_path / "candidates", files)
    first = ingest(root, cache_dir=tmp_path / "cache")
    assert sorted(calls) == ["broken.pdf", "cover_letter.pdf", "cv.pdf"]
    second = ingest(root, cache_dir=tmp_path / "cache")
    assert len(calls) == 3  # nothing parsed again, the broken file included
    assert dump(second) == dump(first)
    assert second.candidates[1].errors == first.candidates[1].errors != []


def test_changed_file_is_read_again(
    tmp_path: Path, files: dict[str, Path], calls: list[str]
) -> None:
    root = make_folder(tmp_path / "candidates", files)
    ingest(root, cache_dir=tmp_path / "cache")
    shutil.copy(files["date_column_pdf"], root / "Jane Doe" / "cv.pdf")
    calls.clear()
    ingest(root, cache_dir=tmp_path / "cache")
    assert calls == ["cv.pdf"]


def test_new_extractor_version_reads_again(
    tmp_path: Path, files: dict[str, Path], calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_folder(tmp_path / "candidates", files)
    ingest(root, cache_dir=tmp_path / "cache")
    monkeypatch.setattr(cache, "extraction_fingerprint", lambda: "a-newer-extractor")
    calls.clear()
    ingest(root, cache_dir=tmp_path / "cache")
    assert len(calls) == 3


def test_same_file_elsewhere_keeps_its_own_path(tmp_path: Path, files: dict[str, Path]) -> None:
    def extract(path: Path, doc_type: DocType, display: str) -> SourceDocument:
        return pipeline.extract_document(path, doc_type, display)

    cached = cached_extractor(extract, tmp_path / "cache")
    a = cached(files["two_column_pdf"], DocType.OTHER, "a/cv.pdf")
    b = cached(files["two_column_pdf"], DocType.OTHER, "b/other-name.pdf")
    assert (a.path, b.path) == ("a/cv.pdf", "b/other-name.pdf")
    assert a.text == b.text


def test_damaged_entry_is_replaced(tmp_path: Path, files: dict[str, Path]) -> None:
    def extract(path: Path, doc_type: DocType, display: str) -> SourceDocument:
        return pipeline.extract_document(path, doc_type, display)

    cached = cached_extractor(extract, tmp_path / "cache")
    first = cached(files["two_column_pdf"], DocType.OTHER, "cv.pdf")
    [entry] = (tmp_path / "cache").rglob("*.json")
    entry.write_text("{not json")
    assert cached(files["two_column_pdf"], DocType.OTHER, "cv.pdf") == first


def test_errors_are_cached_with_their_message(tmp_path: Path) -> None:
    calls: list[str] = []

    def extract(path: Path, doc_type: DocType, display: str) -> SourceDocument:
        calls.append(display)
        raise ExtractionError("The PDF is damaged or incomplete.")

    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"x")
    cached = cached_extractor(extract, tmp_path / "cache")
    for _ in range(2):
        with pytest.raises(ExtractionError, match="damaged or incomplete"):
            cached(broken, DocType.OTHER, "broken.pdf")
    assert calls == ["broken.pdf"]


def test_no_cache_dir_means_no_cache(tmp_path: Path) -> None:
    def extract(path: Path, doc_type: DocType, display: str) -> SourceDocument:
        raise AssertionError("unused")

    assert cached_extractor(extract, None) is extract
