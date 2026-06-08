from cgis.guardian.providers.base import BaseProvider


class GeminiProvider(BaseProvider):
    """Google Gemini provider. Requires: uv sync --extra guardian"""

    def __init__(self, api_key: str, model_name: str = "gemini-1.5-pro") -> None:
        self._api_key = api_key
        self._model_name = model_name

    async def generate_content(self, system_prompt: str, user_prompt: str) -> str:
        _install_hint = "google-generativeai is required. Install with: uv sync --extra guardian"
        try:
            import google.generativeai as genai  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(_install_hint) from exc
        genai.configure(api_key=self._api_key)
        model = genai.GenerativeModel(self._model_name, system_instruction=system_prompt)
        response = await model.generate_content_async(user_prompt)
        return str(response.text)
