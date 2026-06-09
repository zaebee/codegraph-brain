"""Mistral AI LLM provider for Guardian."""

from cgis.guardian.providers.base import BaseProvider, ProviderUsage


class MistralProvider(BaseProvider):
    """Mistral AI provider. Requires: uv sync --group guardian"""

    def __init__(self, api_key: str, model_name: str = "mistral-medium-latest") -> None:
        """Store credentials; mistralai is imported lazily at call time."""
        super().__init__()
        self._api_key = api_key
        self._model_name = model_name

    async def generate_content(self, system_prompt: str, user_prompt: str) -> str:
        """Send prompts to Mistral and return the text response."""
        _install_hint = "mistralai is required. Install with: uv sync --group guardian"
        try:
            from mistralai.client import Mistral  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(_install_hint) from exc
        async with Mistral(api_key=self._api_key) as client:
            response = await client.chat.complete_async(
                model=self._model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
        if not response.choices:
            _msg = f"Mistral returned no choices for model {self._model_name}"
            raise ValueError(_msg)
        content = response.choices[0].message.content
        if content is None:
            _msg = f"Mistral returned null message content for model {self._model_name}"
            raise ValueError(_msg)
        usage = getattr(response, "usage", None)
        if usage is not None:
            self.last_usage = ProviderUsage(
                prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            )
        return str(content)
