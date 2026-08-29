"""A scripted LLM provider, so the agent loop can be tested deterministically.

Replays a fixed sequence of turns instead of calling an API. Every loop test in
the suite runs on this — no network, no key, no cost.
"""

from src.llm.base import (
    LLMConfig,
    LLMProvider,
    Message,
    ToolCall,
    ToolCallResponse,
    ToolSchema,
    Usage,
)


def call(name: str, _id: str = "c1", **arguments) -> ToolCall:
    return ToolCall(id=_id, name=name, arguments=arguments)


def turn(*tool_calls: ToolCall, text: str = "", tokens: int = 100) -> ToolCallResponse:
    return ToolCallResponse(
        text=text,
        tool_calls=list(tool_calls),
        finish_reason="tool_calls" if tool_calls else "stop",
        usage=Usage(prompt_tokens=tokens, completion_tokens=tokens // 10),
    )


class FakeProvider(LLMProvider):
    """Replays `script` turn by turn. Records what it was asked."""

    def __init__(self, script: list[ToolCallResponse] | None = None):
        self.script = list(script or [])
        self.calls: list[dict] = []
        self.raise_on_call: Exception | None = None

    def complete_with_tools(
        self,
        system_message: str,
        messages: list[Message],
        tools: list[ToolSchema],
        config: LLMConfig,
    ) -> ToolCallResponse:
        self.calls.append(
            {
                "system": system_message,
                "messages": list(messages),
                "tool_names": [t.name for t in tools],
            }
        )
        if self.raise_on_call is not None:
            raise self.raise_on_call
        if not self.script:
            # Nothing scripted left: behave like a model that has said its piece.
            return turn(text="(out of script)")
        return self.script.pop(0)

    def complete(self, system_message: str, user_message: str, config: LLMConfig) -> str:
        return "fake completion"

    def count_tokens(self, text: str) -> int:
        return max(len(text) // 4, 1)

    def max_context_tokens(self, model: str) -> int:
        return 128_000
