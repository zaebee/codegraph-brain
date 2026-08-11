"""Shared provider stubs and canned JSON for guardian unit tests."""

from pydantic import BaseModel

from cgis.guardian.providers.base import BaseProvider

FINDING_JSON = (
    '{"findings": [{"file": "a.py", "line": 1, "severity": "major", "category": "logic",'
    ' "title": "t", "evidence": "e", "problem": "p", "fix": "f", "confidence": 90}],'
    ' "summary": "s"}'
)


class StubProvider(BaseProvider):
    """Returns canned JSON per call; records prompts."""

    def __init__(self, responses: list[str]) -> None:
        """Store canned responses and initialise prompt log."""
        super().__init__()
        self._responses = list(responses)
        self.prompts: list[str] = []

    async def generate_content(self, system_prompt: str, user_prompt: str) -> str:
        """Not used in tests."""
        raise NotImplementedError

    async def generate_structured(
        self,
        system_prompt: str,  # noqa: ARG002
        user_prompt: str,
        schema: type[BaseModel],  # noqa: ARG002
    ) -> str:
        """Record the prompt and return the next canned response."""
        self.prompts.append(user_prompt)
        return self._responses.pop(0)


class BoomProvider(BaseProvider):
    """Raises on every structured call — simulates a transport/API failure."""

    async def generate_content(self, system_prompt: str, user_prompt: str) -> str:
        """Not used in tests."""
        raise NotImplementedError

    async def generate_structured(
        self,
        system_prompt: str,  # noqa: ARG002
        user_prompt: str,  # noqa: ARG002
        schema: type[BaseModel],  # noqa: ARG002
    ) -> str:
        """Simulate a provider/API failure."""
        _msg = "boom"
        raise RuntimeError(_msg)


class FlakyProvider(BaseProvider):
    """Raises a queued exception per call, then answers; records backoff sleeps.

    Distinct from BoomProvider, which always fails: this one models a provider
    that recovers, which is what a retry path has to be tested against.
    """

    def __init__(self, errors: list[Exception], response: str) -> None:
        """Queue the failures to raise before `response` is finally returned."""
        super().__init__()
        self._errors = list(errors)
        self._response = response
        self.calls = 0
        self.slept: list[float] = []

    async def generate_content(self, system_prompt: str, user_prompt: str) -> str:
        """Not used in tests."""
        raise NotImplementedError

    async def generate_structured(
        self,
        system_prompt: str,  # noqa: ARG002
        user_prompt: str,  # noqa: ARG002
        schema: type[BaseModel],  # noqa: ARG002
    ) -> str:
        """Raise the next queued error, or return the canned response."""
        self.calls += 1
        if self._errors:
            raise self._errors.pop(0)
        return self._response

    async def _sleep(self, seconds: float) -> None:
        """Record the backoff instead of waiting."""
        self.slept.append(seconds)
