"""The OpenCode Go provider (#775), with the network stubbed.

Every branch here exists because failing it quietly would corrupt a review.
A spent quota, a truncated answer and an upstream 4xx all produce "no
judgement", and a finder that produces no judgement is indistinguishable from
one that found nothing.

The gateway speaks the OpenAI shape, so the request tests pin the parts this
provider chooses: the endpoint, the budget field, the reasoning depth and the
session header. The failure tests pin what it refuses to return.
"""

from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from cgis.guardian.providers import opencode as mod
from cgis.guardian.providers.opencode import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_OPENCODE_BASE_URL,
    DEFAULT_OPENCODE_EFFORT,
    DEFAULT_OPENCODE_MODEL,
    DEFAULT_REQUEST_TIMEOUT,
    OpenCodeProvider,
    OpenCodeQuotaError,
    OpenCodeReplyError,
)

_ENDPOINT = f"{DEFAULT_OPENCODE_BASE_URL}/chat/completions"


class _Judgement(BaseModel):
    verdict: str
    rationale: str


def _reply(
    content: str | None = '{"verdict": "refuted", "rationale": "r"}',
    *,
    status: int = 200,
    finish: str = "stop",
    usage: dict[str, int] | None = None,
    reasoning: str | None = None,
    body: dict[str, Any] | None = None,
) -> httpx.Response:
    message: dict[str, Any] = {"content": content}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    payload = body or {
        "choices": [{"message": message, "finish_reason": finish}],
        "usage": usage or {"prompt_tokens": 11, "completion_tokens": 7},
    }
    return httpx.Response(status, json=payload, request=httpx.Request("POST", _ENDPOINT))


def _serve(monkeypatch: pytest.MonkeyPatch, response: httpx.Response) -> dict[str, list[Any]]:
    """Route every POST to `response`; return the URLs, headers, bodies and timeouts sent."""
    captured: dict[str, list[Any]] = {
        "urls": [],
        "headers": [],
        "bodies": [],
        "timeouts": [],
    }

    class _Client:
        def __init__(self, **kw: object) -> None:
            captured["timeouts"].append(kw["timeout"])

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def post(self, url: str, **kwargs: object) -> httpx.Response:
            captured["urls"].append(url)
            captured["headers"].append(kwargs["headers"])
            captured["bodies"].append(kwargs["json"])
            return response

    monkeypatch.setattr(mod.httpx, "AsyncClient", _Client)
    return captured


def _provider(**overrides: object) -> OpenCodeProvider:
    settings: dict[str, Any] = {"api_key": "k", "model_name": DEFAULT_OPENCODE_MODEL}
    settings.update(overrides)
    return OpenCodeProvider(**settings)


class TestTheFailuresThatWouldLookLikeSilence:
    """Each of these would produce "no verdict", and each must say why instead."""

    @pytest.mark.asyncio
    async def test_a_spent_quota_raises_rather_than_returning_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """429 is not an answer, and the body names which limit was hit."""
        _serve(monkeypatch, _reply(status=429))
        provider = _provider()
        with pytest.raises(OpenCodeQuotaError, match="refutes nothing"):
            await provider.generate_content("s", "u")

    @pytest.mark.asyncio
    async def test_any_other_http_error_is_raised_not_degraded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An auth, model or effort mistake fails loudly, with the gateway's message."""
        _serve(monkeypatch, _reply(status=400, body={"error": {"message": "bad effort"}}))
        provider = _provider()
        with pytest.raises(httpx.HTTPStatusError):
            await provider.generate_content("s", "u")

    @pytest.mark.asyncio
    async def test_hitting_the_token_budget_raises_rather_than_returning_reasoning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _serve(monkeypatch, _reply(content="We need to assess the claim", finish="length"))
        provider = _provider(max_tokens=600)
        with pytest.raises(RuntimeError, match="reasoning, not an answer"):
            await provider.generate_content("s", "u")

    @pytest.mark.asyncio
    async def test_empty_content_beside_reasoning_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DeepSeek writes the chain of thought to `reasoning_content`.

        An empty `content` beside a non-empty thought means the answer never
        arrived; returning "" would be counted as an unparseable verdict.
        """
        _serve(monkeypatch, _reply(content=None, reasoning="The claim seems..."))
        provider = _provider()
        with pytest.raises(OpenCodeReplyError, match="reasoning but no content"):
            await provider.generate_content("s", "u")

    @pytest.mark.asyncio
    async def test_an_empty_choices_list_is_reported_not_an_indexerror(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An upstream content filter returns a 200 with an empty list."""
        _serve(monkeypatch, _reply(body={"choices": [], "usage": {}}))
        provider = _provider()
        with pytest.raises(RuntimeError, match="no choices"):
            await provider.generate_content("s", "u")


class TestTheRequest:
    """What goes on the wire, and what deliberately does not."""

    @pytest.mark.asyncio
    async def test_the_endpoint_and_the_identifying_headers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The gateway asks for a client user agent and a stable per-run session id."""
        captured = _serve(monkeypatch, _reply())
        provider = _provider()
        await provider.generate_content("s", "u")
        await provider.generate_content("s", "u")
        assert captured["urls"][0] == _ENDPOINT
        headers = captured["headers"][0]
        assert headers["Authorization"] == "Bearer k"
        assert str(headers["User-Agent"]).startswith("codegraph-brain-guardian/")
        sessions = [headers["x-opencode-session"] for headers in captured["headers"]]
        assert sessions[0]
        assert sessions[0] == sessions[1]

    @pytest.mark.asyncio
    async def test_the_base_url_can_be_pinned_and_its_trailing_slash_removed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A gateway pin with a trailing slash must not produce `//chat/completions`."""
        captured = _serve(monkeypatch, _reply())
        await _provider(base_url="https://gateway.example/v1/").generate_content("s", "u")
        assert captured["urls"][0] == "https://gateway.example/v1/chat/completions"

    @pytest.mark.asyncio
    async def test_the_timeout_is_the_providers_own(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured = _serve(monkeypatch, _reply())
        await _provider().generate_content("s", "u")
        await _provider(timeout=45.0).generate_content("s", "u")
        assert captured["timeouts"] == [DEFAULT_REQUEST_TIMEOUT, 45.0]

    @pytest.mark.asyncio
    async def test_the_budget_field_and_the_effort(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`max_tokens`, not `max_completion_tokens`; the effort is stated, not inherited."""
        captured = _serve(monkeypatch, _reply())
        await _provider().generate_content("s", "u")
        await _provider(effort="high").generate_content("s", "u")
        bodies = captured["bodies"]
        assert bodies[0]["max_tokens"] == DEFAULT_MAX_TOKENS
        assert bodies[0]["reasoning_effort"] == DEFAULT_OPENCODE_EFFORT
        assert bodies[1]["reasoning_effort"] == "high"

    @pytest.mark.asyncio
    async def test_effort_is_exposed_for_the_run_record(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _serve(monkeypatch, _reply())
        provider = _provider(effort="max")
        await provider.generate_content("s", "u")
        assert provider.effort == "max"

    @pytest.mark.asyncio
    async def test_an_unset_temperature_is_not_sent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An unset knob must not become a value this project invented."""
        captured = _serve(monkeypatch, _reply())
        await _provider().generate_content("s", "u")
        assert "temperature" not in captured["bodies"][0]

    @pytest.mark.asyncio
    async def test_a_zero_temperature_is_sent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """0.0 is the one temperature that states something, and it is falsy."""
        captured = _serve(monkeypatch, _reply())
        await _provider(temperature=0.0).generate_content("s", "u")
        assert captured["bodies"][0]["temperature"] == 0.0

    @pytest.mark.asyncio
    async def test_structured_generation_carries_the_schema(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _serve(monkeypatch, _reply())
        await _provider().generate_structured("s", "u", _Judgement)
        fmt = captured["bodies"][0]["response_format"]
        assert fmt["type"] == "json_schema"
        assert fmt["json_schema"]["name"] == "_Judgement"
        assert "verdict" in fmt["json_schema"]["schema"]["properties"]

    @pytest.mark.asyncio
    async def test_usage_is_recorded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _serve(monkeypatch, _reply(usage={"prompt_tokens": 100, "completion_tokens": 20}))
        provider = _provider()
        await provider.generate_content("s", "u")
        assert provider.last_usage.total_tokens == 120
        assert provider.cumulative_usage.total_tokens == 120

    @pytest.mark.asyncio
    async def test_a_reply_with_no_usage_is_not_a_crash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Some upstreams omit it; losing the token count must not lose the answer."""
        _serve(monkeypatch, _reply(body={"choices": [{"message": {"content": "ok"}}]}))
        assert await _provider().generate_content("s", "u") == "ok"


def test_the_default_model_is_the_cheap_deepseek() -> None:
    """The model the switch is measured on, and the one the catalog prices at 0.15/0.6."""
    assert DEFAULT_OPENCODE_MODEL == "deepseek-v4.1-flash"


def test_blank_settings_fall_back_to_their_defaults() -> None:
    """An empty endpoint, a bare slash or a missing effort must not build a broken URL.

    External callers can pass whitespace or a null where the environment path
    always supplies a string; the default is safer than an empty host or an
    AttributeError before the first request.
    """
    provider = _provider(base_url=" / ", effort=None)
    assert provider.effort == DEFAULT_OPENCODE_EFFORT
    assert provider._base_url == DEFAULT_OPENCODE_BASE_URL  # noqa: SLF001  # white-box: default
