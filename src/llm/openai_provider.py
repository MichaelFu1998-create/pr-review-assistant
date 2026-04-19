"""OpenAI LLM provider using the v1.0+ SDK."""

import logging

import tiktoken
from openai import OpenAI

from .base import LLMProvider, LLMConfig

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


class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str, base_url: str | None = None):
        kwargs = {"api_key": api_key, "max_retries": 3}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = OpenAI(**kwargs)
        self._encoding = None

    def complete(self, system_message: str, user_message: str, config: LLMConfig) -> str:
        logger.info(f"Requesting OpenAI completion with model={config.model}")
        response = self.client.chat.completions.create(
            model=config.model,
            temperature=config.temperature,
            max_completion_tokens=config.max_tokens,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_message},
            ],
        )
        return response.choices[0].message.content or ""

    def count_tokens(self, text: str) -> int:
        if self._encoding is None:
            try:
                self._encoding = tiktoken.encoding_for_model("gpt-4o")
            except Exception:
                self._encoding = tiktoken.get_encoding("cl100k_base")
        return len(self._encoding.encode(text))

    def max_context_tokens(self, model: str) -> int:
        return MODEL_CONTEXT_SIZES.get(model, 128_000)
