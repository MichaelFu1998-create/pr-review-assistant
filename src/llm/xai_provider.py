"""xAI (Grok) provider.

xAI serves an OpenAI-compatible Chat Completions API, including tool calling,
so this is the OpenAI provider pointed at a different host with its own
context-window table.
"""

import logging

from .openai_provider import OpenAIProvider

logger = logging.getLogger(__name__)

XAI_BASE_URL = "https://api.x.ai/v1"

# xAI ships and retires model IDs quickly. Anything absent falls through to
# OpenAIProvider.max_context_tokens' conservative default rather than
# over-promising a window the model does not have.
MODEL_CONTEXT_SIZES = {
    "grok-4": 256_000,
    "grok-4-fast": 2_000_000,
    "grok-4-fast-reasoning": 2_000_000,
    "grok-4-fast-non-reasoning": 2_000_000,
    "grok-code-fast-1": 256_000,
    "grok-3": 131_072,
    "grok-3-mini": 131_072,
    "grok-2": 131_072,
}


class XAIProvider(OpenAIProvider):
    context_sizes = MODEL_CONTEXT_SIZES
    default_base_url = XAI_BASE_URL

    def __init__(self, api_key: str, base_url: str | None = None):
        super().__init__(api_key=api_key, base_url=base_url)
        logger.info("Using xAI provider at %s", base_url or XAI_BASE_URL)
