"""Mistral AI LLM provider for Guardian."""

from pydantic import BaseModel

from cgis.guardian.providers.base import BaseProvider, ProviderUsage


class MistralProvider(BaseProvider):
    """Mistral AI provider. Requires: uv sync --group guardian"""

    def __init__(self, api_key: str, model_name: str = "mistral-medium-latest") -> None:
        """Store credentials; mistralai is imported lazily at call time."""
        super().__init__()
        self._api_key = api_key
        self._model_name = model_name

    async def _generate(self, system_prompt: str, user_prompt: str, *, json_mode: bool) -> str:
        """Shared transport: one chat.complete_async call, optional json_object mode."""
        _install_hint = "mistralai is required. Install with: uv sync --group guardian"
        try:
            from mistralai.client import Mistral  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(_install_hint) from exc
        extra: dict[str, object] = {}
        if json_mode:
            extra["response_format"] = {"type": "json_object"}
        async with Mistral(api_key=self._api_key) as client:
            response = await client.chat.complete_async(
                model=self._model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                **extra,
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
                prompt_tokens=getattr(usage, "prompt_tokens", 0),
                completion_tokens=getattr(usage, "completion_tokens", 0),
            )
        return str(content)

    async def generate_content(self, system_prompt: str, user_prompt: str) -> str:
        """Send prompts to Mistral and return the text response."""
        return await self._generate(system_prompt, user_prompt, json_mode=False)

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
    ) -> str:
        """Send prompts in json_object mode.

        Mistral's json_object mode takes no schema parameter — the schema is
        described in the user prompt (spec §2.4); the argument exists to
        satisfy the BaseProvider contract.
        """
        del schema
        return await self._generate(system_prompt, user_prompt, json_mode=True)
