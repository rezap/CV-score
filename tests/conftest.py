"""Keep tests independent of the developer's machine.

Without this, a real ``cvscore.yaml`` or ``.env`` in the repo root, or Azure / Langfuse
variables exported in the shell, would leak into the settings the tests load.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

_ENV_PREFIXES = ("CVSCORE_", "LANGFUSE_", "AZURE_", "OPENAI_")


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in list(os.environ):
        if name.startswith(_ENV_PREFIXES):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    yield
    # Code under test (e.g. load_dotenv) may set variables directly; drop them too.
    for name in list(os.environ):
        if name.startswith(_ENV_PREFIXES):
            del os.environ[name]
