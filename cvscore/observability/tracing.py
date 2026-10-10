"""Langfuse tracing for LangChain / LangGraph runs.

Usage::

    tracer = Tracer.from_config(settings.langfuse)
    config = tracer.run_config(run_id="2026-10-10-backend-dev", trace_name="score-candidate",
                               tags=["job:backend-dev"], metadata={"candidate_id": "c-017"})
    graph.invoke(state, config=config)
    tracer.flush()

Each run is a Langfuse session (``run_id``); candidate IDs passed here must be
pseudonymous, never names.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables import RunnableConfig

from cvscore.config import LangfuseConfig
from cvscore.observability.masking import mask_pii


@dataclass
class Tracer:
    """Holds the Langfuse client (if enabled) and builds per-run LangChain configs."""

    client: Any | None = None
    _handlers: list[BaseCallbackHandler] = field(default_factory=list)

    @classmethod
    def from_config(cls, config: LangfuseConfig) -> Tracer:
        if not config.enabled:
            return cls()
        from langfuse import Langfuse
        from langfuse.langchain import CallbackHandler

        client = Langfuse(
            base_url=config.host,
            environment=config.environment,
            release=config.release,
            sample_rate=config.sample_rate,
            mask=mask_pii if config.mask_pii else None,
        )
        return cls(client=client, _handlers=[CallbackHandler()])

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def run_config(
        self,
        *,
        run_id: str,
        trace_name: str | None = None,
        tags: Sequence[str] = (),
        metadata: dict[str, Any] | None = None,
        max_concurrency: int | None = None,
    ) -> RunnableConfig:
        """A LangChain ``RunnableConfig`` that routes callbacks to Langfuse."""
        md: dict[str, Any] = {"run_id": run_id, **(metadata or {})}
        if self.enabled:
            md["langfuse_session_id"] = run_id
            md["langfuse_tags"] = list(tags)
            if trace_name:
                md["langfuse_trace_name"] = trace_name
        config: RunnableConfig = {
            "callbacks": list(self._handlers),
            "metadata": md,
            "tags": list(tags),
        }
        if trace_name:
            config["run_name"] = trace_name
        if max_concurrency is not None:
            config["max_concurrency"] = max_concurrency
        return config

    def check_auth(self) -> bool:
        """True if the Langfuse keys and host are valid. False when disabled."""
        return bool(self.client and self.client.auth_check())

    def flush(self) -> None:
        if self.client is not None:
            self.client.flush()
