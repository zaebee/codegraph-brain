"""OpenRouter provider — an OpenAI-compatible gateway to many vendors (#246).

#246 needs a skeptic from a vendor other than the finder's, and the two the
repository already speaks to are the two it is comparing. Ollama would answer
that at no API cost, but it needs a server this project does not have. OpenRouter
reaches a third vendor over an OpenAI-shaped HTTP API with a free tier, which is
what the comparison was blocked on.

**Reasoning models set the token budget here.** The free models measured on
2026-08-17 spend most of their completion on reasoning before emitting the
answer: 709 tokens for `nemotron-3-nano-30b`, 2,133 for `nemotron-3-super-120b`,
2,243 for `cohere/north-mini-code`. A first probe capped output at 600 and read
the truncated reasoning as "this model cannot produce JSON" — the model was fine
and the measurement was not, which is why `DEFAULT_MAX_TOKENS` is generous and
why exceeding it is reported rather than returned as a short answer.

Free-tier quota is not discoverable in advance: `/api/v1/auth/key` returns no
limit for a free key and completions carry no rate-limit headers. Exhaustion
arrives as HTTP 429 mid-run, and a 429 that degrades to "no judgement" would
look exactly like a skeptic that refutes nothing — the very hypothesis under
test. So a 429 raises, loudly, rather than being smoothed into an empty answer.
"""

import json
from typing import Any, ClassVar

import httpx
from pydantic import BaseModel

from cgis.guardian.providers.base import BaseProvider, ProviderUsage

#: OpenAI-compatible completions endpoint.
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

#: Wall-clock budget for one call. Measured latencies on the free tier span
#: 6.9s to 98.8s for a single judgement, so a timeout tuned to the fast models
#: would fail the slow ones by construction rather than by fault.
DEFAULT_REQUEST_TIMEOUT = 300.0

#: Completion budget for one call. Well above the 2,243 tokens the most verbose
#: model measured needed, because the cost of being wrong is asymmetric: a
#: budget that is too high wastes nothing on the free tier, and one that is too
#: low silently returns reasoning where a verdict was expected.
DEFAULT_MAX_TOKENS = 8000

#: Whether the model is asked to think before answering.
#:
#: Off, and stated rather than left to the model. `qwen3.7-plus` puts its chain
#: of thought in a separate `reasoning` field and fills `content` only at the
#: end, so on the larger prompts it spent the whole budget thinking and returned
#: `content: null` — 118 of 135 findings unruled in the first paid run, which the
#: validity gate caught and refused to score.
#:
#: Raising the budget would have fixed the symptom and broken the experiment.
#: The arm this one is compared against, `gemini-2.5-flash`, is not doing
#: extended thinking, so leaving the model's default on would make the two arms
#: differ in a dimension nobody chose — the same reason `temperature` is not
#: invented here, arriving from the other side: the honest move is to set it and
#: say so, not to inherit it and be unable to describe the run afterwards.
DEFAULT_REASONING = False

_TOO_MANY_REQUESTS = 429


class OpenRouterQuotaError(RuntimeError):
    """Raised on HTTP 429 — the free tier's daily allowance is spent.

    Its own type, and never swallowed. Every other transport failure can be
    retried into a judgement; this one cannot, and a run that treated it as
    "the skeptic had nothing to say" would report exhausted quota as a lenient
    skeptic. That is the exact shape of the result #246 is testing for, so it
    must not be reachable by accident.
    """


class OpenRouterProvider(BaseProvider):
    """A chat model reached through OpenRouter's OpenAI-compatible API."""

    name: ClassVar[str] = "openrouter"

    def __init__(
        self,
        api_key: str,
        model_name: str,
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float | None = None,
        reasoning: bool = DEFAULT_REASONING,
    ) -> None:
        """Store the key, model, timeout and generation budget.

        `temperature` is None by default and then not sent at all, leaving the
        model's own value in place — the rule the Mistral and Ollama providers
        already follow, so an unset knob never becomes a value this project
        invented and could not describe afterwards.
        """
        super().__init__()
        self._api_key = api_key
        self._model_name = model_name
        self._timeout = timeout
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._reasoning = reasoning

    async def _post(self, payload: dict[str, Any]) -> str:
        """One completion call; return the message content.

        `response_format` is passed through when the caller asks for a schema.
        Not every model honours it — the ones measured here comply through the
        prompt rather than the parameter — so the caller still parses
        defensively; sending it costs nothing and helps where it is supported.
        """
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(OPENROUTER_URL, headers=headers, json=payload)
        if response.status_code == _TOO_MANY_REQUESTS:
            # The body is carried, not summarised. OpenRouter says whether the
            # limit was per-minute on one upstream or the account's daily
            # allowance, and those have different remedies — wait, or stop. An
            # exception that dropped it would leave the operator guessing, the
            # same defect as a CalledProcessError that swallows stderr.
            _msg = (
                f"OpenRouter returned 429 for {self._model_name}: {response.text.strip()[:400]}. "
                f"A run that continued would record unanswered findings, which is "
                f"indistinguishable from a skeptic that refutes nothing."
            )
            raise OpenRouterQuotaError(_msg)
        response.raise_for_status()
        body = response.json()
        # An error object with HTTP 200: OpenRouter reports upstream refusals
        # this way, and `.json()["choices"]` would raise KeyError with nothing
        # in the message about which provider declined or why.
        if "choices" not in body:
            _msg = f"OpenRouter returned no choices for {self._model_name}: {body}"
            raise RuntimeError(_msg)
        choice = body["choices"][0]
        usage = body.get("usage") or {}
        self._record_usage(
            ProviderUsage(
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
            )
        )
        if choice.get("finish_reason") == "length":
            # Reported, not returned. A model cut off mid-reasoning yields prose
            # where JSON was asked for, and the caller would record that as an
            # unparseable answer — attributing to the model a limit this file
            # chose. Measured the hard way: a 600-token probe read three capable
            # models as incapable.
            _msg = (
                f"{self._model_name} hit the {self._max_tokens}-token budget before finishing. "
                f"Raise max_tokens; the reply so far is reasoning, not an answer."
            )
            raise RuntimeError(_msg)
        return str(choice["message"].get("content") or "")

    def _payload(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        """The request body common to both generation modes."""
        payload: dict[str, Any] = {
            "model": self._model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": self._max_tokens,
            # Always sent, in both directions, so a run can state what it did.
            "reasoning": {"enabled": self._reasoning},
        }
        # `is not None`, not truthiness: 0.0 is the one temperature that states
        # something, and a falsy test would drop exactly that value.
        if self._temperature is not None:
            payload["temperature"] = self._temperature
        return payload

    async def generate_content(self, system_prompt: str, user_prompt: str) -> str:
        """Free-text generation."""
        return await self._retry(lambda: self._post(self._payload(system_prompt, user_prompt)))

    async def generate_structured(
        self, system_prompt: str, user_prompt: str, schema: type[BaseModel]
    ) -> str:
        """Generation asked to conform to `schema`; returns raw JSON text."""
        payload = self._payload(system_prompt, user_prompt)
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "strict": True,
                "schema": schema.model_json_schema(),
            },
        }
        return await self._retry(lambda: self._post(payload))


def parse_or_raise(text: str, schema: type[BaseModel]) -> BaseModel:
    """Validate `text` against `schema`, tolerating a fenced code block.

    Models that answer through the prompt rather than through
    `response_format` often wrap the object in ```json fences. Stripping them
    here keeps that from being counted as an unparseable answer, which would
    inflate exactly the "unruled" rate an experiment reads as leniency.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1]
        stripped = stripped.rsplit("```", 1)[0]
    return schema.model_validate(json.loads(stripped))
