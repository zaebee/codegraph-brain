"""The Anthropic provider, with the SDK client stubbed.

The branches here guard the two ways a paid run goes wrong quietly: a reply
that is not an answer (a refusal, a truncated one) recorded as if it were, and
a request sent more times than the budget assumed.
"""

import json
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from cgis.guardian.providers import anthropic as mod
from cgis.guardian.providers.anthropic import AnthropicProvider, AnthropicReplyError
from cgis.guardian.providers.base import MAX_ATTEMPTS


class _Judgement(BaseModel):
    verdict: str
    rationale: str


def _message(
    text: str = '{"verdict": "refuted", "rationale": "r"}',
    *,
    stop: str = "end_turn",
    blocks: list[Any] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        content=blocks
        if blocks is not None
        else [
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
        stop_reason=stop,
        stop_details=SimpleNamespace(category="cyber", explanation="declined")
        if stop == "refusal"
        else None,
        usage=SimpleNamespace(
            input_tokens=100,
            cache_creation_input_tokens=20,
            cache_read_input_tokens=5,
            output_tokens=40,
        ),
    )


class _Client:
    """Stands in for AsyncAnthropic; records how it was built and what it was sent."""

    def __init__(
        self, reply: SimpleNamespace, built: dict[str, Any], sent: list[dict[str, Any]]
    ) -> None:
        self._reply = reply
        self._sent = sent
        self.messages = self
        self.closed = False
        built["client"] = self

    async def create(self, **kwargs: object) -> SimpleNamespace:
        self._sent.append(kwargs)
        return self._reply

    async def close(self) -> None:
        self.closed = True


def _provider(
    reply: SimpleNamespace | None = None,
    **kwargs: Any,  # noqa: ANN401
) -> tuple[AnthropicProvider, dict[str, Any], list[dict[str, Any]]]:
    built: dict[str, Any] = {}
    sent: list[dict[str, Any]] = []

    def factory(**client_kwargs: object) -> _Client:
        built.update(client_kwargs)
        return _Client(reply or _message(), built, sent)

    provider = AnthropicProvider(api_key="k", client_factory=factory, **kwargs)
    return provider, built, sent


async def test_structured_call_sends_the_schema_and_returns_the_text() -> None:
    provider, _built, sent = _provider()
    raw = await provider.generate_structured("sys", "user", _Judgement)
    assert json.loads(raw) == {"verdict": "refuted", "rationale": "r"}
    fmt = sent[0]["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert set(fmt["schema"]["properties"]) == {"verdict", "rationale"}
    assert fmt["schema"]["additionalProperties"] is False


async def test_effort_is_sent_and_temperature_never_is() -> None:
    provider, _built, sent = _provider(effort="low")
    await provider.generate_content("sys", "user")
    assert sent[0]["output_config"] == {"effort": "low"}
    assert "temperature" not in sent[0]
    assert "thinking" not in sent[0]


async def test_an_empty_system_prompt_is_left_out() -> None:
    # calibrate.py sends "" as the system prompt; the API rejects an empty one.
    provider, _built, sent = _provider()
    await provider.generate_content("", "user")
    assert "system" not in sent[0]


async def test_the_sdk_owns_the_retries_and_gets_exactly_the_shared_budget() -> None:
    provider, built, _sent = _provider(timeout=12.0)
    await provider.generate_content("sys", "user")
    assert built["max_retries"] == MAX_ATTEMPTS - 1
    assert built["timeout"] == 12.0
    assert built["client"].closed


async def test_usage_counts_cached_input_and_thinking_output() -> None:
    provider, _built, _sent = _provider()
    await provider.generate_content("sys", "user")
    await provider.generate_content("sys", "user")
    assert provider.last_usage.prompt_tokens == 125
    assert provider.last_usage.completion_tokens == 40
    assert provider.cumulative_usage.total_tokens == 330


async def test_a_refusal_raises_with_its_category() -> None:
    provider, _built, _sent = _provider(_message(stop="refusal"))
    with pytest.raises(AnthropicReplyError, match=r"refused.*cyber"):
        await provider.generate_content("sys", "user")
    # Billed even though refused: the run's cost must still see it.
    assert provider.cumulative_usage.total_tokens == 165


async def test_a_truncated_reply_raises_instead_of_returning_half_an_answer() -> None:
    provider, _built, _sent = _provider(_message(stop="max_tokens"), max_tokens=64)
    with pytest.raises(AnthropicReplyError, match="64-token"):
        await provider.generate_structured("sys", "user", _Judgement)


async def test_a_reply_with_no_text_raises() -> None:
    provider, _built, _sent = _provider(
        _message(blocks=[SimpleNamespace(type="thinking", thinking="")])
    )
    with pytest.raises(AnthropicReplyError, match="no text"):
        await provider.generate_content("sys", "user")


def test_an_unknown_effort_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="effort"):
        AnthropicProvider(api_key="k", effort="extreme")


def test_the_default_model_is_the_cheap_one() -> None:
    assert mod.DEFAULT_ANTHROPIC_MODEL == "claude-haiku-5-5"


def test_the_provider_states_its_effort_for_the_record() -> None:
    provider, _built, _sent = _provider(effort="medium")
    assert provider.effort == "medium"


def test_a_provider_without_effort_states_none() -> None:
    from cgis.guardian.providers.mistral import MistralProvider  # noqa: PLC0415

    assert MistralProvider(api_key="k").effort is None
