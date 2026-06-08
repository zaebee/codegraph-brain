"""Google Gemini LLM provider for Guardian."""

from cgis.guardian.providers.base import BaseProvider


class GeminiProvider(BaseProvider):
    """Google Gemini provider. Requires: uv sync --group guardian"""

    def __init__(self, api_key: str, model_name: str = "gemini-2.0-flash") -> None:
        """Store credentials; google-genai is imported lazily at call time."""
        self._api_key = api_key
        self._model_name = model_name

    async def generate_content(self, system_prompt: str, user_prompt: str) -> str:
        """Send prompts to Gemini and return the text response."""
        _install_hint = "google-genai is required. Install with: uv sync --group guardian"
        try:
            from google import genai  # noqa: PLC0415
            from google.genai import types  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(_install_hint) from exc
        client = genai.Client(api_key=self._api_key)
        response = await client.aio.models.generate_content(
            model=self._model_name,
            contents=user_prompt,
            config=types.GenerateContentConfig(system_instruction=system_prompt),
        )
        return str(response.text)
