"""Verify action/workflow inputs reach the serialized xAI request unchanged.

The real SDK uses an in-memory HTTP transport: no API key, network, or spend.
"""

import json
import os
import re
from pathlib import Path

import pytest
import yaml
from openai import PermissionDeniedError

# Use the transport module used by the installed SDK (httpx or httpx2).
try:
    from openai._base_client import httpx2 as httpx
except ImportError:
    from openai._base_client import httpx

from src import main as orchestrator
from src.config import Config
from src.llm.base import LLMConfig, Message, ToolSchema
from src.llm.xai_provider import XAIProvider


ROOT = Path(__file__).resolve().parents[1]
MODEL = "grok-4.20-0309-non-reasoning"


@pytest.fixture
def action_environment(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith("INPUT_"):
            monkeypatch.delenv(key)
    action = yaml.safe_load((ROOT / "action.yaml").read_text())
    # Resolve the actual Docker action input -> environment mapping.
    for env, expression in action["runs"]["env"].items():
        name = re.fullmatch(r"\$\{\{ inputs\.(\w+) \}\}", expression).group(1)
        monkeypatch.setenv(env, str(action["inputs"][name].get("default", "")))
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("INPUT_XAI_API_KEY", "xai-test-only")
    monkeypatch.setenv("INPUT_GITHUB_TOKEN", "github-test-only")
    monkeypatch.setenv("INPUT_GITHUB_PR_ID", "1")


def invoke(provider, config, mode):
    if mode == "pipeline":
        return provider.complete("Review this change", "test change", config)
    return provider.complete_with_tools(
        "Review this change", [Message.user("test change")],
        [ToolSchema(name="finish", description="Finish", parameters={"type": "object"})],
        config,
    )


def response():
    return httpx.Response(200, json={
        "id": "test-completion", "object": "chat.completion", "created": 0,
        "model": MODEL,
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "Done"}}],
    })


@pytest.mark.parametrize("mode", ["agent", "adaptive", "pipeline"])
@pytest.mark.parametrize("source", ["action", "omitted", "workflow", "readme", "legacy"])
def test_workflow_to_api_model(action_environment, monkeypatch, mode, source):
    if source == "omitted":
        monkeypatch.delenv("INPUT_MODEL")
    elif source == "workflow":
        workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yaml").read_text())
        step = next(s for s in workflow["jobs"]["test"]["steps"]
                    if s.get("run") == "pytest tests/ -q")
        monkeypatch.setenv("INPUT_MODEL", step["env"]["INPUT_MODEL"])
    elif source == "readme":
        workflow = (ROOT / "README.md").read_text().split("```yaml\n", 1)[1].split("```", 1)[0]
        step = yaml.safe_load(workflow)["jobs"]["review"]["steps"][-1]
        monkeypatch.setenv("INPUT_MODEL", step["with"]["model"])
    elif source == "legacy":
        monkeypatch.setenv("INPUT_OPENAI_MODEL", MODEL)
    monkeypatch.setenv("INPUT_AGENT_MODE", mode)
    requests = []

    def handle(request):
        assert str(request.url) == "https://api.x.ai/v1/chat/completions"
        requests.append(json.loads(request.content))
        return response()

    def review(config, provider, llm_config, *args):
        assert isinstance(provider, XAIProvider)
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            provider.client = provider.client.with_options(http_client=client)
            invoke(provider, llm_config, config.agent_mode)

    monkeypatch.setattr(orchestrator, "get_repo_and_pull", lambda *args: (None, None))
    monkeypatch.setattr(orchestrator, "files_for_review", lambda *args: {"test.py": {}})
    monkeypatch.setattr(orchestrator, "run_agent_review", review)
    monkeypatch.setattr(orchestrator, "run_pipeline_review", review)
    orchestrator.main()
    assert len(requests) == 1
    assert requests[0]["model"] == MODEL
    assert "reasoning_effort" not in requests[0]


@pytest.mark.parametrize("mode", ["agent", "pipeline"])
@pytest.mark.parametrize("rejected", ["temperature", "reasoning_effort", "permission"])
def test_retries_never_switch_models(mode, rejected):
    provider = XAIProvider(api_key="xai-test-only")
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(403 if rejected == "permission" else 400, json={
                "error": {"message": f"Unsupported parameter: {rejected}"},
            })
        return response()

    config = LLMConfig(reasoning_effort="high" if rejected == "reasoning_effort" else "")
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        provider.client = provider.client.with_options(http_client=client)
        if rejected == "permission":
            with pytest.raises(PermissionDeniedError):
                invoke(provider, config, mode)
            assert len(requests) == 1
        else:
            invoke(provider, config, mode)
            assert len(requests) == 2
            assert rejected not in requests[1]
    assert all(request["model"] == MODEL for request in requests)


def test_direct_defaults_and_context_budget():
    assert Config().model == LLMConfig().model == MODEL
    assert Config().reasoning_effort == LLMConfig().reasoning_effort == ""
    provider = XAIProvider.__new__(XAIProvider)
    assert provider.max_context_tokens(MODEL) == 180_000
