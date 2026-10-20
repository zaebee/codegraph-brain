"""Google Gemini LLM provider for Guardian."""

from typing import ClassVar

from pydantic import BaseModel

from cgis.guardian.providers.base import DEFAULT_REQUEST_TIMEOUT, BaseProvider, ProviderUsage


def _count(meta: object, field: str) -> int:
    """A token count from usage metadata; the SDK gives None, not 0, for a field it lacks."""
    return int(getattr(meta, field, 0) or 0)


class GeminiProvider(BaseProvider):
    """Google Gemini provider. Requires: uv sync --group guardian"""

    name: ClassVar[str] = "gemini"

    def __init__(
        self,
        api_key: str,
        model_name: str = "gemini-2.5-flash",
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        """Store credentials and the request timeout; google-genai is imported lazily.

        HttpOptions.timeout is documented in MILLISECONDS — the same unit trap
        as Mistral, and the opposite of Ollama's seconds.
        """
        super().__init__()
        self._api_key = api_key
        self._model_name = model_name
        self._timeout_ms = int(timeout * 1000)

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
        client = genai.Client(
            api_key=self._api_key,
            http_options=types.HttpOptions(timeout=self._timeout_ms),
        )
        try:
            response = await client.aio.models.generate_content(
                model=self._model_name,
                contents=user_prompt,
                config=types.GenerateContentConfig(**config_kwargs),
            )
            meta = getattr(response, "usage_metadata", None)
            if meta is not None:
                # Thinking is billed at the output rate and reported apart from
                # the answer; gemini-2.5 thinks by default and this provider sets
                # no thinking config, so leaving it out under-reported every pass.
                self._record_usage(
                    ProviderUsage(
                        prompt_tokens=_count(meta, "prompt_token_count"),
                        completion_tokens=_count(meta, "candidates_token_count")
                        + _count(meta, "thoughts_token_count"),
                    )
                )
            return str(response.text)
        finally:
            # genai.Client opens BOTH a sync and an async httpx pool on
            # construction, and each close covers only its own half — the SDK
            # docstrings say so explicitly in both directions. Mistral and Ollama
            # get this from `async with`; Gemini's client is not an async context
            # manager, so it is spelled out here (#283). With the retry from #275
            # calling this up to MAX_ATTEMPTS times, leaking would compound.
            try:
                await client.aio.aclose()
            finally:
                # Nested so the sync pool is released even if the async teardown
                # itself throws — two flat statements would skip it, which is the
                # exact class of leak this change exists to remove (found in review).
                client.close()

    async def generate_content(self, system_prompt: str, user_prompt: str) -> str:
        """Send prompts to Gemini and return the text response."""
        return await self._retry(lambda: self._generate(system_prompt, user_prompt, None))

    async def generate_structured(
        self, system_prompt: str, user_prompt: str, schema: type[BaseModel]
    ) -> str:
        """Send prompts in native JSON mode constrained by schema."""
        return await self._retry(lambda: self._generate(system_prompt, user_prompt, schema))
