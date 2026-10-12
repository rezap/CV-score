"""Reads candidate documents (PDF, Word, text, application forms) into typed models."""

from cvscore.ingest.document import ExtractionError
from cvscore.ingest.forms import read_form_table
from cvscore.ingest.pipeline import IngestResult, extract_file, ingest

__all__ = ["ExtractionError", "IngestResult", "extract_file", "ingest", "read_form_table"]
