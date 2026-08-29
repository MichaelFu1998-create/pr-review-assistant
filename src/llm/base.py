"""Provider-agnostic LLM interface.

``complete()`` serves the v1 single-shot pipeline and is unchanged.
``complete_with_tools()`` is what the v2 agent loop runs on: it takes a
conversation plus a set of callable tools and returns whatever the model
decided to do next — prose, tool calls, or both.

The message and tool shapes here are normalised. OpenAI-wire providers map
onto them almost directly; Anthropic's native format differs (tool results are
``tool_result`` blocks inside a *user* message rather than a ``tool`` role), so
that provider translates. Keeping the loop free of provider conditionals is the
whole point.
"""

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class LLMConfig:
    model: str = "grok-4.6"
    temperature: float = 1.0
    max_tokens: int = 32000
    # Reasoning models (grok-4.6, the gpt-5/o-series) take a depth dial instead
    # of a temperature. Sent only when set.
    reasoning_effort: str = ""


@dataclass
class Usage:
    """Token accounting for one request. Summed across an agent run."""

    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
        )


@dataclass
class ToolSchema:
    """A tool offered to the model. ``parameters`` is JSON Schema."""

    name: str
    description: str
    parameters: dict

    def to_openai(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_anthropic(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }


@dataclass
class ToolCall:
    """One tool invocation requested by the model.

    ``arguments`` is already decoded. Models do occasionally emit malformed
    JSON; rather than raising — which would kill an otherwise healthy review —
    we record ``parse_error`` and let the loop hand it back to the model as a
    tool result it can correct.
    """

    id: str
    name: str
    arguments: dict = field(default_factory=dict)
    parse_error: str | None = None

    @classmethod
    def from_raw_arguments(cls, id: str, name: str, raw: str | None) -> "ToolCall":
        if not raw:
            return cls(id=id, name=name, arguments={})
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("Tool call %s had unparseable arguments: %s", name, e)
            return cls(id=id, name=name, arguments={}, parse_error=str(e))
        if not isinstance(parsed, dict):
            return cls(
                id=id,
                name=name,
                arguments={},
                parse_error=f"expected a JSON object, got {type(parsed).__name__}",
            )
        return cls(id=id, name=name, arguments=parsed)


@dataclass
class ToolCallResponse:
    """What the model returned for one turn."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    usage: Usage = field(default_factory=Usage)

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


@dataclass
class Message:
    """One conversation turn, in normalised form.

    role is "user", "assistant", or "tool". Assistant turns may carry
    ``tool_calls``; tool turns must carry ``tool_call_id``.
    """

    role: str
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str = ""
    name: str = ""

    @classmethod
    def user(cls, content: str) -> "Message":
        return cls(role="user", content=content)

    @classmethod
    def assistant(cls, content: str = "", tool_calls: list[ToolCall] | None = None) -> "Message":
        return cls(role="assistant", content=content, tool_calls=tool_calls or [])

    @classmethod
    def tool_result(cls, tool_call_id: str, name: str, content: str) -> "Message":
        return cls(role="tool", content=content, tool_call_id=tool_call_id, name=name)


class LLMProvider(ABC):
    @abstractmethod
    def complete(self, system_message: str, user_message: str, config: LLMConfig) -> str:
        """Send a chat completion request and return the response text."""
        ...

    @abstractmethod
    def complete_with_tools(
        self,
        system_message: str,
        messages: list[Message],
        tools: list[ToolSchema],
        config: LLMConfig,
    ) -> ToolCallResponse:
        """Send a tool-enabled request and return the model's next move."""
        ...

    @abstractmethod
    def count_tokens(self, text: str) -> int:
        """Count the number of tokens in the given text."""
        ...

    @abstractmethod
    def max_context_tokens(self, model: str) -> int:
        """Return the maximum context window size for the given model."""
        ...
