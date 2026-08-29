"""OpenAI LLM provider using the v1.0+ SDK.

Also the base for every OpenAI-wire-compatible provider (xAI, Ollama, vLLM,
Azure) — those differ only in base URL and context-window table.
"""

import json
import logging

import tiktoken
from openai import OpenAI

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

# Known context window sizes for common models
MODEL_CONTEXT_SIZES = {
    "gpt-5.4-mini-2026-03-17": 1_047_576,
    "gpt-5-nano": 128_000,
    "gpt-5-mini": 128_000,
    "gpt-4.1-nano": 1_047_576,
    "gpt-4.1-mini": 1_047_576,
    "gpt-4.1": 1_047_576,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-3.5-turbo": 16_385,
}

DEFAULT_CONTEXT_TOKENS = 128_000


class OpenAIProvider(LLMProvider):
    # Subclasses override these two; everything else is shared.
    context_sizes: dict[str, int] = MODEL_CONTEXT_SIZES
    default_base_url: str | None = None

    def __init__(self, api_key: str, base_url: str | None = None):
        kwargs = {"api_key": api_key, "max_retries": 3}
        resolved = base_url or self.default_base_url
        if resolved:
            kwargs["base_url"] = resolved
        self.client = OpenAI(**kwargs)
        self._encoding = None
        # Set once a request is rejected for sending one of these, so we stop
        # sending it for the rest of the run instead of paying the retry each call.
        self._omit_temperature = False
        self._omit_reasoning_effort = False

    # --- v1 single-shot path (unchanged behaviour) ---

    def complete(self, system_message: str, user_message: str, config: LLMConfig) -> str:
        logger.info(f"Requesting OpenAI completion with model={config.model}")
        response = self._create(
            config=config,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_message},
            ],
        )
        return response.choices[0].message.content or ""

    # --- v2 agent path ---

    def complete_with_tools(
        self,
        system_message: str,
        messages: list[Message],
        tools: list[ToolSchema],
        config: LLMConfig,
    ) -> ToolCallResponse:
        wire = [{"role": "system", "content": system_message}]
        wire.extend(self._to_wire(m) for m in messages)

        kwargs = {}
        if tools:
            kwargs["tools"] = [t.to_openai() for t in tools]
            kwargs["tool_choice"] = "auto"

        response = self._create(config=config, messages=wire, **kwargs)
        choice = response.choices[0]
        message = choice.message

        tool_calls = [
            ToolCall.from_raw_arguments(
                id=call.id,
                name=call.function.name,
                raw=call.function.arguments,
            )
            for call in (message.tool_calls or [])
        ]

        return ToolCallResponse(
            text=message.content or "",
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "",
            usage=self._usage(response),
        )

    # --- internals ---

    def _create(self, config: LLMConfig, messages: list[dict], **kwargs):
        """Call the API, dropping `temperature` if the model refuses it.

        Reasoning models (the gpt-5 and o-series families) accept only the
        default temperature and 400 on anything else. Students do set
        temperature: 0 expecting determinism, so degrade rather than fail.
        """
        params = {
            "model": config.model,
            "max_completion_tokens": config.max_tokens,
            "messages": messages,
            **kwargs,
        }
        if not self._omit_temperature:
            params["temperature"] = config.temperature
        # The depth/cost dial on reasoning models. Only sent when explicitly
        # configured, since providers reject it on models that lack it.
        if config.reasoning_effort and not self._omit_reasoning_effort:
            params["reasoning_effort"] = config.reasoning_effort

        try:
            return self.client.chat.completions.create(**params)
        except Exception as e:
            if _is_temperature_error(e) and not self._omit_temperature:
                logger.warning(
                    "Model %s rejected temperature=%s; retrying without it and "
                    "omitting it for the rest of this run.",
                    config.model,
                    config.temperature,
                )
                self._omit_temperature = True
                params.pop("temperature", None)
                return self.client.chat.completions.create(**params)
            if _is_reasoning_effort_error(e) and "reasoning_effort" in params:
                logger.warning(
                    "Model %s does not accept reasoning_effort; retrying without it.",
                    config.model,
                )
                self._omit_reasoning_effort = True
                params.pop("reasoning_effort", None)
                return self.client.chat.completions.create(**params)
            raise

    @staticmethod
    def _to_wire(message: Message) -> dict:
        if message.role == "tool":
            return {
                "role": "tool",
                "tool_call_id": message.tool_call_id,
                "content": message.content,
            }
        if message.role == "assistant" and message.tool_calls:
            return {
                "role": "assistant",
                # The API rejects a null content alongside tool_calls on some
                # deployments; an empty string is accepted everywhere.
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments),
                        },
                    }
                    for call in message.tool_calls
                ],
            }
        return {"role": message.role, "content": message.content}

    @staticmethod
    def _usage(response) -> Usage:
        usage = getattr(response, "usage", None)
        if not usage:
            return Usage()
        return Usage(
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )

    def count_tokens(self, text: str) -> int:
        if self._encoding is None:
            try:
                self._encoding = tiktoken.encoding_for_model("gpt-4o")
            except Exception:
                self._encoding = tiktoken.get_encoding("cl100k_base")
        return len(self._encoding.encode(text))

    def max_context_tokens(self, model: str) -> int:
        return self.context_sizes.get(model, DEFAULT_CONTEXT_TOKENS)


def _is_temperature_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "temperature" in text and ("unsupported" in text or "does not support" in text)


def _is_reasoning_effort_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "reasoning_effort" in text or "reasoning effort" in text
