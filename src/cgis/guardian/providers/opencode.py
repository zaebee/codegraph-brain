"""OpenCode Go provider — DeepSeek through an OpenAI-compatible gateway (#775).

Guardian used to spend a second credential and a second bill on Anthropic for
the finder, while the rest of the stack already reached opencode-go. The
gateway is OpenAI-shaped, so `providers/openrouter.py` is the template: httpx,
an explicit token budget and temperature, and transport failures that raise
rather than degrade to "no judgement".

**Reasoning is a request field, not an inherited default.** The gateway states
`reasoning_effort` per model — `low|high|max` for `deepseek-v4.1-flash` in the
live catalog on 2026-11-05 — and the model writes its chain of thought to a
separate `reasoning_content` field. Only `content` is the answer: an empty
`content` beside a non-empty `reasoning_content` means the model was still
thinking, and returning that as text would be recorded as an unparseable
verdict. That is the same failure OpenRouter's budget guard exists for, which
is why the budget is generous and exceeding it is raised, not returned.

**A 429 is its own error and is never swallowed.** Quota arrives mid-run, after
some findings are judged and some are not; a run that continued would record
unanswered findings, which is indistinguishable from a skeptic that refutes
nothing — the exact result #246 is testing for.

`GUARDIAN_OPENCODE_BASE_URL` overrides the endpoint so a gateway pin needs no
code change. The trailing slash is removed here rather than at every caller,
because `…/v1/` + `/chat/completions` would otherwise double the separator.
"""

import uuid
from typing import Any, ClassVar, Final, override

import httpx

from cgis import __version__
from cgis.guardian.providers.base import RETRYABLE_EXCEPTIONS, ProviderUsage
from cgis.guardian.providers.openai_compatible import OpenAICompatibleProvider

#: The model this provider exists for: the finder's default, priced at
#: 0.15/0.6 per 1M tokens in/out when the switch was measured (#775).
DEFAULT_OPENCODE_MODEL: Final = "deepseek-v4.1-flash"

#: OpenAI-compatible completions base. The catalog's `api.chat-completions`
#: entry points at this exact URL; POSTs go to `{base}/chat/completions`.
DEFAULT_OPENCODE_BASE_URL: Final = "https://opencode.ai/inference/go/openai/v1"

#: Wall-clock budget for one call. Reasoning models are slow and the finder's
#: prompt is large; a timeout tuned to a fast model fails the capable ones by
#: construction, which is what OpenRouter's 300 s exists to avoid.
DEFAULT_REQUEST_TIMEOUT: Final = 300.0

#: Completion budget, reasoning included. The report is JSON, but the model
#: spends completion tokens thinking first; a reply cut short is raised rather
#: than returned, so too low a budget is a failed call, not a short answer.
DEFAULT_MAX_TOKENS: Final = 8000

#: How hard the model is asked to think. `low` matches the finder's one-pass
#: answer (the same choice `DEFAULT_ANTHROPIC_EFFORT` makes) and bills the least
#: thinking. An explicit value is passed through without a local allowlist: the
#: catalog can grow past today's three levels, and a hard-coded set would reject
#: a value the gateway accepts. A genuinely bad value fails loudly at the
#: gateway, where `raise_for_status` keeps the 4xx message.
DEFAULT_OPENCODE_EFFORT: Final = "low"

_BAD_REQUEST = 400
_TOO_MANY_REQUESTS = 429


class OpenCodeTransientError(RuntimeError):
    """A 400 the gateway marks as an upstream failure it could not complete (#779).

    `inference_failed` is not a request-validation error: the same request
    succeeds on the next attempt when the gateway retries it itself, and it does
    so only sometimes (the console log shows `attemptCount` 1 on the runs that
    failed and 2 on one that succeeded). Retried through `_retry`, never
    returned and never degraded to "no judgement".
    """


class OpenCodeQuotaError(RuntimeError):
    """Raised on HTTP 429 — the gateway's quota for this key is spent.

    Its own type, and never swallowed: every other transport failure can be
    retried into a judgement, but this one cannot, and a run that treated it as
    "the finder had nothing to say" would report exhausted quota as a clean
    review. The same reasoning as `OpenRouterQuotaError` (#246).
    """


class OpenCodeReplyError(RuntimeError):
    """The reply is not an answer: it contains reasoning but no `content`.

    Raised rather than returned as empty text, because the caller would count
    that as an unparseable answer — attributing to the model a loss this file
    can name, and inflating exactly the "unruled" rate a review reads.
    """


class OpenCodeProvider(OpenAICompatibleProvider):
    """A DeepSeek model reached through opencode-go's OpenAI-compatible API."""

    name: ClassVar[str] = "opencode"

    def __init__(
        self,
        api_key: str,
        model_name: str = DEFAULT_OPENCODE_MODEL,
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float | None = None,
        effort: str = DEFAULT_OPENCODE_EFFORT,
        base_url: str = DEFAULT_OPENCODE_BASE_URL,
        session_id: str | None = None,
    ) -> None:
        """Store the key, model, endpoint and generation settings.

        `temperature` is None by default and then not sent at all, leaving the
        model's own value in place — the rule every sampled provider here
        follows, so an unset knob never becomes a value this project invented.

        `session_id` is stable for one provider instance, i.e. one review run.
        The gateway asks for it to route and cache a conversation; a fresh id
        per call would split a run into unrelated sessions. It is injectable so
        a test can assert the header without pinning a random value.
        """
        super().__init__(api_key, model_name, timeout, max_tokens, temperature)
        self._effort = (effort or "").strip() or DEFAULT_OPENCODE_EFFORT
        # One expression, so the default cannot be skipped by a whitespace-only
        # value or a bare "/" and the empty-string fallback is not duplicated.
        self._base_url = (base_url or "").strip().rstrip("/") or DEFAULT_OPENCODE_BASE_URL
        self._session_id = session_id or uuid.uuid4().hex

    @property
    @override
    def effort(self) -> str:
        """The reasoning depth sent on every call, for the run record (#713)."""
        return self._effort

    @override
    def _retryable_exceptions(self) -> tuple[type[Exception], ...]:
        """Retry the gateway's transient 400 beside the transport failures.

        `inference_failed` is an upstream failure, not a request-validation
        error: the same request succeeds on the next attempt when the gateway
        retries it itself (#779). Every other 4xx still fails on the first
        attempt, as the base class documents.
        """
        return (*RETRYABLE_EXCEPTIONS, OpenCodeTransientError)

    async def _post(self, payload: dict[str, Any]) -> str:
        """One completion call; return the message content.

        `response_format` is passed through when the caller asks for a schema.
        The model's compat record advertises structured output, and sending the
        schema costs nothing where it is ignored; the caller still parses
        defensively either way.
        """
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            # Identifies this client to the gateway. The catalog's docs ask for
            # both headers: the user agent for attribution, the session id for
            # routing and cache locality.
            "User-Agent": f"codegraph-brain-guardian/{__version__}",
            "x-opencode-session": self._session_id,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/chat/completions", headers=headers, json=payload
            )
        if response.status_code == _TOO_MANY_REQUESTS:
            # The body is carried, not summarised: it says whether the limit is
            # per-minute or the account's allowance, and those have different
            # remedies — wait, or stop. Dropping it would leave the operator
            # guessing, the same defect as an exception that swallows stderr.
            _msg = (
                f"OpenCode returned 429 for {self._model_name}: "
                f"{response.text.strip()[:400]}. A run that continued would "
                f"record unanswered findings, which is indistinguishable from a "
                f"skeptic that refutes nothing."
            )
            raise OpenCodeQuotaError(_msg)
        if response.is_error:
            # Every other HTTP failure is carried with its body, not reduced to
            # the status: the gateway states why the upstream call failed, and
            # dropping it is what made #779 read as a bare "400 Bad Request".
            body_text = response.text.strip()
            _msg = (
                f"OpenCode returned HTTP {response.status_code} for {self._model_name}: "
                f"{body_text[:400] or '(empty body)'}"
            )
            if response.status_code == _BAD_REQUEST and "inference_failed" in body_text:
                raise OpenCodeTransientError(_msg)
            raise RuntimeError(_msg)
        body = response.json()
        # An error object with HTTP 200 is how some gateways report an upstream
        # refusal; `body["choices"]` would raise KeyError with nothing in the
        # message about what declined or why.
        if not body.get("choices"):
            _msg = f"OpenCode returned no choices for {self._model_name}: {body}"
            raise RuntimeError(_msg)
        choice = body["choices"][0]
        usage = body.get("usage") or {}
        self._record_usage(
            ProviderUsage(
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
            )
        )
        message = choice.get("message") or {}
        if choice.get("finish_reason") == "length":
            # Reported, not returned. A model cut off mid-reasoning yields prose
            # where JSON was asked for, and the caller would record that as an
            # unparseable answer — attributing to the model a limit this file
            # chose.
            _msg = (
                f"{self._model_name} hit the {self._max_tokens}-token budget before finishing. "
                f"Raise max_tokens; the reply so far is reasoning, not an answer."
            )
            raise RuntimeError(_msg)
        content = str(message.get("content") or "")
        reasoning = str(message.get("reasoning_content") or "")
        if not content.strip() and reasoning.strip():
            # DeepSeek puts the chain of thought in its own field and fills
            # `content` only at the end. An empty answer beside a non-empty
            # thought is truncated reasoning, not a verdict.
            _msg = (
                f"{self._model_name} returned reasoning but no content. "
                f"The answer was cut off before it reached `content`; the "
                f"reasoning alone is not a verdict."
            )
            raise OpenCodeReplyError(_msg)
        return content

    @override
    def _payload(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        """The request body; this gateway takes reasoning as an effort level.

        `reasoning_effort` is always sent, so a run can state the depth it ran
        at and the same value is exposed through `effort` for the record. The
        budget field is `max_tokens`, not the `max_completion_tokens` spelling
        some OpenAI deployments use.
        """
        payload = self._shared_payload(system_prompt, user_prompt)
        payload["reasoning_effort"] = self._effort
        return payload
