"""Abstract base class for LLM providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LLMConfig:
    model: str = "gpt-5.4-mini-2026-03-17"
    temperature: float = 1.0
    max_tokens: int = 32000


class LLMProvider(ABC):
    @abstractmethod
    def complete(self, system_message: str, user_message: str, config: LLMConfig) -> str:
        """Send a chat completion request and return the response text."""
        ...

    @abstractmethod
    def count_tokens(self, text: str) -> int:
        """Count the number of tokens in the given text."""
        ...

    @abstractmethod
    def max_context_tokens(self, model: str) -> int:
        """Return the maximum context window size for the given model."""
        ...
