"""Tests for the provider tool-calling contract and message translation."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.config import Config
from src.llm.base import (
    LLMConfig,
    Message,
    ToolCall,
    ToolSchema,
    Usage,
)
from src.llm.anthropic_provider import _join_text, _to_wire
from src.llm.openai_provider import OpenAIProvider
from src.main import PROVIDER_KEYS, validate_provider_key


SCHEMA = ToolSchema(
    name="read_file",
    description="Read a file",
    parameters={"type": "object", "properties": {"path": {"type": "string"}}},
)


class TestToolCallParsing:
    def test_valid_json_arguments(self):
        call = ToolCall.from_raw_arguments("c1", "read_file", '{"path": "a.py"}')
        assert call.arguments == {"path": "a.py"}
        assert call.parse_error is None

    def test_empty_arguments(self):
        assert ToolCall.from_raw_arguments("c1", "finish", None).arguments == {}
        assert ToolCall.from_raw_arguments("c1", "finish", "").arguments == {}

    def test_malformed_json_records_error_instead_of_raising(self):
        call = ToolCall.from_raw_arguments("c1", "read_file", '{"path": ')
        assert call.arguments == {}
        assert call.parse_error is not None

    def test_non_object_json_rejected(self):
        call = ToolCall.from_raw_arguments("c1", "read_file", '["a.py"]')
        assert call.arguments == {}
        assert "expected a JSON object" in call.parse_error


class TestUsage:
    def test_total_and_addition(self):
        a = Usage(prompt_tokens=10, completion_tokens=5)
        b = Usage(prompt_tokens=1, completion_tokens=2)
        assert a.total_tokens == 15
        assert (a + b).total_tokens == 18


class TestToolSchema:
    def test_openai_shape(self):
        out = SCHEMA.to_openai()
        assert out["type"] == "function"
        assert out["function"]["name"] == "read_file"
        assert out["function"]["parameters"]["type"] == "object"

    def test_anthropic_shape_uses_input_schema(self):
        out = SCHEMA.to_anthropic()
        assert out["name"] == "read_file"
        assert "input_schema" in out
        assert "parameters" not in out


def _openai_response(content=None, tool_calls=None, finish_reason="stop"):
    message = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
    )


def _provider():
    p = OpenAIProvider.__new__(OpenAIProvider)
    p.client = MagicMock()
    p._encoding = None
    p._omit_temperature = False
    return p


class TestOpenAIToolCalling:
    def test_parses_tool_calls_and_usage(self):
        p = _provider()
        raw_call = SimpleNamespace(
            id="call_1",
            function=SimpleNamespace(name="read_file", arguments='{"path": "a.py"}'),
        )
        p.client.chat.completions.create.return_value = _openai_response(
            content=None, tool_calls=[raw_call], finish_reason="tool_calls"
        )

        result = p.complete_with_tools("sys", [Message.user("go")], [SCHEMA], LLMConfig())

        assert result.wants_tools
        assert result.tool_calls[0].name == "read_file"
        assert result.tool_calls[0].arguments == {"path": "a.py"}
        assert result.usage.total_tokens == 120
        assert result.text == ""

    def test_text_only_response_wants_no_tools(self):
        p = _provider()
        p.client.chat.completions.create.return_value = _openai_response(content="done")
        result = p.complete_with_tools("sys", [Message.user("go")], [SCHEMA], LLMConfig())
        assert result.wants_tools is False
        assert result.text == "done"

    def test_tools_omitted_when_none_offered(self):
        p = _provider()
        p.client.chat.completions.create.return_value = _openai_response(content="hi")
        p.complete_with_tools("sys", [Message.user("go")], [], LLMConfig())
        kwargs = p.client.chat.completions.create.call_args.kwargs
        assert "tools" not in kwargs
        assert "tool_choice" not in kwargs

    def test_system_message_leads_the_wire_format(self):
        p = _provider()
        p.client.chat.completions.create.return_value = _openai_response(content="hi")
        p.complete_with_tools("be careful", [Message.user("go")], [SCHEMA], LLMConfig())
        messages = p.client.chat.completions.create.call_args.kwargs["messages"]
        assert messages[0] == {"role": "system", "content": "be careful"}
        assert messages[1] == {"role": "user", "content": "go"}


class TestOpenAIWireTranslation:
    def test_assistant_with_tool_calls_serialises_arguments(self):
        msg = Message.assistant(
            tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "a.py"})]
        )
        wire = OpenAIProvider._to_wire(msg)
        assert wire["role"] == "assistant"
        assert wire["content"] == ""  # never null alongside tool_calls
        assert json.loads(wire["tool_calls"][0]["function"]["arguments"]) == {"path": "a.py"}

    def test_tool_result_carries_call_id(self):
        wire = OpenAIProvider._to_wire(Message.tool_result("c1", "read_file", "contents"))
        assert wire == {"role": "tool", "tool_call_id": "c1", "content": "contents"}

    def test_plain_user_message(self):
        assert OpenAIProvider._to_wire(Message.user("hi")) == {
            "role": "user",
            "content": "hi",
        }


class TestTemperatureFallback:
    def test_retries_without_temperature_when_model_refuses(self):
        p = _provider()
        refusal = Exception("Unsupported value: 'temperature' does not support 0.0")
        p.client.chat.completions.create.side_effect = [
            refusal,
            _openai_response(content="ok"),
        ]

        result = p.complete_with_tools(
            "sys", [Message.user("go")], [], LLMConfig(temperature=0.0)
        )

        assert result.text == "ok"
        assert p._omit_temperature is True
        first, second = p.client.chat.completions.create.call_args_list
        assert "temperature" in first.kwargs
        assert "temperature" not in second.kwargs

    def test_unrelated_errors_propagate(self):
        p = _provider()
        p.client.chat.completions.create.side_effect = RuntimeError("connection reset")
        with pytest.raises(RuntimeError, match="connection reset"):
            p.complete_with_tools("sys", [Message.user("go")], [], LLMConfig())


class TestAnthropicTranslation:
    def test_join_text_skips_tool_use_blocks(self):
        response = SimpleNamespace(
            content=[
                SimpleNamespace(type="tool_use", id="c1", name="read_file", input={}),
                SimpleNamespace(type="text", text="hello"),
                SimpleNamespace(type="text", text=" world"),
            ]
        )
        assert _join_text(response) == "hello world"

    def test_join_text_on_tool_only_turn(self):
        response = SimpleNamespace(
            content=[SimpleNamespace(type="tool_use", id="c1", name="f", input={})]
        )
        assert _join_text(response) == ""

    def test_assistant_tool_calls_become_content_blocks(self):
        wire = _to_wire(
            [
                Message.user("go"),
                Message.assistant(
                    "thinking",
                    [ToolCall(id="c1", name="read_file", arguments={"path": "a.py"})],
                ),
            ]
        )
        assert wire[0] == {"role": "user", "content": "go"}
        blocks = wire[1]["content"]
        assert blocks[0] == {"type": "text", "text": "thinking"}
        assert blocks[1]["type"] == "tool_use"
        assert blocks[1]["input"] == {"path": "a.py"}

    def test_assistant_without_text_omits_empty_text_block(self):
        wire = _to_wire([Message.assistant("", [ToolCall(id="c1", name="f")])])
        assert [b["type"] for b in wire[0]["content"]] == ["tool_use"]

    def test_consecutive_tool_results_merge_into_one_user_message(self):
        """Anthropic rejects one message per tool_result; they must be batched."""
        wire = _to_wire(
            [
                Message.user("go"),
                Message.assistant(
                    "",
                    [
                        ToolCall(id="c1", name="read_file"),
                        ToolCall(id="c2", name="read_file"),
                    ],
                ),
                Message.tool_result("c1", "read_file", "first"),
                Message.tool_result("c2", "read_file", "second"),
            ]
        )
        assert len(wire) == 3
        results = wire[2]
        assert results["role"] == "user"
        assert [b["tool_use_id"] for b in results["content"]] == ["c1", "c2"]

    def test_results_flush_before_a_following_turn(self):
        wire = _to_wire(
            [
                Message.assistant("", [ToolCall(id="c1", name="f")]),
                Message.tool_result("c1", "f", "out"),
                Message.user("next"),
            ]
        )
        assert [m["role"] for m in wire] == ["assistant", "user", "user"]
        assert wire[1]["content"][0]["type"] == "tool_result"
        assert wire[2]["content"] == "next"


class TestProviderKeyValidation:
    def test_each_provider_requires_its_own_key(self):
        for provider, field in PROVIDER_KEYS.items():
            missing = Config(llm_provider=provider)
            assert field in validate_provider_key(missing)

            present = Config(llm_provider=provider, **{field: "sk-test"})
            assert validate_provider_key(present) is None

    def test_unknown_provider_is_rejected_by_name(self):
        error = validate_provider_key(Config(llm_provider="groq"))
        assert "Unknown llm_provider 'groq'" in error
        assert "xai" in error

    def test_openai_key_does_not_satisfy_xai(self):
        config = Config(llm_provider="xai", openai_api_key="sk-openai")
        assert validate_provider_key(config) is not None
