"""Ollama LLM provider for Guardian — local/colab inference, no API key.

Targets an Ollama server (default http://localhost:11434, or a tunneled port via
GUARDIAN_OLLAMA_HOST). The model must already be pulled on that server
(`ollama pull <model>`). Useful for free local benching and for a cross-model
skeptic built from two distinct local models.
"""

from collections.abc import Mapping
from typing import ClassVar

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

#: Repetition penalties applied to schema-constrained output only.
#:
#: Ollama takes these from the model's chat template when nothing overrides
#: them — qwen3.5:9b ships `presence_penalty 1.5`, `repeat_penalty 1.1`. Those
#: are chat presets: they reward novel tokens, which is a reasonable default for
#: conversation and an unexamined one for extraction, where the finder's JSON
#: repeats its keys in every object by construction.
#:
#: Neutral rather than tuned. The point is not that 0.0 is optimal, it is that
#: the value should be one this project chose and can state — a run whose
#: sampling is inherited cannot be described afterwards, which is the gap
#: retraction R5 was about.
#:
#: Free-text generation keeps the model's own values: only the structured path
#: has the argument above.
#: Token budget for a single generation, or None for the model's own behaviour.
#:
#: qwen3.5:9b produced 9,358 tokens on a 6.5k-token prompt without emitting a
#: stop token and hit the client timeout, which returns nothing at all. The
#: schema's `findings` array carries no `maxItems`, so grammar-constrained
#: decoding always permits another element and closing the array stays a choice
#: the model can decline.
#:
#: A budget turns that into a truncated response, and a truncated response
#: yields its valid prefix and is flagged `parse_failed`, so the benchmark
#: excludes it instead of scoring a finder that was cut off — the budget bounds
#: cost without quietly shortening a measurement.
#:
#: Not the finding cap #249 removed: that limited how many claims the model was
#: allowed to make and depressed recall by construction. 8192 sits well above
#: the largest real finder output measured (6,784 tokens on Mistral), so a
#: healthy run should never reach it.
DEFAULT_OLLAMA_NUM_PREDICT = 8192

EXTRACTION_PENALTIES: dict[str, float] = {
    "presence_penalty": 0.0,
    "frequency_penalty": 0.0,
    "repeat_penalty": 1.0,
}


class PromptTruncatedError(RuntimeError):
    """The prompt filled the context window, so the model saw only part of it.

    Deliberately not a retryable transport error and deliberately not a warning.
    Ollama truncates silently, and a truncated review is not a worse review — it
    is a review of a different input, scored as though it were a review of the
    whole one. Every bench number computed on it would look ordinary. This is the
    same failure the spec keeps retracting for: a quantity measured on one thing
    and reported against another.
    """


class OllamaProvider(BaseProvider):
    """Ollama provider. Requires: uv sync --group guardian (ollama client)."""

    name: ClassVar[str] = "ollama"

    def __init__(
        self,
        model_name: str,
        host: str | None = None,
        timeout: float = DEFAULT_OLLAMA_TIMEOUT,
        num_ctx: int = DEFAULT_OLLAMA_NUM_CTX,
        temperature: float | None = None,
        penalties: Mapping[str, float] | None = None,
        num_predict: int | None = None,
    ) -> None:
        """Store model, host (None → localhost:11434), timeout, window and sampling.

        `temperature` is None by default and then not sent, leaving the model's
        own value in place — the same rule the Mistral provider follows, so an
        unset knob never becomes a value this project invented.

        `penalties` overrides the repetition defaults applied to structured
        output (see `EXTRACTION_PENALTIES`), so an experiment can restore the
        model's own and say that it did. An empty mapping sends none of them.

        `num_predict` bounds one generation (see `DEFAULT_OLLAMA_NUM_PREDICT`);
        None leaves the model unbounded.
        """
        super().__init__()
        self._model_name = model_name
        self._host = host
        self._timeout = timeout
        self._num_ctx = num_ctx
        self._temperature = temperature
        self._penalties = dict(penalties) if penalties is not None else None
        self._num_predict = num_predict

    async def _generate(
        self, system_prompt: str, user_prompt: str, *, fmt: str | dict[str, object]
    ) -> str:
        """Shared transport: one non-streaming chat call.

        fmt is "" (free text), "json" (valid JSON, any shape), or a JSON Schema
        dict (constrained decoding — forces a schema-conformant object). Small
        local models emit null in required fields under plain "json"; the schema
        form prevents that.
        """
        _install_hint = "ollama is required. Install with: uv sync --group guardian"
        try:
            from ollama import AsyncClient  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(_install_hint) from exc
        # Context manager so the underlying httpx pool is closed each call.
        options: dict[str, object] = {"num_ctx": self._num_ctx}
        # `is not None`, not truthiness: 0.0 is the one temperature that states
        # something, and a falsy test would drop exactly that value.
        if self._temperature is not None:
            options["temperature"] = self._temperature
        if self._num_predict is not None:
            options["num_predict"] = self._num_predict
        if fmt:
            # `fmt` is truthy for "json" and for a schema dict, and empty for
            # free text — so the penalties are neutralised exactly where the
            # output is required to repeat its own structure.
            options.update(self._penalties if self._penalties is not None else EXTRACTION_PENALTIES)

        async with AsyncClient(host=self._host, timeout=self._timeout) as client:
            response = await client.chat(
                model=self._model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                stream=False,
                format=fmt,
                options=options,
            )
        self._refuse_if_truncated(response.prompt_eval_count)
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

    def _refuse_if_truncated(self, prompt_eval_count: int | None) -> None:
        """Raise when the reported prompt tokens reached the context window.

        `prompt_eval_count` cannot exceed the window, so reaching it means the
        window — not the prompt — decided how much the model saw.

        Only the upper bound is a signal. A *small* count proves nothing: Ollama
        reports fewer evaluated tokens when a prefix was cached, so comparing
        against the prompt's own length would fire on healthy calls. `None` is
        unknown rather than guilty; the field is optional in the API.
        """
        if prompt_eval_count is None or prompt_eval_count < self._num_ctx:
            return
        _msg = (
            f"Ollama truncated the prompt for {self._model_name}: it evaluated "
            f"{prompt_eval_count} tokens against a context window of {self._num_ctx}, "
            "so the model reviewed only part of the input. Raise "
            "GUARDIAN_OLLAMA_NUM_CTX (watch VRAM — a 14B-Q4 tops out near 16k on "
            "16GB, an 8B-Q4 reaches 32-48k), use a longer-context model, or trim "
            "the prompt with GUARDIAN_NO_GRAPH."
        )
        raise PromptTruncatedError(_msg)

    async def generate_content(self, system_prompt: str, user_prompt: str) -> str:
        """Send prompts to Ollama and return the text response."""
        return await self._retry(lambda: self._generate(system_prompt, user_prompt, fmt=""))

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
    ) -> str:
        """Send prompts with Ollama's schema-constrained format (structured output).

        Passing the model's JSON Schema makes Ollama constrain decoding to a
        conformant object — small local models otherwise emit null in required
        fields under plain "json" mode, which fails strict validation downstream.
        """
        return await self._retry(
            lambda: self._generate(system_prompt, user_prompt, fmt=schema.model_json_schema())
        )
