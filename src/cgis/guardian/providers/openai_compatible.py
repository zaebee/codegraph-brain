"""Shared request plumbing for OpenAI-compatible providers (#246, #775).

OpenRouter and OpenCode Go speak the same chat-completions shape, so the
prompt, budget, temperature and schema handling live here once. A change to
that contract moves both providers; only the endpoint, headers, error handling
and the reasoning knob remain provider-specific.
"""

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel

from cgis.guardian.providers.base import BaseProvider


class OpenAICompatibleProvider(BaseProvider, ABC):
    """A model reached through an OpenAI-shaped chat-completions API."""

    #: Concrete subclasses declare their own `name`; this intermediate stays
    #: abstract (`_post` and `_reasoning_payload` are still unimplemented), so
    #: `BaseProvider.__init_subclass__` does not demand one here.
    name: ClassVar[str]

    def __init__(
        self,
        api_key: str,
        model_name: str,
        timeout: float,
        max_tokens: int,
        temperature: float | None,
    ) -> None:
        """Store the settings every OpenAI-compatible provider sends.

        `temperature` is None by default and then not sent at all, leaving the
        model's own value in place — the rule every sampled provider here
        follows, so an unset knob never becomes a value this project invented
        and could not describe afterwards.
        """
        super().__init__()
        self._api_key = api_key
        self._model_name = model_name
        self._timeout = timeout
        self._max_tokens = max_tokens
        self._temperature = temperature

    @abstractmethod
    async def _post(self, payload: dict[str, Any]) -> str:
        """Send one completion; return the message content or raise."""

    @abstractmethod
    def _payload(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        """The full request body; each provider adds its own reasoning knob."""

    def _shared_payload(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        """The fields both providers send: model, prompt, budget, temperature."""
        payload: dict[str, Any] = {
            "model": self._model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": self._max_tokens,
        }
        # `is not None`, not truthiness: 0.0 is the one temperature that states
        # something, and a falsy test would drop exactly that value.
        if self._temperature is not None:
            payload["temperature"] = self._temperature
        return payload

    def _response_format(self, schema: type[BaseModel]) -> dict[str, Any]:
        """The `response_format` body that asks for `schema`.

        Built in one place so the base pass and a provider's prompt-only
        fallback (opencode-go, #781) describe the same schema.
        """
        return {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "strict": True,
                "schema": schema.model_json_schema(),
            },
        }

    async def generate_content(self, system_prompt: str, user_prompt: str) -> str:
        """Free-text generation."""
        return await self._retry(lambda: self._post(self._payload(system_prompt, user_prompt)))

    async def generate_structured(
        self, system_prompt: str, user_prompt: str, schema: type[BaseModel]
    ) -> str:
        """Generation asked to conform to `schema`; returns raw JSON text."""
        payload = self._payload(system_prompt, user_prompt)
        payload["response_format"] = self._response_format(schema)
        return await self._retry(lambda: self._post(payload))
