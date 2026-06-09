"""Unit tests for GuardianReviewer, PromptBuilder, and providers."""

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cgis.guardian.collector import ContextCollector
from cgis.guardian.core import GuardianReviewer
from cgis.guardian.prompts import PromptBuilder
from cgis.guardian.providers.base import BaseProvider, ProviderUsage
from cgis.guardian.providers.gemini import GeminiProvider
from cgis.guardian.providers.mistral import MistralProvider

# ---------------------------------------------------------------------------
# PromptBuilder
# ---------------------------------------------------------------------------


def test_build_system_prompt_contains_guardian() -> None:
    """System prompt establishes Guardian persona."""
    prompt = PromptBuilder.build_system_prompt()
    assert "Guardian" in prompt
    assert "Architect" in prompt


def test_build_user_prompt_includes_all_sections() -> None:
    """User prompt contains diff, contributing, and ontology sections."""
    context = {"diff": "the diff", "contributing": "the rules", "ontology": "the ontology"}
    prompt = PromptBuilder.build_user_prompt(context)
    assert "the diff" in prompt
    assert "the rules" in prompt
    assert "the ontology" in prompt


def test_build_user_prompt_includes_graph_section() -> None:
    """Graph context is injected as section 4 when present."""
    context = {
        "diff": "diff",
        "contributing": "rules",
        "ontology": "onto",
        "graph_context": "```mermaid\ngraph TD\n```",
    }
    prompt = PromptBuilder.build_user_prompt(context)
    assert "STRUCTURAL IMPACT GRAPHS" in prompt
    assert "```mermaid" in prompt


def test_build_user_prompt_omits_graph_section_when_absent() -> None:
    """Graph section is absent when context has no graph_context key."""
    context = {"diff": "diff", "contributing": "rules", "ontology": "onto"}
    prompt = PromptBuilder.build_user_prompt(context)
    assert "STRUCTURAL IMPACT GRAPHS" not in prompt


# ---------------------------------------------------------------------------
# GuardianReviewer
# ---------------------------------------------------------------------------


class _FakeProvider(BaseProvider):
    """Minimal in-memory provider for testing."""

    def __init__(self, response: str = "LGTM") -> None:
        """Store canned response."""
        super().__init__()
        self._response = response

    async def generate_content(self, system_prompt: str, user_prompt: str) -> str:  # noqa: ARG002
        """Return the canned response."""
        return self._response


@pytest.fixture
def collector(tmp_path: Path) -> ContextCollector:
    """Return a ContextCollector rooted at tmp_path."""
    return ContextCollector(project_root=tmp_path)


async def test_run_review_returns_provider_response(collector: ContextCollector) -> None:
    """GuardianReviewer.run_review() returns whatever the provider returns."""
    with patch.object(
        collector,
        "collect_all",
        return_value={"diff": "d", "contributing": "c", "ontology": "o"},
    ):
        reviewer = GuardianReviewer(
            provider=_FakeProvider("looks good"), context_collector=collector
        )
        result = await reviewer.run_review()
    assert result == "looks good"


async def test_run_review_passes_context_to_prompt(collector: ContextCollector) -> None:
    """collect_all() output is forwarded into the user prompt."""
    captured: dict[str, str] = {}

    class _CapturingProvider(BaseProvider):
        async def generate_content(self, system_prompt: str, user_prompt: str) -> str:  # noqa: ARG002
            """Capture the user prompt and return ok."""
            captured["user"] = user_prompt
            return "ok"

    with patch.object(
        collector,
        "collect_all",
        return_value={"diff": "MY_DIFF", "contributing": "", "ontology": ""},
    ):
        reviewer = GuardianReviewer(provider=_CapturingProvider(), context_collector=collector)
        await reviewer.run_review()

    assert "MY_DIFF" in captured["user"]


async def test_provider_last_usage_defaults_to_zero() -> None:
    """last_usage is initialised to zero before any call."""
    provider = _FakeProvider()
    assert provider.last_usage.prompt_tokens == 0
    assert provider.last_usage.completion_tokens == 0
    assert provider.last_usage.total_tokens == 0


async def test_provider_last_usage_after_call(collector: ContextCollector) -> None:
    """last_usage reflects values set by the provider after generate_content."""

    class _UsageProvider(BaseProvider):
        async def generate_content(self, system_prompt: str, user_prompt: str) -> str:  # noqa: ARG002
            """Return ok and set fake usage."""
            self.last_usage = ProviderUsage(prompt_tokens=100, completion_tokens=50)
            return "ok"

    p = _UsageProvider()
    with patch.object(
        collector, "collect_all", return_value={"diff": "", "contributing": "", "ontology": ""}
    ):
        await GuardianReviewer(provider=p, context_collector=collector).run_review()

    assert p.last_usage.prompt_tokens == 100
    assert p.last_usage.completion_tokens == 50
    assert p.last_usage.total_tokens == 150


def test_graph_stats_initialised_to_zero(tmp_path: Path) -> None:
    """graph_stats starts at zero before collect_graph_context is called."""
    c = ContextCollector(project_root=tmp_path)
    assert c.graph_stats == {"total": 0, "with_graph": 0}


def test_graph_stats_updated_when_no_db(tmp_path: Path) -> None:
    """graph_stats stays at zero when db_path is None."""
    c = ContextCollector(project_root=tmp_path)
    c.collect_graph_context()
    assert c.graph_stats == {"total": 0, "with_graph": 0}


# ---------------------------------------------------------------------------
# ContextCollector — subprocess paths
# ---------------------------------------------------------------------------


def test_get_git_diff_success(tmp_path: Path) -> None:
    """Returns stdout on success."""
    c = ContextCollector(project_root=tmp_path)
    mock_result = MagicMock()
    mock_result.stdout = "diff output"
    with patch("cgis.guardian.collector.subprocess.run", return_value=mock_result):
        assert c.get_git_diff() == "diff output"


def test_get_git_diff_error(tmp_path: Path) -> None:
    """Returns error string when git command fails."""
    c = ContextCollector(project_root=tmp_path)
    err = subprocess.CalledProcessError(1, "git")
    err.stderr = "fatal: not a repo"
    with patch("cgis.guardian.collector.subprocess.run", side_effect=err):
        assert "Error getting git diff" in c.get_git_diff()


def test_get_changed_py_files_filters_non_py(tmp_path: Path) -> None:
    """Only .py files are returned."""
    c = ContextCollector(project_root=tmp_path)
    mock_result = MagicMock()
    mock_result.stdout = "src/cgis/foo.py\nsrc/cgis/bar.ts\nREADME.md\n"
    with patch("cgis.guardian.collector.subprocess.run", return_value=mock_result):
        assert c.get_changed_py_files() == ["src/cgis/foo.py"]


def test_get_changed_py_files_on_error(tmp_path: Path) -> None:
    """Returns empty list when git command fails."""
    c = ContextCollector(project_root=tmp_path)
    with patch(
        "cgis.guardian.collector.subprocess.run",
        side_effect=subprocess.CalledProcessError(1, "git"),
    ):
        assert c.get_changed_py_files() == []


def test_read_file_missing(tmp_path: Path) -> None:
    """Returns error string for missing file."""
    assert "not found" in ContextCollector(project_root=tmp_path).read_file("nonexistent.md")


def test_read_file_exists(tmp_path: Path) -> None:
    """Returns file contents when file exists."""
    f = tmp_path / "CONTRIBUTING.md"
    f.write_text("hello rules")
    assert ContextCollector(project_root=tmp_path).read_file("CONTRIBUTING.md") == "hello rules"


# ---------------------------------------------------------------------------
# GeminiProvider
# ---------------------------------------------------------------------------


async def test_gemini_provider_returns_text() -> None:
    """GeminiProvider.generate_content() returns response.text."""
    mock_response = MagicMock()
    mock_response.text = "gemini says LGTM"

    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

    mock_genai = MagicMock()
    mock_genai.Client.return_value = mock_client

    provider = GeminiProvider(api_key="fake")
    with patch.dict(
        "sys.modules",
        {
            "google": MagicMock(genai=mock_genai),
            "google.genai": mock_genai,
            "google.genai.types": MagicMock(),
        },
    ):
        result = await provider.generate_content("sys", "user")

    assert result == "gemini says LGTM"


async def test_gemini_provider_import_error() -> None:
    """ImportError is raised with install hint when google-genai is missing."""
    provider = GeminiProvider(api_key="fake")
    with (
        patch.dict(
            "sys.modules",
            {"google": None, "google.genai": None, "google.genai.types": None},  # type: ignore[dict-item]
        ),
        pytest.raises((ImportError, TypeError)),
    ):
        await provider.generate_content("sys", "user")


# ---------------------------------------------------------------------------
# MistralProvider
# ---------------------------------------------------------------------------


def _mistral_modules(mock_instance: MagicMock) -> dict[str, MagicMock]:
    """Build sys.modules entries so `from mistralai.client import Mistral` returns mock_instance.

    mistralai is in the guardian dep-group, not dev — it's absent in CI test runs.
    We inject a fake module tree instead of installing the real package.
    """
    mock_client_mod = MagicMock()
    mock_client_mod.Mistral = MagicMock(return_value=mock_instance)
    mock_top = MagicMock()
    mock_top.client = mock_client_mod
    return {"mistralai": mock_top, "mistralai.client": mock_client_mod}


def _make_mistral_client(response: MagicMock) -> MagicMock:
    """Build an async-context-manager mock client returning response."""
    inst = MagicMock()
    inst.chat.complete_async = AsyncMock(return_value=response)
    inst.__aenter__ = AsyncMock(return_value=inst)
    inst.__aexit__ = AsyncMock(return_value=False)
    return inst


async def test_mistral_provider_returns_text() -> None:
    """MistralProvider.generate_content() returns the message content."""
    mock_choice = MagicMock()
    mock_choice.message.content = "mistral says LGTM"
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    inst = _make_mistral_client(mock_response)
    provider = MistralProvider(api_key="fake")
    with patch.dict("sys.modules", _mistral_modules(inst)):
        result = await provider.generate_content("sys", "user")

    assert result == "mistral says LGTM"


async def test_mistral_provider_empty_choices() -> None:
    """ValueError is raised when Mistral returns no choices."""
    mock_response = MagicMock()
    mock_response.choices = []

    inst = _make_mistral_client(mock_response)
    provider = MistralProvider(api_key="fake")
    with (
        patch.dict("sys.modules", _mistral_modules(inst)),
        pytest.raises(ValueError, match="no choices"),
    ):
        await provider.generate_content("sys", "user")


async def test_mistral_provider_null_content() -> None:
    """ValueError is raised when Mistral returns null message content."""
    mock_choice = MagicMock()
    mock_choice.message.content = None
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    inst = _make_mistral_client(mock_response)
    provider = MistralProvider(api_key="fake")
    with (
        patch.dict("sys.modules", _mistral_modules(inst)),
        pytest.raises(ValueError, match="null message content"),
    ):
        await provider.generate_content("sys", "user")
