# Guardian Chunked Review Implementation Plan (slice 2 of #154)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the slice-1 chunker into the guardian review loop behind `GUARDIAN_FEATURES=chunked`: per-chunk finder passes, merge + dedup, single skeptic pass, one report — with accurate multi-call token accounting.

**Architecture:** New `src/cgis/guardian/chunked.py` holds the routing entry point (`run_review_routed`) and the chunked orchestration; `core.py` only gets its finder pass extracted into a reusable module-level coroutine. `ContextCollector` gains a per-chunk context method. `BaseProvider` gains cumulative usage accounting (also fixes the existing retry-loses-tokens bug). Spec: `docs/specs/2026-06-11-guardian-chunked-review-design.md`.

**Tech Stack:** Python 3.12, Pydantic v2 (frozen models), structlog, pytest + pytest-asyncio, mypy strict.

**Branch:** `feat/guardian-chunked-review` (already exists; spec committed as a55873a).

**Verification before every commit:** `make format && make lint && make type-check && uv run pytest -q` (doc-coverage at the end).

---

## File map

| File | Change |
|---|---|
| `src/cgis/guardian/providers/base.py` | + `cumulative_usage`, + `_record_usage` |
| `src/cgis/guardian/providers/gemini.py` | use `_record_usage` |
| `src/cgis/guardian/providers/mistral.py` | use `_record_usage` |
| `src/cgis/guardian/core.py` | extract module-level `finder_pass` |
| `src/cgis/guardian/collector.py` | + `"chunked"` flag, `files` param, `_graph_sections` helper, `collect_for_chunk` |
| `src/cgis/guardian/chunked.py` | NEW: `RoutedReview`, `_cap_chunks`, `_dedup`, `run_chunked_review`, `run_review_routed` |
| `src/cgis/guardian/runner.py` | route through `run_review_routed`, report cumulative usage, pass `chunk_count` |
| `src/cgis/guardian/metrics.py` | + `chunk_count` param |
| `scripts/guardian_bench.py` | route through `run_review_routed`, cumulative usage, `chunks` field |
| `tests/unit/test_guardian_chunked.py` | NEW test module |
| `tests/unit/test_guardian_core.py`, `test_guardian_collector.py`, `test_guardian_metrics.py` | added tests |

Existing tests must pass UNMODIFIED except where a task explicitly says otherwise.

---

### Task 1: Cumulative token usage in BaseProvider

Multiple finder calls per review break `last_usage`-based accounting (it only
reflects the final call; the parse-retry already silently drops its first
call's tokens today). Providers route usage through one recording method.

**Files:**
- Modify: `src/cgis/guardian/providers/base.py`
- Modify: `src/cgis/guardian/providers/gemini.py:37-42`
- Modify: `src/cgis/guardian/providers/mistral.py:43-48`
- Test: `tests/unit/test_guardian_core.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_guardian_core.py` (it already has provider tests around line 189; reuse its imports — `ProviderUsage` and `BaseProvider` are already imported there):

```python
def test_provider_cumulative_usage_defaults_to_zero() -> None:
    """cumulative_usage starts at zero, like last_usage."""
    provider = _SequenceProvider([])
    assert provider.cumulative_usage.total_tokens == 0


def test_record_usage_updates_last_and_accumulates() -> None:
    """_record_usage sets last_usage to the new value and sums cumulative_usage."""
    provider = _SequenceProvider([])
    provider._record_usage(ProviderUsage(prompt_tokens=100, completion_tokens=10))
    provider._record_usage(ProviderUsage(prompt_tokens=200, completion_tokens=20))
    assert provider.last_usage.prompt_tokens == 200
    assert provider.last_usage.completion_tokens == 20
    assert provider.cumulative_usage.prompt_tokens == 300
    assert provider.cumulative_usage.completion_tokens == 30
    assert provider.cumulative_usage.total_tokens == 330
```

(`_SequenceProvider` already exists in that file as a BaseProvider stub.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_guardian_core.py -k cumulative_or_record -v` — use `-k "cumulative or record_usage"`.
Expected: FAIL with `AttributeError: ... 'cumulative_usage'`.

- [ ] **Step 3: Implement**

In `src/cgis/guardian/providers/base.py`, replace `BaseProvider.__init__` and add the method:

```python
    def __init__(self) -> None:
        """Initialise usage counters to zero.

        last_usage reflects the most recent LLM call; cumulative_usage sums
        every call this provider instance has made (a chunked review makes
        N finder calls — and even a single-pass review makes 2 on a parse
        retry, whose first call last_usage used to silently drop).
        """
        self.last_usage: ProviderUsage = ProviderUsage()
        self.cumulative_usage: ProviderUsage = ProviderUsage()

    def _record_usage(self, usage: ProviderUsage) -> None:
        """Record one call's token usage: set last_usage, add to cumulative."""
        self.last_usage = usage
        self.cumulative_usage = ProviderUsage(
            prompt_tokens=self.cumulative_usage.prompt_tokens + usage.prompt_tokens,
            completion_tokens=self.cumulative_usage.completion_tokens + usage.completion_tokens,
        )
```

In `src/cgis/guardian/providers/gemini.py`, replace the usage block (lines 37–42):

```python
        meta = getattr(response, "usage_metadata", None)
        if meta is not None:
            self._record_usage(
                ProviderUsage(
                    prompt_tokens=getattr(meta, "prompt_token_count", 0),
                    completion_tokens=getattr(meta, "candidates_token_count", 0),
                )
            )
```

In `src/cgis/guardian/providers/mistral.py`, replace the usage block (lines 43–48):

```python
        usage = getattr(response, "usage", None)
        if usage is not None:
            self._record_usage(
                ProviderUsage(
                    prompt_tokens=getattr(usage, "prompt_tokens", 0),
                    completion_tokens=getattr(usage, "completion_tokens", 0),
                )
            )
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass (new tests included).

- [ ] **Step 5: Commit**

```bash
git add src/cgis/guardian/providers/ tests/unit/test_guardian_core.py
git commit -m "feat(guardian): cumulative token usage on BaseProvider"
```

---

### Task 2: Extract module-level `finder_pass` in core.py

The chunked orchestrator needs the finder pass (structured call → sanitize →
one retry → parse_failed fallback) without duplicating retry logic.

**Files:**
- Modify: `src/cgis/guardian/core.py`
- Test: existing `tests/unit/test_guardian_core.py` (behaviour unchanged) + one new test

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_guardian_core.py`:

```python
@pytest.mark.asyncio
async def test_finder_pass_is_module_level() -> None:
    """finder_pass works standalone, without a GuardianReviewer instance."""
    from cgis.guardian.core import finder_pass

    provider = _SequenceProvider([FINDING_JSON])
    result = await finder_pass(provider, {"diff": "d"})
    assert len(result.findings) == 1
    assert result.findings[0].file == "a.py"


@pytest.mark.asyncio
async def test_finder_retry_accumulates_usage() -> None:
    """A parse retry makes TWO calls; cumulative_usage must count both (spec §4.7)."""
    from cgis.guardian.core import finder_pass

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
```

(If `_SequenceProvider.generate_structured` has a different parameter spelling
in the file, match it; `BaseModel` and `ProviderUsage` are already imported
in this test module.)

(`FINDING_JSON` comes from `guardian_stubs` and is already imported in this file.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_guardian_core.py::test_finder_pass_is_module_level -v`
Expected: FAIL with `ImportError: cannot import name 'finder_pass'`.

- [ ] **Step 3: Implement**

In `src/cgis/guardian/core.py`, move the body of `GuardianReviewer._finder_pass`
into a module-level coroutine placed right after `_sanitize_finder_result`,
and make the method delegate. The prompt builder is stateless — instantiate
it inside the function:

```python
async def finder_pass(provider: BaseProvider, context: dict[str, str]) -> ReviewResult:
    """Run the finder (pass 1) with parse-retry semantics.

    Parse policy (spec §2.3): one retry with the validation error appended;
    on a second failure the raw text becomes the summary with parse_failed=True.
    Module-level so the chunked orchestrator reuses it per chunk (slice 2).
    """
    builder = PromptBuilder()
    system_prompt = builder.build_system_prompt()
    user_prompt = builder.build_user_prompt(context)
    raw = await provider.generate_structured(system_prompt, user_prompt, ReviewResult)
    try:
        return _sanitize_finder_result(ReviewResult.model_validate_json(extract_json(raw)))
    except ValidationError as exc:
        log.warning(
            "Structured output failed validation; retrying once.",
            validation_error=str(exc),
        )
        retry_prompt = user_prompt + _RETRY_SUFFIX.format(error=exc)
        raw = await provider.generate_structured(system_prompt, retry_prompt, ReviewResult)
        try:
            return _sanitize_finder_result(ReviewResult.model_validate_json(extract_json(raw)))
        except ValidationError:
            log.exception("Structured output failed twice; falling back to raw text.")
            return ReviewResult(findings=[], summary=raw, parse_failed=True)
```

Then in `GuardianReviewer`:
- delete `self.prompt_builder = PromptBuilder()` from `__init__` ONLY IF nothing
  else references it — grep first: `grep -rn "prompt_builder" src tests`. If
  tests reference it, keep the attribute.
- replace `_finder_pass` with:

```python
    async def _finder_pass(self, context: dict[str, str]) -> ReviewResult:
        """Delegate to the module-level finder_pass (kept for call-site stability)."""
        return await finder_pass(self.provider, context)
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest tests/unit/test_guardian_core.py -q`
Expected: all pass — the retry/fallback tests exercise the moved body through `run_review`.

- [ ] **Step 5: Commit**

```bash
git add src/cgis/guardian/core.py tests/unit/test_guardian_core.py
git commit -m "refactor(guardian): extract module-level finder_pass for reuse"
```

---

### Task 3: Collector — `chunked` flag, file-scoped collection, `collect_for_chunk`

**Files:**
- Modify: `src/cgis/guardian/collector.py`
- Test: `tests/unit/test_guardian_collector.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_guardian_collector.py`. Check the file's existing
imports first and add what is missing among these:

```python
from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.guardian.chunker import Chunk
from cgis.guardian.collector import ContextCollector, parse_features
from cgis.storage.sqlite_store import SQLiteStore
```

```python
def test_parse_features_accepts_chunked() -> None:
    """'chunked' is a valid feature name."""
    assert parse_features("chunked") == frozenset({"chunked"})


def test_collect_for_chunk_diff_is_chunk_diff(tmp_path: Path) -> None:
    """The chunk's own diff slice rides in the context, not the full PR diff."""
    collector = ContextCollector(project_root=tmp_path)
    chunk = Chunk(files=("src/a.py",), diff="diff --git a/src/a.py b/src/a.py\n+x\n")
    context = collector.collect_for_chunk(chunk)
    assert context["diff"] == chunk.diff


def test_collect_for_chunk_full_files_restricted_to_chunk(tmp_path: Path) -> None:
    """Only the chunk's .py files appear in full_files — not other changed files."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("a = 1\n")
    (tmp_path / "src" / "b.py").write_text("b = 2\n")
    collector = ContextCollector(project_root=tmp_path)
    chunk = Chunk(files=("src/a.py", "README.md"), diff="d")
    context = collector.collect_for_chunk(chunk)
    assert "src/a.py" in context["full_files"]
    assert "src/b.py" not in context["full_files"]


def test_collect_for_chunk_graph_and_stats_accumulate(tmp_path: Path) -> None:
    """Graph context is chunk-scoped (flow fallback ON) and stats sum across chunks."""
    db = tmp_path / "graph.db"
    nodes = [
        Node(id="pkg.mod", type=NodeType.MODULE, name="mod",
             file_path="pkg/mod.py", start_line=1, end_line=1),
        Node(id="pkg.other", type=NodeType.MODULE, name="other",
             file_path="pkg/other.py", start_line=1, end_line=1),
    ]
    edges = [Edge(id="e1", source="pkg.other", target="pkg.mod", type=EdgeType.CALLS)]
    with SQLiteStore(str(db)) as store:
        store.save_graph(nodes, edges, overwrite=True)
    collector = ContextCollector(project_root=tmp_path, db_path=db, source_root="src")
    ctx1 = collector.collect_for_chunk(Chunk(files=("src/pkg/mod.py",), diff="d1"))
    assert "graph_context" in ctx1  # impact graph: pkg.other -> pkg.mod
    collector.collect_for_chunk(Chunk(files=("src/pkg/ghost.py",), diff="d2"))
    assert collector.graph_stats["total"] == 2  # accumulated, not overwritten
    assert collector.graph_stats["with_graph"] == 1


def test_collect_graph_context_unchanged_without_db(tmp_path: Path) -> None:
    """Regression: the global path still leaves stats at zero when db is missing."""
    collector = ContextCollector(project_root=tmp_path)
    assert collector.collect_graph_context() == ""
    assert collector.graph_stats == {"total": 0, "with_graph": 0, "flow_fallback": 0}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_guardian_collector.py -k "chunked or for_chunk or unchanged_without_db" -v`
Expected: `test_parse_features_accepts_chunked` FAILS with ValueError mention; `collect_for_chunk` tests FAIL with AttributeError.

- [ ] **Step 3: Implement**

In `src/cgis/guardian/collector.py`:

1. `VALID_FEATURES = frozenset({"full_files", "flow", "drift", "chunked"})`

2. Add the import: `from cgis.guardian.chunker import Chunk` (no cycle:
   chunker does not import collector).

3. `collect_full_files` gains a parameter — change the signature and first line:

```python
    def collect_full_files(self, files: list[str] | None = None) -> str:
        """Full HEAD text of given (default: changed) .py files, smallest-first under budgets.

        Per-file cap ~1200 lines and a global ~120K-char budget; omitted files get
        an explicit note so the model never reads absence-of-file as absence-of-code.
        In chunked mode the budget applies per chunk (spec §4.2).
        """
        changed = files if files is not None else self.get_changed_py_files()
```

(rest of the body unchanged.)

4. Extract the graph loop into a helper returning sections + LOCAL stats
   (no self.graph_stats writes), with an explicit flow switch:

```python
    def _graph_sections(
        self, changed_files: list[str], *, flow: bool
    ) -> tuple[list[str], dict[str, int]]:
        """Impact-graph Mermaid sections + local stats for the given files.

        Pure with respect to self.graph_stats — callers decide whether to
        overwrite (global path) or accumulate (per-chunk path).
        """
        stats = {"total": 0, "with_graph": 0, "flow_fallback": 0}
        if self.db_path is None or not self.db_path.exists() or not changed_files:
            return [], stats
        stats["total"] = len(changed_files)
        compiler = MermaidCompiler()
        sections: list[str] = []
        with SQLiteStore(str(self.db_path)) as store:
            engine = QueryEngine(store)
            for rel_path in changed_files:
                module_fqn = file_path_to_module_fqn(rel_path, self.source_root)
                nodes, edges = engine.get_impact_graph(module_fqn, max_depth=2)
                title = "Impact graph"
                if not nodes and flow:
                    # New file: nothing references it yet (#94) — show what it calls.
                    nodes, edges = engine.get_flow_graph(module_fqn, max_depth=2)
                    title = "Dependency graph (outbound)"
                    if nodes:
                        stats["flow_fallback"] += 1
                if not nodes:
                    log.debug("No impact graph for module", fqn=module_fqn)
                    continue
                mermaid = compiler.compile(nodes, edges)
                sections.append(f"#### {title} for `{module_fqn}`:\n```mermaid\n{mermaid}\n```")
        stats["with_graph"] = len(sections)
        return sections, stats
```

5. Rewrite `collect_graph_context` on top of it, preserving today's
   observable behaviour exactly (early return without touching stats when no
   db; warning when nothing found):

```python
    def collect_graph_context(self) -> str:
        """Query graph.db for impact graphs of changed files; return Mermaid blocks."""
        if self.db_path is None or not self.db_path.exists():
            return ""
        changed_files = self.get_changed_py_files()
        if not changed_files:
            return ""
        sections, stats = self._graph_sections(changed_files, flow="flow" in self.features)
        self.graph_stats = stats
        if stats["total"] > 0 and stats["with_graph"] == 0:
            log.warning(
                "No graph context found for any changed file.",
                changed_files=stats["total"],
                project_root=str(self.project_root),
            )
        return "\n\n".join(sections)
```

6. Add `collect_for_chunk` (after `collect_all`):

```python
    def collect_for_chunk(self, chunk: Chunk) -> dict[str, str]:
        """Per-chunk context: the chunk's diff, full files, and impact graphs (spec §4.2).

        chunked implies per-chunk full_files, graph context, AND the flow
        fallback — each chunk gets a small, complete world. graph_stats
        ACCUMULATE across chunks so the footer coverage stays truthful.
        """
        py_files = [f for f in chunk.files if f.endswith(".py")]
        context: dict[str, str] = {
            "diff": chunk.diff,
            "contributing": self.read_file("CONTRIBUTING.md"),
            "ontology": self.read_file("docs/ontology/core.yaml"),
        }
        sections, stats = self._graph_sections(py_files, flow=True)
        for key, value in stats.items():
            self.graph_stats[key] = self.graph_stats.get(key, 0) + value
        if sections:
            context["graph_context"] = "\n\n".join(sections)
        full_files = self.collect_full_files(py_files)
        if full_files:
            context["full_files"] = full_files
        return context
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass, including ALL existing collector tests unmodified.

- [ ] **Step 5: Commit**

```bash
git add src/cgis/guardian/collector.py tests/unit/test_guardian_collector.py
git commit -m "feat(guardian): chunked feature flag + per-chunk context collection"
```

---

### Task 4: `chunked.py` — pure parts (`RoutedReview`, `_cap_chunks`, `_dedup`)

**Files:**
- Create: `src/cgis/guardian/chunked.py`
- Create: `tests/unit/test_guardian_chunked.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_guardian_chunked.py`:

```python
"""Unit tests for chunked review orchestration (spec: 2026-06-11-guardian-chunked-review)."""

from cgis.guardian.chunked import MAX_CHUNKS, RoutedReview, _cap_chunks, _dedup
from cgis.guardian.chunker import Chunk
from cgis.guardian.findings import Finding, ReviewResult


def _finding(file: str = "a.py", line: int | None = 1, category: str = "logic",
             confidence: int = 90, title: str = "t") -> Finding:
    """Minimal finding for merge/dedup tests."""
    return Finding(file=file, line=line, severity="major", category=category,
                   title=title, evidence="e", problem="p", fix="f", confidence=confidence)


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_guardian_chunked.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cgis.guardian.chunked'`.

- [ ] **Step 3: Implement**

Create `src/cgis/guardian/chunked.py`:

```python
"""Chunked review: per-chunk finder passes behind GUARDIAN_FEATURES=chunked.

Slice 2 of #154 (spec: 2026-06-11-guardian-chunked-review-design.md). The
finder LGTMs large PRs (attention dilution); each chunk gets a small,
complete world instead — its own diff, full files, and impact graph.
"""

import structlog
from pydantic import BaseModel

from cgis.guardian.chunker import Chunk
from cgis.guardian.findings import Finding, ReviewResult

log = structlog.getLogger(__name__)

MAX_CHUNKS = 8


class RoutedReview(BaseModel, frozen=True):
    """Review outcome plus chunk accounting (chunk_count=None = single-pass path)."""

    result: ReviewResult
    chunk_count: int | None = None


def _cap_chunks(chunks: list[Chunk]) -> list[Chunk]:
    """Bound API calls (spec §4.3): keep the MAX_CHUNKS-1 largest, merge the rest.

    The overflow chunk goes last; kept chunks stay in slice-1 order (sorted
    by first file). Ties in size break by first file name — deterministic.
    """
    if len(chunks) <= MAX_CHUNKS:
        return chunks
    ranked = sorted(chunks, key=lambda c: (-len(c.diff), c.files[0]))
    keep, rest = ranked[: MAX_CHUNKS - 1], ranked[MAX_CHUNKS - 1 :]
    rest_sorted = sorted(rest, key=lambda c: c.files[0])
    overflow = Chunk(
        files=tuple(sorted({f for c in rest_sorted for f in c.files})),
        diff="".join(c.diff for c in rest_sorted),
    )
    log.warning("Chunk cap hit; smallest chunks merged.", merged=len(rest), cap=MAX_CHUNKS)
    return [*sorted(keep, key=lambda c: c.files[0]), overflow]


def _dedup(findings: list[Finding]) -> list[Finding]:
    """Drop duplicate (file, line, category) findings, keeping the higher confidence.

    Cross-chunk duplicates are impossible after the per-chunk file filter
    (chunks partition files) — this is insurance against intra-pass
    duplicates. First-occurrence order is preserved.
    """
    best: dict[tuple[str, int | None, str], Finding] = {}
    order: list[tuple[str, int | None, str]] = []
    for finding in findings:
        key = (finding.file, finding.line, finding.category)
        if key not in best:
            best[key] = finding
            order.append(key)
        elif finding.confidence > best[key].confidence:
            best[key] = finding
    return [best[k] for k in order]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_guardian_chunked.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cgis/guardian/chunked.py tests/unit/test_guardian_chunked.py
git commit -m "feat(guardian): chunked review pure parts — cap and dedup"
```

---

### Task 5: `run_chunked_review` orchestration

**Files:**
- Modify: `src/cgis/guardian/chunked.py`
- Test: `tests/unit/test_guardian_chunked.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_guardian_chunked.py`. New imports needed at top:

```python
import json
from pathlib import Path

import pytest
from guardian_stubs import StubProvider
from pydantic import BaseModel

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.guardian.chunked import run_chunked_review
from cgis.guardian.collector import ContextCollector
from cgis.guardian.providers.base import BaseProvider
from cgis.storage.sqlite_store import SQLiteStore
```

Helpers and tests:

```python
def fdiff(path: str, body: str = "+x = 1") -> str:
    """One minimal single-hunk diff block for `path` (same as chunker tests)."""
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -0,0 +1 @@\n{body}\n"


def _finder_json(file: str, summary: str = "ok") -> str:
    """Canned finder response with one finding in `file`."""
    return json.dumps({
        "findings": [{"file": file, "line": 1, "severity": "major", "category": "logic",
                      "title": f"bug in {file}", "evidence": "e", "problem": "p",
                      "fix": "f", "confidence": 90}],
        "summary": summary,
    })


_LGTM = '{"findings": [], "summary": "clean"}'
_CONFIRM_ALL = (
    '{"verdicts": [{"finding_index": 0, "verdict": "confirmed", "rationale": "r"}]}'
)


def _collector(tmp_path: Path, diff: str, *, with_db: bool = True) -> ContextCollector:
    """Collector with a stubbed diff and a real (empty) graph DB."""
    db = tmp_path / "graph.db"
    if with_db:
        with SQLiteStore(str(db)) as store:
            store.save_graph([], [], overwrite=True)
    collector = ContextCollector(
        project_root=tmp_path, db_path=db if with_db else None, source_root="src"
    )
    collector._diff_cache = diff  # bypass git
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_guardian_chunked.py -v`
Expected: new tests FAIL with `ImportError: cannot import name 'run_chunked_review'`.

- [ ] **Step 3: Implement**

Add to `src/cgis/guardian/chunked.py` (new imports at top of file):

```python
from cgis.guardian.chunker import Chunk, build_chunks
from cgis.guardian.collector import ContextCollector
from cgis.guardian.core import finder_pass
from cgis.guardian.findings import Finding, ReviewResult, extract_json
from cgis.guardian.providers.base import BaseProvider
from cgis.guardian.skeptic import (
    SKEPTIC_SYSTEM_PROMPT,
    SkepticResult,
    apply_verdicts,
    build_skeptic_prompt,
)
from cgis.storage.sqlite_store import SQLiteStore
```

and the orchestrator:

```python
async def run_chunked_review(
    *,
    provider: BaseProvider,
    collector: ContextCollector,
    skeptic_provider: BaseProvider | None,
) -> RoutedReview:
    """Per-chunk finder passes -> filter -> merge -> dedup -> one skeptic pass.

    Degradations (spec §5): a chunk whose finder call raises or never parses
    contributes zero findings and a ⚠ bullet; parse_failed on the merged
    result only when EVERY chunk failed; skeptic failure returns the merged
    findings unverified.
    """
    diff = collector.get_git_diff()
    if collector.db_path is None:  # routed guard (§4.1) — belt and braces
        _msg = "run_chunked_review requires a graph DB"
        raise RuntimeError(_msg)
    with SQLiteStore(str(collector.db_path)) as store:
        chunks = build_chunks(diff, store, source_root=collector.source_root)
    if not chunks:
        return RoutedReview(
            result=ReviewResult(findings=[], summary="Empty diff — nothing to review."),
            chunk_count=0,
        )
    chunks = _cap_chunks(chunks)

    bullets: list[str] = []
    kept: list[Finding] = []
    finding_contexts: list[dict[str, str]] = []
    failed = 0
    for chunk in chunks:
        label = ", ".join(chunk.files)
        context = collector.collect_for_chunk(chunk)
        try:
            result = await finder_pass(provider, context)
        except Exception:
            log.warning("Chunk finder call failed; chunk skipped.",
                        files=chunk.files, exc_info=True)
            failed += 1
            bullets.append(f"- [{label}]: ⚠ finder call failed")
            continue
        if result.parse_failed:
            failed += 1
            bullets.append(f"- [{label}]: ⚠ finder output unparsable")
            continue
        allowed = set(chunk.files)
        survivors = [f for f in result.findings if f.file in allowed]
        for dropped in (f for f in result.findings if f.file not in allowed):
            log.warning("Out-of-chunk finding dropped.", file=dropped.file, title=dropped.title)
        if survivors:
            finding_contexts.append(context)
        kept.extend(survivors)
        bullets.append(f"- [{label}]: {result.summary}")

    merged = ReviewResult(
        findings=_dedup(kept),
        summary="\n".join(bullets),
        parse_failed=failed == len(chunks),
    )
    if skeptic_provider is None or not merged.findings or merged.parse_failed:
        return RoutedReview(result=merged, chunk_count=len(chunks))

    # ONE skeptic pass over chunks that produced findings — not the full PR
    # diff: attention dilution hits the skeptic too (spec §4.5).
    skeptic_context = {
        "diff": "\n".join(c["diff"] for c in finding_contexts),
        "full_files": "\n\n".join(
            c["full_files"] for c in finding_contexts if "full_files" in c
        ),
    }
    try:
        raw = await skeptic_provider.generate_structured(
            SKEPTIC_SYSTEM_PROMPT,
            build_skeptic_prompt(skeptic_context, merged.findings),
            SkepticResult,
        )
        verdicts = SkepticResult.model_validate_json(extract_json(raw))
    except Exception:
        log.warning("Skeptic pass failed; returning unverified findings.", exc_info=True)
        return RoutedReview(
            result=merged.model_copy(update={"skeptic_status": "failed"}),
            chunk_count=len(chunks),
        )
    verified = apply_verdicts(merged.findings, verdicts)
    return RoutedReview(
        result=merged.model_copy(update={"findings": verified, "skeptic_status": "ok"}),
        chunk_count=len(chunks),
    )
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/cgis/guardian/chunked.py tests/unit/test_guardian_chunked.py
git commit -m "feat(guardian): chunked review orchestration with single skeptic pass"
```

---

### Task 6: `run_review_routed` + runner integration + metrics

**Files:**
- Modify: `src/cgis/guardian/chunked.py`
- Modify: `src/cgis/guardian/runner.py:106-161`
- Modify: `src/cgis/guardian/metrics.py:11-45`
- Test: `tests/unit/test_guardian_chunked.py`, `tests/unit/test_guardian_metrics.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_guardian_chunked.py` (add `from cgis.guardian.chunked import run_review_routed` to its imports):

```python
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
    routed = await run_review_routed(
        provider=provider, collector=collector, skeptic_provider=None
    )
    assert routed.chunk_count is None
    assert len(provider.prompts) == 1


@pytest.mark.asyncio
async def test_routed_flag_with_db_chunks(tmp_path: Path) -> None:
    """chunked + DB -> the chunked path runs."""
    diff = fdiff("src/a.py") + fdiff("src/b.py")
    collector = _collector(tmp_path, diff)
    collector.features = frozenset({"chunked"})
    provider = StubProvider([_LGTM, _LGTM])
    routed = await run_review_routed(
        provider=provider, collector=collector, skeptic_provider=None
    )
    assert routed.chunk_count == 2
    assert len(provider.prompts) == 2
```

Append to `tests/unit/test_guardian_metrics.py` (check its existing imports — it
already imports `record_review` and uses `tmp_path`):

```python
def test_record_review_chunk_count(tmp_path: Path) -> None:
    """chunk_count rides in the entry; defaults to None for single-pass."""
    path = tmp_path / "m.jsonl"
    record_review(model="m", pr=1, prompt_tokens=1, completion_tokens=1,
                  findings_total=0, lgtm=True, chunk_count=3, metrics_path=path)
    record_review(model="m", pr=2, prompt_tokens=1, completion_tokens=1,
                  findings_total=0, lgtm=True, metrics_path=path)
    entries = [json.loads(line) for line in path.read_text().splitlines()]
    assert entries[0]["chunk_count"] == 3
    assert entries[1]["chunk_count"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_guardian_chunked.py tests/unit/test_guardian_metrics.py -k "routed or chunk_count" -v`
Expected: ImportError on `run_review_routed`; TypeError on `chunk_count`.

- [ ] **Step 3: Implement routing**

Add to `src/cgis/guardian/chunked.py` (import `GuardianReviewer` from
`cgis.guardian.core` alongside `finder_pass`):

```python
async def run_review_routed(
    *,
    provider: BaseProvider,
    collector: ContextCollector,
    skeptic_provider: BaseProvider | None,
) -> RoutedReview:
    """Single entry point for runner and bench: chunked vs single-pass (spec §4.1).

    chunked without a graph DB falls back to single pass: build_chunks would
    degrade to all-isolated chunks = one API call per file with zero
    connectivity benefit — strictly worse than the status quo.
    """
    chunked = "chunked" in collector.features
    if chunked and (collector.db_path is None or not collector.db_path.exists()):
        log.warning("chunked requested but no graph DB; falling back to single pass.")
        chunked = False
    if not chunked:
        reviewer = GuardianReviewer(
            provider=provider,
            context_collector=collector,
            skeptic_provider=skeptic_provider,
        )
        return RoutedReview(result=await reviewer.run_review(), chunk_count=None)
    return await run_chunked_review(
        provider=provider, collector=collector, skeptic_provider=skeptic_provider
    )
```

- [ ] **Step 4: Implement metrics param**

In `src/cgis/guardian/metrics.py`, `record_review` gains a keyword param after
`skeptic_status`:

```python
    skeptic_status: str = "off",
    chunk_count: int | None = None,
    metrics_path: Path = _DEFAULT_METRICS_FILE,
```

and the entry dict gains, after `"skeptic_status"`:

```python
        "chunk_count": chunk_count,
```

- [ ] **Step 5: Rewire the runner**

In `src/cgis/guardian/runner.py`:

- Replace the import of `GuardianReviewer`:
  `from cgis.guardian.core import GuardianReviewer` →
  `from cgis.guardian.chunked import run_review_routed`
- In `run_guardian`, replace the reviewer construction + `run_review()` call
  (lines 122–127) with:

```python
    routed = await run_review_routed(
        provider=provider,
        collector=collector,
        skeptic_provider=skeptic[0] if skeptic else None,
    )
    result = routed.result
    report = render_report(result)
```

- In the `record_review(...)` call: replace
  `prompt_tokens=provider.last_usage.prompt_tokens` →
  `prompt_tokens=provider.cumulative_usage.prompt_tokens`, same for
  `completion_tokens`, and add `chunk_count=routed.chunk_count,`.
- In the footer line: `usage=provider.last_usage` →
  `usage=provider.cumulative_usage`.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass — runner tests exercise the routed single-pass path
(StubProvider never records usage, so cumulative == last == zero there).

- [ ] **Step 7: Commit**

```bash
git add src/cgis/guardian/chunked.py src/cgis/guardian/runner.py \
        src/cgis/guardian/metrics.py tests/unit/
git commit -m "feat(guardian): route reviews through run_review_routed; cumulative usage in metrics"
```

---

### Task 7: Bench integration

**Files:**
- Modify: `scripts/guardian_bench.py:74-130`
- Test: manual smoke (script has no unit tests by convention — it is a thin
  wrapper over tested modules)

- [ ] **Step 1: Implement**

In `scripts/guardian_bench.py`:

- Replace `from cgis.guardian.core import GuardianReviewer` with
  `from cgis.guardian.chunked import run_review_routed`.
- In `_run_one`, replace the reviewer construction + call (lines 98–103) with:

```python
            routed = await run_review_routed(
                provider=provider,
                collector=collector,
                skeptic_provider=skeptic[0] if skeptic else None,
            )
            result = routed.result
```

- In the `entry` dict: replace
  `"prompt_tokens": provider.last_usage.prompt_tokens` →
  `"prompt_tokens": provider.cumulative_usage.prompt_tokens` (same for
  completion), and add after `"parse_failed"`:

```python
        "chunks": routed.chunk_count,
```

- [ ] **Step 2: Smoke-check imports and types**

Run: `make lint && make type-check && uv run python scripts/guardian_bench.py --help`
Expected: lint/type clean; `--help` prints usage (proves imports resolve).

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add scripts/guardian_bench.py
git commit -m "feat(guardian): bench replays route through run_review_routed"
```

---

### Task 8: Full verification + doc coverage

**Files:** none new.

- [ ] **Step 1: Full gate**

Run: `make format && make lint && make type-check && uv run pytest -q && make doc-coverage`
Expected: format no changes, lint clean, mypy clean, all tests pass, doc coverage ≥ 90%.

- [ ] **Step 2: Regression sanity vs spec §3.2**

Run: `uv run pytest tests/unit/test_guardian_core.py tests/unit/test_guardian_runner.py tests/unit/test_guardian_collector.py -q`
Expected: pass — confirms the unchunked path is behaviourally unchanged.

- [ ] **Step 3: Commit anything format touched**

```bash
git status --short   # if clean, skip
git add -u && git commit -m "chore(guardian): formatting"
```

---

## Controller tasks (NOT for implementation subagents)

**Task 9 — PR + reviews:** push `feat/guardian-chunked-review`
(`git push origin feat/guardian-chunked-review`), open PR referencing #154
slice 2, run guardian + gemini review cycles. Squash-merge ONLY on explicit
user confirmation.

**Task 10 — Benchmark phase 1 (gate, spec §7):** after merge (or on the
branch with worktree replay — bench checks out PR heads, the guardian CODE
under test is the local checkout):
`set -a; . ./.env; set +a; GUARDIAN_PROVIDER=gemini GUARDIAN_MODEL=gemini-2.5-flash GUARDIAN_SKEPTIC=gemini GUARDIAN_SKEPTIC_MODEL=gemini-3.5-flash GUARDIAN_FEATURES=chunked uv run python scripts/guardian_bench.py --runs 3`
Gate: mean recall ≥ 0.27 AND mean recall {140,143,144} > 0 AND noise/PR ≤ 1.5
AND no PR drops > 0.05 below its baseline. Abort if spend hits 6 PLN.

**Task 11 — Benchmark phase 2 (model matrix, only if phase 1 passes):**
same command with `GUARDIAN_MODEL=gemini-3.5-flash` then
`GUARDIAN_MODEL=gemini-3.1-pro`, `--pr 140 --pr 143 --pr 144 --runs 2`.
Switch prod finder model only if a candidate beats 2.5-flash×chunked
large-PR mean recall by ≥ 0.05 with noise ≤ 1.5.

**Task 12 — Prod flag:** set repo var `GUARDIAN_FEATURES=chunked` ONLY after
the phase-1 gate passes and the PR is merged. Update memory.
