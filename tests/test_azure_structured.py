"""The real Azure chat model path, against a local fake Azure OpenAI endpoint."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from cvscore.config import AzureConfig, ChatModelConfig, ModelRole
from cvscore.job import JobProfileDraft
from cvscore.llm.factory import get_chat_model
from cvscore.llm.structured import CachedLLM, ChatModelLLM

DRAFT = {
    "title": "Data Engineer",
    "seniority": None,
    "domain": None,
    "summary": "Builds pipelines.",
    "responsibilities": ["Build pipelines"],
    "must_have": [{"description": "Python"}],
    "nice_to_have": [],
    "key_skills": ["Python"],
    "traits": [],
    "screening": [],
}


class FakeAzure(BaseHTTPRequestHandler):
    requests: list[dict[str, Any]] = []

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeAzure.requests.append(
            {"path": self.path, "auth": self.headers.get("Authorization"), "body": body}
        )
        response = json.dumps(
            {
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": "gpt",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": json.dumps(DRAFT)},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def endpoint(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    FakeAzure.requests = []
    server = HTTPServer(("127.0.0.1", 0), FakeAzure)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_structured_output_request(endpoint: str, tmp_path: Path) -> None:
    config = AzureConfig(
        endpoint=endpoint,
        models={ModelRole.AGENT: ChatModelConfig(deployment="gpt-dep")},
        max_retries=0,
    )
    model = get_chat_model(ModelRole.AGENT, config, token_provider=lambda: "entra-token")
    llm = CachedLLM(ChatModelLLM(model, model_id="azure:gpt-dep"), tmp_path / "cache")
    messages = [SystemMessage("system"), HumanMessage("job ad")]

    result = llm.generate(JobProfileDraft, messages, None)
    assert result == JobProfileDraft.model_validate(DRAFT)

    [req] = FakeAzure.requests
    assert req["path"].startswith("/openai/deployments/gpt-dep/chat/completions")
    assert req["auth"] == "Bearer entra-token"
    fmt = req["body"]["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["name"] == "JobProfileDraft"
    assert [m["role"] for m in req["body"]["messages"]] == ["system", "user"]

    # Same request again: answered from the cache, no HTTP call.
    assert llm.generate(JobProfileDraft, messages, None) == result
    assert len(FakeAzure.requests) == 1


def test_v1_endpoint_request(endpoint: str) -> None:
    config = AzureConfig(
        endpoint=f"{endpoint}/openai/v1",
        models={ModelRole.AGENT: ChatModelConfig(deployment="gpt-5.6-terra")},
        max_retries=0,
    )
    tokens = iter(["token-1", "token-2"])
    model = get_chat_model(ModelRole.AGENT, config, token_provider=lambda: next(tokens))
    llm = ChatModelLLM(model, model_id="azure:gpt-5.6-terra")
    messages = [SystemMessage("system"), HumanMessage("job ad")]

    assert llm.generate(JobProfileDraft, messages, None) == JobProfileDraft.model_validate(DRAFT)
    assert llm.generate(JobProfileDraft, messages, None) == JobProfileDraft.model_validate(DRAFT)
    first, second = FakeAzure.requests
    assert first["path"] == "/openai/v1/chat/completions"  # no deployment path, no api-version
    assert first["body"]["model"] == "gpt-5.6-terra"
    assert "temperature" not in first["body"]  # a reasoning model
    assert first["body"]["response_format"]["json_schema"]["strict"] is True
    # The Entra ID token is fetched again for each request, so it never goes stale.
    assert (first["auth"], second["auth"]) == ("Bearer token-1", "Bearer token-2")
