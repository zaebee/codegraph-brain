"""Abstract base class for LLM provider implementations."""

import abc

from pydantic import BaseModel, computed_field


class ProviderUsage(BaseModel, frozen=True):
    """Token usage reported by the LLM after a single generate_content call."""

    prompt_tokens: int = 0
    completion_tokens: int = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_tokens(self) -> int:
        """Total tokens consumed by this LLM request, used for cost tracking."""
        return self.prompt_tokens + self.completion_tokens


class BaseProvider(abc.ABC):
    """Abstract base class for LLM providers."""

    def __init__(self) -> None:
        """Initialise usage counters to zero.

        last_usage reflects the most recent LLM call; cumulative_usage sums
        every call this provider instance has made (a chunked review makes
        N finder calls — and even a single-pass review makes 2 on a parse
        retry, whose first call last_usage used to silently drop).
        """
        self.last_usage: ProviderUsage = ProviderUsage()
        self.cumulative_usage: ProviderUsage = ProviderUsage()

    def _record_usage(self, usage: ProviderUsage) -> None:
        """Record one call's token usage: set last_usage, add to cumulative."""
        self.last_usage = usage
        self.cumulative_usage = ProviderUsage(
            prompt_tokens=self.cumulative_usage.prompt_tokens + usage.prompt_tokens,
            completion_tokens=self.cumulative_usage.completion_tokens + usage.completion_tokens,
        )

    @abc.abstractmethod
    async def generate_content(self, system_prompt: str, user_prompt: str) -> str:
        """Send a prompt to the LLM and return the text response."""

    @abc.abstractmethod
    async def generate_structured(
        self, system_prompt: str, user_prompt: str, schema: type[BaseModel]
    ) -> str:
        """Send a prompt requesting JSON conforming to schema; return raw JSON text."""
