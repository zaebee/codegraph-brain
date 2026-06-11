"""Unit tests for GuardianReviewer, PromptBuilder, and providers."""

import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from guardian_stubs import FINDING_JSON, StubProvider
from pydantic import BaseModel, ValidationError

from cgis.guardian.collector import ContextCollector
from cgis.guardian.core import GuardianReviewer, finder_pass
from cgis.guardian.findings import ReviewResult
from cgis.guardian.prompts import PromptBuilder
from cgis.guardian.providers.base import BaseProvider, ProviderUsage
from cgis.guardian.providers.gemini import GeminiProvider
from cgis.guardian.providers.mistral import MistralProvider

_VALID_JSON = '{"findings": [], "summary": "all good"}'

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

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],  # noqa: ARG002
    ) -> str:
        """Structured variant — same canned behaviour as generate_content."""
        return await self.generate_content(system_prompt, user_prompt)


class _SequenceProvider(BaseProvider):
    """Returns queued responses; records structured-call prompts."""

    def __init__(self, responses: list[str]) -> None:
        """Queue the canned responses."""
        super().__init__()
        self._responses = list(responses)
        self.structured_prompts: list[str] = []

    async def generate_content(self, system_prompt: str, user_prompt: str) -> str:  # noqa: ARG002
        """Pop and return the next queued response."""
        return self._responses.pop(0)

    async def generate_structured(
        self,
        system_prompt: str,  # noqa: ARG002
        user_prompt: str,
        schema: type[BaseModel],  # noqa: ARG002
    ) -> str:
        """Record the prompt, then pop and return the next queued response."""
        self.structured_prompts.append(user_prompt)
        return self._responses.pop(0)


@pytest.fixture
def collector(tmp_path: Path) -> ContextCollector:
    """Return a ContextCollector rooted at tmp_path."""
    return ContextCollector(project_root=tmp_path)


async def test_run_review_parses_valid_json(collector: ContextCollector) -> None:
    """A valid JSON response becomes a ReviewResult on the first pass."""
    reviewer = GuardianReviewer(
        provider=_SequenceProvider([FINDING_JSON]), context_collector=collector
    )
    result = await reviewer.run_review()
    assert result.parse_failed is False
    assert len(result.findings) == 1
    assert result.findings[0].file == "a.py"


async def test_run_review_strips_markdown_fences(collector: ContextCollector) -> None:
    """A fenced ```json response still parses."""
    fenced = f"```json\n{_VALID_JSON}\n```"
    reviewer = GuardianReviewer(provider=_SequenceProvider([fenced]), context_collector=collector)
    result = await reviewer.run_review()
    assert result.summary == "all good"


async def test_run_review_retries_once_on_invalid_json(collector: ContextCollector) -> None:
    """First invalid response triggers one retry whose prompt cites the error."""
    provider = _SequenceProvider(["not json at all", _VALID_JSON])
    reviewer = GuardianReviewer(provider=provider, context_collector=collector)
    result = await reviewer.run_review()
    assert result.parse_failed is False
    assert len(provider.structured_prompts) == 2
    assert "failed validation" in provider.structured_prompts[1].lower()


async def test_run_review_falls_back_after_two_failures(collector: ContextCollector) -> None:
    """Two invalid responses → raw text in summary, parse_failed=True."""
    provider = _SequenceProvider(["garbage one", "garbage two"])
    reviewer = GuardianReviewer(provider=provider, context_collector=collector)
    result = await reviewer.run_review()
    assert result.parse_failed is True
    assert result.findings == []
    assert result.summary == "garbage two"


async def test_run_review_passes_context_to_prompt(collector: ContextCollector) -> None:
    """collect_all() output is forwarded into the user prompt."""
    captured: dict[str, str] = {}

    class _CapturingProvider(BaseProvider):
        async def generate_content(self, system_prompt: str, user_prompt: str) -> str:  # noqa: ARG002
            """Capture the user prompt and return valid JSON so parsing succeeds."""
            captured["user"] = user_prompt
            return _VALID_JSON

        async def generate_structured(
            self,
            system_prompt: str,
            user_prompt: str,
            schema: type[BaseModel],  # noqa: ARG002
        ) -> str:
            """Structured variant — same canned behaviour as generate_content."""
            return await self.generate_content(system_prompt, user_prompt)

    with patch.object(
        collector,
        "collect_all",
        return_value={"diff": "MY_DIFF", "contributing": "", "ontology": ""},
    ):
        reviewer = GuardianReviewer(provider=_CapturingProvider(), context_collector=collector)
        await reviewer.run_review()

    assert "MY_DIFF" in captured["user"]


def test_provider_cumulative_usage_defaults_to_zero() -> None:
    """cumulative_usage starts at zero, like last_usage."""
    provider = _SequenceProvider([])
    assert provider.cumulative_usage.total_tokens == 0


def test_record_usage_updates_last_and_accumulates() -> None:
    """_record_usage sets last_usage to the new value and sums cumulative_usage."""
    provider = _SequenceProvider([])
    provider._record_usage(ProviderUsage(prompt_tokens=100, completion_tokens=10))  # noqa: SLF001
    provider._record_usage(ProviderUsage(prompt_tokens=200, completion_tokens=20))  # noqa: SLF001
    assert provider.last_usage.prompt_tokens == 200
    assert provider.last_usage.completion_tokens == 20
    assert provider.cumulative_usage.prompt_tokens == 300
    assert provider.cumulative_usage.completion_tokens == 30
    assert provider.cumulative_usage.total_tokens == 330


def test_provider_last_usage_defaults_to_zero() -> None:
    """last_usage is initialised to zero before any call."""
    provider = _FakeProvider()
    assert provider.last_usage.prompt_tokens == 0
    assert provider.last_usage.completion_tokens == 0
    assert provider.last_usage.total_tokens == 0


def test_provider_usage_total_tokens_computed() -> None:
    """total_tokens is the sum even when prompt_tokens is zero."""
    usage = ProviderUsage(prompt_tokens=0, completion_tokens=100)
    assert usage.total_tokens == 100


def test_provider_usage_is_immutable() -> None:
    """ProviderUsage is frozen — mutation raises ValidationError."""
    usage = ProviderUsage(prompt_tokens=100, completion_tokens=50)
    with pytest.raises(ValidationError):
        usage.prompt_tokens = 200  # type: ignore[misc]


async def test_provider_last_usage_after_call(collector: ContextCollector) -> None:
    """last_usage reflects values set by the provider after generate_content."""

    class _UsageProvider(BaseProvider):
        async def generate_content(self, system_prompt: str, user_prompt: str) -> str:  # noqa: ARG002
            """Return valid JSON and set fake usage (single-call semantics)."""
            self.last_usage = ProviderUsage(prompt_tokens=100, completion_tokens=50)
            return _VALID_JSON

        async def generate_structured(
            self,
            system_prompt: str,
            user_prompt: str,
            schema: type[BaseModel],  # noqa: ARG002
        ) -> str:
            """Structured variant — same canned behaviour as generate_content."""
            return await self.generate_content(system_prompt, user_prompt)

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
    assert c.graph_stats == {"total": 0, "with_graph": 0, "flow_fallback": 0}


def test_graph_stats_updated_when_no_db(tmp_path: Path) -> None:
    """graph_stats stays at zero when db_path is None."""
    c = ContextCollector(project_root=tmp_path)
    c.collect_graph_context()
    assert c.graph_stats == {"total": 0, "with_graph": 0, "flow_fallback": 0}


def test_graph_stats_empty_changed_files(tmp_path: Path) -> None:
    """graph_stats stays zero when no .py files are changed."""
    db = tmp_path / "graph.db"
    db.write_bytes(b"")  # db exists but changed files list is empty
    c = ContextCollector(project_root=tmp_path, db_path=db)
    with patch("cgis.guardian.collector.ContextCollector.get_changed_py_files", return_value=[]):
        c.collect_graph_context()
    assert c.graph_stats == {"total": 0, "with_graph": 0, "flow_fallback": 0}


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
    assert "response_format" not in inst.chat.complete_async.call_args.kwargs


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


async def test_gemini_generate_structured_sets_json_mode() -> None:
    """generate_structured passes response_mime_type + response_schema to the SDK."""
    mock_response = MagicMock()
    mock_response.text = '{"findings": [], "summary": "ok"}'
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
        result = await provider.generate_structured("sys", "user", ReviewResult)

    # `from google.genai import types` resolves via mock_genai.types (attribute lookup),
    # not via sys.modules["google.genai.types"], so we assert on mock_genai.types.
    assert result == '{"findings": [], "summary": "ok"}'
    config_kwargs = mock_genai.types.GenerateContentConfig.call_args.kwargs
    assert config_kwargs["response_mime_type"] == "application/json"
    assert config_kwargs["response_schema"] is ReviewResult
    assert config_kwargs["system_instruction"] == "sys"


async def test_mistral_generate_structured_sets_json_object() -> None:
    """generate_structured passes response_format=json_object to the SDK."""
    mock_choice = MagicMock()
    mock_choice.message.content = '{"findings": [], "summary": "ok"}'
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    inst = _make_mistral_client(mock_response)
    provider = MistralProvider(api_key="fake")
    with patch.dict("sys.modules", _mistral_modules(inst)):
        result = await provider.generate_structured("sys", "user", ReviewResult)

    assert result == '{"findings": [], "summary": "ok"}'
    call_kwargs = inst.chat.complete_async.call_args.kwargs
    assert call_kwargs["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_finder_pass_is_module_level() -> None:
    """finder_pass works standalone, without a GuardianReviewer instance."""
    provider = _SequenceProvider([FINDING_JSON])
    result = await finder_pass(provider, {"diff": "d"})
    assert len(result.findings) == 1
    assert result.findings[0].file == "a.py"


@pytest.mark.asyncio
async def test_finder_retry_accumulates_usage() -> None:
    """A parse retry makes TWO calls; cumulative_usage must count both (spec §4.7)."""

    class _UsageSequenceProvider(_SequenceProvider):
        """_SequenceProvider that records fixed usage on every call."""

        async def generate_structured(
            self, system_prompt: str, user_prompt: str, schema: type[BaseModel]
        ) -> str:
            """Record usage, then answer from the canned sequence."""
            self._record_usage(ProviderUsage(prompt_tokens=10, completion_tokens=2))
            return await super().generate_structured(system_prompt, user_prompt, schema)

    provider = _UsageSequenceProvider(["not json", FINDING_JSON])
    result = await finder_pass(provider, {"diff": "d"})
    assert len(result.findings) == 1
    assert provider.last_usage.prompt_tokens == 10  # last call only
    assert provider.cumulative_usage.prompt_tokens == 20  # both calls
    assert provider.cumulative_usage.completion_tokens == 4


def test_build_user_prompt_demands_json_output() -> None:
    """OUTPUT FORMAT section requests raw JSON matching the findings schema."""
    prompt = PromptBuilder.build_user_prompt({"diff": "d"})
    assert '"findings"' in prompt
    assert '"summary"' in prompt
    assert "ONLY a JSON object" in prompt
    assert "**[Logic Bug" not in prompt  # old markdown template is gone


def test_build_user_prompt_lgtm_example_is_valid_json() -> None:
    """The inline LGTM example must itself parse — it teaches the model the format."""
    prompt = PromptBuilder.build_user_prompt({"diff": "d"})
    marker = "Example LGTM response: "
    start = prompt.index(marker) + len(marker)
    example = prompt[start:].split("\n", 1)[0].strip()
    parsed = json.loads(example)
    assert parsed["findings"] == []
    assert isinstance(parsed["summary"], str)


def test_user_prompt_includes_full_files_section() -> None:
    """The FULL FILE CONTENTS section appears iff the context provides it."""
    with_files = PromptBuilder.build_user_prompt({"diff": "d", "full_files": "#### `a.py`"})
    without = PromptBuilder.build_user_prompt({"diff": "d"})
    assert "FULL FILE CONTENTS (HEAD)" in with_files
    assert "FULL FILE CONTENTS" not in without


def test_user_prompt_includes_drift_section() -> None:
    """The ARCHITECTURAL DRIFT section appears iff the context provides it."""
    with_drift = PromptBuilder.build_user_prompt({"diff": "d", "drift": "| domain |"})
    assert "ARCHITECTURAL DRIFT (motif-basis)" in with_drift
    assert "ontology" in with_drift  # the reading instruction names the category


# ---------------------------------------------------------------------------
# Skeptic pass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finder_hallucinated_skeptic_fields_are_reset(tmp_path: Path) -> None:
    """Skeptic-owned fields the finder LLM fills in are wiped (they're in its schema).

    A hallucinated verdict="refuted" would make visible_findings() silently
    drop a finder finding; skeptic_status="ok" without a skeptic is a lie.
    Observed in the wild: gemini-3.5-flash bench rows with skeptic off.
    """
    hallucinated = (
        '{"findings": [{"file": "a.py", "line": 1, "severity": "major", "category": "logic",'
        ' "title": "t", "evidence": "e", "problem": "p", "fix": "f", "confidence": 90,'
        ' "verdict": "refuted", "skeptic_note": "made up"}],'
        ' "summary": "s", "skeptic_status": "ok"}'
    )
    finder = StubProvider([hallucinated])
    collector = ContextCollector(project_root=tmp_path)
    reviewer = GuardianReviewer(provider=finder, context_collector=collector)
    with patch.object(collector, "collect_all", return_value={"diff": "d"}):
        result = await reviewer.run_review()
    assert result.skeptic_status == "off"
    assert result.findings[0].verdict is None
    assert result.findings[0].skeptic_note is None


@pytest.mark.asyncio
async def test_skeptic_pass_merges_verdicts(tmp_path: Path) -> None:
    """With a skeptic provider, verdicts land on findings and status is 'ok'."""
    finder = StubProvider([FINDING_JSON])
    skeptic = StubProvider(
        ['{"verdicts": [{"finding_index": 0, "verdict": "confirmed", "rationale": "r"}]}']
    )
    collector = ContextCollector(project_root=tmp_path)
    reviewer = GuardianReviewer(
        provider=finder, context_collector=collector, skeptic_provider=skeptic
    )
    with patch.object(collector, "collect_all", return_value={"diff": "d"}):
        result = await reviewer.run_review()
    assert result.skeptic_status == "ok"
    assert result.findings[0].verdict == "confirmed"


@pytest.mark.asyncio
async def test_skeptic_not_called_on_lgtm(tmp_path: Path) -> None:
    """An empty pass 1 has nothing to refute — the skeptic is never called (spec §5.4)."""
    finder = StubProvider(['{"findings": [], "summary": "ok"}'])
    skeptic = StubProvider([])  # would raise IndexError if called
    collector = ContextCollector(project_root=tmp_path)
    reviewer = GuardianReviewer(
        provider=finder, context_collector=collector, skeptic_provider=skeptic
    )
    with patch.object(collector, "collect_all", return_value={"diff": "d"}):
        result = await reviewer.run_review()
    assert result.skeptic_status == "off"
    assert skeptic.prompts == []


@pytest.mark.asyncio
async def test_skeptic_failure_degrades_to_single_pass(tmp_path: Path) -> None:
    """Skeptic crash → single-pass findings, skeptic_status='failed' (spec §5.5)."""
    finder = StubProvider([FINDING_JSON])
    skeptic = StubProvider(["not json at all {{{"])
    collector = ContextCollector(project_root=tmp_path)
    reviewer = GuardianReviewer(
        provider=finder, context_collector=collector, skeptic_provider=skeptic
    )
    with patch.object(collector, "collect_all", return_value={"diff": "d"}):
        result = await reviewer.run_review()
    assert result.skeptic_status == "failed"
    assert result.findings[0].verdict is None
