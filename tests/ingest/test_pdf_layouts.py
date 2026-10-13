"""PDF patterns found on real CVs (LaTeX templates, CV builders, design tools)."""

from pathlib import Path

import pytest

from cvscore.ingest import ExtractionError, extract_file
from cvscore.ingest.identify import names_in
from cvscore.ingest.pdf import _not_icon
from cvscore.models import BlockKind, DocType, SourceDocument
from tests.ingest import builders


def extract(path: Path) -> SourceDocument:
    return extract_file(path, doc_type=DocType.CV, display_path=path.name)


def test_words_without_space_characters_stay_apart(tmp_path: Path) -> None:
    doc = extract(builders.latex_style_pdf(tmp_path / "cv.pdf"))
    assert "Built an internal app to run batch campaigns for marketing" in doc.text
    assert "queuing and A/B testing" in doc.text


def test_design_tool_layout(tmp_path: Path) -> None:
    doc = extract(builders.design_tool_pdf(tmp_path / "cv.pdf"))
    headings = [b.text for b in doc.blocks if b.kind is BlockKind.HEADING]
    # Tracked headings are rejoined, with their word space.
    assert {"CONTACT", "ABOUT ME", "SKILLS", "EXPERIENCE"} <= set(headings)
    # The sidebar is read before the main column, although the widest empty band on the
    # page is the right margin, not the gap between the columns.
    text = doc.text
    assert text.index("Copywriting") < text.index("Marketing Manager")
    assert "Grew online sales from NOK 4M to NOK 11M in three years" in text
    # The name wraps over two lines.
    assert names_in(doc, Path("cv.pdf"))[0] == ("Sofie Hansen", "top of the CV")


def test_icon_font_glyphs_are_dropped() -> None:
    icon = {"object_type": "char", "fontname": "ABCDEF+FontAwesome5Free-Solid"}
    bullet = {"object_type": "char", "fontname": "Symbol"}
    assert not _not_icon(icon)
    assert _not_icon(bullet)  # Word draws list bullets in Symbol
    assert _not_icon({"object_type": "rect"})


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"", "The file is empty."),
        (b"<!doctype html><html>Sorry</html>", "is a web page"),
        (b"PK\x03\x04 docx", "is a Word document or a zip file"),
        (b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog", "damaged or incomplete"),
    ],
)
def test_unreadable_files_say_why(tmp_path: Path, content: bytes, message: str) -> None:
    path = tmp_path / "cv.pdf"
    path.write_bytes(content)
    with pytest.raises(ExtractionError, match=message):
        extract(path)


def test_password_protected(tmp_path: Path) -> None:
    with pytest.raises(ExtractionError, match="password-protected"):
        extract(builders.password_pdf(tmp_path / "cv.pdf"))


def test_cli_inspect(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from cvscore.cli import app

    cv = builders.design_tool_pdf(tmp_path / "cv.pdf")
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"")
    result = CliRunner().invoke(app, ["inspect", str(cv), str(broken), "--redacted"])
    assert result.exit_code == 0, result.output
    assert "name: Sofie Hansen (top of the CV)" in result.output
    assert "H  p1 EXPERIENCE" in result.output
    assert "[REDACTED: contact details]" in result.output
    assert "sofie.hansen@example.com" not in result.output
    assert "cannot read: The file is empty." in result.output


def test_cli_inspect_reads_scans_with_ocr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from cvscore import cli
    from cvscore.ingest.ocr import PageTranscript
    from tests.fakes import FakeLLM

    (tmp_path / "cvscore.yaml").write_text("langfuse:\n  enabled: false\n")
    fake = FakeLLM({PageTranscript: lambda _: {"text": "## Experience\n- Data Engineer, Initech"}})
    monkeypatch.setattr(cli, "llm_provider", lambda settings, cache_dir: fake.provider())
    scan = builders.scanned_pdf(tmp_path / "scan.pdf")

    result = CliRunner().invoke(cli.app, ["inspect", str(scan)])
    assert result.exit_code == 0, result.output
    assert "pdfplumber+ocr" in result.output and "Data Engineer, Initech" in result.output

    result = CliRunner().invoke(cli.app, ["inspect", str(scan), "--no-ocr"])
    assert "no text layer" in result.output and len(fake.calls) == 1
