"""Unit tests for provider retry semantics and timeout wiring (#275)."""

import sys
from types import ModuleType, SimpleNamespace
from typing import ClassVar

import httpx
import pytest
from pydantic import BaseModel

from cgis.guardian.providers.base import DEFAULT_REQUEST_TIMEOUT, MAX_ATTEMPTS, BaseProvider
from cgis.guardian.providers.gemini import GeminiProvider
from cgis.guardian.providers.mistral import MistralProvider


class _Recorder(BaseProvider):
    """A provider whose transport is scripted by the test.

    Distinct from guardian_stubs.StubProvider, which returns canned JSON: this
    one scripts per-call outcomes and spies on the backoff.
    """

    name: ClassVar[str] = "gemini"

    def __init__(self, outcomes: list[object]) -> None:
        """Store the scripted per-call outcomes: an exception to raise or a value."""
        super().__init__()
        self.outcomes = outcomes
        self.calls = 0
        self.slept: list[float] = []

    async def _transport(self) -> str:
        """Return or raise the next scripted outcome."""
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return str(outcome)

    async def _sleep(self, seconds: float) -> None:
        """Record the backoff instead of waiting."""
        self.slept.append(seconds)

    async def generate_content(
        self,
        system_prompt: str,  # noqa: ARG002
        user_prompt: str,  # noqa: ARG002
    ) -> str:
        """Route the scripted transport through the retry helper."""
        return await self._retry(self._transport)

    async def generate_structured(
        self,
        system_prompt: str,  # noqa: ARG002
        user_prompt: str,  # noqa: ARG002
        schema: type[BaseModel],  # noqa: ARG002
    ) -> str:
        """Route the scripted transport through the retry helper."""
        return await self._retry(self._transport)


async def test_retry_returns_the_value_after_a_transient_timeout() -> None:
    provider = _Recorder([httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow"), "ok"])

    result = await provider.generate_content("sys", "usr")

    assert result == "ok"
    assert provider.calls == 3


async def test_retry_gives_up_after_max_attempts_and_reraises() -> None:
    provider = _Recorder([httpx.ReadTimeout("slow")] * MAX_ATTEMPTS)

    with pytest.raises(httpx.ReadTimeout):
        await provider.generate_content("sys", "usr")

    assert provider.calls == MAX_ATTEMPTS


async def test_backoff_grows_exponentially_between_attempts() -> None:
    provider = _Recorder([httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow"), "ok"])

    await provider.generate_content("sys", "usr")

    assert provider.slept == [2.0, 4.0]


async def test_network_errors_are_retried_too() -> None:
    provider = _Recorder([httpx.ConnectError("refused"), "ok"])

    assert await provider.generate_content("sys", "usr") == "ok"
    assert provider.calls == 2


async def test_a_non_transient_error_is_not_retried() -> None:
    """An auth or validation failure must fail fast, not burn three calls."""
    provider = _Recorder([ValueError("bad api key")])

    with pytest.raises(ValueError, match="bad api key"):
        await provider.generate_content("sys", "usr")

    assert provider.calls == 1
    assert provider.slept == []


def test_default_request_timeout_is_generous_enough_to_have_slack() -> None:
    """The SDK default that killed #274 was 60 s; ours must exceed it."""
    assert DEFAULT_REQUEST_TIMEOUT > 60.0


def test_mistral_converts_the_timeout_to_milliseconds() -> None:
    """A seconds-vs-milliseconds slip is a factor-of-1000 error nothing else catches."""
    provider = MistralProvider(api_key="k", timeout=45.0)

    assert provider._timeout_ms == 45000  # noqa: SLF001  # white-box: timeout wiring


def test_mistral_defaults_to_the_shared_timeout() -> None:
    provider = MistralProvider(api_key="k")

    assert provider._timeout_ms == int(  # noqa: SLF001  # white-box: timeout wiring
        DEFAULT_REQUEST_TIMEOUT * 1000
    )


def test_gemini_converts_the_timeout_to_milliseconds() -> None:
    """HttpOptions.timeout is documented in milliseconds, same trap as Mistral."""
    provider = GeminiProvider(api_key="k", timeout=45.0)

    assert provider._timeout_ms == 45000  # noqa: SLF001  # white-box: timeout wiring


def test_gemini_defaults_to_the_shared_timeout() -> None:
    provider = GeminiProvider(api_key="k")

    assert provider._timeout_ms == int(  # noqa: SLF001  # white-box: timeout wiring
        DEFAULT_REQUEST_TIMEOUT * 1000
    )


def _install_fake_genai(monkeypatch: pytest.MonkeyPatch, meta: object) -> None:
    """Put a stand-in `google.genai` in sys.modules; its one reply carries `meta` as usage.

    A fake module rather than the SDK: CI installs no guardian extra, and these
    tests are about what the provider does with the usage it is handed.
    """

    class _Models:
        async def generate_content(self, **_kwargs: object) -> object:
            return SimpleNamespace(text='{"ok": true}', usage_metadata=meta)

    class _Aio:
        models = _Models()

        async def aclose(self) -> None:
            return None

    class _Client:
        def __init__(self, **_kwargs: object) -> None:
            self.aio = _Aio()

        def close(self) -> None:
            return None

    types = ModuleType("google.genai.types")
    types.HttpOptions = lambda **_kw: None  # type: ignore[attr-defined]
    types.GenerateContentConfig = lambda **_kw: None  # type: ignore[attr-defined]
    genai = ModuleType("google.genai")
    genai.Client = _Client  # type: ignore[attr-defined]
    genai.types = types  # type: ignore[attr-defined]
    google = ModuleType("google")
    google.genai = genai  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", types)


async def test_gemini_bills_thinking_as_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    """gemini-2.5 thinks by default and bills it at the output rate (#714 review).

    Recording only `candidates_token_count` under-reported every gemini pass,
    and a budget priced from it would let a gemini arm overspend its cap.
    """
    meta = SimpleNamespace(
        prompt_token_count=100, candidates_token_count=40, thoughts_token_count=60
    )
    _install_fake_genai(monkeypatch, meta)
    provider = GeminiProvider(api_key="k")
    await provider.generate_content("sys", "user")
    assert provider.last_usage.prompt_tokens == 100
    assert provider.last_usage.completion_tokens == 100


async def test_gemini_without_thinking_counts_candidates_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK reports `thoughts_token_count=None` when the model did not think."""
    meta = SimpleNamespace(
        prompt_token_count=100, candidates_token_count=40, thoughts_token_count=None
    )
    _install_fake_genai(monkeypatch, meta)
    provider = GeminiProvider(api_key="k")
    await provider.generate_content("sys", "user")
    assert provider.last_usage.completion_tokens == 40


def test_every_call_is_kept_for_per_request_pricing() -> None:
    """A price tier decided per request needs each request, not their sum."""
    from cgis.guardian.providers.base import ProviderUsage  # noqa: PLC0415

    provider = GeminiProvider(api_key="k")
    provider._record_usage(ProviderUsage(prompt_tokens=10, completion_tokens=1))  # noqa: SLF001
    provider._record_usage(ProviderUsage(prompt_tokens=20, completion_tokens=2))  # noqa: SLF001
    assert [u.prompt_tokens for u in provider.call_usages] == [10, 20]
    assert provider.cumulative_usage.prompt_tokens == 30
