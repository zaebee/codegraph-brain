"""Ollama must not return a review of a prompt it silently cut in half.

Ollama truncates any prompt longer than `num_ctx` without saying so. For a
reviewer that is not a performance problem, it is a measurement problem: the
finder is shown a fraction of the diff, recall falls, and every number
downstream looks entirely normal. The provider already sets an explicit window
for this reason — this is the other half, which notices when the window was not
enough.

The signal is `prompt_eval_count`, the token count Ollama reports back. It
cannot exceed the context window, so reaching it means the window was the
binding constraint on how much of the prompt the model saw.
"""

import sys
import types
from collections.abc import Callable

import pytest
from pydantic import BaseModel

from cgis.guardian.providers.ollama import OllamaProvider, PromptTruncatedError

#: Installs a scripted `ollama` module for one test.
InstallFake = Callable[["_FakeResponse"], None]


class _Schema(BaseModel):
    """Minimal structured-output schema."""

    value: str


class _FakeResponse:
    """What `AsyncClient.chat` returns, reduced to the fields the provider reads."""

    def __init__(self, content: str | None, prompt_eval_count: int | None, eval_count: int) -> None:
        self.message = types.SimpleNamespace(content=content)
        self.prompt_eval_count = prompt_eval_count
        self.eval_count = eval_count


class _FakeClient:
    """Stands in for `ollama.AsyncClient`, recording the options it was given."""

    last_options: dict[str, object] | None = None

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def chat(self, **kwargs: object) -> _FakeResponse:
        """Record the call and return the scripted response."""
        type(self).last_options = kwargs.get("options")
        return self._response


@pytest.fixture
def install_fake_ollama(monkeypatch: pytest.MonkeyPatch) -> InstallFake:
    """Replace the `ollama` module so the provider's local import finds our client."""

    def _install(response: _FakeResponse) -> None:
        module = types.ModuleType("ollama")
        module.AsyncClient = lambda **_kwargs: _FakeClient(response)  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "ollama", module)

    return _install


class _SleeplessProvider(OllamaProvider):
    """Records backoffs instead of waiting, so a retry would be visible and free."""

    def __init__(self, *, model_name: str, num_ctx: int) -> None:
        """Build the provider and the list its backoffs land in."""
        super().__init__(model_name=model_name, num_ctx=num_ctx)
        self.slept: list[float] = []

    async def _sleep(self, seconds: float) -> None:
        """Record the backoff rather than sleeping through it."""
        self.slept.append(seconds)


class TestTruncationGuard:
    """A prompt that filled the window is refused, not scored."""

    @pytest.mark.asyncio
    async def test_raises_when_the_prompt_filled_the_context_window(
        self, install_fake_ollama: InstallFake
    ) -> None:
        """`prompt_eval_count == num_ctx` means the window bound the prompt."""
        install_fake_ollama(_FakeResponse('{"value": "x"}', prompt_eval_count=4096, eval_count=10))
        provider = _provider(num_ctx=4096)

        with pytest.raises(PromptTruncatedError, match="4096"):
            await provider.generate_content("sys", "user")

    @pytest.mark.asyncio
    async def test_the_error_names_the_model_and_the_knob_that_fixes_it(
        self, install_fake_ollama: InstallFake
    ) -> None:
        """A truncation this quiet must not produce a message that hides the cause."""
        install_fake_ollama(_FakeResponse("{}", prompt_eval_count=8192, eval_count=1))
        provider = _provider(num_ctx=8192)

        with pytest.raises(PromptTruncatedError) as caught:
            await provider.generate_content("sys", "user")

        message = str(caught.value)
        assert "qwen3:8b" in message
        assert "GUARDIAN_OLLAMA_NUM_CTX" in message

    @pytest.mark.asyncio
    async def test_a_prompt_that_fits_is_returned_normally(
        self, install_fake_ollama: InstallFake
    ) -> None:
        """The guard must not fire on the common case."""
        install_fake_ollama(_FakeResponse("ok", prompt_eval_count=4095, eval_count=10))

        assert await _provider(num_ctx=4096).generate_content("sys", "user") == "ok"

    @pytest.mark.asyncio
    async def test_a_cached_prefix_reporting_few_tokens_is_not_truncation(
        self, install_fake_ollama: InstallFake
    ) -> None:
        """A *small* count is not a signal: Ollama reports fewer on a cache hit.

        Only reaching the window is unambiguous, which is why the guard tests
        `>=` against `num_ctx` rather than comparing to the prompt's own length.
        """
        install_fake_ollama(_FakeResponse("ok", prompt_eval_count=3, eval_count=10))

        assert await _provider(num_ctx=32768).generate_content("sys", "user") == "ok"

    @pytest.mark.asyncio
    async def test_a_missing_count_does_not_fire_the_guard(
        self, install_fake_ollama: InstallFake
    ) -> None:
        """`prompt_eval_count` is optional in the API; absent is unknown, not guilty."""
        install_fake_ollama(_FakeResponse("ok", prompt_eval_count=None, eval_count=10))

        assert await _provider(num_ctx=4096).generate_content("sys", "user") == "ok"

    @pytest.mark.asyncio
    async def test_the_guard_covers_structured_output_too(
        self, install_fake_ollama: InstallFake
    ) -> None:
        """The finder uses the structured path, so a guard on free text alone is none."""
        install_fake_ollama(_FakeResponse('{"value": "x"}', prompt_eval_count=4096, eval_count=5))
        provider = _provider(num_ctx=4096)

        with pytest.raises(PromptTruncatedError):
            await provider.generate_structured("sys", "user", _Schema)

    @pytest.mark.asyncio
    async def test_truncation_is_not_retried(self, install_fake_ollama: InstallFake) -> None:
        """Retrying an identical oversized prompt burns minutes to fail identically."""
        install_fake_ollama(_FakeResponse("ok", prompt_eval_count=4096, eval_count=1))
        provider = _SleeplessProvider(model_name="qwen3:8b", num_ctx=4096)

        with pytest.raises(PromptTruncatedError):
            await provider.generate_content("sys", "user")

        assert provider.slept == []


def _provider(*, num_ctx: int) -> OllamaProvider:
    """An OllamaProvider with a named model and an explicit window."""
    return OllamaProvider(model_name="qwen3:8b", num_ctx=num_ctx)


class TestTransportFailures:
    """The two error paths either side of the guard, neither previously covered."""

    @pytest.mark.asyncio
    async def test_a_missing_ollama_package_names_the_install_command(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The client is imported lazily, so its absence surfaces at call time."""
        monkeypatch.setitem(sys.modules, "ollama", None)

        with pytest.raises(ImportError, match="uv sync --group guardian"):
            await _provider(num_ctx=4096).generate_content("sys", "user")

    @pytest.mark.asyncio
    async def test_null_content_is_an_error_rather_than_an_empty_review(
        self, install_fake_ollama: InstallFake
    ) -> None:
        """An empty review scores as "found nothing", which is a real finding.

        Small models return null content under schema-constrained decoding often
        enough that letting it through would quietly depress recall — the same
        shape as the truncation this module exists to refuse.
        """
        install_fake_ollama(_FakeResponse(None, prompt_eval_count=10, eval_count=0))

        with pytest.raises(ValueError, match="null message content"):
            await _provider(num_ctx=4096).generate_content("sys", "user")
