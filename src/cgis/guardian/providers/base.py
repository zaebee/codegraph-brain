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
        """Sum of prompt and completion tokens."""
        return self.prompt_tokens + self.completion_tokens


class BaseProvider(abc.ABC):
    """Abstract base class for LLM providers."""

    def __init__(self) -> None:
        """Initialise last_usage to zero; updated after each generate_content call."""
        self.last_usage: ProviderUsage = ProviderUsage()

    @abc.abstractmethod
    async def generate_content(self, system_prompt: str, user_prompt: str) -> str:
        """Sends a prompt to the LLM and returns the text response."""
