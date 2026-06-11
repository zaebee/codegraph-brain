"""Unit tests for chunked review orchestration (spec: 2026-06-11-guardian-chunked-review)."""

import json
from pathlib import Path

import pytest
from guardian_stubs import StubProvider
from pydantic import BaseModel

from cgis.guardian.chunked import (
    MAX_CHUNKS,
    RoutedReview,
    _cap_chunks,
    _dedup,
    run_chunked_review,
    run_review_routed,
)
from cgis.guardian.chunker import Chunk
from cgis.guardian.collector import ContextCollector
from cgis.guardian.findings import Finding, ReviewResult
from cgis.guardian.providers.base import BaseProvider
from cgis.storage.sqlite_store import SQLiteStore


def _finding(
    file: str = "a.py",
    line: int | None = 1,
    category: str = "logic",
    confidence: int = 90,
    title: str = "t",
) -> Finding:
    """Minimal finding for merge/dedup tests."""
    return Finding(
        file=file,
        line=line,
        severity="major",
        category=category,
        title=title,
        evidence="e",
        problem="p",
        fix="f",
        confidence=confidence,
    )


def test_routed_review_chunk_count_defaults_none() -> None:
    """RoutedReview carries result + chunk accounting; None = single-pass."""
    rr = RoutedReview(result=ReviewResult(findings=[], summary="s"))
    assert rr.chunk_count is None


def test_cap_chunks_noop_at_or_under_max() -> None:
    """<= MAX_CHUNKS chunks come back unchanged, same order."""
    chunks = [Chunk(files=(f"{i}.py",), diff=f"d{i}\n") for i in range(MAX_CHUNKS)]
    assert _cap_chunks(chunks) == chunks


def test_cap_chunks_merges_smallest_into_overflow() -> None:
    """11 chunks -> 7 largest kept (sorted by first file) + 1 overflow, last."""
    big = [Chunk(files=(f"big{i}.py",), diff="x" * (100 + i) + "\n") for i in range(7)]
    small = [Chunk(files=(f"small{i}.py",), diff=f"s{i}\n") for i in range(4)]
    capped = _cap_chunks(big + small)
    assert len(capped) == MAX_CHUNKS
    overflow = capped[-1]
    assert overflow.files == tuple(sorted(f"small{i}.py" for i in range(4)))
    assert all(f"s{i}\n" in overflow.diff for i in range(4))
    assert [c.files[0] for c in capped[:-1]] == sorted(f"big{i}.py" for i in range(7))


def test_dedup_keeps_higher_confidence() -> None:
    """Same (file, line, category) -> one survivor, the more confident one."""
    low = _finding(confidence=81, title="low")
    high = _finding(confidence=95, title="high")
    other = _finding(file="b.py", title="other")
    result = _dedup([low, other, high])
    assert [f.title for f in result] == ["high", "other"]


def test_dedup_distinct_lines_kept() -> None:
    """Different lines are different findings."""
    assert len(_dedup([_finding(line=1), _finding(line=2), _finding(line=None)])) == 3


def fdiff(path: str, body: str = "+x = 1") -> str:
    """One minimal single-hunk diff block for `path` (same as chunker tests)."""
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -0,0 +1 @@\n{body}\n"


def _finder_json(file: str, summary: str = "ok") -> str:
    """Canned finder response with one finding in `file`."""
    return json.dumps(
        {
            "findings": [
                {
                    "file": file,
                    "line": 1,
                    "severity": "major",
                    "category": "logic",
                    "title": f"bug in {file}",
                    "evidence": "e",
                    "problem": "p",
                    "fix": "f",
                    "confidence": 90,
                }
            ],
            "summary": summary,
        }
    )


_LGTM = '{"findings": [], "summary": "clean"}'
_CONFIRM_ALL = '{"verdicts": [{"finding_index": 0, "verdict": "confirmed", "rationale": "r"}]}'


def _collector(tmp_path: Path, diff: str, *, with_db: bool = True) -> ContextCollector:
    """Collector with a stubbed diff and a real (empty) graph DB."""
    db = tmp_path / "graph.db"
    if with_db:
        with SQLiteStore(str(db)) as store:
            store.save_graph([], [], overwrite=True)
    collector = ContextCollector(
        project_root=tmp_path, db_path=db if with_db else None, source_root="src"
    )
    collector._diff_cache = diff  # noqa: SLF001  # bypass git
    return collector


class _BoomProvider(BaseProvider):
    """Raises on every structured call."""

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


@pytest.mark.asyncio
async def test_chunked_one_finder_call_per_chunk(tmp_path: Path) -> None:
    """Two isolated files -> two chunks -> two finder calls, merged findings."""
    diff = fdiff("src/a.py") + fdiff("src/b.py")
    provider = StubProvider([_finder_json("src/a.py"), _finder_json("src/b.py")])
    routed = await run_chunked_review(
        provider=provider, collector=_collector(tmp_path, diff), skeptic_provider=None
    )
    assert routed.chunk_count == 2
    assert len(provider.prompts) == 2
    assert {f.file for f in routed.result.findings} == {"src/a.py", "src/b.py"}
    assert routed.result.summary.count("- [") == 2


@pytest.mark.asyncio
async def test_chunked_empty_diff_no_llm_calls(tmp_path: Path) -> None:
    """Empty diff -> zero chunks, zero API calls, clean LGTM-ish result."""
    provider = StubProvider([])
    routed = await run_chunked_review(
        provider=provider, collector=_collector(tmp_path, ""), skeptic_provider=None
    )
    assert routed.chunk_count == 0
    assert provider.prompts == []
    assert routed.result.findings == []
    assert not routed.result.parse_failed


@pytest.mark.asyncio
async def test_chunked_out_of_chunk_finding_dropped(tmp_path: Path) -> None:
    """A finding pointing outside its chunk's files is a hallucination -> dropped."""
    diff = fdiff("src/a.py")
    provider = StubProvider([_finder_json("src/elsewhere.py")])
    routed = await run_chunked_review(
        provider=provider, collector=_collector(tmp_path, diff), skeptic_provider=None
    )
    assert routed.result.findings == []


@pytest.mark.asyncio
async def test_chunked_llm_path_artifacts_normalized(tmp_path: Path) -> None:
    """Findings with './' or diff-header 'a/' prefixes are kept and canonicalized."""
    diff = fdiff("src/a.py")
    provider = StubProvider(
        [
            json.dumps(
                {
                    "findings": [
                        {
                            "file": "a/src/a.py",
                            "line": 1,
                            "severity": "major",
                            "category": "logic",
                            "title": "prefixed",
                            "evidence": "e",
                            "problem": "p",
                            "fix": "f",
                            "confidence": 90,
                        },
                        {
                            "file": "./src/a.py",
                            "line": 2,
                            "severity": "minor",
                            "category": "tests",
                            "title": "dotted",
                            "evidence": "e",
                            "problem": "p",
                            "fix": "f",
                            "confidence": 85,
                        },
                    ],
                    "summary": "s",
                }
            )
        ]
    )
    routed = await run_chunked_review(
        provider=provider, collector=_collector(tmp_path, diff), skeptic_provider=None
    )
    assert [f.file for f in routed.result.findings] == ["src/a.py", "src/a.py"]
    assert {f.title for f in routed.result.findings} == {"prefixed", "dotted"}


@pytest.mark.asyncio
async def test_chunked_one_chunk_raises_others_survive(tmp_path: Path) -> None:
    """A failing chunk contributes a ⚠ bullet; the other chunk still reviews."""

    class _FlakyProvider(StubProvider):
        """Raises for the chunk containing src/a.py, answers normally otherwise."""

        async def generate_structured(
            self, system_prompt: str, user_prompt: str, schema: type[BaseModel]
        ) -> str:
            """Fail on the a.py chunk's prompt only."""
            if "src/a.py" in user_prompt:
                _msg = "boom"
                raise RuntimeError(_msg)
            return await super().generate_structured(system_prompt, user_prompt, schema)

    diff = fdiff("src/a.py") + fdiff("src/b.py")
    provider = _FlakyProvider([_finder_json("src/b.py")])
    routed = await run_chunked_review(
        provider=provider, collector=_collector(tmp_path, diff), skeptic_provider=None
    )
    assert "⚠ finder call failed" in routed.result.summary
    assert {f.file for f in routed.result.findings} == {"src/b.py"}
    assert not routed.result.parse_failed


@pytest.mark.asyncio
async def test_chunked_context_collection_failure_costs_one_chunk(tmp_path: Path) -> None:
    """A flaky graph DB during collect_for_chunk skips that chunk, not the review."""
    collector = _collector(tmp_path, fdiff("src/a.py") + fdiff("src/b.py"))
    original = collector.collect_for_chunk

    def _flaky(chunk: Chunk) -> dict[str, str]:
        """Blow up on the a.py chunk's context only."""
        if "src/a.py" in chunk.files:
            _msg = "db locked"
            raise RuntimeError(_msg)
        return original(chunk)

    collector.collect_for_chunk = _flaky  # type: ignore[method-assign]
    provider = StubProvider([_finder_json("src/b.py")])
    routed = await run_chunked_review(provider=provider, collector=collector, skeptic_provider=None)
    assert "⚠ finder call failed" in routed.result.summary
    assert {f.file for f in routed.result.findings} == {"src/b.py"}
    assert not routed.result.parse_failed


@pytest.mark.asyncio
async def test_chunked_all_chunks_fail_sets_parse_failed(tmp_path: Path) -> None:
    """Every chunk failing -> parse_failed=True on the merged result."""
    diff = fdiff("src/a.py") + fdiff("src/b.py")
    routed = await run_chunked_review(
        provider=_BoomProvider(), collector=_collector(tmp_path, diff), skeptic_provider=None
    )
    assert routed.result.parse_failed
    assert routed.result.findings == []


@pytest.mark.asyncio
async def test_chunked_unparsable_chunk_marked(tmp_path: Path) -> None:
    """A chunk whose finder output never parses gets the unparsable bullet."""
    diff = fdiff("src/a.py")
    provider = StubProvider(["not json", "still not json"])  # initial + retry
    routed = await run_chunked_review(
        provider=provider, collector=_collector(tmp_path, diff), skeptic_provider=None
    )
    assert "⚠ finder output unparsable" in routed.result.summary
    assert routed.result.parse_failed  # the only chunk failed


@pytest.mark.asyncio
async def test_chunked_single_skeptic_pass_scoped_diff(tmp_path: Path) -> None:
    """One skeptic call; its prompt contains only chunks WITH findings."""
    diff = fdiff("src/a.py") + fdiff("src/b.py")
    finder = StubProvider([_finder_json("src/a.py"), _LGTM])
    skeptic = StubProvider([_CONFIRM_ALL])
    routed = await run_chunked_review(
        provider=finder, collector=_collector(tmp_path, diff), skeptic_provider=skeptic
    )
    assert len(skeptic.prompts) == 1
    assert "a/src/a.py" in skeptic.prompts[0]
    assert "a/src/b.py" not in skeptic.prompts[0]  # LGTM chunk's diff excluded
    assert routed.result.skeptic_status == "ok"
    assert routed.result.findings[0].verdict == "confirmed"


@pytest.mark.asyncio
async def test_chunked_no_findings_skips_skeptic(tmp_path: Path) -> None:
    """All chunks LGTM -> the skeptic is never called."""
    diff = fdiff("src/a.py")
    skeptic = StubProvider([])
    routed = await run_chunked_review(
        provider=StubProvider([_LGTM]),
        collector=_collector(tmp_path, diff),
        skeptic_provider=skeptic,
    )
    assert skeptic.prompts == []
    assert routed.result.skeptic_status == "off"


@pytest.mark.asyncio
async def test_chunked_skeptic_failure_returns_unverified(tmp_path: Path) -> None:
    """Skeptic blowing up degrades to unverified findings, status=failed."""
    diff = fdiff("src/a.py")
    routed = await run_chunked_review(
        provider=StubProvider([_finder_json("src/a.py")]),
        collector=_collector(tmp_path, diff),
        skeptic_provider=_BoomProvider(),
    )
    assert routed.result.skeptic_status == "failed"
    assert routed.result.findings[0].verdict is None


@pytest.mark.asyncio
async def test_routed_no_flag_single_pass(tmp_path: Path) -> None:
    """Without 'chunked' the single-pass path runs: ONE finder call for two files."""
    diff = fdiff("src/a.py") + fdiff("src/b.py")
    provider = StubProvider([_LGTM])
    routed = await run_review_routed(
        provider=provider, collector=_collector(tmp_path, diff), skeptic_provider=None
    )
    assert routed.chunk_count is None
    assert len(provider.prompts) == 1


@pytest.mark.asyncio
async def test_routed_flag_without_db_falls_back(tmp_path: Path) -> None:
    """chunked + no graph DB -> single pass (warn), not isolated-chunk spam."""
    diff = fdiff("src/a.py") + fdiff("src/b.py")
    collector = _collector(tmp_path, diff, with_db=False)
    collector.features = frozenset({"chunked"})
    provider = StubProvider([_LGTM])
    routed = await run_review_routed(provider=provider, collector=collector, skeptic_provider=None)
    assert routed.chunk_count is None
    assert len(provider.prompts) == 1


@pytest.mark.asyncio
async def test_routed_flag_with_db_chunks(tmp_path: Path) -> None:
    """chunked + DB -> the chunked path runs."""
    diff = fdiff("src/a.py") + fdiff("src/b.py")
    collector = _collector(tmp_path, diff)
    collector.features = frozenset({"chunked"})
    provider = StubProvider([_LGTM, _LGTM])
    routed = await run_review_routed(provider=provider, collector=collector, skeptic_provider=None)
    assert routed.chunk_count == 2
    assert len(provider.prompts) == 2
