"""Tests for the guardian script runner (provider selection + orchestration)."""

from pathlib import Path

import pytest
from pydantic import BaseModel

from cgis.guardian.collector import ContextCollector
from cgis.guardian.providers.base import BaseProvider, ProviderUsage
from cgis.guardian.runner import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_MISTRAL_MODEL,
    build_footer,
    build_provider,
    build_skeptic_provider,
    run_guardian,
)

_VALID_JSON = '{"findings": [], "summary": "all good"}'


class _FakeProvider(BaseProvider):
    """Returns canned structured JSON."""

    def __init__(self, response: str = _VALID_JSON) -> None:
        """Store the canned response."""
        super().__init__()
        self._response = response

    async def generate_content(
        self,
        system_prompt: str,  # noqa: ARG002
        user_prompt: str,  # noqa: ARG002
    ) -> str:
        """Return the canned response."""
        return self._response

    async def generate_structured(
        self,
        system_prompt: str,  # noqa: ARG002
        user_prompt: str,  # noqa: ARG002
        schema: type[BaseModel],  # noqa: ARG002
    ) -> str:
        """Return the canned response."""
        return self._response


def test_build_provider_requires_a_key() -> None:
    """No API keys in env → RuntimeError with guidance."""
    with pytest.raises(RuntimeError, match="MISTRAL_API_KEY or GEMINI_API_KEY"):
        build_provider({})


def test_build_provider_prefers_explicit_provider() -> None:
    """GUARDIAN_PROVIDER=mistral wins even when both keys are present."""
    _provider, model = build_provider(
        {"GUARDIAN_PROVIDER": "mistral", "MISTRAL_API_KEY": "k", "GEMINI_API_KEY": "g"}
    )
    assert model == "mistral-medium-latest"


def test_build_provider_model_override() -> None:
    """GUARDIAN_MODEL overrides the per-provider default."""
    _, model = build_provider({"GEMINI_API_KEY": "g", "GUARDIAN_MODEL": "gemini-x"})
    assert model == "gemini-x"


def test_build_provider_explicit_gemini_requires_its_key() -> None:
    """GUARDIAN_PROVIDER=gemini with only a mistral key is an explicit error."""
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY must be set"):
        build_provider({"GUARDIAN_PROVIDER": "gemini", "MISTRAL_API_KEY": "k"})


def test_build_provider_rejects_unknown_provider() -> None:
    """A typo in GUARDIAN_PROVIDER fails loudly, not with the generic key error."""
    with pytest.raises(RuntimeError, match="Unknown GUARDIAN_PROVIDER"):
        build_provider({"GUARDIAN_PROVIDER": "anthropic", "GEMINI_API_KEY": "g"})


def test_build_provider_explicit_gemini_with_key() -> None:
    """GUARDIAN_PROVIDER=gemini selects gemini even when both keys are present."""
    _, model = build_provider(
        {"GUARDIAN_PROVIDER": "gemini", "GEMINI_API_KEY": "g", "MISTRAL_API_KEY": "k"}
    )
    assert model == "gemini-2.5-flash"


def test_build_footer_includes_model_and_tokens() -> None:
    """Footer lists model, token counts, and graph coverage."""
    footer = build_footer(
        model="m1",
        usage=ProviderUsage(prompt_tokens=10, completion_tokens=2),
        stats={"total": 4, "with_graph": 2},
    )
    assert "m1" in footer
    assert "12" in footer
    assert "2/4" in footer


def test_build_skeptic_provider_default_is_other_provider() -> None:
    """Primary gemini -> skeptic mistral by default (spec §5.5), when its key exists."""
    env = {"GEMINI_API_KEY": "g", "MISTRAL_API_KEY": "m"}
    built = build_skeptic_provider(env, primary="gemini")
    assert built is not None
    _provider, model = built
    assert model == DEFAULT_MISTRAL_MODEL


def test_build_skeptic_provider_off() -> None:
    """GUARDIAN_SKEPTIC=off disables the pass."""
    env = {"GUARDIAN_SKEPTIC": "off", "GEMINI_API_KEY": "g", "MISTRAL_API_KEY": "m"}
    assert build_skeptic_provider(env, primary="gemini") is None


def test_build_skeptic_provider_same_provider_model_override() -> None:
    """GUARDIAN_SKEPTIC=gemini + GUARDIAN_SKEPTIC_MODEL allows a gemini+gemini pair."""
    env = {
        "GUARDIAN_SKEPTIC": "gemini",
        "GUARDIAN_SKEPTIC_MODEL": "gemini-2.5-flash",
        "GEMINI_API_KEY": "g",
    }
    built = build_skeptic_provider(env, primary="gemini")
    assert built is not None
    _, model = built
    assert model == "gemini-2.5-flash"


def test_build_skeptic_provider_mistral_primary_defaults_to_gemini() -> None:
    """Primary mistral -> skeptic gemini by default (spec §5.5), when its key exists."""
    env = {"GEMINI_API_KEY": "g", "MISTRAL_API_KEY": "m"}
    built = build_skeptic_provider(env, primary="mistral")
    assert built is not None
    _provider, model = built
    assert model == DEFAULT_GEMINI_MODEL


def test_build_skeptic_provider_unknown_value_disabled() -> None:
    """GUARDIAN_SKEPTIC=<unknown> returns None (graceful single-pass), not an error."""
    env = {"GUARDIAN_SKEPTIC": "claude", "GEMINI_API_KEY": "g", "MISTRAL_API_KEY": "m"}
    assert build_skeptic_provider(env, primary="gemini") is None


def test_build_skeptic_provider_missing_key_degrades_to_none() -> None:
    """No API key for the chosen skeptic → None (graceful single-pass), not an error."""
    env = {"GEMINI_API_KEY": "g"}  # default skeptic for gemini primary is mistral — no key
    assert build_skeptic_provider(env, primary="gemini") is None


async def test_run_guardian_smoke(tmp_path: Path) -> None:
    """End-to-end with a fake provider: review → render → metrics line.

    ContextCollector.collect_all() gracefully handles a non-git tmp_path:
    get_git_diff() returns an error string (no exception), and read_file()
    returns "Error: File ... not found." for missing CONTRIBUTING.md /
    ontology.yaml.  The FakeProvider ignores the prompt content and always
    returns valid JSON, so no mock of collect_all() is needed.
    """
    metrics = tmp_path / "m.jsonl"
    collector = ContextCollector(project_root=tmp_path)
    report = await run_guardian(
        provider=_FakeProvider(),
        model="fake-model",
        collector=collector,
        pr=152,
        metrics_path=metrics,
    )
    assert report.startswith("LGTM — no defects found in this diff.")
    assert metrics.exists()
