"""Tests for the guardian script runner (provider selection + orchestration)."""

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from guardian_stubs import FINDING_JSON, StubProvider
from pydantic import BaseModel

from cgis.guardian.collector import ContextCollector
from cgis.guardian.providers.base import BaseProvider, ProviderUsage
from cgis.guardian.providers.ollama import OllamaProvider
from cgis.guardian.recording import load_finder_recording
from cgis.guardian.runner import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_MISTRAL_MODEL,
    build_footer,
    build_provider,
    build_skeptic_provider,
    impact_threshold,
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


# ---------------------------------------------------------------------------
# Ollama provider (local/colab inference, no API key) — issue #255
# ---------------------------------------------------------------------------


def test_build_provider_ollama_requires_a_model() -> None:
    """GUARDIAN_PROVIDER=ollama without GUARDIAN_MODEL is an explicit error."""
    with pytest.raises(RuntimeError, match="GUARDIAN_MODEL must name an Ollama model"):
        build_provider({"GUARDIAN_PROVIDER": "ollama"})


def test_build_provider_ollama_selects_model_and_host() -> None:
    """ollama needs no API key; model + host come from env (no key required)."""
    provider, model = build_provider(
        {
            "GUARDIAN_PROVIDER": "ollama",
            "GUARDIAN_MODEL": "qwen2.5-coder:14b",
            "GUARDIAN_OLLAMA_HOST": "http://localhost:11435",
        }
    )
    assert model == "qwen2.5-coder:14b"
    assert isinstance(provider, OllamaProvider)
    assert provider._host == "http://localhost:11435"  # noqa: SLF001  # white-box: host wiring


def test_build_provider_ollama_num_ctx_from_env() -> None:
    """GUARDIAN_OLLAMA_NUM_CTX overrides the default context window (VRAM/length tuning)."""
    provider, _ = build_provider(
        {"GUARDIAN_PROVIDER": "ollama", "GUARDIAN_MODEL": "m", "GUARDIAN_OLLAMA_NUM_CTX": "8192"}
    )
    assert isinstance(provider, OllamaProvider)
    assert provider._num_ctx == 8192  # noqa: SLF001  # white-box: ctx wiring


def test_build_provider_unknown_lists_ollama() -> None:
    """A typo in GUARDIAN_PROVIDER names all three valid providers."""
    with pytest.raises(RuntimeError, match="'mistral', 'gemini', or 'ollama'"):
        build_provider({"GUARDIAN_PROVIDER": "anthropic", "GEMINI_API_KEY": "g"})


def test_build_skeptic_provider_ollama_cross_model() -> None:
    """A distinct ollama skeptic model = free cross-model skeptic (#246)."""
    env = {
        "GUARDIAN_SKEPTIC": "ollama",
        "GUARDIAN_SKEPTIC_MODEL": "llama3.1:8b",
        "GUARDIAN_MODEL": "qwen2.5-coder:14b",
    }
    built = build_skeptic_provider(env, primary="ollama")
    assert built is not None
    provider, model = built
    assert isinstance(provider, OllamaProvider)
    assert model == "llama3.1:8b"  # skeptic model override wins over GUARDIAN_MODEL


def test_build_skeptic_provider_ollama_falls_back_to_finder_model() -> None:
    """Without GUARDIAN_SKEPTIC_MODEL the skeptic reuses GUARDIAN_MODEL."""
    env = {"GUARDIAN_SKEPTIC": "ollama", "GUARDIAN_MODEL": "qwen2.5-coder:14b"}
    built = build_skeptic_provider(env, primary="ollama")
    assert built is not None
    _provider, model = built
    assert model == "qwen2.5-coder:14b"


def test_build_skeptic_provider_ollama_needs_a_model() -> None:
    """No model for an ollama skeptic → None (graceful single-pass), not an error."""
    assert build_skeptic_provider({"GUARDIAN_SKEPTIC": "ollama"}, primary="ollama") is None


def _fake_ollama_client(resp: object) -> AsyncMock:
    """An AsyncMock usable as `async with AsyncClient(...) as client` whose chat returns resp."""
    client = AsyncMock()
    client.chat = AsyncMock(return_value=resp)
    client.__aenter__.return_value = client  # `async with` yields the client itself
    client.__aexit__.return_value = False
    return client


@pytest.mark.asyncio
async def test_ollama_provider_structured_uses_json_format_and_records_usage() -> None:
    """generate_structured sends format='json' and maps eval counts to usage."""
    pytest.importorskip("ollama")  # optional guardian-group dep

    class _Schema(BaseModel):
        x: int

    resp = SimpleNamespace(
        message=SimpleNamespace(content='{"x": 1}'),
        prompt_eval_count=11,
        eval_count=4,
    )
    fake_client = _fake_ollama_client(resp)
    provider = OllamaProvider(model_name="m", host="http://localhost:11435")
    with patch("ollama.AsyncClient", return_value=fake_client):
        out = await provider.generate_structured("sys", "usr", schema=_Schema)
    assert out == '{"x": 1}'
    kwargs = fake_client.chat.call_args.kwargs
    # schema-constrained decoding (not plain "json") → conformant object from small models
    assert kwargs["format"]["title"] == "_Schema"  # schema dict, not the literal "json"
    assert kwargs["options"]["num_ctx"] == 32768  # explicit window → no silent truncation
    assert provider.cumulative_usage.prompt_tokens == 11
    assert provider.cumulative_usage.completion_tokens == 4


@pytest.mark.asyncio
async def test_ollama_provider_content_uses_no_json_format() -> None:
    """generate_content sends an empty format (free text), not JSON mode."""
    pytest.importorskip("ollama")  # optional guardian-group dep
    resp = SimpleNamespace(
        message=SimpleNamespace(content="plain text"),
        prompt_eval_count=None,  # some responses omit counts → default to 0
        eval_count=None,
    )
    fake_client = _fake_ollama_client(resp)
    provider = OllamaProvider(model_name="m")
    with patch("ollama.AsyncClient", return_value=fake_client):
        out = await provider.generate_content("sys", "usr")
    assert out == "plain text"
    assert fake_client.chat.call_args.kwargs["format"] == ""
    assert provider.cumulative_usage.total_tokens == 0


async def test_run_guardian_smoke(tmp_path: Path) -> None:
    """End-to-end with a fake provider: review → render → metrics line.

    ContextCollector.collect_all() gracefully handles a non-git tmp_path:
    get_git_diff() returns an error string (no exception), and read_file()
    returns "" for missing CONTRIBUTING.md / ontology.yaml (so those prompt
    sections are simply omitted).  The FakeProvider ignores the prompt content
    and always returns valid JSON, so no mock of collect_all() is needed.
    """
    metrics = tmp_path / "m.jsonl"
    collector = ContextCollector(project_root=tmp_path)
    report, posted_inline = await run_guardian(
        provider=_FakeProvider(),
        model="fake-model",
        collector=collector,
        pr=152,
        metrics_path=metrics,
    )
    assert report.startswith("LGTM — no defects found in this diff.")
    assert metrics.exists()
    assert posted_inline is False


# ---------------------------------------------------------------------------
# Inline review path tests (spec §6.5, §8)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_guardian_posts_inline_and_reports_success(tmp_path: Path) -> None:
    """Smoke test (spec §8): canned JSON → ReviewResult → inline post; posted=True."""
    provider = StubProvider([FINDING_JSON])
    collector = ContextCollector(project_root=tmp_path)
    with (
        patch.object(collector, "collect_all", return_value={"diff": "d"}),
        patch.object(
            collector,
            "get_git_diff",
            return_value="diff --git a/a.py b/a.py\n+++ b/a.py\n@@ -0,0 +1,1 @@\n+x = 1\n",
        ),
        patch("cgis.guardian.runner.post_inline_review") as mock_post,
    ):
        report, posted = await run_guardian(
            provider=provider,
            model="m",
            collector=collector,
            pr=153,
            metrics_path=tmp_path / "m.jsonl",
            inline_repo="zaebee/codegraph-brain",
        )
    assert posted is True
    mock_post.assert_called_once()
    assert "**[Logic Bug]" in report  # report still rendered for the artifact


@pytest.mark.asyncio
async def test_run_guardian_inline_failure_falls_back(tmp_path: Path) -> None:
    """API rejection → posted=False, report intact (peter-evans fallback, spec §6.5)."""
    provider = StubProvider([FINDING_JSON])
    collector = ContextCollector(project_root=tmp_path)
    with (
        patch.object(collector, "collect_all", return_value={"diff": "d"}),
        patch.object(collector, "get_git_diff", return_value=""),
        patch(
            "cgis.guardian.runner.post_inline_review",
            side_effect=subprocess.CalledProcessError(1, "gh"),
        ),
    ):
        report, posted = await run_guardian(
            provider=provider,
            model="m",
            collector=collector,
            pr=153,
            metrics_path=tmp_path / "m.jsonl",
            inline_repo="zaebee/codegraph-brain",
        )
    assert posted is False
    assert "**[Logic Bug]" in report


@pytest.mark.asyncio
async def test_run_guardian_no_inline_repo_skips_posting(tmp_path: Path) -> None:
    """inline_repo=None (local runs, bench) → no posting attempted, posted=False."""
    provider = StubProvider([FINDING_JSON])
    collector = ContextCollector(project_root=tmp_path)
    with (
        patch.object(collector, "collect_all", return_value={"diff": "d"}),
        patch("cgis.guardian.runner.post_inline_review") as mock_post,
    ):
        _, posted = await run_guardian(
            provider=provider,
            model="m",
            collector=collector,
            pr=None,
            metrics_path=tmp_path / "m.jsonl",
        )
    assert posted is False
    mock_post.assert_not_called()


def test_impact_threshold_defaults_to_zero_and_reads_env() -> None:
    """Ships inert: the knob only moves once the benchmark shows the distribution."""
    assert impact_threshold({}) == 0
    assert impact_threshold({"GUARDIAN_IMPACT_THRESHOLD": "4"}) == 4
    assert impact_threshold({"GUARDIAN_IMPACT_THRESHOLD": "nonsense"}) == 0
    assert impact_threshold({"GUARDIAN_IMPACT_THRESHOLD": "-3"}) == 0
    assert impact_threshold({"GUARDIAN_IMPACT_THRESHOLD": "99"}) == 10


async def test_run_guardian_records_the_finder_pass(tmp_path: Path) -> None:
    """The artifact that makes a review re-scorable offline (#279)."""
    recording_path = tmp_path / "finder.json"
    provider = StubProvider([FINDING_JSON])
    collector = ContextCollector(project_root=tmp_path)
    with (
        patch.object(collector, "collect_all", return_value={"diff": "d"}),
        patch.object(collector, "get_git_diff", return_value="the-diff"),
    ):
        await run_guardian(
            provider=provider,
            model="m",
            collector=collector,
            pr=1,
            metrics_path=tmp_path / "m.jsonl",
            record_finder=recording_path,
        )

    loaded = load_finder_recording(recording_path)
    assert loaded.diff == "the-diff"
    assert [f.file for f in loaded.result.findings] == ["a.py"]


async def test_run_guardian_records_nothing_without_the_flag(tmp_path: Path) -> None:
    recording_path = tmp_path / "finder.json"
    provider = StubProvider([FINDING_JSON])
    collector = ContextCollector(project_root=tmp_path)
    with (
        patch.object(collector, "collect_all", return_value={"diff": "d"}),
        patch.object(collector, "get_git_diff", return_value="the-diff"),
    ):
        await run_guardian(
            provider=provider,
            model="m",
            collector=collector,
            pr=1,
            metrics_path=tmp_path / "m.jsonl",
        )

    assert not recording_path.exists()


async def test_refuted_findings_survive_into_the_recording(tmp_path: Path) -> None:
    """The regression test for the whole 'post-skeptic recording is safe' argument.

    apply_judgements annotates rather than drops (tests/unit/test_guardian_skeptic.py
    guards that directly); this asserts the end-to-end consequence — a refuted
    finding still reaches the file, and loads back unjudged.
    """
    recording_path = tmp_path / "finder.json"
    provider = StubProvider([FINDING_JSON])
    skeptic = StubProvider(['{"verdict": "refuted", "impact_score": 0, "rationale": "b"}'])
    collector = ContextCollector(project_root=tmp_path)
    with (
        patch.object(collector, "collect_all", return_value={"diff": "d"}),
        patch.object(collector, "get_git_diff", return_value="the-diff"),
    ):
        await run_guardian(
            provider=provider,
            model="m",
            collector=collector,
            pr=1,
            metrics_path=tmp_path / "m.jsonl",
            skeptic=(skeptic, "stub-skeptic"),
            record_finder=recording_path,
        )

    loaded = load_finder_recording(recording_path)
    assert len(loaded.result.findings) == 1
    assert loaded.result.findings[0].verdict is None


async def test_a_failed_recording_does_not_lose_the_review(tmp_path: Path) -> None:
    """The recording is diagnostic; a failed write must not cost the report (#279).

    Found by review: the sibling post_inline_review call is guarded for the same
    reason, and an unguarded write here would drop report, comment and metrics
    for a review that had already completed.
    """
    metrics = tmp_path / "m.jsonl"
    provider = StubProvider([FINDING_JSON])
    collector = ContextCollector(project_root=tmp_path)
    with (
        patch.object(collector, "collect_all", return_value={"diff": "d"}),
        patch.object(collector, "get_git_diff", return_value="the-diff"),
        patch(
            "cgis.guardian.runner.save_finder_recording",
            side_effect=OSError("disk full"),
        ),
    ):
        report, _posted = await run_guardian(
            provider=provider,
            model="m",
            collector=collector,
            pr=1,
            metrics_path=metrics,
            record_finder=tmp_path / "finder.json",
        )

    assert "**[Logic Bug]" in report
    assert metrics.exists()
