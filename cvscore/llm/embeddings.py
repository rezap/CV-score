"""Text embeddings with an on-disk cache (used by semantic screening)."""

from __future__ import annotations

import hashlib
import json
from array import array
from collections.abc import Callable
from pathlib import Path

from langchain_core.embeddings import Embeddings

Embedder = Callable[[list[str]], list[list[float]]]


def cached_embedder(model: Embeddings, model_id: str, cache_dir: Path | None) -> Embedder:
    """Embed texts, reusing vectors cached by (model, text); only new texts are sent.

    Vectors are stored as raw float64 (``.f64``): exact, and a third of the size of JSON
    and much quicker to load. Entries in the older ``.json`` form are still read."""

    def path(text: str) -> Path:
        assert cache_dir is not None
        key = hashlib.sha256(f"{model_id}\0{text}".encode()).hexdigest()
        return cache_dir / "embeddings" / f"{key[:2]}" / f"{key}.f64"

    def load(p: Path) -> list[float] | None:
        if p.is_file():
            vector = array("d")
            vector.frombytes(p.read_bytes())
            return vector.tolist()
        legacy = p.with_suffix(".json")
        if legacy.is_file():
            data: list[float] = json.loads(legacy.read_text())
            return data
        return None

    def embed(texts: list[str]) -> list[list[float]]:
        found: dict[int, list[float]] = {}
        if cache_dir is not None:
            for i, t in enumerate(texts):
                vector = load(path(t))
                if vector is not None:
                    found[i] = vector
        todo = [i for i in range(len(texts)) if i not in found]
        if todo:
            vectors = model.embed_documents([texts[i] for i in todo])
            for i, v in zip(todo, vectors, strict=True):
                found[i] = list(v)
                if cache_dir is not None:
                    p = path(texts[i])
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(array("d", v).tobytes())
        return [found[i] for i in range(len(texts))]

    return embed
