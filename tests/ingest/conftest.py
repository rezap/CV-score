from pathlib import Path

import pytest

from tests.ingest import builders


@pytest.fixture(scope="session")
def files(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("cv_files")
    return {
        "two_column_pdf": builders.two_column_cv_pdf(d / "two_column.pdf"),
        "date_column_pdf": builders.date_column_cv_pdf(d / "date_column.pdf"),
        "letter_pdf": builders.single_column_pdf(d / "letter.pdf"),
        "scanned_pdf": builders.scanned_pdf(d / "scanned.pdf"),
        "cv_docx": builders.cv_docx(d / "cv.docx"),
        "layout_docx": builders.layout_table_docx(d / "layout.docx"),
    }
