"""Model-facing schemas must be valid for OpenAI/Azure strict structured outputs."""

from typing import Any

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel

from cvscore.extract import CandidateProfileDraft
from cvscore.ingest.ocr import PageTranscript
from cvscore.job import JobProfileDraft
from cvscore.score import ScoringDraft

UNSUPPORTED = {"default", "minimum", "maximum", "minLength", "maxLength", "pattern", "format"}


def walk(node: Any, path: str = "$") -> list[str]:
    problems: list[str] = []
    if isinstance(node, dict):
        for key in UNSUPPORTED & node.keys():
            problems.append(f"{path}: '{key}' not allowed")
        if node.get("type") == "object":
            props = set(node.get("properties", {}))
            if node.get("additionalProperties") is not False:
                problems.append(f"{path}: additionalProperties must be false")
            if set(node.get("required", [])) != props:
                problems.append(f"{path}: all properties must be required")
        for key, value in node.items():
            problems += walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            problems += walk(value, f"{path}[{i}]")
    return problems


@pytest.mark.parametrize(
    "schema", [JobProfileDraft, CandidateProfileDraft, ScoringDraft, PageTranscript]
)
def test_strict_schema(schema: type[BaseModel]) -> None:
    params = convert_to_openai_tool(schema, strict=True)["function"]["parameters"]
    assert walk(params) == []
