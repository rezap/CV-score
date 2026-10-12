"""Structured (schema-validated) LLM calls, with an on-disk cache.

Every LLM step in the pipeline asks for a Pydantic object, never free text. Schemas sent
to the model live next to the step that uses them and follow OpenAI strict-mode rules:
every field required (``X | None`` for optional values, no defaults), no numeric bounds,
no free-form dicts. ``tests/test_llm_schemas.py`` enforces this.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol, TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel

from cvscore.config import ModelRole, Settings

T = TypeVar("T", bound=BaseModel)


class StructuredLLM(Protocol):
    """Produces an instance of ``schema`` from chat messages."""

    model_id: str

    def generate(
        self, schema: type[T], messages: Sequence[BaseMessage], config: RunnableConfig | None
    ) -> T: ...


LLMProvider = Callable[[ModelRole], StructuredLLM]


class ChatModelLLM:
    """A LangChain chat model used with strict JSON-schema structured output."""

    def __init__(self, model: BaseChatModel, model_id: str) -> None:
        self.model = model
        self.model_id = model_id

    def generate(
        self, schema: type[T], messages: Sequence[BaseMessage], config: RunnableConfig | None
    ) -> T:
        runnable = self.model.with_structured_output(schema, method="json_schema", strict=True)
        result = runnable.invoke(list(messages), config=config)
        if not isinstance(result, schema):  # pragma: no cover - defensive
            result = schema.model_validate(result)
        return result


class CachedLLM:
    """Caches results on disk by (model, schema, messages), so re-runs cost nothing."""

    def __init__(self, inner: StructuredLLM, cache_dir: Path) -> None:
        self.inner = inner
        self.model_id = inner.model_id
        self.cache_dir = cache_dir

    def _key(self, schema: type[BaseModel], messages: Sequence[BaseMessage]) -> str:
        payload = json.dumps(
            {
                "model": self.model_id,
                "schema": schema.__name__,
                "schema_def": schema.model_json_schema(),
                "messages": [[m.type, m.content] for m in messages],
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def generate(
        self, schema: type[T], messages: Sequence[BaseMessage], config: RunnableConfig | None
    ) -> T:
        path = self.cache_dir / f"{self._key(schema, messages)}.json"
        if path.is_file():
            return schema.model_validate_json(path.read_text(encoding="utf-8"))
        result = self.inner.generate(schema, messages, config)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(result.model_dump_json(), encoding="utf-8")
        return result


def azure_provider(settings: Settings, *, cache_dir: Path | None = None) -> LLMProvider:
    """Provider backed by the Azure deployments configured for each role."""
    from cvscore.llm.factory import get_chat_model

    instances: dict[ModelRole, StructuredLLM] = {}

    def provide(role: ModelRole) -> StructuredLLM:
        if role not in instances:
            model = get_chat_model(role, settings.azure)
            deployment = settings.azure.models[role].deployment
            llm: StructuredLLM = ChatModelLLM(model, model_id=f"azure:{deployment}")
            if cache_dir is not None:
                llm = CachedLLM(llm, cache_dir)
            instances[role] = llm
        return instances[role]

    return provide
