"""Mistral AI LLM provider for Guardian."""

from typing import ClassVar

from pydantic import BaseModel

from cgis.guardian.providers.base import DEFAULT_REQUEST_TIMEOUT, BaseProvider, ProviderUsage


class MistralProvider(BaseProvider):
    """Mistral AI provider. Requires: uv sync --group guardian"""

    name: ClassVar[str] = "mistral"

    def __init__(
        self,
        api_key: str,
        model_name: str = "mistral-medium-latest",
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
        temperature: float | None = None,
    ) -> None:
        """Store credentials, the request timeout and the sampling temperature.

        The SDK takes MILLISECONDS and hard-codes 60 000 when given nothing
        (mistralai/client/chat.py) — that default is what killed the review on
        #274, so it is always overridden here.

        `temperature` is `None` by default and then not sent at all, leaving the
        API's own default in place. That is deliberate: inventing a default here
        would silently change the production reviewer, whereas #342 Phase 3 sets
        an explicit 0.7 that was registered before the run.
        """
        super().__init__()
        self._api_key = api_key
        self._model_name = model_name
        self._timeout_ms = int(timeout * 1000)
        self._temperature = temperature

    async def _generate(self, system_prompt: str, user_prompt: str, *, json_mode: bool) -> str:
        """Shared transport: one chat.complete_async call, optional json_object mode."""
        _install_hint = "mistralai is required. Install with: uv sync --group guardian"
        try:
            from mistralai.client import Mistral  # noqa: PLC0415
            from mistralai.client.utils.retries import (  # noqa: PLC0415
                BackoffStrategy,
                RetryConfig,
            )
        except ImportError as exc:
            raise ImportError(_install_hint) from exc

        # Built here rather than at module import, where a missing SDK would
        # have produced a silent None and a run with no rate-limit retry at all.
        # Past this line the SDK is present by construction, so there is no
        # degraded path to get wrong.
        #
        # The SDK knows which statuses to retry — 429, 500, 502, 503, 504 — but
        # only when handed a config: `retry_config` defaults to UNSET, which
        # disables retrying outright. Mistral rate-limits hard, and a sequential
        # benchmark arm loses a whole PR to the first 429 without this.
        #
        # `retry_connection_errors=False` on purpose: `BaseProvider._retry`
        # already owns timeouts and network failures, and two layers retrying
        # one failure multiply the wait without improving the odds.
        retry_config = RetryConfig(
            strategy="backoff",
            backoff=BackoffStrategy(
                initial_interval=1_000,
                max_interval=60_000,
                exponent=1.5,
                max_elapsed_time=300_000,
            ),
            retry_connection_errors=False,
        )
        extra: dict[str, object] = {}
        if json_mode:
            extra["response_format"] = {"type": "json_object"}
        # `is not None`, not truthiness: 0.0 is the one temperature that is a
        # deliberate claim, and a falsy test would drop exactly that value.
        if self._temperature is not None:
            extra["temperature"] = self._temperature
        async with Mistral(
            api_key=self._api_key,
            timeout_ms=self._timeout_ms,
            retry_config=retry_config,
        ) as client:
            response = await client.chat.complete_async(
                model=self._model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                **extra,
            )
        if not response.choices:
            _msg = f"Mistral returned no choices for model {self._model_name}"
            raise ValueError(_msg)
        content = response.choices[0].message.content
        if content is None:
            _msg = f"Mistral returned null message content for model {self._model_name}"
            raise ValueError(_msg)
        usage = getattr(response, "usage", None)
        if usage is not None:
            self._record_usage(
                ProviderUsage(
                    prompt_tokens=getattr(usage, "prompt_tokens", 0),
                    completion_tokens=getattr(usage, "completion_tokens", 0),
                )
            )
        return str(content)

    async def generate_content(self, system_prompt: str, user_prompt: str) -> str:
        """Send prompts to Mistral and return the text response."""
        return await self._retry(
            lambda: self._generate(system_prompt, user_prompt, json_mode=False)
        )

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
    ) -> str:
        """Send prompts in json_object mode.

        Mistral's json_object mode takes no schema parameter — the schema is
        described in the user prompt (spec §2.4); the argument exists to
        satisfy the BaseProvider contract.
        """
        del schema
        return await self._retry(lambda: self._generate(system_prompt, user_prompt, json_mode=True))
