"""Ollama LLM provider for Guardian — local/colab inference, no API key.

Targets an Ollama server (default http://localhost:11434, or a tunneled port via
GUARDIAN_OLLAMA_HOST). The model must already be pulled on that server
(`ollama pull <model>`). Useful for free local benching and for a cross-model
skeptic built from two distinct local models.
"""

from pydantic import BaseModel

from cgis.guardian.providers.base import BaseProvider, ProviderUsage

# Colab/local GPUs are slow and cold-load weights on the first call — a tight
# timeout would spuriously fail the first request of every run.
DEFAULT_OLLAMA_TIMEOUT = 600.0

# Ollama defaults num_ctx to ~2048 and SILENTLY TRUNCATES longer prompts — which
# would feed the finder a fraction of the diff and quietly tank recall. Guardian
# prompts (diff + graph + files) run tens of thousands of tokens, so set an
# explicit window. 32768 matches qwen2.5-coder's max; raise it (e.g. for
# llama3.1's 128k) or lower it for VRAM via GUARDIAN_OLLAMA_NUM_CTX.
DEFAULT_OLLAMA_NUM_CTX = 32768


class OllamaProvider(BaseProvider):
    """Ollama provider. Requires: uv sync --group guardian (ollama client)."""

    def __init__(
        self,
        model_name: str,
        host: str | None = None,
        timeout: float = DEFAULT_OLLAMA_TIMEOUT,
        num_ctx: int = DEFAULT_OLLAMA_NUM_CTX,
    ) -> None:
        """Store model, host (None → localhost:11434), timeout, and context window."""
        super().__init__()
        self._model_name = model_name
        self._host = host
        self._timeout = timeout
        self._num_ctx = num_ctx

    async def _generate(self, system_prompt: str, user_prompt: str, *, json_mode: bool) -> str:
        """Shared transport: one non-streaming chat call, optional JSON format."""
        _install_hint = "ollama is required. Install with: uv sync --group guardian"
        try:
            from ollama import AsyncClient  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(_install_hint) from exc
        # Context manager so the underlying httpx pool is closed each call.
        async with AsyncClient(host=self._host, timeout=self._timeout) as client:
            response = await client.chat(
                model=self._model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                stream=False,
                format="json" if json_mode else "",
                options={"num_ctx": self._num_ctx},
            )
        content = response.message.content
        if content is None:
            _msg = f"Ollama returned null message content for model {self._model_name}"
            raise ValueError(_msg)
        self._record_usage(
            ProviderUsage(
                prompt_tokens=response.prompt_eval_count or 0,
                completion_tokens=response.eval_count or 0,
            )
        )
        return str(content)

    async def generate_content(self, system_prompt: str, user_prompt: str) -> str:
        """Send prompts to Ollama and return the text response."""
        return await self._generate(system_prompt, user_prompt, json_mode=False)

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
    ) -> str:
        """Send prompts in JSON mode (format="json").

        Like Mistral's json_object mode, Ollama's "json" format takes no schema —
        the schema is described in the user prompt (spec §2.4); the argument
        exists to satisfy the BaseProvider contract.
        """
        del schema
        return await self._generate(system_prompt, user_prompt, json_mode=True)
