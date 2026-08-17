"""The OpenRouter provider (#246), with the network stubbed.

Every branch here exists because failing it quietly would corrupt the
experiment the provider was added for. A spent quota, a truncated reasoning
model and an upstream refusal all produce "no verdict", and a skeptic that
produces no verdict is indistinguishable from one that refutes nothing — which
is the hypothesis #246 tests.
"""

import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from cgis.guardian.providers import openrouter as mod
from cgis.guardian.providers.openrouter import (
    OpenRouterProvider,
    OpenRouterQuotaError,
    parse_or_raise,
)


class _Judgement(BaseModel):
    verdict: str
    rationale: str


def _reply(
    content: str = '{"verdict": "refuted", "rationale": "r"}',
    *,
    status: int = 200,
    finish: str = "stop",
    usage: dict[str, int] | None = None,
    body: dict[str, Any] | None = None,
) -> httpx.Response:
    payload = body or {
        "choices": [{"message": {"content": content}, "finish_reason": finish}],
        "usage": usage or {"prompt_tokens": 11, "completion_tokens": 7},
    }
    return httpx.Response(status, json=payload, request=httpx.Request("POST", mod.OPENROUTER_URL))


def _serve(monkeypatch: pytest.MonkeyPatch, response: httpx.Response) -> list[dict[str, Any]]:
    """Route every POST to `response`; return the list of request bodies sent."""
    sent: list[dict[str, Any]] = []

    class _Client:
        def __init__(self, **_kw: object) -> None:
            pass

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def post(self, _url: str, **kwargs: object) -> httpx.Response:
            sent.append(kwargs["json"])  # type: ignore[arg-type]
            return response

    monkeypatch.setattr(mod.httpx, "AsyncClient", _Client)
    return sent


def _provider(
    max_tokens: int = mod.DEFAULT_MAX_TOKENS, temperature: float | None = None
) -> OpenRouterProvider:
    return OpenRouterProvider(
        api_key="k",
        model_name="vendor/model:free",
        max_tokens=max_tokens,
        temperature=temperature,
    )


class TestTheFailuresThatWouldLookLikeLeniency:
    """Each of these returns "no verdict", and each must say why."""

    @pytest.mark.asyncio
    async def test_a_spent_quota_raises_rather_than_returning_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """429 is not an answer. The free tier gives no warning before it.

        `/api/v1/auth/key` reports no limit for a free key and completions carry
        no rate-limit headers, so exhaustion is only ever discovered by hitting
        it — mid-run, after some findings have been judged and some have not.
        """
        _serve(monkeypatch, _reply(status=429))
        provider = _provider()
        with pytest.raises(OpenRouterQuotaError, match="refutes nothing"):
            await provider.generate_content("s", "u")

    @pytest.mark.asyncio
    async def test_hitting_the_token_budget_raises_rather_than_returning_reasoning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The mistake that read three capable models as incapable.

        A 600-token probe cut these reasoning models off mid-thought and
        returned the reasoning as the answer, which parses as nothing. The limit
        was this project's choice; attributing it to the model was the error.
        """
        _serve(monkeypatch, _reply(content="We need to assess the claim", finish="length"))
        provider = _provider(max_tokens=600)
        with pytest.raises(RuntimeError, match="reasoning, not an answer"):
            await provider.generate_content("s", "u")

    @pytest.mark.asyncio
    async def test_an_error_object_with_http_200_is_reported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OpenRouter reports an upstream refusal this way, with a 200.

        `body["choices"]` would raise KeyError and lose the message naming which
        provider declined — which is how the account's allowed-providers setting
        surfaces, and it is a one-click fix nobody can apply without the text.
        """
        refusal = {"error": {"message": "No allowed providers are available"}}
        _serve(monkeypatch, _reply(body=refusal))
        provider = _provider()
        with pytest.raises(RuntimeError, match="No allowed providers"):
            await provider.generate_content("s", "u")


class TestTheRequest:
    """What goes on the wire, and what deliberately does not."""

    @pytest.mark.asyncio
    async def test_an_unset_temperature_is_not_sent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An unset knob must not become a value this project invented."""
        sent = _serve(monkeypatch, _reply())
        await _provider().generate_content("s", "u")
        assert "temperature" not in sent[0]

    @pytest.mark.asyncio
    async def test_a_zero_temperature_is_sent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """0.0 is the one temperature that states something, and it is falsy."""
        sent = _serve(monkeypatch, _reply())
        await _provider(temperature=0.0).generate_content("s", "u")
        assert sent[0]["temperature"] == 0.0

    @pytest.mark.asyncio
    async def test_structured_generation_carries_the_schema(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent = _serve(monkeypatch, _reply())
        await _provider().generate_structured("s", "u", _Judgement)
        fmt = sent[0]["response_format"]
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


class TestFencedJson:
    """Models that answer through the prompt rather than `response_format`."""

    def test_a_bare_object_parses(self) -> None:
        parsed = parse_or_raise('{"verdict": "refuted", "rationale": "r"}', _Judgement)
        assert parsed.verdict == "refuted"  # type: ignore[attr-defined]

    def test_a_fenced_object_parses(self) -> None:
        """```json fences would otherwise count as an unparseable answer.

        That inflates the "unruled" rate, and a high unruled rate is read as a
        lenient skeptic — the exact confusion this whole file guards against.
        """
        text = '```json\n{"verdict": "confirmed", "rationale": "r"}\n```'
        parsed = parse_or_raise(text, _Judgement)
        assert parsed.verdict == "confirmed"  # type: ignore[attr-defined]

    def test_prose_still_raises(self) -> None:
        """Tolerating fences must not become tolerating anything."""
        with pytest.raises(json.JSONDecodeError):
            parse_or_raise("I think the claim is correct.", _Judgement)
