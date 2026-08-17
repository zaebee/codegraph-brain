"""Sampling must reach the API, and a 429 must not eat a review (#342 Phase 3).

Phase 3 registered `temperature = 0.7` before any money was spent. The provider
was not sending a temperature at all, so running the phase as it stood would have
measured the API default while the spec claimed 0.7 — registering one
configuration and executing another, which is the failure retraction R2 was for.

The rate-limit half is separate. The Mistral SDK knows how to back off a 429, but
its `retry_config` defaults to UNSET, which disables retrying entirely: the first
429 in a sequential run of 57 reviews raises and that PR is lost from the arm.
"""

import sys
import types
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import BaseModel

from cgis.guardian.providers.mistral import MistralProvider


class _Schema(BaseModel):
    """Minimal structured-output schema."""

    value: str


class _FakeBackoff:
    """The SDK's BackoffStrategy, reduced to the fields the provider sets."""

    def __init__(
        self,
        initial_interval: int,
        max_interval: int,
        exponent: float,
        max_elapsed_time: int,
    ) -> None:
        self.initial_interval = initial_interval
        self.max_interval = max_interval
        self.exponent = exponent
        self.max_elapsed_time = max_elapsed_time


class _FakeRetryConfig:
    """The SDK's RetryConfig, reduced to the fields the provider sets."""

    def __init__(self, strategy: str, backoff: _FakeBackoff, retry_connection_errors: bool) -> None:
        self.strategy = strategy
        self.backoff = backoff
        self.retry_connection_errors = retry_connection_errors


class _FakeChat:
    """Captures the keyword arguments the provider sends to `chat.complete_async`."""

    def __init__(self, sink: dict[str, Any]) -> None:
        self._sink = sink

    async def complete_async(self, **kwargs: object) -> types.SimpleNamespace:
        """Record the call and return one usable choice."""
        self._sink.update(kwargs)
        message = types.SimpleNamespace(content="ok")
        choice = types.SimpleNamespace(message=message)
        return types.SimpleNamespace(choices=[choice], usage=None)


class _FakeMistral:
    """Stands in for `mistralai.client.Mistral`, recording its constructor kwargs."""

    def __init__(self, ctor_sink: dict[str, Any], call_sink: dict[str, Any]) -> None:
        self._ctor_sink = ctor_sink
        self.chat = _FakeChat(call_sink)

    async def __aenter__(self) -> "_FakeMistral":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


@pytest.fixture
def spy() -> tuple[dict[str, Any], dict[str, Any], Callable[[pytest.MonkeyPatch], None]]:
    """Return (constructor kwargs, call kwargs, installer for the fake SDK)."""
    ctor: dict[str, Any] = {}
    call: dict[str, Any] = {}

    def install(monkeypatch: pytest.MonkeyPatch) -> None:
        def factory(**kwargs: object) -> _FakeMistral:
            ctor.update(kwargs)
            return _FakeMistral(ctor, call)

        module = types.ModuleType("mistralai.client")
        module.Mistral = factory  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "mistralai.client", module)

        # The retries submodule is faked too, so this asserts what the provider
        # *sends* rather than whether the optional SDK happens to be installed.
        # CI installs no guardian extras, and the first version of these tests
        # passed locally and failed there for exactly that reason.
        retries = types.ModuleType("mistralai.client.utils.retries")
        retries.RetryConfig = _FakeRetryConfig  # type: ignore[attr-defined]
        retries.BackoffStrategy = _FakeBackoff  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "mistralai.client.utils.retries", retries)

    return ctor, call, install


class TestTemperature:
    """What the spec registered has to be what the API receives."""

    @pytest.mark.asyncio
    async def test_the_registered_temperature_reaches_the_api(
        self,
        monkeypatch: pytest.MonkeyPatch,
        spy: tuple[dict[str, Any], dict[str, Any], Callable[[pytest.MonkeyPatch], None]],
    ) -> None:
        """Phase 3 is registered at 0.7, so 0.7 must appear in the request."""
        _, call, install = spy
        install(monkeypatch)

        await MistralProvider(api_key="k", temperature=0.7).generate_content("s", "u")

        assert call["temperature"] == 0.7

    @pytest.mark.asyncio
    async def test_no_temperature_is_sent_when_none_is_configured(
        self,
        monkeypatch: pytest.MonkeyPatch,
        spy: tuple[dict[str, Any], dict[str, Any], Callable[[pytest.MonkeyPatch], None]],
    ) -> None:
        """Unset must stay unset, or this becomes a config change to every caller.

        The frozen Gemini-era behaviour is "whatever the API defaults to", and
        the spec keeps it that way deliberately. Sending an invented default
        here would silently alter the production reviewer.
        """
        _, call, install = spy
        install(monkeypatch)

        await MistralProvider(api_key="k").generate_content("s", "u")

        assert "temperature" not in call

    @pytest.mark.asyncio
    async def test_temperature_zero_is_sent_rather_than_treated_as_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
        spy: tuple[dict[str, Any], dict[str, Any], Callable[[pytest.MonkeyPatch], None]],
    ) -> None:
        """0.0 is falsy, and a truthiness test would drop the one value that is a claim."""
        _, call, install = spy
        install(monkeypatch)

        await MistralProvider(api_key="k", temperature=0.0).generate_content("s", "u")

        assert call["temperature"] == 0.0

    @pytest.mark.asyncio
    async def test_the_structured_path_carries_it_too(
        self,
        monkeypatch: pytest.MonkeyPatch,
        spy: tuple[dict[str, Any], dict[str, Any], Callable[[pytest.MonkeyPatch], None]],
    ) -> None:
        """The finder uses JSON mode, so a temperature on free text alone is none."""
        _, call, install = spy
        install(monkeypatch)

        provider = MistralProvider(api_key="k", temperature=0.7)

        await provider.generate_structured("s", "u", _Schema)

        assert call["temperature"] == 0.7
        assert call["response_format"] == {"type": "json_object"}


class TestRateLimitRetry:
    """A 429 in a 57-review run must cost seconds, not a PR."""

    @pytest.mark.asyncio
    async def test_the_client_is_given_a_retry_config(
        self,
        monkeypatch: pytest.MonkeyPatch,
        spy: tuple[dict[str, Any], dict[str, Any], Callable[[pytest.MonkeyPatch], None]],
    ) -> None:
        """The SDK retries 429 only when handed a config; its default is UNSET."""
        ctor, _, install = spy
        install(monkeypatch)

        await MistralProvider(api_key="k").generate_content("s", "u")

        assert ctor["retry_config"] is not None
        assert ctor["retry_config"].strategy == "backoff"

    @pytest.mark.asyncio
    async def test_connection_errors_stay_with_the_provider_retry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        spy: tuple[dict[str, Any], dict[str, Any], Callable[[pytest.MonkeyPatch], None]],
    ) -> None:
        """Two layers retrying the same failure multiply the wait for no gain.

        `BaseProvider._retry` already owns timeouts and network errors, so the
        SDK is told to leave them alone and handle only the status codes it
        knows about.
        """
        ctor, _, install = spy
        install(monkeypatch)

        await MistralProvider(api_key="k").generate_content("s", "u")

        assert ctor["retry_config"].retry_connection_errors is False
