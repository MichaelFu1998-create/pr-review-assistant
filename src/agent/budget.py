"""Budget guards for the agent loop.

Students run this on a shared API key, so an agent that loops on a confusing
file must stop on its own rather than draining a class's quota. Every limit is
a stop condition, not a suggestion.
"""

import logging
import time
from dataclasses import dataclass, field

from ..llm.base import Usage

logger = logging.getLogger(__name__)


@dataclass
class Budget:
    """Step, token, and wall-clock limits for one agent run."""

    max_steps: int = 25
    max_tokens: int = 150_000
    max_seconds: float = 600.0

    steps: int = 0
    usage: Usage = field(default_factory=Usage)
    started_at: float = field(default_factory=time.monotonic)
    stop_reason: str = ""

    # A model that calls the same tool with the same arguments over and over is
    # stuck; it will not free itself, so cut the run rather than burn the budget.
    max_repeated_calls: int = 3
    _call_counts: dict[tuple, int] = field(default_factory=dict)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def tokens_used(self) -> int:
        return self.usage.total_tokens

    def record_step(self, usage: Usage) -> None:
        self.steps += 1
        self.usage = self.usage + usage

    def record_call(self, name: str, arguments: dict) -> bool:
        """Track a tool call. Returns False if it is a stuck repeat."""
        key = (name, repr(sorted(arguments.items())))
        self._call_counts[key] = self._call_counts.get(key, 0) + 1
        return self._call_counts[key] <= self.max_repeated_calls

    def exhausted(self) -> bool:
        """True when the run must stop. Sets stop_reason on the way out."""
        if self.steps >= self.max_steps:
            self.stop_reason = f"step limit reached ({self.max_steps})"
        elif self.tokens_used >= self.max_tokens:
            self.stop_reason = f"token budget exhausted ({self.tokens_used}/{self.max_tokens})"
        elif self.elapsed >= self.max_seconds:
            self.stop_reason = f"time limit reached ({self.max_seconds:.0f}s)"
        else:
            return False
        logger.warning("Agent budget: %s", self.stop_reason)
        return True

    def remaining_steps(self) -> int:
        return max(self.max_steps - self.steps, 0)

    def summary(self) -> dict:
        return {
            "steps": self.steps,
            "prompt_tokens": self.usage.prompt_tokens,
            "completion_tokens": self.usage.completion_tokens,
            "total_tokens": self.tokens_used,
            "elapsed_seconds": round(self.elapsed, 1),
            "stop_reason": self.stop_reason or "completed",
        }

    def split(self, n: int) -> "Budget":
        """A per-specialist budget, for multi mode.

        Each specialist gets an equal share of tokens and time but its own step
        count, since steps are what bound a single agent's depth.
        """
        n = max(n, 1)
        return Budget(
            max_steps=self.max_steps,
            max_tokens=max(self.max_tokens // n, 10_000),
            max_seconds=self.max_seconds,
            max_repeated_calls=self.max_repeated_calls,
        )
