"""The two-pass finder that asks for its own graph context (#246 experiment C)."""

import json
from pathlib import Path

import pytest
from guardian_stubs import FINDING_JSON, StubProvider
from pydantic import BaseModel

from cgis.guardian.chunked import run_review_routed
from cgis.guardian.collector import ContextCollector
from cgis.guardian.context_requests import (
    MAX_ANSWER_CHARS,
    MAX_REQUESTS,
    MAX_TOTAL_CHARS,
    ContextRequest,
    answer_requests,
)
from cgis.guardian.prompts import PromptBuilder
from cgis.guardian.providers.base import ProviderUsage
from cgis.languages.python.extractor import PythonExtractor
from cgis.pipeline import IngestionPipeline
from cgis.storage.sqlite_store import SQLiteStore

_LGTM = '{"findings": [], "summary": "clean"}'
_CONFIRM = '{"verdict": "confirmed", "impact_score": 6, "rationale": "r"}'


class _MeteredProvider(StubProvider):
    """A stub that bills every call like a real provider: 100 in, 10 out."""

    async def generate_structured(
        self, system_prompt: str, user_prompt: str, schema: type[BaseModel]
    ) -> str:
        """Record one call's usage, then answer as the stub does."""
        self._record_usage(ProviderUsage(prompt_tokens=100, completion_tokens=10))
        return await super().generate_structured(system_prompt, user_prompt, schema)


def _project(tmp_path: Path, *, with_test: bool = False) -> Path:
    """A two-function project ingested into graph.db; returns the db path."""
    (tmp_path / "pkg").mkdir()
    if with_test:
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_a.py").write_text(
            "from pkg.a import helper\n\n\ndef test_helper():\n    assert helper() == 1\n",
            encoding="utf-8",
        )
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "a.py").write_text(
        "def helper():\n    return 1\n\n\ndef caller():\n    return helper()\n", encoding="utf-8"
    )
    db = tmp_path / "graph.db"
    with SQLiteStore(str(db)) as store:
        IngestionPipeline({".py": PythonExtractor()}).run(str(tmp_path), store=store)
    return db


def _collector(tmp_path: Path, db: Path | None, features: frozenset[str]) -> ContextCollector:
    """A collector over `tmp_path` with a canned diff, so no git is needed."""
    collector = ContextCollector(
        project_root=tmp_path, db_path=db, source_root="", features=features
    )
    diff = "diff --git a/pkg/a.py b/pkg/a.py\n--- a/pkg/a.py\n+++ b/pkg/a.py\n@@ -1 +1 @@\n+x\n"
    collector.source._diff_cache = diff  # noqa: SLF001  # bypass git
    return collector


def _requests(*items: tuple[str, str]) -> str:
    return json.dumps({"requests": [{"symbol": s, "kind": k} for s, k in items]})


def test_context_request_brings_the_source_and_callers(tmp_path: Path) -> None:
    db = _project(tmp_path)
    text, outcomes = answer_requests(
        db, [ContextRequest(symbol="helper", kind="context")], source_root=str(tmp_path)
    )
    assert outcomes[0].resolved == "pkg.a.helper"
    assert "return 1" in text  # the source
    assert "pkg.a.caller" in text  # its caller


def test_callers_request_lists_the_upstream(tmp_path: Path) -> None:
    db = _project(tmp_path)
    text, outcomes = answer_requests(
        db, [ContextRequest(symbol="pkg.a.helper", kind="callers")], source_root=str(tmp_path)
    )
    assert outcomes[0].resolved == "pkg.a.helper"
    assert "pkg.a.caller" in text


def test_callers_of_an_uncalled_function_are_none(tmp_path: Path) -> None:
    """Containment is not a call: the file holding a function is not its caller (review #720)."""
    db = _project(tmp_path)
    text, _ = answer_requests(
        db, [ContextRequest(symbol="pkg.a.caller", kind="callers")], source_root=""
    )
    assert "no static callers found" in text


def test_callers_leave_tests_out_and_say_so(tmp_path: Path) -> None:
    db = _project(tmp_path, with_test=True)
    text, _ = answer_requests(
        db, [ContextRequest(symbol="pkg.a.helper", kind="callers")], source_root=""
    )
    assert "test_helper" not in text
    assert "test caller(s) left out" in text


def test_unknown_symbol_is_said_not_dropped(tmp_path: Path) -> None:
    db = _project(tmp_path)
    text, outcomes = answer_requests(
        db, [ContextRequest(symbol="nowhere_at_all", kind="context")], source_root=""
    )
    assert outcomes[0].resolved is None
    assert "nowhere_at_all" in text
    assert "unresolved" in text


def test_a_directory_is_not_a_graph(tmp_path: Path) -> None:
    text, outcomes = answer_requests(
        tmp_path, [ContextRequest(symbol="helper", kind="context")], source_root=""
    )
    assert "No graph is available" in text
    assert outcomes[0].resolved is None


def test_requests_beyond_the_limit_are_ignored(tmp_path: Path) -> None:
    db = _project(tmp_path)
    many = [ContextRequest(symbol="helper", kind="context")] * (MAX_REQUESTS + 3)
    _, outcomes = answer_requests(db, many, source_root=str(tmp_path))
    assert len(outcomes) == MAX_REQUESTS


def test_answers_are_capped(tmp_path: Path) -> None:
    (tmp_path / "big.py").write_text(
        "def big():\n" + "".join(f"    x{i} = {i}\n" for i in range(3000)), encoding="utf-8"
    )
    db = tmp_path / "graph.db"
    with SQLiteStore(str(db)) as store:
        IngestionPipeline({".py": PythonExtractor()}).run(str(tmp_path), store=store)
    reqs = [ContextRequest(symbol="big", kind="context")] * MAX_REQUESTS
    text, outcomes = answer_requests(db, reqs, source_root=str(tmp_path))
    assert all(o.chars <= MAX_ANSWER_CHARS for o in outcomes)
    assert len(text) <= MAX_TOTAL_CHARS + 2000  # headings and truncation notes on top
    assert "truncated" in text


def test_no_section_renders_the_prompt_unchanged() -> None:
    """Production prompts must not move: the section exists only when asked for."""
    base = {"diff": "d"}
    assert PromptBuilder.build_user_prompt(base) == PromptBuilder.build_user_prompt(
        {**base, "requested_context": ""}
    )
    assert "### 7." not in PromptBuilder.build_user_prompt(base)
    assert "CONTEXT YOU REQUESTED" in PromptBuilder.build_user_prompt(
        {**base, "requested_context": "stuff"}
    )


@pytest.mark.asyncio
async def test_two_passes_feed_the_answer_into_the_finder(tmp_path: Path) -> None:
    db = _project(tmp_path)
    provider = StubProvider([_requests(("helper", "context")), FINDING_JSON])
    routed = await run_review_routed(
        provider=provider,
        collector=_collector(tmp_path, db, frozenset({"context_requests"})),
        skeptic_provider=None,
    )
    assert len(provider.prompts) == 2
    assert "CONTEXT YOU REQUESTED" not in provider.prompts[0]
    assert "CONTEXT YOU REQUESTED" in provider.prompts[1]
    assert "return 1" in provider.prompts[1]
    assert len(routed.result.findings) == 1
    assert routed.requests is not None
    assert routed.requests.parsed is True
    assert [o.resolved for o in routed.requests.outcomes] == ["pkg.a.helper"]


@pytest.mark.asyncio
async def test_unparsable_requests_mark_the_review_failed(tmp_path: Path) -> None:
    """Pre-registered: a pass-1 parse failure excludes the review, it is not silently 1-pass."""
    db = _project(tmp_path)
    provider = StubProvider(["not json", "still not json", _LGTM])
    routed = await run_review_routed(
        provider=provider,
        collector=_collector(tmp_path, db, frozenset({"context_requests"})),
        skeptic_provider=None,
    )
    assert routed.requests is not None
    assert routed.requests.parsed is False
    assert routed.result.parse_failed is True


@pytest.mark.asyncio
async def test_no_graph_still_runs_pass_two(tmp_path: Path) -> None:
    provider = StubProvider([_requests(("helper", "context")), _LGTM])
    routed = await run_review_routed(
        provider=provider,
        collector=_collector(tmp_path, None, frozenset({"context_requests"})),
        skeptic_provider=None,
    )
    assert len(provider.prompts) == 2
    assert "no graph" in provider.prompts[1].lower()
    assert routed.requests is not None
    assert routed.requests.outcomes[0].resolved is None


@pytest.mark.asyncio
async def test_the_skeptic_judges_the_pass_two_findings(tmp_path: Path) -> None:
    db = _project(tmp_path)
    skeptic = StubProvider([_CONFIRM])
    routed = await run_review_routed(
        provider=StubProvider([_requests(("helper", "context")), FINDING_JSON]),
        collector=_collector(tmp_path, db, frozenset({"context_requests"})),
        skeptic_provider=skeptic,
    )
    assert len(skeptic.prompts) == 1
    assert routed.result.skeptic_status == "ok"
    assert routed.result.findings[0].impact_score == 6


@pytest.mark.asyncio
async def test_pass_one_tokens_are_its_own(tmp_path: Path) -> None:
    """Pass 1 parsed on its retry: two calls are its own, the pass-2 call is not."""
    db = _project(tmp_path)
    provider = _MeteredProvider(["not json", _requests(("helper", "context")), _LGTM])
    routed = await run_review_routed(
        provider=provider,
        collector=_collector(tmp_path, db, frozenset({"context_requests"})),
        skeptic_provider=None,
    )
    assert routed.requests is not None
    assert (routed.requests.prompt_tokens, routed.requests.completion_tokens) == (200, 20)
    assert provider.cumulative_usage.prompt_tokens == 300


@pytest.mark.asyncio
@pytest.mark.parametrize("other", ["chunked", "axes", "axes_paired"])
async def test_combined_with_another_shape_is_refused(tmp_path: Path, other: str) -> None:
    db = _project(tmp_path)
    provider = StubProvider([])
    collector = _collector(tmp_path, db, frozenset({"context_requests", other}))
    with pytest.raises(ValueError, match="context_requests"):
        await run_review_routed(provider=provider, collector=collector, skeptic_provider=None)
