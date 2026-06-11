"""Google Gemini LLM provider for Guardian."""

from pydantic import BaseModel

from cgis.guardian.providers.base import BaseProvider, ProviderUsage


class GeminiProvider(BaseProvider):
    """Google Gemini provider. Requires: uv sync --group guardian"""

    def __init__(self, api_key: str, model_name: str = "gemini-2.5-flash") -> None:
        """Store credentials; google-genai is imported lazily at call time."""
        super().__init__()
        self._api_key = api_key
        self._model_name = model_name

    async def _generate(
        self, system_prompt: str, user_prompt: str, schema: type[BaseModel] | None
    ) -> str:
        """Shared transport: one generate_content call, optional JSON mode."""
        _install_hint = "google-genai is required. Install with: uv sync --group guardian"
        try:
            from google import genai  # noqa: PLC0415
            from google.genai import types  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(_install_hint) from exc
        config_kwargs: dict[str, object] = {"system_instruction": system_prompt}
        if schema is not None:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = schema
        client = genai.Client(api_key=self._api_key)
        response = await client.aio.models.generate_content(
            model=self._model_name,
            contents=user_prompt,
            config=types.GenerateContentConfig(**config_kwargs),
        )
        meta = getattr(response, "usage_metadata", None)
        if meta is not None:
            self._record_usage(
                ProviderUsage(
                    prompt_tokens=getattr(meta, "prompt_token_count", 0),
                    completion_tokens=getattr(meta, "candidates_token_count", 0),
                )
            )
        return str(response.text)

    async def generate_content(self, system_prompt: str, user_prompt: str) -> str:
        """Send prompts to Gemini and return the text response."""
        return await self._generate(system_prompt, user_prompt, None)

    async def generate_structured(
        self, system_prompt: str, user_prompt: str, schema: type[BaseModel]
    ) -> str:
        """Send prompts in native JSON mode constrained by schema."""
        return await self._generate(system_prompt, user_prompt, schema)
