import pytest

from cvscore.config import LangfuseConfig
from cvscore.observability import Tracer, mask_pii, mask_text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Contact jane.doe+cv@example.co.uk now", "Contact [EMAIL] now"),
        ("Call +49 (0)30 1234-5678.", "Call [PHONE]."),
        ("Tel: 020 7946 0958", "Tel: [PHONE]"),
        ("See https://www.linkedin.com/in/jane-doe", "See [PROFILE_URL]"),
        ("Led a team of 12 from 2019 to 2023, revenue +35%", None),
    ],
)
def test_mask_text(text: str, expected: str | None) -> None:
    assert mask_text(text) == (expected if expected is not None else text)


def test_mask_pii_recurses() -> None:
    data = {"input": ["a@b.com", ("x", 1)], "n": 3}
    assert mask_pii(data=data) == {"input": ["[EMAIL]", ("x", 1)], "n": 3}


def test_disabled_tracer_has_no_callbacks() -> None:
    tracer = Tracer.from_config(LangfuseConfig(enabled=False))
    config = tracer.run_config(run_id="r1", trace_name="t", tags=["job:x"], metadata={"c": 1})
    assert not tracer.enabled
    assert config["callbacks"] == []
    assert config["metadata"] == {"run_id": "r1", "c": 1}
    assert not tracer.check_auth()


def test_enabled_tracer_sets_langfuse_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://localhost:1")
    tracer = Tracer.from_config(LangfuseConfig(enabled=True))
    config = tracer.run_config(run_id="r1", trace_name="score", tags=["job:x"])
    assert tracer.enabled
    assert len(config["callbacks"]) == 1
    md = config["metadata"]
    assert md["langfuse_session_id"] == "r1"
    assert md["langfuse_tags"] == ["job:x"]
    assert md["langfuse_trace_name"] == "score"
    assert config["run_name"] == "score"
