"""Anthropic Claude LLM provider."""

import logging

from .base import (
    LLMConfig,
    LLMProvider,
    Message,
    ToolCall,
    ToolCallResponse,
    ToolSchema,
    Usage,
)

logger = logging.getLogger(__name__)

DEFAULT_CONTEXT_TOKENS = 200_000


class AnthropicProvider(LLMProvider):
    """LLM provider for Anthropic's Claude models."""

    # Known context window sizes
    MODEL_CONTEXT_SIZES = {
        "claude-opus-4-6": 200_000,
        "claude-sonnet-4-6": 200_000,
        "claude-haiku-4-5-20251001": 200_000,
        "claude-3-5-sonnet-20241022": 200_000,
        "claude-3-haiku-20240307": 200_000,
    }

    def __init__(self, api_key: str):
        try:
            import anthropic
        except ImportError:
            raise ImportError(
                "The 'anthropic' package is required for Claude support. "
                "Install it with: pip install anthropic"
            )
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=3)

    # --- v1 single-shot path ---

    def complete(self, system_message: str, user_message: str, config: LLMConfig) -> str:
        logger.info(f"Requesting Anthropic completion with model={config.model}")
        response = self.client.messages.create(
            model=config.model,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            system=system_message,
            messages=[
                {"role": "user", "content": user_message},
            ],
        )
        return _join_text(response)

    # --- v2 agent path ---

    def complete_with_tools(
        self,
        system_message: str,
        messages: list[Message],
        tools: list[ToolSchema],
        config: LLMConfig,
    ) -> ToolCallResponse:
        kwargs = {}
        if tools:
            kwargs["tools"] = [t.to_anthropic() for t in tools]

        response = self.client.messages.create(
            model=config.model,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            system=system_message,
            messages=_to_wire(messages),
            **kwargs,
        )

        tool_calls = [
            ToolCall(
                id=block.id,
                name=block.name,
                # The SDK hands back `input` already decoded, so unlike the
                # OpenAI path there is no JSON string to parse.
                arguments=dict(block.input or {}),
            )
            for block in response.content
            if getattr(block, "type", None) == "tool_use"
        ]

        return ToolCallResponse(
            text=_join_text(response),
            tool_calls=tool_calls,
            finish_reason=response.stop_reason or "",
            usage=Usage(
                prompt_tokens=getattr(response.usage, "input_tokens", 0) or 0,
                completion_tokens=getattr(response.usage, "output_tokens", 0) or 0,
            ),
        )

    def count_tokens(self, text: str) -> int:
        # Anthropic uses a similar tokenizer; approximate at 1 token per 4 chars
        return len(text) // 4

    def max_context_tokens(self, model: str) -> int:
        return self.MODEL_CONTEXT_SIZES.get(model, DEFAULT_CONTEXT_TOKENS)


def _join_text(response) -> str:
    """Concatenate the text blocks of a response.

    Indexing content[0] is not safe once tools are in play: a turn that calls a
    tool may lead with a tool_use block, or with no text block at all.
    """
    return "".join(
        block.text
        for block in response.content
        if getattr(block, "type", None) == "text"
    )


def _to_wire(messages: list[Message]) -> list[dict]:
    """Translate normalised messages into Anthropic's format.

    Two shape differences matter. Assistant tool calls are ``tool_use`` content
    blocks rather than a sibling ``tool_calls`` field; and tool results are
    ``tool_result`` blocks inside a *user* message, with every result for one
    assistant turn batched into a single message. Emitting them as separate
    messages is rejected by the API, so consecutive tool results are merged.
    """
    wire: list[dict] = []
    pending_results: list[dict] = []

    def flush_results() -> None:
        if pending_results:
            wire.append({"role": "user", "content": list(pending_results)})
            pending_results.clear()

    for message in messages:
        if message.role == "tool":
            pending_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id,
                    "content": message.content,
                }
            )
            continue

        flush_results()

        if message.role == "assistant" and message.tool_calls:
            blocks: list[dict] = []
            if message.content:
                blocks.append({"type": "text", "text": message.content})
            blocks.extend(
                {
                    "type": "tool_use",
                    "id": call.id,
                    "name": call.name,
                    "input": call.arguments,
                }
                for call in message.tool_calls
            )
            wire.append({"role": "assistant", "content": blocks})
        else:
            wire.append({"role": message.role, "content": message.content})

    flush_results()
    return wire
