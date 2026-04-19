"""Anthropic Claude LLM provider."""

import logging

from .base import LLMProvider, LLMConfig

logger = logging.getLogger(__name__)


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
        return response.content[0].text

    def count_tokens(self, text: str) -> int:
        # Anthropic uses a similar tokenizer; approximate at 1 token per 4 chars
        return len(text) // 4

    def max_context_tokens(self, model: str) -> int:
        return self.MODEL_CONTEXT_SIZES.get(model, 200_000)
