# Guardian Context + Skeptic + Inline Comments Implementation Plan (Plan 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement spec §§4–6 of `docs/specs/2026-06-10-guardian-sprint-design.md` — context-collector upgrades behind ablation flags, the cross-provider skeptic pass, and inline PR comments — each measured against the committed baseline (gemini recall 0.22 / precision 0.61 / noise 0.94/PR in `benchmarks/guardian/results.jsonl`).

**Architecture:** Three independent layers on top of the merged structured-findings contract: (1) `ContextCollector` gains a `features` frozenset gating three new context sections; (2) a pure `skeptic.py` module merges second-provider verdicts into frozen `Finding` copies; (3) a pure diff-line indexer plus a `gh api` poster deliver findings as one GitHub review. Every LLM-touching step degrades gracefully — guardian never fails CI.

**Tech Stack:** Python 3.12, Pydantic v2 (frozen models), structlog, pytest, `gh` CLI (subprocess, mocked in tests). No new dependencies.

**Benchmark gates (spec §9):** context upgrades = ablation runs vs baseline; skeptic = noise↓ with at most ONE lost ground-truth match across the whole set; inline = no gate (delivery only).

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `src/cgis/guardian/collector.py` | modify | `features` flag set; `collect_full_files()`; flow-fallback; `collect_drift()` |
| `src/cgis/guardian/prompts.py` | modify | prompt sections for full files + drift |
| `src/cgis/guardian/skeptic.py` | create | `SkepticVerdict`/`SkepticResult`, skeptic prompt, `apply_verdicts()`, `visible_findings()` |
| `src/cgis/guardian/findings.py` | modify | `ReviewResult.skeptic_status` field |
| `src/cgis/guardian/core.py` | modify | optional skeptic pass in `run_review()` |
| `src/cgis/guardian/render.py` | modify | hide refuted findings; `render_inline_comment()`; `render_review_body()` |
| `src/cgis/guardian/diff_index.py` | create | pure `diff_line_index()` hunk parser |
| `src/cgis/guardian/github_poster.py` | create | `build_review()` (pure) + `post_inline_review()` (gh subprocess) |
| `src/cgis/guardian/runner.py` | modify | `build_skeptic_provider()`; features env parsing; inline posting in `run_guardian` |
| `scripts/guardian_review.py` | modify | `--inline` flag, `GITHUB_OUTPUT` flag, report fallback |
| `scripts/guardian_bench.py` | modify | features + skeptic honored in replays |
| `.github/workflows/guardian.yml` | modify | conditional peter-evans fallback |
| `tests/unit/test_guardian_collector.py` | modify | feature flags, full-file caps, flow-fallback, drift section |
| `tests/unit/test_guardian_skeptic.py` | create | verdict merge logic |
| `tests/unit/test_guardian_diff_index.py` | create | hunk parsing |
| `tests/unit/test_guardian_poster.py` | create | payload correctness, mocked subprocess |
| `tests/unit/test_guardian_render.py` | modify | refuted hidden, inline/body renders |
| `tests/unit/test_guardian_core.py` | modify | skeptic pass orchestration |
| `tests/unit/test_guardian_runner.py` | modify | skeptic provider selection, FakeProvider smoke test |

**Branch:** `feat/guardian-context-skeptic-inline` from `main`.

**Conventions (verbatim from this repo):**
- All models frozen Pydantic; updates via `model_copy(update={...})`.
- mypy strict: full annotations everywhere, including tests.
- Error messages assigned to `_msg` before `raise` (TRY003 style used here).
- Commits end with `Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>`.
- Verification before every commit: `make format && make lint && make type-check && make pytest && make doc-coverage`.
- The line-number gate for findings is `ge=1`, never `gt=0` — gemini's Schema rejects `exclusiveMinimum`.

---

### Task 1: Feature flags on ContextCollector

**Files:**
- Modify: `src/cgis/guardian/collector.py`
- Modify: `src/cgis/guardian/runner.py` (export parse helper usage comes later; flag parsing lives in collector module)
- Test: `tests/unit/test_guardian_collector.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_guardian_collector.py`:

```python
def test_parse_features_valid_and_empty() -> None:
    """parse_features splits, strips, validates; empty string means no features."""
    from cgis.guardian.collector import parse_features

    assert parse_features("") == frozenset()
    assert parse_features("full_files, drift") == frozenset({"full_files", "drift"})


def test_parse_features_unknown_raises() -> None:
    """An unknown feature name fails loud — silent typos would skew ablations."""
    from cgis.guardian.collector import parse_features

    with pytest.raises(ValueError, match="Unknown GUARDIAN_FEATURES"):
        parse_features("full_files,typo")


def test_collector_default_features_empty(tmp_path: Path) -> None:
    """Default ContextCollector has no features enabled (baseline behavior)."""
    collector = ContextCollector(project_root=tmp_path)
    assert collector.features == frozenset()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_guardian_collector.py -k features -v`
Expected: FAIL with `ImportError: cannot import name 'parse_features'`

- [ ] **Step 3: Implement**

In `src/cgis/guardian/collector.py`, after the `log = structlog.getLogger(__name__)` line add:

```python
VALID_FEATURES = frozenset({"full_files", "flow", "drift"})


def parse_features(raw: str) -> frozenset[str]:
    """Parse a GUARDIAN_FEATURES value ('full_files,flow,drift') into a validated set.

    Raises ValueError on unknown names: a typo silently disabling an ablation
    arm would corrupt the benchmark comparison.
    """
    items = {item.strip() for item in raw.split(",") if item.strip()}
    unknown = items - VALID_FEATURES
    if unknown:
        _msg = f"Unknown GUARDIAN_FEATURES: {sorted(unknown)}; valid: {sorted(VALID_FEATURES)}"
        raise ValueError(_msg)
    return frozenset(items)
```

Extend `ContextCollector.__init__` signature with `features: frozenset[str] = frozenset(),` (after `source_root`), store `self.features = features`, and append to the docstring: `features gates the optional context sections (spec §4): "full_files", "flow", "drift".`

- [ ] **Step 4: Run tests, full gates, commit**

Run: `uv run pytest tests/unit/test_guardian_collector.py -v` → all PASS.
Run: `make format && make lint && make type-check && make pytest && make doc-coverage`

```bash
git add src/cgis/guardian/collector.py tests/unit/test_guardian_collector.py
git commit -m "feat(guardian): feature flag plumbing on ContextCollector

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 2: Full file contents section (spec §4.1, #126)

**Files:**
- Modify: `src/cgis/guardian/collector.py`
- Modify: `src/cgis/guardian/prompts.py`
- Test: `tests/unit/test_guardian_collector.py`, `tests/unit/test_guardian_core.py` (prompt assertion lives near existing prompt tests — check where `build_user_prompt` is tested; if absent, add to `tests/unit/test_guardian_core.py`)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_guardian_collector.py`:

```python
def test_collect_full_files_reads_changed_files(tmp_path: Path) -> None:
    """Full HEAD text of each changed .py file appears in a fenced block."""
    (tmp_path / "small.py").write_text("x = 1\n")
    collector = ContextCollector(project_root=tmp_path, features=frozenset({"full_files"}))
    with patch.object(collector, "get_changed_py_files", return_value=["small.py"]):
        result = collector.collect_full_files()
    assert "#### `small.py`" in result
    assert "x = 1" in result


def test_collect_full_files_per_file_line_cap(tmp_path: Path) -> None:
    """A file over the per-file line cap is omitted with an explicit note."""
    (tmp_path / "big.py").write_text("x = 1\n" * 1300)
    collector = ContextCollector(project_root=tmp_path, features=frozenset({"full_files"}))
    with patch.object(collector, "get_changed_py_files", return_value=["big.py"]):
        result = collector.collect_full_files()
    assert "file omitted: too large (big.py)" in result
    assert "```python" not in result


def test_collect_full_files_global_budget_smallest_first(tmp_path: Path) -> None:
    """The global char budget fills smallest-first; the overflow file gets a note."""
    (tmp_path / "tiny.py").write_text("a = 1\n")
    (tmp_path / "mid.py").write_text("# pad\n" * 25_000)  # ~150K chars, under line cap? no:
    # 25_000 lines exceeds the 1200-line cap — use long lines instead:
    (tmp_path / "mid.py").write_text(("y" * 200 + "\n") * 1000)  # ~201K chars, 1000 lines
    collector = ContextCollector(project_root=tmp_path, features=frozenset({"full_files"}))
    with patch.object(collector, "get_changed_py_files", return_value=["mid.py", "tiny.py"]):
        result = collector.collect_full_files()
    assert "#### `tiny.py`" in result
    assert "file omitted: budget exhausted (mid.py)" in result


def test_collect_full_files_skips_deleted(tmp_path: Path) -> None:
    """A changed file that no longer exists on HEAD (deleted) is skipped silently."""
    collector = ContextCollector(project_root=tmp_path, features=frozenset({"full_files"}))
    with patch.object(collector, "get_changed_py_files", return_value=["gone.py"]):
        assert collector.collect_full_files() == ""


def test_collect_all_full_files_gated_by_feature(tmp_path: Path) -> None:
    """collect_all adds 'full_files' only when the feature flag is on."""
    (tmp_path / "a.py").write_text("z = 3\n")
    base = {"get_git_diff": "diff", "read_file": "content"}
    off = ContextCollector(project_root=tmp_path)
    on = ContextCollector(project_root=tmp_path, features=frozenset({"full_files"}))
    for collector in (off, on):
        with (
            patch.object(collector, "get_git_diff", return_value=base["get_git_diff"]),
            patch.object(collector, "read_file", return_value=base["read_file"]),
            patch.object(collector, "get_changed_py_files", return_value=["a.py"]),
        ):
            context = collector.collect_all()
        assert ("full_files" in context) == (collector is on)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_guardian_collector.py -k full_files -v`
Expected: FAIL with `AttributeError: ... has no attribute 'collect_full_files'`

- [ ] **Step 3: Implement collector method**

In `src/cgis/guardian/collector.py` add module constants after `VALID_FEATURES`:

```python
_MAX_FILE_LINES = 1200
_MAX_TOTAL_CHARS = 120_000
```

Add method to `ContextCollector` (after `read_file`):

```python
def collect_full_files(self) -> str:
    """Full HEAD text of changed .py files, smallest-first under budgets (spec §4.1).

    Per-file cap ~1200 lines and a global ~120K-char budget; omitted files get
    an explicit note so the model never reads absence-of-file as absence-of-code.
    """
    changed = self.get_changed_py_files()
    sized: list[tuple[int, str, str]] = []
    omitted: list[str] = []
    for rel_path in changed:
        path = self.project_root / rel_path
        if not path.exists():  # deleted in this PR — nothing to show at HEAD
            continue
        text = path.read_text()
        if text.count("\n") + 1 > _MAX_FILE_LINES:
            omitted.append(f"file omitted: too large ({rel_path})")
            continue
        sized.append((len(text), rel_path, text))

    sections: list[str] = []
    used = 0
    for size, rel_path, text in sorted(sized):
        if used + size > _MAX_TOTAL_CHARS:
            omitted.append(f"file omitted: budget exhausted ({rel_path})")
            continue
        used += size
        sections.append(f"#### `{rel_path}`\n```python\n{text}\n```")
    return "\n\n".join(sections + omitted)
```

In `collect_all()`, after the `graph_context` block add:

```python
if "full_files" in self.features:
    full_files = self.collect_full_files()
    if full_files:
        context["full_files"] = full_files
```

- [ ] **Step 4: Add the prompt section + test**

Test (append to `tests/unit/test_guardian_core.py` — or wherever `PromptBuilder` is currently tested; search first with `grep -rn "build_user_prompt" tests/`):

```python
def test_user_prompt_includes_full_files_section() -> None:
    """The FULL FILE CONTENTS section appears iff the context provides it."""
    from cgis.guardian.prompts import PromptBuilder

    with_files = PromptBuilder.build_user_prompt({"diff": "d", "full_files": "#### `a.py`"})
    without = PromptBuilder.build_user_prompt({"diff": "d"})
    assert "FULL FILE CONTENTS (HEAD)" in with_files
    assert "FULL FILE CONTENTS" not in without
```

In `src/cgis/guardian/prompts.py` `build_user_prompt`, after the `graph_section` construction add:

```python
full_files = context.get("full_files", "")
full_files_section = ""
if full_files:
    full_files_section = f"""
### 5. FULL FILE CONTENTS (HEAD)
Complete current text of the changed files (oversized files carry an explicit
omission note — treat a note as missing context, not missing code).

{full_files}
"""
```

and render it by inserting `{full_files_section}` immediately after `{graph_section}` in the returned f-string.

- [ ] **Step 5: Run tests, full gates, commit**

Run: `uv run pytest tests/unit/test_guardian_collector.py tests/unit/test_guardian_core.py -v` → PASS.
Run: `make format && make lint && make type-check && make pytest && make doc-coverage`

```bash
git add src/cgis/guardian/collector.py src/cgis/guardian/prompts.py tests/unit/
git commit -m "feat(guardian): full-file context section behind full_files flag (#126)

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 3: Flow-fallback for new files (spec §4.2, #94)

**Files:**
- Modify: `src/cgis/guardian/collector.py:84-117` (`collect_graph_context`)
- Test: `tests/unit/test_guardian_collector.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_guardian_collector.py`:

```python
def test_flow_fallback_on_empty_impact(tmp_db: Path) -> None:
    """Empty impact graph + 'flow' feature falls back to the outbound flow graph."""
    node = _make_node("cgis.newmod.func", "src/cgis/newmod.py")
    mock_engine = MagicMock()
    mock_engine.get_impact_graph.return_value = ([], [])
    mock_engine.get_flow_graph.return_value = ([node], [])

    collector = ContextCollector(
        project_root=tmp_db.parent, db_path=tmp_db, features=frozenset({"flow"})
    )
    with (
        patch.object(collector, "get_changed_py_files", return_value=["src/cgis/newmod.py"]),
        patch("cgis.guardian.collector.SQLiteStore") as mock_store_cls,
        patch("cgis.guardian.collector.QueryEngine", return_value=mock_engine),
    ):
        mock_store_cls.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_store_cls.return_value.__exit__ = MagicMock(return_value=False)
        result = collector.collect_graph_context()

    assert "Dependency graph (outbound) for `cgis.newmod`" in result
    mock_engine.get_flow_graph.assert_called_once_with("cgis.newmod", max_depth=2)
    assert collector.graph_stats["flow_fallback"] == 1


def test_no_flow_fallback_without_feature(tmp_db: Path) -> None:
    """Without the 'flow' feature the fallback is never attempted (baseline behavior)."""
    mock_engine = MagicMock()
    mock_engine.get_impact_graph.return_value = ([], [])

    collector = ContextCollector(project_root=tmp_db.parent, db_path=tmp_db)
    with (
        patch.object(collector, "get_changed_py_files", return_value=["src/cgis/newmod.py"]),
        patch("cgis.guardian.collector.SQLiteStore") as mock_store_cls,
        patch("cgis.guardian.collector.QueryEngine", return_value=mock_engine),
    ):
        mock_store_cls.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_store_cls.return_value.__exit__ = MagicMock(return_value=False)
        collector.collect_graph_context()

    mock_engine.get_flow_graph.assert_not_called()
    assert collector.graph_stats["flow_fallback"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_guardian_collector.py -k flow -v`
Expected: FAIL (`get_flow_graph` not called / `KeyError: 'flow_fallback'`)

- [ ] **Step 3: Implement**

In `collect_graph_context`, replace the per-file loop body and the stats assignment:

```python
flow_fallbacks = 0
with SQLiteStore(str(self.db_path)) as store:
    engine = QueryEngine(store)
    for rel_path in changed_files:
        module_fqn = file_path_to_module_fqn(rel_path, self.source_root)
        nodes, edges = engine.get_impact_graph(module_fqn, max_depth=2)
        title = "Impact graph"
        if not nodes and "flow" in self.features:
            # New file: nothing references it yet (#94) — show what it calls.
            nodes, edges = engine.get_flow_graph(module_fqn, max_depth=2)
            title = "Dependency graph (outbound)"
            if nodes:
                flow_fallbacks += 1
        if not nodes:
            log.debug("No impact graph for module", fqn=module_fqn)
            continue
        mermaid = compiler.compile(nodes, edges)
        sections.append(f"#### {title} for `{module_fqn}`:\n```mermaid\n{mermaid}\n```")

self.graph_stats = {
    "total": total_changed,
    "with_graph": len(sections),
    "flow_fallback": flow_fallbacks,
}
```

Also update the `__init__` initial value to `self.graph_stats: dict[str, int] = {"total": 0, "with_graph": 0, "flow_fallback": 0}`.

Note: `QueryEngine.get_flow_graph(start_node_id, max_depth)` already exists in `src/cgis/query/engine.py` with the same signature shape as `get_impact_graph`.

- [ ] **Step 4: Run tests, full gates, commit**

Run: `uv run pytest tests/unit/test_guardian_collector.py -v` → PASS (existing `test_collect_graph_context_injects_mermaid` asserts on `Impact graph for` — verify the new title string keeps it passing; the section header for the non-fallback path must remain `#### Impact graph for \`{fqn}\`:`).
Run: `make format && make lint && make type-check && make pytest && make doc-coverage`

```bash
git add src/cgis/guardian/collector.py tests/unit/test_guardian_collector.py
git commit -m "feat(guardian): flow-graph fallback for new files behind flow flag (#94)

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

### Task 4: Architectural drift section (spec §4.3)

**Files:**
- Modify: `src/cgis/guardian/collector.py`
- Modify: `src/cgis/guardian/prompts.py`
- Test: `tests/unit/test_guardian_collector.py`, `tests/unit/test_guardian_core.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_guardian_collector.py`:

```python
def test_collect_drift_renders_table(tmp_db: Path, tmp_path: Path) -> None:
    """Drift section renders a markdown table row per domain plus quotient lines."""
    from dataclasses import dataclass, field

    patterns = tmp_path / "docs" / "ontology" / "patterns.yaml"
    patterns.parent.mkdir(parents=True)
    patterns.write_text("patterns: {}\n")

    fake_report = MagicMock()
    fake_report.fqn_prefix = "cgis.query"
    fake_report.expected_pattern = "layered_dag"
    fake_report.drift_score = 0.61
    fake_report.tolerance = 0.50

    fake_scorer = MagicMock()
    fake_domain = MagicMock()
    fake_scorer.load_project_domains.return_value = [fake_domain]
    fake_scorer.load_project_level.return_value = []
    fake_scorer.score.return_value = fake_report

    collector = ContextCollector(
        project_root=tmp_path, db_path=tmp_db, features=frozenset({"drift"})
    )
    with (
        patch("cgis.guardian.collector.DriftScorer", return_value=fake_scorer),
        patch("cgis.guardian.collector.FingerprintExtractor"),
        patch("cgis.guardian.collector.SQLiteStore") as mock_store_cls,
    ):
        mock_store_cls.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_store_cls.return_value.__exit__ = MagicMock(return_value=False)
        result = collector.collect_drift()

    assert "| cgis.query | layered_dag | 0.61 | 0.50 | ⚠ |" in result


def test_collect_drift_missing_patterns_returns_empty(tmp_db: Path, tmp_path: Path) -> None:
    """No patterns.yaml → empty string, never an exception (spec §4.4)."""
    collector = ContextCollector(
        project_root=tmp_path, db_path=tmp_db, features=frozenset({"drift"})
    )
    assert collector.collect_drift() == ""


def test_collect_drift_swallows_scorer_errors(tmp_db: Path, tmp_path: Path) -> None:
    """A DriftScorer crash degrades to an empty section — guardian never fails a review."""
    patterns = tmp_path / "docs" / "ontology" / "patterns.yaml"
    patterns.parent.mkdir(parents=True)
    patterns.write_text("patterns: {}\n")
    collector = ContextCollector(
        project_root=tmp_path, db_path=tmp_db, features=frozenset({"drift"})
    )
    with patch("cgis.guardian.collector.DriftScorer", side_effect=RuntimeError("boom")):
        assert collector.collect_drift() == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_guardian_collector.py -k drift -v`
Expected: FAIL with `AttributeError: ... 'collect_drift'`

- [ ] **Step 3: Implement**

Add imports to `src/cgis/guardian/collector.py`:

```python
from cgis.query.drift import DriftScorer
from cgis.query.fingerprint import FingerprintExtractor
from cgis.query.quotient import build_quotient
```

Add method to `ContextCollector`:

```python
def collect_drift(self) -> str:
    """Compact per-domain drift table + quotient k=1 lines (spec §4.3).

    First real consumer of drift v2 outside tests — the soft enforcement
    channel deferred in #146/#151. Any failure degrades to an empty section.
    """
    if self.db_path is None or not self.db_path.exists():
        return ""
    patterns = self.project_root / "docs" / "ontology" / "patterns.yaml"
    if not patterns.exists():
        return ""
    try:
        scorer = DriftScorer(str(patterns))
        domains = scorer.load_project_domains()
        quotient_lines: list[str] = []
        with SQLiteStore(str(self.db_path)) as store:
            extractor = FingerprintExtractor(store)
            reports = [scorer.score(extractor.extract(d.fqn_prefix), d) for d in domains]
            level = scorer.load_project_level()
            if level:
                qnodes, qedges = build_quotient(
                    store.get_all_nodes(), store.get_all_edges(), domains
                )
                q_extractor = FingerprintExtractor.from_graph(qnodes, qedges)
                quotient_lines = [
                    f"Quotient k=1 [{b.name}] vs {qr.expected_pattern}: "
                    f"drift={qr.drift_score:.2f} (observe-only)"
                    for b in level
                    for qr in [scorer.score(q_extractor.extract(b.fqn_prefix), b)]
                ]
    except Exception:
        log.warning("Drift section skipped.", exc_info=True)
        return ""

    rows = [
        f"| {r.fqn_prefix} | {r.expected_pattern or '(hygiene)'} "
        f"| {r.drift_score:.2f} | {r.tolerance:.2f} "
        f"| {'⚠' if r.drift_score > r.tolerance else ''} |"
        for r in reports
    ]
    table = "| domain | expected | drift | tolerance | over |\n|---|---|---|---|---|\n"
    return table + "\n".join(rows) + ("\n" + "\n".join(quotient_lines) if quotient_lines else "")
```

In `collect_all()` add (after the `full_files` block):

```python
if "drift" in self.features:
    drift = self.collect_drift()
    if drift:
        context["drift"] = drift
```

- [ ] **Step 4: Prompt section + test**

Test (next to the full-files prompt test):

```python
def test_user_prompt_includes_drift_section() -> None:
    """The ARCHITECTURAL DRIFT section appears iff the context provides it."""
    from cgis.guardian.prompts import PromptBuilder

    with_drift = PromptBuilder.build_user_prompt({"diff": "d", "drift": "| domain |"})
    assert "ARCHITECTURAL DRIFT (motif-basis)" in with_drift
    assert "ontology" in with_drift  # the reading instruction names the category
```

In `prompts.py` `build_user_prompt`, after `full_files_section`:

```python
drift = context.get("drift", "")
drift_section = ""
if drift:
    drift_section = f"""
### 6. ARCHITECTURAL DRIFT (motif-basis)
Per-domain drift vs the declared ideal pattern. A PR pushing a domain past its
tolerance (⚠) is an `ontology`-category finding. The quotient line is
observe-only — do NOT flag it.

{drift}
"""
```

Insert `{drift_section}` after `{full_files_section}` in the returned f-string.

- [ ] **Step 5: Run tests, full gates, commit**

Run: `make format && make lint && make type-check && make pytest && make doc-coverage`

```bash
git add src/cgis/guardian/collector.py src/cgis/guardian/prompts.py tests/unit/
git commit -m "feat(guardian): architectural drift context section behind drift flag

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 5: Skeptic models and merge logic (spec §5.2–5.3, pure — no LLM)

**Files:**
- Create: `src/cgis/guardian/skeptic.py`
- Modify: `src/cgis/guardian/findings.py` (add `skeptic_status` to `ReviewResult`)
- Test: `tests/unit/test_guardian_skeptic.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_guardian_skeptic.py`:

```python
"""Unit tests for skeptic verdict models and the pure merge logic (spec §5)."""

from cgis.guardian.findings import Finding
from cgis.guardian.skeptic import (
    SkepticResult,
    SkepticVerdict,
    apply_verdicts,
    build_skeptic_prompt,
    visible_findings,
)

_FINDING = Finding(
    file="src/cgis/cli.py",
    line=42,
    severity="major",
    category="logic",
    title="off-by-one",
    evidence="range(n + 1)",
    problem="iterates past the end.",
    fix="use range(n).",
    confidence=85,
)


def _verdict(index: int, verdict: str, rationale: str = "because") -> SkepticVerdict:
    return SkepticVerdict(finding_index=index, verdict=verdict, rationale=rationale)  # type: ignore[arg-type]


def test_confirmed_sets_verdict_and_note() -> None:
    """confirmed → verdict + skeptic_note on a new frozen copy."""
    merged = apply_verdicts([_FINDING], SkepticResult(verdicts=[_verdict(0, "confirmed")]))
    assert merged[0].verdict == "confirmed"
    assert merged[0].skeptic_note == "because"
    assert _FINDING.verdict is None  # original untouched (frozen)


def test_refuted_marks_but_keeps_finding() -> None:
    """refuted → marked, kept in the list (metrics must see killed findings)."""
    merged = apply_verdicts([_FINDING], SkepticResult(verdicts=[_verdict(0, "refuted")]))
    assert merged[0].verdict == "refuted"


def test_uncertain_discounts_to_above_gate() -> None:
    """uncertain at confidence 89 → round(89*0.9)=80, stays uncertain (boundary)."""
    f = _FINDING.model_copy(update={"confidence": 89})
    merged = apply_verdicts([f], SkepticResult(verdicts=[_verdict(0, "uncertain")]))
    assert merged[0].verdict == "uncertain"
    assert merged[0].confidence == 80


def test_uncertain_discount_below_gate_refutes() -> None:
    """uncertain at confidence 88 → round(88*0.9)=79 < 80 → treated as refuted."""
    f = _FINDING.model_copy(update={"confidence": 88})
    merged = apply_verdicts([f], SkepticResult(verdicts=[_verdict(0, "uncertain")]))
    assert merged[0].verdict == "refuted"


def test_out_of_range_and_duplicate_indices_discarded() -> None:
    """Out-of-range or duplicate finding_index verdicts are dropped, not applied."""
    verdicts = SkepticResult(
        verdicts=[_verdict(5, "refuted"), _verdict(0, "confirmed"), _verdict(0, "refuted")]
    )
    merged = apply_verdicts([_FINDING], verdicts)
    assert merged[0].verdict == "confirmed"  # first valid verdict wins; duplicate ignored


def test_unruled_finding_keeps_none_verdict() -> None:
    """A finding the skeptic never ruled on keeps verdict=None and is not filtered."""
    merged = apply_verdicts([_FINDING], SkepticResult(verdicts=[]))
    assert merged[0].verdict is None
    assert visible_findings(merged) == merged


def test_visible_findings_drops_only_refuted() -> None:
    """visible_findings filters refuted; confirmed/uncertain/None stay."""
    kept = _FINDING.model_copy(update={"verdict": "confirmed"})
    dropped = _FINDING.model_copy(update={"verdict": "refuted", "title": "x"})
    assert visible_findings([kept, dropped]) == [kept]


def test_skeptic_prompt_contains_findings_and_stance() -> None:
    """The skeptic prompt lists indexed findings and the refute-by-default stance."""
    prompt = build_skeptic_prompt({"diff": "the-diff"}, [_FINDING])
    assert "the-diff" in prompt
    assert "[0]" in prompt and "off-by-one" in prompt
    assert "REFUTE" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_guardian_skeptic.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cgis.guardian.skeptic'`

- [ ] **Step 3: Implement `src/cgis/guardian/skeptic.py`**

```python
"""Cross-model skeptic pass: verdict models, prompt, and pure merge logic (spec §5)."""

from collections.abc import Iterable
from typing import Literal

import structlog
from pydantic import BaseModel

from cgis.guardian.findings import Finding

log = structlog.getLogger(__name__)

_CONFIDENCE_GATE = 80
_UNCERTAIN_MULTIPLIER = 0.9  # keeps original confidence >= 89; smaller would
# refute EVERY uncertain finding and make the branch dead code (spec §5.3).


class SkepticVerdict(BaseModel, frozen=True):
    """The skeptic's ruling on one pass-1 finding, addressed by list index."""

    finding_index: int
    verdict: Literal["confirmed", "refuted", "uncertain"]
    rationale: str


class SkepticResult(BaseModel, frozen=True):
    """All verdicts from one skeptic call (spec §5.2: one call, not N)."""

    verdicts: list[SkepticVerdict]


SKEPTIC_SYSTEM_PROMPT = (
    "You are a skeptical senior reviewer double-checking another reviewer's findings. "
    "Your job is to REFUTE: for each finding, look for evidence in the diff that the "
    "claimed defect does not exist, is already handled, or misreads the code. "
    "Only confirm a finding when the evidence clearly supports it. "
    "If you are uncertain, refute — a wrong finding wastes more time than a missed one."
)


def build_skeptic_prompt(context: dict[str, str], findings: list[Finding]) -> str:
    """Assemble the skeptic user prompt: same diff context + indexed findings list."""
    listed = "\n".join(
        f"[{i}] {f.severity} {f.category} at {f.file}:{f.line} — {f.title}\n"
        f"    evidence: {f.evidence}\n    problem: {f.problem}"
        for i, f in enumerate(findings)
    )
    return f"""Another reviewer produced the findings below for this diff.
Try to REFUTE each one against the diff; if uncertain, refute.

### DIFF
{context.get("diff", "")}

### FULL FILE CONTENTS (if available)
{context.get("full_files", "")}

### FINDINGS TO VERIFY
{listed}

### OUTPUT FORMAT
Return ONLY a JSON object: {{"verdicts": [{{"finding_index": 0,
"verdict": "confirmed|refuted|uncertain", "rationale": "one sentence"}}]}}
Rule on every finding exactly once, by its [index]."""


def apply_verdicts(findings: list[Finding], skeptic: SkepticResult) -> list[Finding]:
    """Merge skeptic verdicts into new frozen Finding copies (spec §5.3).

    Out-of-range / duplicate indices are discarded and logged. Unruled findings
    keep verdict=None. uncertain discounts confidence ×0.9; below the 80 gate
    it becomes refuted.
    """
    by_index: dict[int, SkepticVerdict] = {}
    for v in skeptic.verdicts:
        if not 0 <= v.finding_index < len(findings):
            log.warning("Skeptic verdict index out of range; discarded.", index=v.finding_index)
            continue
        if v.finding_index in by_index:
            log.warning("Duplicate skeptic verdict index; discarded.", index=v.finding_index)
            continue
        by_index[v.finding_index] = v

    merged: list[Finding] = []
    for i, finding in enumerate(findings):
        verdict = by_index.get(i)
        if verdict is None:
            merged.append(finding)  # absence of a verdict is not a refutation
            continue
        if verdict.verdict == "uncertain":
            discounted = round(finding.confidence * _UNCERTAIN_MULTIPLIER)
            final = "refuted" if discounted < _CONFIDENCE_GATE else "uncertain"
            merged.append(
                finding.model_copy(
                    update={
                        "verdict": final,
                        "skeptic_note": verdict.rationale,
                        "confidence": discounted,
                    }
                )
            )
            continue
        merged.append(
            finding.model_copy(
                update={"verdict": verdict.verdict, "skeptic_note": verdict.rationale}
            )
        )
    return merged


def visible_findings(findings: Iterable[Finding]) -> list[Finding]:
    """Findings that appear in the rendered report: everything not refuted."""
    return [f for f in findings if f.verdict != "refuted"]
```

- [ ] **Step 4: Add `skeptic_status` to `ReviewResult`**

In `src/cgis/guardian/findings.py`, add to `ReviewResult`:

```python
    # "off" = skeptic not configured; "ok" = verdicts merged; "failed" = skeptic
    # call errored, single-pass results returned (spec §5.5 — never silent).
    skeptic_status: Literal["off", "ok", "failed"] = "off"
```

- [ ] **Step 5: Run tests, full gates, commit**

Run: `uv run pytest tests/unit/test_guardian_skeptic.py tests/unit/test_guardian_findings.py -v` → PASS.
Run: `make format && make lint && make type-check && make pytest && make doc-coverage`

```bash
git add src/cgis/guardian/skeptic.py src/cgis/guardian/findings.py tests/unit/test_guardian_skeptic.py
git commit -m "feat(guardian): skeptic verdict models and pure merge logic

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

### Task 6: Skeptic pass in core + provider selection in runner (spec §5.1, §5.5)

**Design note (supersedes spec §5.5's fixed pairing):** the skeptic provider AND
model are independently configurable. The spec's "always the other provider"
default stays, but `GUARDIAN_SKEPTIC_MODEL` allows same-provider/different-model
pairs (e.g. finder gemini-3.5-flash + skeptic gemini-2.5-flash) — necessary
because mistral's free tier cannot ingest large-PR prompts (36–62k tokens exceed
its per-minute token cap), and large PRs are where the skeptic matters most.
The benchmark (Task 12) decides the production pairing.

**Files:**
- Modify: `src/cgis/guardian/core.py`
- Modify: `src/cgis/guardian/runner.py`
- Test: `tests/unit/test_guardian_core.py`, `tests/unit/test_guardian_runner.py`

- [ ] **Step 1: Write the failing core tests**

Append to `tests/unit/test_guardian_core.py` (follow the existing fake-provider pattern in that file — check how `run_review` is currently tested; reuse its fake/mock provider helper):

```python
_FINDING_JSON = (
    '{"findings": [{"file": "a.py", "line": 1, "severity": "major", "category": "logic",'
    ' "title": "t", "evidence": "e", "problem": "p", "fix": "f", "confidence": 90}],'
    ' "summary": "s"}'
)


class _StubProvider(BaseProvider):
    """Returns canned JSON per call; records prompts."""

    def __init__(self, responses: list[str]) -> None:
        super().__init__()
        self._responses = list(responses)
        self.prompts: list[str] = []

    async def generate_content(self, system_prompt: str, user_prompt: str) -> str:
        raise NotImplementedError

    async def generate_structured(
        self, system_prompt: str, user_prompt: str, schema: type[BaseModel]
    ) -> str:
        self.prompts.append(user_prompt)
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_skeptic_pass_merges_verdicts(tmp_path: Path) -> None:
    """With a skeptic provider, verdicts land on findings and status is 'ok'."""
    finder = _StubProvider([_FINDING_JSON])
    skeptic = _StubProvider(
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
    finder = _StubProvider(['{"findings": [], "summary": "ok"}'])
    skeptic = _StubProvider([])  # would raise IndexError if called
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
    finder = _StubProvider([_FINDING_JSON])
    skeptic = _StubProvider(["not json at all {{{"])
    collector = ContextCollector(project_root=tmp_path)
    reviewer = GuardianReviewer(
        provider=finder, context_collector=collector, skeptic_provider=skeptic
    )
    with patch.object(collector, "collect_all", return_value={"diff": "d"}):
        result = await reviewer.run_review()
    assert result.skeptic_status == "failed"
    assert result.findings[0].verdict is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_guardian_core.py -k skeptic -v`
Expected: FAIL with `TypeError: ... unexpected keyword argument 'skeptic_provider'`

- [ ] **Step 3: Implement in `core.py`**

Extend `GuardianReviewer.__init__`:

```python
def __init__(
    self,
    provider: BaseProvider,
    context_collector: ContextCollector,
    skeptic_provider: BaseProvider | None = None,
) -> None:
    """Wire up the LLM provider, context collector, prompt builder, and optional skeptic."""
    self.provider = provider
    self.context_collector = context_collector
    self.prompt_builder = PromptBuilder()
    self.skeptic_provider = skeptic_provider
```

Restructure `run_review` so the existing parse/retry logic produces a pass-1
`ReviewResult` (extract the current try/retry body into a private
`async def _finder_pass(self, context: dict[str, str]) -> ReviewResult`), then:

```python
async def run_review(self) -> ReviewResult:
    """Run the review; optionally verify findings with the skeptic pass (spec §5)."""
    context = self.context_collector.collect_all()
    result = await self._finder_pass(context)
    if self.skeptic_provider is None or not result.findings or result.parse_failed:
        return result
    try:
        raw = await self.skeptic_provider.generate_structured(
            SKEPTIC_SYSTEM_PROMPT,
            build_skeptic_prompt(context, result.findings),
            SkepticResult,
        )
        verdicts = SkepticResult.model_validate_json(extract_json(raw))
    except Exception:
        log.warning("Skeptic pass failed; returning single-pass results.", exc_info=True)
        return result.model_copy(update={"skeptic_status": "failed"})
    merged = apply_verdicts(result.findings, verdicts)
    return result.model_copy(update={"findings": merged, "skeptic_status": "ok"})
```

New imports in `core.py`:

```python
from cgis.guardian.skeptic import (
    SKEPTIC_SYSTEM_PROMPT,
    SkepticResult,
    apply_verdicts,
    build_skeptic_prompt,
)
```

- [ ] **Step 4: Skeptic provider selection in runner + tests**

Append to `tests/unit/test_guardian_runner.py`:

```python
def test_build_skeptic_provider_default_is_other_provider() -> None:
    """Primary gemini → skeptic mistral by default (spec §5.5), when its key exists."""
    env = {"GEMINI_API_KEY": "g", "MISTRAL_API_KEY": "m"}
    built = build_skeptic_provider(env, primary="gemini")
    assert built is not None
    provider, model = built
    assert model == DEFAULT_MISTRAL_MODEL


def test_build_skeptic_provider_off() -> None:
    """GUARDIAN_SKEPTIC=off disables the pass."""
    env = {"GUARDIAN_SKEPTIC": "off", "GEMINI_API_KEY": "g", "MISTRAL_API_KEY": "m"}
    assert build_skeptic_provider(env, primary="gemini") is None


def test_build_skeptic_provider_same_provider_model_override() -> None:
    """GUARDIAN_SKEPTIC=gemini + GUARDIAN_SKEPTIC_MODEL allows a gemini×gemini pair."""
    env = {
        "GUARDIAN_SKEPTIC": "gemini",
        "GUARDIAN_SKEPTIC_MODEL": "gemini-2.5-flash",
        "GEMINI_API_KEY": "g",
    }
    built = build_skeptic_provider(env, primary="gemini")
    assert built is not None
    _, model = built
    assert model == "gemini-2.5-flash"


def test_build_skeptic_provider_missing_key_degrades_to_none() -> None:
    """No API key for the chosen skeptic → None (graceful single-pass), not an error."""
    env = {"GEMINI_API_KEY": "g"}  # default skeptic for gemini primary is mistral — no key
    assert build_skeptic_provider(env, primary="gemini") is None
```

Implement in `src/cgis/guardian/runner.py` (after `build_provider`):

```python
def build_skeptic_provider(
    env: Mapping[str, str], *, primary: str
) -> tuple[BaseProvider, str] | None:
    """Return (skeptic_provider, model) or None for single-pass (spec §5.5).

    Default skeptic = the provider opposite to the primary; GUARDIAN_SKEPTIC
    overrides ('gemini'|'mistral'|'off'); GUARDIAN_SKEPTIC_MODEL overrides the
    model, enabling same-provider/different-model pairs. A missing API key
    degrades to None — a review never fails because of the skeptic.
    """
    choice = env.get("GUARDIAN_SKEPTIC", "").lower()
    if choice == "off":
        return None
    if choice not in ("", "gemini", "mistral"):
        log.warning("Unknown GUARDIAN_SKEPTIC; skeptic disabled.", value=choice)
        return None
    name = choice or ("mistral" if primary == "gemini" else "gemini")
    model_override = env.get("GUARDIAN_SKEPTIC_MODEL")
    if name == "mistral":
        key = env.get("MISTRAL_API_KEY")
        if not key:
            log.warning("Skeptic disabled: MISTRAL_API_KEY not set.")
            return None
        model = model_override or DEFAULT_MISTRAL_MODEL
        return MistralProvider(api_key=key, model_name=model), model
    key = env.get("GEMINI_API_KEY")
    if not key:
        log.warning("Skeptic disabled: GEMINI_API_KEY not set.")
        return None
    model = model_override or DEFAULT_GEMINI_MODEL
    return GeminiProvider(api_key=key, model_name=model), model
```

`build_provider` must also expose which provider family it chose. It already
returns the model name; add a third element OR (simpler, no breakage) derive
the family in the caller: in `scripts/guardian_review.py` and
`scripts/guardian_bench.py`, after `provider, model = build_provider(...)`,
compute `primary = "mistral" if isinstance(provider, MistralProvider) else "gemini"`.

Wire into `run_guardian`: add parameter `skeptic: tuple[BaseProvider, str] | None = None`,
pass `skeptic_provider=skeptic[0] if skeptic else None` to `GuardianReviewer`,
and record the skeptic model in metrics (extend `record_review` with
`skeptic_model: str | None = None` and `skeptic_status: str = "off"` keys in the entry dict —
modify `src/cgis/guardian/metrics.py` accordingly, add to the entry:
`"skeptic_model": skeptic_model, "skeptic_status": skeptic_status`).

- [ ] **Step 5: Run tests, full gates, commit**

Run: `uv run pytest tests/unit/test_guardian_core.py tests/unit/test_guardian_runner.py tests/unit/test_guardian_metrics.py -v` → PASS.
Run: `make format && make lint && make type-check && make pytest && make doc-coverage`

```bash
git add src/cgis/guardian/ tests/unit/ 
git commit -m "feat(guardian): cross-model skeptic pass with configurable provider/model

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 7: Render refuted-aware report (spec §5.3)

**Files:**
- Modify: `src/cgis/guardian/render.py`
- Test: `tests/unit/test_guardian_render.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_guardian_render.py`:

```python
def test_render_report_hides_refuted_findings() -> None:
    """Refuted findings stay in the model (for metrics) but vanish from the report."""
    refuted = _FINDING.model_copy(update={"verdict": "refuted", "title": "killed"})
    kept = _FINDING.model_copy(update={"verdict": "confirmed"})
    text = render_report(ReviewResult(findings=[refuted, kept], summary="s"))
    assert "killed" not in text
    assert "off-by-one in pagination" in text


def test_render_report_all_refuted_is_lgtm_with_note() -> None:
    """All findings refuted → LGTM line plus an explicit skeptic note (never silent)."""
    refuted = _FINDING.model_copy(update={"verdict": "refuted"})
    text = render_report(ReviewResult(findings=[refuted], summary="s", skeptic_status="ok"))
    assert text.startswith("LGTM")
    assert "1 finding was refuted by the skeptic pass" in text


def test_render_report_notes_skeptic_failure() -> None:
    """skeptic_status='failed' adds a visible degradation note (spec §7: never silent)."""
    text = render_report(ReviewResult(findings=[_FINDING], summary="s", skeptic_status="failed"))
    assert "Skeptic pass failed; findings are single-pass." in text
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_guardian_render.py -v`
Expected: the three new tests FAIL.

- [ ] **Step 3: Implement**

In `src/cgis/guardian/render.py`, import `from cgis.guardian.skeptic import visible_findings` and rewrite `render_report`:

```python
def render_report(result: ReviewResult) -> str:
    """Render the full review; refuted findings are hidden but never silently."""
    if result.parse_failed:
        return (
            "⚠️ Guardian could not produce structured output; raw response below.\n\n"
            + result.summary
        )
    visible = visible_findings(result.findings)
    refuted_count = len(result.findings) - len(visible)
    notes: list[str] = []
    if refuted_count:
        plural = "finding was" if refuted_count == 1 else "findings were"
        notes.append(f"_{refuted_count} {plural} refuted by the skeptic pass._")
    if result.skeptic_status == "failed":
        notes.append("_Skeptic pass failed; findings are single-pass._")
    suffix = ("\n\n" + "\n".join(notes)) if notes else ""
    if not visible:
        return f"LGTM — no defects found in this diff.\n\n{result.summary}{suffix}"
    ordered = sorted(visible, key=lambda f: _SEVERITY_ORDER[f.severity])
    blocks = [render_finding(f) for f in ordered]
    return "\n\n".join(blocks) + f"\n\n---\n**Summary:** {result.summary}{suffix}"
```

- [ ] **Step 4: Run tests, full gates, commit**

Run: `make format && make lint && make type-check && make pytest && make doc-coverage`

```bash
git add src/cgis/guardian/render.py tests/unit/test_guardian_render.py
git commit -m "feat(guardian): hide refuted findings in report with explicit notes

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

### Task 8: Diff line index (spec §6.2, pure)

**Files:**
- Create: `src/cgis/guardian/diff_index.py`
- Test: `tests/unit/test_guardian_diff_index.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_guardian_diff_index.py`:

```python
"""Unit tests for the pure unified-diff RIGHT-side line indexer (spec §6.2)."""

from cgis.guardian.diff_index import diff_line_index

_SIMPLE = """\
diff --git a/src/x.py b/src/x.py
index 111..222 100644
--- a/src/x.py
+++ b/src/x.py
@@ -10,4 +10,5 @@ def f():
 context1
-removed
+added1
+added2
 context2
"""

_RENAME = """\
diff --git a/old.py b/new.py
similarity index 90%
rename from old.py
rename to new.py
--- a/old.py
+++ b/new.py
@@ -1,2 +1,2 @@
 keep
+fresh
"""

_NEW_FILE = """\
diff --git a/brand.py b/brand.py
new file mode 100644
--- /dev/null
+++ b/brand.py
@@ -0,0 +1,2 @@
+line1
+line2
"""

_DELETED = """\
diff --git a/dead.py b/dead.py
deleted file mode 100644
--- a/dead.py
+++ /dev/null
@@ -1,2 +0,0 @@
-line1
-line2
"""


def test_added_and_context_lines_indexed() -> None:
    """RIGHT side = context + added lines, numbered from the hunk's +start."""
    index = diff_line_index(_SIMPLE)
    # @@ +10,5: 10=context1, 11=added1, 12=added2, 13=context2 (removed has no RIGHT line)
    assert index["src/x.py"] == {10, 11, 12, 13}


def test_rename_keyed_by_new_path() -> None:
    """Renames are keyed by the NEW path so keys match Finding.file."""
    index = diff_line_index(_RENAME)
    assert "new.py" in index and "old.py" not in index
    assert index["new.py"] == {1, 2}


def test_new_file_all_lines() -> None:
    """A new file's lines are all commentable."""
    assert diff_line_index(_NEW_FILE)["brand.py"] == {1, 2}


def test_deleted_file_absent() -> None:
    """A deleted file has no RIGHT side — not in the index at all."""
    assert diff_line_index(_DELETED) == {}


def test_empty_diff() -> None:
    """Empty input → empty index, no crash."""
    assert diff_line_index("") == {}


def test_multiple_files_and_hunks() -> None:
    """Two files in one diff each get their own line set."""
    index = diff_line_index(_SIMPLE + _NEW_FILE)
    assert set(index) == {"src/x.py", "brand.py"}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_guardian_diff_index.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `src/cgis/guardian/diff_index.py`**

```python
"""Pure unified-diff parser: which RIGHT-side lines can carry an inline comment."""

import re

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_NEW_FILE_RE = re.compile(r"^\+\+\+ (?:b/)?(.+)$")


def diff_line_index(diff_text: str) -> dict[str, set[int]]:
    """Map each changed file (new path) to the set of RIGHT-side line numbers.

    GitHub only accepts inline review comments on lines present in the diff;
    context and added lines count, removed lines do not (spec §6.2). Renames
    are keyed by the new path so keys match Finding.file. Files deleted in the
    PR (+++ /dev/null) have no RIGHT side and are excluded.
    """
    index: dict[str, set[int]] = {}
    current: str | None = None
    in_hunk = False
    new_line = 0
    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            current = None
            in_hunk = False
            continue
        file_match = _NEW_FILE_RE.match(line)
        if file_match:
            path = file_match.group(1)
            current = None if path == "/dev/null" else path
            in_hunk = False
            continue
        hunk_match = _HUNK_RE.match(line)
        if hunk_match and current is not None:
            new_line = int(hunk_match.group(1))
            in_hunk = True
            index.setdefault(current, set())
            continue
        if current is None or not in_hunk:
            continue
        if line.startswith("+"):
            index[current].add(new_line)
            new_line += 1
        elif line.startswith("-") or line.startswith("\\"):
            continue  # removed line / "\ No newline" marker: no RIGHT-side line
        else:
            index[current].add(new_line)  # context line
            new_line += 1
    return {path: lines for path, lines in index.items() if lines}
```

- [ ] **Step 4: Run tests, full gates, commit**

Run: `make format && make lint && make type-check && make pytest && make doc-coverage`

```bash
git add src/cgis/guardian/diff_index.py tests/unit/test_guardian_diff_index.py
git commit -m "feat(guardian): pure diff line indexer for inline comment placement

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 9: Inline renders + review builder + gh poster (spec §6.1, §6.3, §6.4)

**Files:**
- Modify: `src/cgis/guardian/render.py` (`render_inline_comment`, `render_review_body`)
- Create: `src/cgis/guardian/github_poster.py`
- Test: `tests/unit/test_guardian_render.py`, `tests/unit/test_guardian_poster.py` (create)

- [ ] **Step 1: Write the failing render tests**

Append to `tests/unit/test_guardian_render.py`:

```python
def test_render_inline_comment_fields() -> None:
    """One inline comment = marker, category, problem, fix, verified line."""
    f = _FINDING.model_copy(update={"verdict": "confirmed", "skeptic_note": "checked"})
    text = render_inline_comment(f, skeptic_model="gemini-2.5-flash")
    assert text.startswith("🟠 **[Logic Bug] — off-by-one in pagination**")
    assert "iterates one element past the end." in text
    assert "Fix: use range(n)." in text
    assert "Verified by gemini-2.5-flash" in text


def test_render_inline_comment_unverified_has_no_verified_line() -> None:
    """No confirmed verdict → no Verified line."""
    text = render_inline_comment(_FINDING, skeptic_model=None)
    assert "Verified by" not in text


def test_render_review_body_with_out_of_diff_findings() -> None:
    """Out-of-diff findings land in a dedicated section of the review body."""
    outside = _FINDING.model_copy(update={"line": None, "title": "file-level issue"})
    body = render_review_body(
        ReviewResult(findings=[outside], summary="checked X"), outside=[outside]
    )
    assert "Findings outside the diff" in body
    assert "file-level issue" in body
    assert "checked X" in body


def test_render_review_body_lgtm() -> None:
    """No findings at all → the canonical LGTM body."""
    body = render_review_body(ReviewResult(findings=[], summary="all good"), outside=[])
    assert body.startswith("LGTM")
```

- [ ] **Step 2: Implement render helpers**

Add to `src/cgis/guardian/render.py`:

```python
def render_inline_comment(finding: Finding, *, skeptic_model: str | None) -> str:
    """One finding as a standalone inline comment body (spec §6.3)."""
    lines = [
        f"{_SEVERITY_MARKER[finding.severity]} "
        f"**[{_CATEGORY_LABEL[finding.category]}] — {finding.title}**",
        f"{finding.problem}",
        f"Fix: {finding.fix}",
    ]
    if finding.verdict == "confirmed" and skeptic_model:
        lines.append(f"_Verified by {skeptic_model}_")
    return "\n\n".join(lines)


def render_review_body(result: ReviewResult, *, outside: list[Finding]) -> str:
    """The review's top-level body: summary plus any out-of-diff findings (spec §6.3)."""
    if not visible_findings(result.findings):
        return render_report(result)
    parts: list[str] = []
    if outside:
        ordered = sorted(outside, key=lambda f: _SEVERITY_ORDER[f.severity])
        blocks = "\n\n".join(render_finding(f) for f in ordered)
        parts.append(f"### Findings outside the diff\n\n{blocks}")
    parts.append(f"**Summary:** {result.summary}")
    return "\n\n".join(parts)
```

Run: `uv run pytest tests/unit/test_guardian_render.py -v` → PASS.

- [ ] **Step 3: Write the failing poster tests**

Create `tests/unit/test_guardian_poster.py`:

```python
"""Unit tests for the GitHub inline-review poster (mocked subprocess, spec §6.4)."""

import json
from unittest.mock import patch

from cgis.guardian.findings import Finding, ReviewResult
from cgis.guardian.github_poster import build_review, post_inline_review

_IN_DIFF = Finding(
    file="src/x.py", line=11, severity="major", category="logic",
    title="in-diff", evidence="e", problem="p", fix="f", confidence=90,
)
_OUTSIDE = Finding(
    file="src/x.py", line=999, severity="minor", category="tests",
    title="outside", evidence="e", problem="p", fix="f", confidence=85,
)
_REFUTED = _IN_DIFF.model_copy(update={"verdict": "refuted", "title": "dead"})


def test_build_review_splits_inline_and_outside() -> None:
    """In-index findings become comments; others go to the body; refuted vanish."""
    result = ReviewResult(findings=[_IN_DIFF, _OUTSIDE, _REFUTED], summary="s")
    body, comments = build_review(
        result, diff_index={"src/x.py": {10, 11, 12}}, skeptic_model=None
    )
    assert len(comments) == 1
    assert comments[0]["path"] == "src/x.py"
    assert comments[0]["line"] == 11
    assert comments[0]["side"] == "RIGHT"
    assert "in-diff" in str(comments[0]["body"])
    assert "outside" in body
    assert "dead" not in body


def test_build_review_line_none_goes_to_body() -> None:
    """A file-level finding (line=None) can never be inline."""
    file_level = _IN_DIFF.model_copy(update={"line": None})
    _, comments = build_review(
        ReviewResult(findings=[file_level], summary="s"),
        diff_index={"src/x.py": {11}},
        skeptic_model=None,
    )
    assert comments == []


def test_post_inline_review_payload() -> None:
    """The gh api call posts one COMMENT review with the built payload on stdin."""
    result = ReviewResult(findings=[_IN_DIFF], summary="s")
    with patch("cgis.guardian.github_poster.subprocess.run") as mock_run:
        post_inline_review(
            repo="zaebee/codegraph-brain",
            pr=153,
            result=result,
            diff_index={"src/x.py": {11}},
            skeptic_model=None,
        )
    args, kwargs = mock_run.call_args
    assert args[0][:3] == ["gh", "api", "repos/zaebee/codegraph-brain/pulls/153/reviews"]
    payload = json.loads(kwargs["input"])
    assert payload["event"] == "COMMENT"
    assert payload["comments"][0]["line"] == 11
    assert kwargs["check"] is True
```

- [ ] **Step 4: Implement `src/cgis/guardian/github_poster.py`**

```python
"""Posts a ReviewResult as one GitHub review with inline comments (spec §6)."""

import json
import subprocess

from cgis.guardian.findings import Finding, ReviewResult
from cgis.guardian.render import render_inline_comment, render_review_body
from cgis.guardian.skeptic import visible_findings


def build_review(
    result: ReviewResult,
    *,
    diff_index: dict[str, set[int]],
    skeptic_model: str | None,
) -> tuple[str, list[dict[str, object]]]:
    """Split findings into inline comments vs body text (pure, spec §6.2).

    A finding whose line is commentable becomes an inline comment; line=None
    or out-of-diff findings land in the review body. Nothing is lost.
    """
    inline: list[Finding] = []
    outside: list[Finding] = []
    for finding in visible_findings(result.findings):
        if finding.line is not None and finding.line in diff_index.get(finding.file, set()):
            inline.append(finding)
        else:
            outside.append(finding)
    comments: list[dict[str, object]] = [
        {
            "path": f.file,
            "line": f.line,
            "side": "RIGHT",
            "body": render_inline_comment(f, skeptic_model=skeptic_model),
        }
        for f in inline
    ]
    return render_review_body(result, outside=outside), comments


def post_inline_review(
    *,
    repo: str,
    pr: int,
    result: ReviewResult,
    diff_index: dict[str, set[int]],
    skeptic_model: str | None,
) -> None:
    """POST one COMMENT review via `gh api` (auto-auth in Actions, spec §6.4).

    Always event=COMMENT, never REQUEST_CHANGES — guardian is an advisor,
    not a gate. Raises CalledProcessError on API rejection; the caller
    decides the fallback (spec §6.5).
    """
    body, comments = build_review(result, diff_index=diff_index, skeptic_model=skeptic_model)
    payload = {"event": "COMMENT", "body": body, "comments": comments}
    subprocess.run(
        ["gh", "api", f"repos/{repo}/pulls/{pr}/reviews", "-X", "POST", "--input", "-"],
        input=json.dumps(payload),
        text=True,
        check=True,
        capture_output=True,
    )
```

- [ ] **Step 5: Run tests, full gates, commit**

Run: `make format && make lint && make type-check && make pytest && make doc-coverage`

```bash
git add src/cgis/guardian/render.py src/cgis/guardian/github_poster.py tests/unit/
git commit -m "feat(guardian): inline review builder and gh api poster

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

### Task 10: Script + runner integration with fallback chain (spec §6.5, §8)

**Files:**
- Modify: `src/cgis/guardian/runner.py` (`run_guardian` gains inline posting)
- Modify: `scripts/guardian_review.py` (`--inline`, `GITHUB_OUTPUT`)
- Test: `tests/unit/test_guardian_runner.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_guardian_runner.py` (reuse `_StubProvider` from the core tests — move it to a shared helper if both files need it, or redefine locally):

```python
_CANNED = (
    '{"findings": [{"file": "a.py", "line": 1, "severity": "major", "category": "logic",'
    ' "title": "t", "evidence": "e", "problem": "p", "fix": "f", "confidence": 90}],'
    ' "summary": "s"}'
)


@pytest.mark.asyncio
async def test_run_guardian_posts_inline_and_reports_success(tmp_path: Path) -> None:
    """Smoke test (spec §8): canned JSON → ReviewResult → inline post; posted=True."""
    provider = _StubProvider([_CANNED])
    collector = ContextCollector(project_root=tmp_path)
    with (
        patch.object(collector, "collect_all", return_value={"diff": "d"}),
        patch.object(collector, "get_git_diff", return_value="diff --git a/a.py b/a.py\n+++ b/a.py\n@@ -0,0 +1,1 @@\n+x = 1\n"),
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
    provider = _StubProvider([_CANNED])
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
    provider = _StubProvider([_CANNED])
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_guardian_runner.py -k run_guardian -v`
Expected: FAIL (`run_guardian` returns `str`, no `inline_repo` parameter).

- [ ] **Step 3: Implement in `runner.py`**

Change `run_guardian`'s signature and return (breaking internal change — same
pattern as Plan 1's `run_review` switch; the only consumers are the two
scripts and tests):

```python
async def run_guardian(
    *,
    provider: BaseProvider,
    model: str,
    collector: ContextCollector,
    pr: int | None,
    metrics_path: Path,
    skeptic: tuple[BaseProvider, str] | None = None,
    inline_repo: str | None = None,
) -> tuple[str, bool]:
    """Run the review; try the inline path when configured.

    Returns (rendered report + footer, posted_inline). posted_inline=False
    covers both "not configured" and "API rejected" — the caller posts the
    big comment in either case (spec §6.5).
    """
    reviewer = GuardianReviewer(
        provider=provider,
        context_collector=collector,
        skeptic_provider=skeptic[0] if skeptic else None,
    )
    result = await reviewer.run_review()
    report = render_report(result)

    posted = False
    if inline_repo is not None and pr is not None:
        try:
            index = diff_line_index(collector.get_git_diff())
            post_inline_review(
                repo=inline_repo,
                pr=pr,
                result=result,
                diff_index=index,
                skeptic_model=skeptic[1] if skeptic else None,
            )
            posted = True
        except Exception:
            log.warning("Inline review failed; falling back to comment.", exc_info=True)

    record_review(
        model=model,
        pr=pr,
        prompt_tokens=provider.last_usage.prompt_tokens,
        completion_tokens=provider.last_usage.completion_tokens,
        findings_total=len(result.findings),
        lgtm=not result.findings and not result.parse_failed,
        parse_failed=result.parse_failed,
        skeptic_model=skeptic[1] if skeptic else None,
        skeptic_status=result.skeptic_status,
        metrics_path=metrics_path,
    )
    footer = build_footer(model=model, usage=provider.last_usage, stats=collector.graph_stats)
    return report + footer, posted
```

New imports in `runner.py`:

```python
from cgis.guardian.diff_index import diff_line_index
from cgis.guardian.github_poster import post_inline_review
```

- [ ] **Step 4: Update `scripts/guardian_review.py`**

After `provider, model = build_provider(os.environ)` add:

```python
primary = "mistral" if isinstance(provider, MistralProvider) else "gemini"
skeptic = build_skeptic_provider(os.environ, primary=primary)
features = parse_features(os.environ.get("GUARDIAN_FEATURES", ""))
inline_repo = os.environ.get("GITHUB_REPOSITORY") if args.inline else None
```

with imports `from cgis.guardian.collector import ContextCollector, parse_features`,
`from cgis.guardian.providers.mistral import MistralProvider`,
`from cgis.guardian.runner import build_provider, build_skeptic_provider, run_guardian`.

Add the argparse flag:

```python
parser.add_argument(
    "--inline",
    action="store_true",
    help="Post findings as an inline GitHub review; fall back to the report file on failure.",
)
```

Pass `features=features` to `ContextCollector(...)` and
`skeptic=skeptic, inline_repo=inline_repo` to `run_guardian(...)`; unpack
`report, posted_inline = await run_guardian(...)`.

After writing/printing the report, emit the workflow output (no-op locally):

```python
github_output = os.environ.get("GITHUB_OUTPUT")
if github_output:
    with Path(github_output).open("a", encoding="utf-8") as fh:
        fh.write(f"posted_inline={'true' if posted_inline else 'false'}\n")
```

- [ ] **Step 5: Run tests, full gates, commit**

Run: `make format && make lint && make type-check && make pytest && make doc-coverage`

```bash
git add src/cgis/guardian/runner.py scripts/guardian_review.py tests/unit/test_guardian_runner.py
git commit -m "feat(guardian): inline review path with report fallback in runner/script

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 11: Workflow update + bench honors features/skeptic

**Files:**
- Modify: `.github/workflows/guardian.yml`
- Modify: `scripts/guardian_bench.py`

- [ ] **Step 1: Workflow changes**

In `.github/workflows/guardian.yml`, "Run Guardian Review" step:

1. Add `id: guardian`.
2. Add to its `env:` block:

```yaml
          GH_TOKEN: ${{ github.token }}
          GITHUB_REPOSITORY: ${{ github.repository }}
          GUARDIAN_FEATURES: ${{ vars.GUARDIAN_FEATURES }}
          GUARDIAN_SKEPTIC: ${{ vars.GUARDIAN_SKEPTIC }}
          GUARDIAN_SKEPTIC_MODEL: ${{ vars.GUARDIAN_SKEPTIC_MODEL }}
```

3. Append `--inline \` to the script invocation (before `--base-branch`).

Make the peter-evans step conditional (spec §6.5 — old path kept until inline
proves itself on live PRs):

```yaml
      - name: Post review as PR comment
        if: steps.guardian.outputs.posted_inline != 'true'
        uses: peter-evans/create-or-update-comment@a111a2c3bacd7be7898ee22d0d71d9aec2bb972c  # v4
        with:
          issue-number: ${{ env.PR_NUMBER }}
          body-path: guardian_report.md
```

Note: `permissions: pull-requests: write` already present on the job — the
`gh api .../reviews` POST works with the default token.

- [ ] **Step 2: Bench changes**

In `scripts/guardian_bench.py` `_run_one`:

```python
provider, model = build_provider(os.environ)
primary = "mistral" if isinstance(provider, MistralProvider) else "gemini"
skeptic = build_skeptic_provider(os.environ, primary=primary)
features = parse_features(os.environ.get("GUARDIAN_FEATURES", ""))
```

Pass `features=features` to the `ContextCollector(...)` call and
`skeptic_provider=skeptic[0] if skeptic else None` to `GuardianReviewer(...)`.

Score over the **post-skeptic visible** findings but dump everything:

```python
visible = visible_findings(result.findings)
matches = match_findings(visible, truth)
```

and extend the JSONL entry with:

```python
"skeptic_model": skeptic[1] if skeptic else None,
"skeptic_status": result.skeptic_status,
"findings": [f.model_dump() for f in result.findings],  # unchanged — includes refuted
```

(The killed-true-positive check in Task 12 compares `findings` with verdicts
against `matched` — refuted findings stay visible to analysis.)

Imports: `from cgis.guardian.collector import ContextCollector, parse_features`,
`from cgis.guardian.providers.mistral import MistralProvider`,
`from cgis.guardian.runner import build_provider, build_skeptic_provider`,
`from cgis.guardian.skeptic import visible_findings`.

- [ ] **Step 3: Validate workflow syntax, run gates, commit**

Run: `uvx --from yamllint yamllint .github/workflows/guardian.yml || true` (advisory)
Run: `make format && make lint && make type-check && make pytest && make doc-coverage`

```bash
git add .github/workflows/guardian.yml scripts/guardian_bench.py
git commit -m "feat(guardian): wire features/skeptic/inline into workflow and bench

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 12: CONTROLLER — ablation benchmark for context upgrades (spec §4, §9)

> Controller task: requires `.env` API keys and spends real tokens (~$0.5–1.0
> total on gemini billing). Coordinate with the user before the big sweeps.

- [ ] **Step 1: Re-baseline check on the production model.** CI runs
  `vars.GUARDIAN_MODEL=gemini-3.5-flash`, but the committed baseline used
  gemini-2.5-flash. Run `GUARDIAN_PROVIDER=gemini GUARDIAN_MODEL=gemini-3.5-flash GUARDIAN_SKEPTIC=off uv run python scripts/guardian_bench.py --runs 3`
  (all 6 PRs) → this is the **production baseline** for the ablations below.
- [ ] **Step 2: Ablation arms**, each all-6-PRs, N=1 (variance known from
  baseline N=3), `GUARDIAN_SKEPTIC=off`, same model as Step 1:
  - `GUARDIAN_FEATURES=full_files`
  - `GUARDIAN_FEATURES=flow`
  - `GUARDIAN_FEATURES=drift`
  - `GUARDIAN_FEATURES=full_files,flow,drift`
- [ ] **Step 3: Decide.** A feature ships enabled (set repo var
  `GUARDIAN_FEATURES`) iff it raises recall without raising noise
  disproportionately; a section that adds tokens without recall gets dropped
  (flag stays off — one-line config, no revert). Expected lever: `full_files`
  on the three recall=0 large-diff PRs.
- [ ] **Step 4: Commit `results.jsonl` + summarize the ablation table in the PR description.**

---

### Task 13: CONTROLLER — skeptic benchmark gate (spec §5.6)

> Controller task: real tokens. The gate is strict — across the whole set,
> multi-pass may lose AT MOST ONE previously-matched ground-truth finding.

- [ ] **Step 1: Skeptic arms** (winning `GUARDIAN_FEATURES` from Task 12 held
  constant), all 6 PRs, N=1:
  - finder gemini-3.5-flash + `GUARDIAN_SKEPTIC=gemini GUARDIAN_SKEPTIC_MODEL=gemini-2.5-flash`
  - finder gemini-2.5-flash + `GUARDIAN_SKEPTIC=gemini GUARDIAN_SKEPTIC_MODEL=gemini-3.5-flash`
  - finder gemini + `GUARDIAN_SKEPTIC=mistral` — expect skeptic degradation
    (`skeptic_status=failed`) on the 3 large PRs; still measures cross-provider
    value on the small ones
  - optional cheap arm: `GUARDIAN_SKEPTIC_MODEL=gemma-4` via the gemini API —
    low expectations (refutation needs reasoning), include only if quota trivial
- [ ] **Step 2: Gate check** per arm: noise strictly down vs the Task 12
  winner AND lost ground-truth matches ≤ 1 (compare `matched` sets; a finding
  present in `findings` with `verdict=refuted` that was previously matched =
  a kill). If every arm over-kills: flip the stance to confirm-by-default in
  `SKEPTIC_SYSTEM_PROMPT` (the most sensitive dial, spec §5.6) and re-run the
  best arm once.
- [ ] **Step 3: Set repo vars** (`GUARDIAN_SKEPTIC`, `GUARDIAN_SKEPTIC_MODEL`)
  to the winning arm — or `GUARDIAN_SKEPTIC=off` if no arm passes the gate.
- [ ] **Step 4: Commit `results.jsonl` + decision summary in the PR.**

---

### Task 14: Final verification, PR, live smoke

- [ ] **Step 1:** `make format && make lint && make type-check && make pytest && make doc-coverage` — all green.
- [ ] **Step 2:** Push branch, open PR titled
  `feat(guardian): context upgrades, cross-model skeptic, inline comments`
  with the ablation + skeptic tables in the description. Request review
  (gemini-code-assist auto-reviews; address per receiving-code-review).
- [ ] **Step 3:** Live smoke on the PR itself: comment `/guardian review` —
  verify the inline review lands (or the fallback comment posts with
  `posted_inline=false` in the step output). Spec §6.5: peter-evans path is
  NOT deleted in this PR.
- [ ] **Step 4:** Squash-merge on explicit user confirmation only.

---

## Self-Review Notes

- **Spec coverage:** §4.1→Task 2, §4.2→Task 3, §4.3→Task 4, §4.4→degradation
  asserted in Tasks 2–4 tests, §5.1–5.3→Tasks 5–6, §5.4→`test_skeptic_not_called_on_lgtm`,
  §5.5→Task 6 runner + §5.6→Task 13, §6.1–6.4→Tasks 8–9, §6.5→Tasks 10–11,
  §6.6→no code needed (new review per run is the API default), §7 table→covered
  by degradation paths in Tasks 4/6/7/10, §8 smoke test→Task 10 Step 1.
- **Deviation from spec §5.5 (documented in Task 6):** skeptic model is
  independently configurable; default pairing unchanged. Driven by the
  measured mistral free-tier token cap; benchmark decides production config.
- **Type consistency:** `run_guardian` returns `tuple[str, bool]` (Tasks 10–11
  consumers updated); `build_skeptic_provider` returns
  `tuple[BaseProvider, str] | None` everywhere; `skeptic_status` literal
  `"off"|"ok"|"failed"` in findings/render/metrics/bench.
- **Known sequencing constraint:** Task 7 imports `visible_findings` from
  Task 5's module; Task 9 imports both Task 5 and Task 7 artifacts; Task 10
  imports Tasks 8–9. Execute in order.




