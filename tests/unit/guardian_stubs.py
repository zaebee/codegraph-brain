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
