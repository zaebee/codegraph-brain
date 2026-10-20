"""Anthropic (Claude) provider for Guardian (#246).

A third vendor for the finder and the skeptic, reached through the official SDK.
The default model is the cheapest current one, because the budget this runs on
is shared with other work and a review costs about a cent on it.

**Retries belong to the SDK here, not to `BaseProvider._retry`.** The SDK is
built on httpx2, so its timeouts are not the httpx exceptions `_retry` catches,
and it retries 408/429/5xx and connection errors on its own. Wrapping it in
`_retry` as well would multiply the attempts — MAX_ATTEMPTS of ours times the
SDK's — on exactly the slow windows where a timed-out request may still have
been completed, and billed, on the server. So the SDK is given the shared
budget, MAX_ATTEMPTS - 1 retries, and nothing else retries.

**No temperature.** Current Claude models reject a non-default sampling value
with a 400; depth is set with `effort` instead, and the provider sends it on
every call so a run can state what it ran on.
"""

from typing import TYPE_CHECKING, Any, ClassVar, Final, override

from pydantic import BaseModel

from cgis.guardian.providers.base import (
    DEFAULT_REQUEST_TIMEOUT,
    MAX_ATTEMPTS,
    BaseProvider,
    ProviderUsage,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from anthropic import AsyncAnthropic
    from anthropic.types import Message, Usage

DEFAULT_ANTHROPIC_MODEL: Final = "claude-haiku-5-5"

#: Sent on every call. Low, because the finder and the skeptic answer from the
#: prompt in one pass, and on a model whose thinking is on by default every
#: thinking token is billed as output.
DEFAULT_EFFORT: Final = "low"

EFFORTS: Final = frozenset({"low", "medium", "high", "xhigh", "max"})

#: Completion budget, thinking included. Step 0 of #246 measured 14,902 output
#: tokens for one finder pass at effort medium, near the old 16,000; a reply cut
#: short is raised, not returned, so a budget too low is a failed paid call.
#: Replies are streamed, so a long one is not bounded by one request's timeout.
DEFAULT_MAX_TOKENS: Final = 32000


class AnthropicReplyError(RuntimeError):
    """The reply is not an answer: refused, truncated, or without text.

    Raised rather than returned as text, for the reason OpenRouter's quota error
    is: a skeptic that returns nothing usable reads as a skeptic that refutes
    nothing, which is the result #246 is testing for.
    """


def _default_client_factory(**kwargs: Any) -> "AsyncAnthropic":  # noqa: ANN401  # SDK kwargs
    """Build an AsyncAnthropic client; the SDK is imported only when used."""
    try:
        from anthropic import AsyncAnthropic  # noqa: PLC0415
    except ImportError as exc:
        _msg = "anthropic is required. Install with: uv sync --extra guardian"
        raise ImportError(_msg) from exc
    return AsyncAnthropic(**kwargs)


def _output_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """The schema in the subset structured outputs accept, via the SDK's own transform."""
    from anthropic import transform_schema  # noqa: PLC0415

    return transform_schema(schema)


class AnthropicProvider(BaseProvider):
    """A Claude model reached through the Anthropic Messages API."""

    name: ClassVar[str] = "anthropic"

    def __init__(
        self,
        api_key: str,
        model_name: str = DEFAULT_ANTHROPIC_MODEL,
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        effort: str = DEFAULT_EFFORT,
        client_factory: "Callable[..., AsyncAnthropic]" = _default_client_factory,
    ) -> None:
        """Store the key, model and generation settings; refuse an unknown effort.

        Refused here rather than by the API: a typo would otherwise fail the
        first call of a run, after the collector has done its work.
        """
        super().__init__()
        if effort not in EFFORTS:
            _msg = f"effort={effort!r} is not one of {sorted(EFFORTS)}"
            raise ValueError(_msg)
        self._api_key = api_key
        self._model_name = model_name
        self._timeout = timeout
        self._max_tokens = max_tokens
        self._effort = effort
        self._client_factory = client_factory

    @property
    @override
    def effort(self) -> str:
        """The effort sent on every call."""
        return self._effort

    async def _create(
        self, system_prompt: str, user_prompt: str, fmt: dict[str, Any] | None
    ) -> str:
        """One Messages call; return the reply's text or raise AnthropicReplyError."""
        output_config: dict[str, Any] = {"effort": self._effort}
        if fmt is not None:
            output_config["format"] = fmt
        request: dict[str, Any] = {
            "model": self._model_name,
            "max_tokens": self._max_tokens,
            "messages": [{"role": "user", "content": user_prompt}],
            "output_config": output_config,
        }
        # The API rejects an empty system prompt, and calibrate.py sends one.
        if system_prompt:
            request["system"] = system_prompt
        client = self._client_factory(
            api_key=self._api_key, timeout=self._timeout, max_retries=MAX_ATTEMPTS - 1
        )
        try:
            async with client.messages.stream(**request) as stream:
                try:
                    message = await stream.get_final_message()
                except Exception:
                    self._record_partial(stream)
                    raise
        finally:
            await client.close()
        self._record(message.usage)
        return self._text(message)

    def _record_partial(self, stream: Any) -> None:  # noqa: ANN401  # the SDK's stream
        """Charge a stream cut after it started: its input was billed at message_start.

        The SDK does not retry mid-stream, so without this a dropped connection
        would be spending the budget never saw. Nothing to record if the cut came
        before message_start. Best effort: it runs inside the handler of the
        error the caller must see, so it never raises over that error.
        """
        try:
            snapshot = stream.current_message_snapshot
        except AssertionError:  # the SDK's guard: no message_start yet, nothing billed
            return
        usage = getattr(snapshot, "usage", None)
        if usage is not None:
            self._record(usage)

    def _record(self, usage: "Usage") -> None:
        """Record input (cached parts included) and output (thinking included)."""
        prompt = sum(
            getattr(usage, key, 0) or 0
            for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        )
        self._record_usage(
            ProviderUsage(prompt_tokens=prompt, completion_tokens=usage.output_tokens or 0)
        )

    def _text(self, message: "Message") -> str:
        """The reply's text, after checking it is a finished answer."""
        if message.stop_reason == "refusal":
            details = message.stop_details
            category = getattr(details, "category", None)
            _msg = f"{self._model_name} refused the request (category: {category})."
            raise AnthropicReplyError(_msg)
        if message.stop_reason == "max_tokens":
            _msg = (
                f"{self._model_name} hit the {self._max_tokens}-token budget before finishing; "
                f"the reply so far is not an answer. Raise max_tokens or lower effort."
            )
            raise AnthropicReplyError(_msg)
        text = "".join(block.text for block in message.content if block.type == "text")
        if not text:
            _msg = f"{self._model_name} returned no text (stop_reason={message.stop_reason})."
            raise AnthropicReplyError(_msg)
        return text

    async def generate_content(self, system_prompt: str, user_prompt: str) -> str:
        """Free-text generation."""
        return await self._create(system_prompt, user_prompt, None)

    async def generate_structured(
        self, system_prompt: str, user_prompt: str, schema: type[BaseModel]
    ) -> str:
        """Generation constrained to `schema` by structured outputs; returns raw JSON text."""
        fmt = {"type": "json_schema", "schema": _output_schema(schema)}
        return await self._create(system_prompt, user_prompt, fmt)
