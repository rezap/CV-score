"""Text embeddings with an on-disk cache (used by semantic screening)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from langchain_core.embeddings import Embeddings

Embedder = Callable[[list[str]], list[list[float]]]


def cached_embedder(model: Embeddings, model_id: str, cache_dir: Path | None) -> Embedder:
    """Embed texts, reusing vectors cached by (model, text); only new texts are sent."""

    def path(text: str) -> Path:
        assert cache_dir is not None
        key = hashlib.sha256(f"{model_id}\0{text}".encode()).hexdigest()
        return cache_dir / "embeddings" / f"{key[:2]}" / f"{key}.json"

    def embed(texts: list[str]) -> list[list[float]]:
        found: dict[int, list[float]] = {}
        if cache_dir is not None:
            for i, t in enumerate(texts):
                p = path(t)
                if p.is_file():
                    found[i] = json.loads(p.read_text())
        todo = [i for i in range(len(texts)) if i not in found]
        if todo:
            vectors = model.embed_documents([texts[i] for i in todo])
            for i, v in zip(todo, vectors, strict=True):
                found[i] = list(v)
                if cache_dir is not None:
                    p = path(texts[i])
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(json.dumps(v))
        return [found[i] for i in range(len(texts))]

    return embed
