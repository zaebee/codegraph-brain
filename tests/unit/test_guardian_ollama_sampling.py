"""Sampling options must reach Ollama, and the chat preset must not decide them.

`GUARDIAN_TEMPERATURE` was wired into Mistral for #342 Phase 3 but never into
Ollama, which passed only `num_ctx`. So a local run inherited whatever the
model's chat template shipped — on qwen3.5:9b that is `temp 1.0`,
`repeat_penalty 1.1` and `presence_penalty 1.5`.

Nobody chose those for an extraction task. A presence penalty rewards novel
tokens, and the structured path asks for JSON whose keys repeat in every object;
whatever it does to content selection, it was not a decision this project made,
and a run whose sampling cannot be stated is the gap retraction R5 was about.
"""

import sys
import types
from typing import Any

import pytest
from pydantic import BaseModel

from cgis.guardian.providers.ollama import OllamaProvider
from cgis.guardian.runner import build_provider, build_skeptic_provider


class _Schema(BaseModel):
    """Minimal structured-output schema."""

    value: str


class _FakeResponse:
    """The response fields the provider reads."""

    def __init__(self) -> None:
        self.message = types.SimpleNamespace(content="ok")
        self.prompt_eval_count = 10
        self.eval_count = 5


class _FakeClient:
    """Records the options the provider sends."""

    def __init__(self, sink: dict[str, Any]) -> None:
        self._sink = sink

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def chat(self, **kwargs: object) -> _FakeResponse:
        """Capture the call and return a usable response."""
        self._sink.update(kwargs)
        return _FakeResponse()


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Install a fake `ollama` module and return the captured chat kwargs."""
    captured: dict[str, Any] = {}
    module = types.ModuleType("ollama")
    module.AsyncClient = lambda **_kwargs: _FakeClient(captured)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ollama", module)
    return captured


def _options(sent: dict[str, Any]) -> dict[str, Any]:
    """The options dict the provider passed to `chat`."""
    options = sent["options"]
    assert isinstance(options, dict)
    return options


class TestTemperature:
    """The registered value has to arrive, and an unset one must not be invented."""

    @pytest.mark.asyncio
    async def test_the_configured_temperature_is_sent(self, sent: dict[str, Any]) -> None:
        await OllamaProvider(model_name="m", temperature=0.7).generate_content("s", "u")

        assert _options(sent)["temperature"] == 0.7

    @pytest.mark.asyncio
    async def test_nothing_is_sent_when_unset(self, sent: dict[str, Any]) -> None:
        """Unset stays the model's own default rather than a value we made up."""
        await OllamaProvider(model_name="m").generate_content("s", "u")

        assert "temperature" not in _options(sent)

    @pytest.mark.asyncio
    async def test_zero_is_sent_rather_than_treated_as_unset(self, sent: dict[str, Any]) -> None:
        """0.0 is falsy and is the one temperature that is a deliberate claim."""
        await OllamaProvider(model_name="m", temperature=0.0).generate_content("s", "u")

        assert _options(sent)["temperature"] == 0.0

    @pytest.mark.asyncio
    async def test_the_context_window_is_still_sent(self, sent: dict[str, Any]) -> None:
        """num_ctx must survive: without it Ollama truncates at ~2048, silently."""
        await OllamaProvider(model_name="m", num_ctx=4096).generate_content("s", "u")

        assert _options(sent)["num_ctx"] == 4096


class TestRepetitionPenalties:
    """Structured extraction does not want a chat template's novelty pressure."""

    @pytest.mark.asyncio
    async def test_the_structured_path_neutralises_them(self, sent: dict[str, Any]) -> None:
        """The finder's JSON repeats its keys in every object by construction."""
        await OllamaProvider(model_name="m").generate_structured("s", "u", _Schema)

        options = _options(sent)
        assert options["presence_penalty"] == 0.0
        assert options["frequency_penalty"] == 0.0
        assert options["repeat_penalty"] == 1.0

    @pytest.mark.asyncio
    async def test_free_text_keeps_the_model_defaults(self, sent: dict[str, Any]) -> None:
        """Only the schema-constrained path has the repetition argument."""
        await OllamaProvider(model_name="m").generate_content("s", "u")

        options = _options(sent)
        assert "presence_penalty" not in options
        assert "repeat_penalty" not in options

    @pytest.mark.asyncio
    async def test_an_explicit_override_wins(self, sent: dict[str, Any]) -> None:
        """An experiment must be able to put the penalties back and say so."""
        provider = OllamaProvider(model_name="m", penalties={"presence_penalty": 1.5})

        await provider.generate_structured("s", "u", _Schema)

        assert _options(sent)["presence_penalty"] == 1.5


class TestEnvWiring:
    """`build_provider` carries the same value to the finder and the skeptic."""

    def test_the_finder_gets_the_environment_temperature(self) -> None:
        provider, _ = build_provider(
            {"GUARDIAN_PROVIDER": "ollama", "GUARDIAN_MODEL": "m", "GUARDIAN_TEMPERATURE": "0.2"}
        )

        assert isinstance(provider, OllamaProvider)
        assert provider._temperature == 0.2  # noqa: SLF001  # white-box: sampling wiring

    def test_the_skeptic_gets_it_too(self) -> None:
        built = build_skeptic_provider(
            {"GUARDIAN_SKEPTIC": "ollama", "GUARDIAN_MODEL": "m", "GUARDIAN_TEMPERATURE": "0.2"},
            primary="mistral",
        )

        assert built is not None
        provider, _ = built
        assert isinstance(provider, OllamaProvider)
        assert provider._temperature == 0.2  # noqa: SLF001  # white-box: sampling wiring
