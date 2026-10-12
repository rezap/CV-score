import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_dev_dependency_group_matches_dev_extra() -> None:
    data = tomllib.loads(PYPROJECT.read_text())
    assert data["dependency-groups"]["dev"] == data["project"]["optional-dependencies"]["dev"]
