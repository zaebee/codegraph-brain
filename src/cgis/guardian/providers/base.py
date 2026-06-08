import abc


class BaseProvider(abc.ABC):
    """Abstract base class for LLM providers."""

    @abc.abstractmethod
    async def generate_content(self, system_prompt: str, user_prompt: str) -> str:
        """Sends a prompt to the LLM and returns the text response."""
