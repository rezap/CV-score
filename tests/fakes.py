"""A fake structured LLM for offline tests."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TypeVar

from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel

from cvscore.config import ModelRole
from cvscore.llm.structured import LLMProvider

T = TypeVar("T", bound=BaseModel)
Handler = Callable[[Sequence[BaseMessage]], Any]


class FakeLLM:
    """Answers each schema with a handler; records every call (role, schema, messages)."""

    model_id = "fake"

    def __init__(self, handlers: dict[type[BaseModel], Handler]) -> None:
        self.handlers = handlers
        self.calls: list[tuple[ModelRole, type[BaseModel], list[BaseMessage]]] = []
        self.role: ModelRole = ModelRole.SCORER
        self.cache_dirs: list[Path | None] = []  # cache folder of each provider made

    def generate(
        self, schema: type[T], messages: Sequence[BaseMessage], config: RunnableConfig | None
    ) -> T:
        self.calls.append((self.role, schema, list(messages)))
        result = self.handlers[schema](messages)
        return result if isinstance(result, schema) else schema.model_validate(result)

    def provider(self) -> LLMProvider:
        def provide(role: ModelRole) -> FakeLLM:
            self.role = role
            return self

        return provide

    def prompts(self) -> str:
        return "\n".join(str(m.content) for _, _, msgs in self.calls for m in msgs)


def documents(messages: Sequence[BaseMessage]) -> dict[str, str]:
    """The <document type="..."> sections of the last message."""
    text = str(messages[-1].content)
    return dict(re.findall(r'<document type="([^"]+)">\n(.*?)\n</document>', text, re.S))


def lines(text: str) -> list[str]:
    """Content lines of a document, without markdown markers."""
    out = []
    for line in text.splitlines():
        line = re.sub(r"^(#+ |- |\| )", "", line).rstrip(" |").strip()
        if len(line) >= 15:
            out.append(line)
    return out
