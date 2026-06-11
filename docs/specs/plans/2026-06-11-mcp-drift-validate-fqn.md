# MCP drift/validate tools + suffix FQN resolution — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose `cgis drift` / `cgis validate` as MCP tools and make FQN lookups forgiving (dot-boundary suffix resolution) in both MCP tools and CLI commands (issue #145).

**Architecture:** A new `SQLiteStore.find_nodes_by_suffix` powers a shared `resolve_fqn` helper (`query/fqn.py`) used by 3 MCP nav tools and 3 CLI commands. Drift orchestration moves out of the CLI handler into `query/drift_service.py::analyze_drift`, consumed by both the CLI and the new `cgis_drift` MCP tool. `cgis_validate` wraps `get_edge_stats()` directly.

**Tech Stack:** Python 3.12, FastMCP, Typer, SQLite, pytest. MyPy strict; interrogate ≥90% (every new function needs a docstring).

**Spec:** `docs/specs/2026-06-11-mcp-drift-validate-fqn-design.md`

**Branch:** `feat/issue-145-mcp-drift-validate` (already created, spec committed).

**Conventions:** real `SQLiteStore` fixtures in tests (no mocks of the store); MCP tools return strings, never raise (STDIO must stay clean); run `make format && make lint && make type-check` before each commit; `uv run pytest <file> -v` for the task's tests.

---

### Task 1: `SQLiteStore.find_nodes_by_suffix`

**Files:**
- Modify: `src/cgis/storage/sqlite_store.py` (add method after `get_node`, ~line 229)
- Test: `tests/unit/test_sqlite_store.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_sqlite_store.py` (it already imports `Node`, `NodeType`, `SQLiteStore`, `Path`, `pytest` — check head of file and reuse existing imports; add any missing):

```python
def _suffix_store(tmp_path: Path, ids: list[str]) -> SQLiteStore:
    """Open a store seeded with FUNCTION nodes for the given FQNs."""
    store = SQLiteStore(str(tmp_path / "suffix.db"))
    store.connect()
    nodes = [
        Node(id=i, type=NodeType.FUNCTION, name=i.rsplit(".", 1)[-1],
             file_path="f.py", start_line=1, end_line=2)
        for i in ids
    ]
    store.save_graph(nodes, [])
    return store


def test_find_nodes_by_suffix_exact_match_wins(tmp_path: Path) -> None:
    store = _suffix_store(tmp_path, ["a.b.run", "c.a.b.run"])
    result = store.find_nodes_by_suffix("a.b.run")
    assert [n.id for n in result] == ["a.b.run"]
    store.close()


def test_find_nodes_by_suffix_dot_boundary(tmp_path: Path) -> None:
    store = _suffix_store(tmp_path, ["a.b.run", "c.run", "x.dry_run"])
    result = store.find_nodes_by_suffix("run")
    assert [n.id for n in result] == ["a.b.run", "c.run"]  # NOT x.dry_run
    store.close()


def test_find_nodes_by_suffix_escapes_like_wildcards(tmp_path: Path) -> None:
    store = _suffix_store(tmp_path, ["a.tv_distance", "a.tvxdistance"])
    result = store.find_nodes_by_suffix("tv_distance")
    assert [n.id for n in result] == ["a.tv_distance"]  # _ is literal
    store.close()


def test_find_nodes_by_suffix_dotted_partial(tmp_path: Path) -> None:
    store = _suffix_store(tmp_path, ["src.cgis.query.triads.tv_distance"])
    result = store.find_nodes_by_suffix("triads.tv_distance")
    assert [n.id for n in result] == ["src.cgis.query.triads.tv_distance"]
    store.close()


def test_find_nodes_by_suffix_orders_and_limits(tmp_path: Path) -> None:
    store = _suffix_store(tmp_path, [f"m{i}.go" for i in range(5)])
    result = store.find_nodes_by_suffix("go", limit=3)
    assert [n.id for n in result] == ["m0.go", "m1.go", "m2.go"]
    store.close()


def test_find_nodes_by_suffix_no_match_returns_empty(tmp_path: Path) -> None:
    store = _suffix_store(tmp_path, ["a.b"])
    assert store.find_nodes_by_suffix("zzz") == []
    store.close()


def test_find_nodes_by_suffix_closed_store_raises(tmp_path: Path) -> None:
    store = SQLiteStore(str(tmp_path / "closed.db"))
    with pytest.raises(RuntimeError):
        store.find_nodes_by_suffix("x")
```

NOTE: check how existing tests in this file open the store — if they use a
context manager or a fixture, mirror that style instead of `connect()/close()`.
The assertions stay the same.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_sqlite_store.py -k suffix -v`
Expected: FAIL — `AttributeError: 'SQLiteStore' object has no attribute 'find_nodes_by_suffix'`

- [ ] **Step 3: Implement the method**

In `src/cgis/storage/sqlite_store.py`, after `get_node` (~line 229):

```python
    def find_nodes_by_suffix(self, name: str, limit: int = 10) -> list[Node]:
        """Find nodes whose FQN equals ``name`` or ends with ``.name``.

        An exact match wins and is returned alone. Otherwise dot-boundary
        suffix matches are returned ordered by id, capped at ``limit``.
        LIKE wildcards in ``name`` (``%``, ``_``) are escaped — they are
        literal characters in FQNs.
        """
        if not self._conn:
            raise RuntimeError(self._error_message)
        exact = self.get_node(name)
        if exact:
            return [exact]
        escaped = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        cursor = self._conn.execute(
            "SELECT * FROM nodes WHERE id LIKE ? ESCAPE '\\' ORDER BY id LIMIT ?",
            (f"%.{escaped}", limit),
        )
        return [self._row_to_node(row) for row in cursor.fetchall()]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_sqlite_store.py -v`
Expected: all PASS (new + existing).

- [ ] **Step 5: Gates + commit**

```bash
make format && make lint && make type-check
git add src/cgis/storage/sqlite_store.py tests/unit/test_sqlite_store.py
git commit -m "feat(storage): find_nodes_by_suffix — dot-boundary FQN suffix lookup (#145)"
```

---

### Task 2: `resolve_fqn` helper

**Files:**
- Create: `src/cgis/query/fqn.py`
- Test: `tests/unit/test_fqn.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_fqn.py`:

```python
"""Unit tests for suffix-based FQN resolution."""

from pathlib import Path

from cgis.core.models import Node, NodeType
from cgis.query.fqn import resolve_fqn
from cgis.storage.sqlite_store import SQLiteStore


def _store(tmp_path: Path, ids: list[str]) -> SQLiteStore:
    """Open a store seeded with FUNCTION nodes for the given FQNs."""
    store = SQLiteStore(str(tmp_path / "fqn.db"))
    store.connect()
    nodes = [
        Node(id=i, type=NodeType.FUNCTION, name=i.rsplit(".", 1)[-1],
             file_path="f.py", start_line=1, end_line=2)
        for i in ids
    ]
    store.save_graph(nodes, [])
    return store


def test_exact_match(tmp_path: Path) -> None:
    store = _store(tmp_path, ["a.b.fn", "c.fn"])
    res = resolve_fqn(store, "a.b.fn")
    assert res.resolved == "a.b.fn"
    assert res.via_suffix is False
    assert res.candidates == []
    store.close()


def test_unique_suffix_resolves(tmp_path: Path) -> None:
    store = _store(tmp_path, ["src.cgis.query.triads.tv_distance"])
    res = resolve_fqn(store, "tv_distance")
    assert res.resolved == "src.cgis.query.triads.tv_distance"
    assert res.via_suffix is True
    store.close()


def test_ambiguous_returns_candidates(tmp_path: Path) -> None:
    store = _store(tmp_path, ["a.fn", "b.fn"])
    res = resolve_fqn(store, "fn")
    assert res.resolved is None
    assert res.candidates == ["a.fn", "b.fn"]
    store.close()


def test_no_match(tmp_path: Path) -> None:
    store = _store(tmp_path, ["a.fn"])
    res = resolve_fqn(store, "ghost")
    assert res.resolved is None
    assert res.candidates == []
    store.close()
```

(Adapt `connect()/close()` to the store-opening style confirmed in Task 1.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_fqn.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cgis.query.fqn'`

- [ ] **Step 3: Implement**

Create `src/cgis/query/fqn.py`:

```python
"""Suffix-based FQN resolution shared by CLI commands and MCP tools.

Fixes the prefix-mismatch UX wart (#145): a graph ingested from ``src``
holds ``cgis.*`` FQNs while users (and agents) often pass ``src.cgis.*``
or bare names. A unique dot-boundary suffix match resolves silently;
ambiguity surfaces the candidates instead of a bare "not found".
"""

from dataclasses import dataclass, field

from cgis.storage.sqlite_store import SQLiteStore


@dataclass(frozen=True)
class FqnResolution:
    """Outcome of resolving a possibly-partial FQN against the graph."""

    resolved: str | None
    candidates: list[str] = field(default_factory=list)
    via_suffix: bool = False


def resolve_fqn(store: SQLiteStore, fqn: str) -> FqnResolution:
    """Resolve ``fqn`` exactly, or by unique dot-boundary suffix match.

    Exact hit resolves as-is; a single suffix hit resolves to the full FQN
    with ``via_suffix=True``; several hits return candidates; no hit returns
    an empty resolution.
    """
    matches = store.find_nodes_by_suffix(fqn)
    if not matches:
        return FqnResolution(resolved=None)
    if matches[0].id == fqn:
        return FqnResolution(resolved=fqn)
    if len(matches) == 1:
        return FqnResolution(resolved=matches[0].id, via_suffix=True)
    return FqnResolution(resolved=None, candidates=[n.id for n in matches])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_fqn.py -v` — all PASS.

- [ ] **Step 5: Gates + commit**

```bash
make format && make lint && make type-check
git add src/cgis/query/fqn.py tests/unit/test_fqn.py
git commit -m "feat(query): resolve_fqn — exact-or-suffix FQN resolution helper (#145)"
```

---

### Task 3: `analyze_drift` service

**Files:**
- Create: `src/cgis/query/drift_service.py`
- Test: `tests/unit/test_drift_service.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_drift_service.py`. Build a tiny real graph + the
minimal patterns YAML (copy the `_YAML` style from `tests/unit/test_drift.py`,
which uses prefixes `cgis.extractors` / `cgis.resolver`):

```python
"""Unit tests for the shared drift-analysis service."""

from pathlib import Path

import pytest

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.query.drift_service import DriftAnalysis, analyze_drift
from cgis.storage.sqlite_store import SQLiteStore

_YAML = """\
version: "1.0.0"
drift_weights:
  hub_count:        0.15
  star_count:       0.15
  chain_len:        0.10
  dag_depth:        0.10
  router_count:     0.10
  cycle_ratio:      0.25
  unresolved_ratio: 0.15
patterns:
  pure_utility:
    description: "Hub pattern"
    cycle_ratio:      {max: 0.0}
project_domains:
  - name: "extraction"
    fqn_prefix: "cgis.extractors"
    expected_pattern: pure_utility
    drift_tolerance: 0.15
"""


@pytest.fixture
def graph_db(tmp_path: Path) -> str:
    """A db with two extractor functions, one calling the other."""
    db = str(tmp_path / "g.db")
    nodes = [
        Node(id="cgis.extractors.a", type=NodeType.FUNCTION, name="a",
             file_path="a.py", start_line=1, end_line=2),
        Node(id="cgis.extractors.b", type=NodeType.FUNCTION, name="b",
             file_path="b.py", start_line=1, end_line=2),
    ]
    edges = [Edge(id="e1", source="cgis.extractors.a",
                  target="cgis.extractors.b", type=EdgeType.CALLS)]
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)
    return db


@pytest.fixture
def patterns_file(tmp_path: Path) -> str:
    p = tmp_path / "patterns.yaml"
    p.write_text(_YAML)
    return str(p)


def test_analyze_drift_returns_report_per_domain(
    graph_db: str, patterns_file: str
) -> None:
    analysis = analyze_drift(graph_db, patterns_file)
    assert isinstance(analysis, DriftAnalysis)
    assert len(analysis.reports) == 1
    assert analysis.reports[0].fqn_prefix == "cgis.extractors"
    assert analysis.quotient == []  # no project_level in YAML


def test_analyze_drift_any_critical_threshold(
    graph_db: str, patterns_file: str
) -> None:
    lenient = analyze_drift(graph_db, patterns_file, max_drift=1.0)
    assert lenient.any_critical is False
    strict = analyze_drift(graph_db, patterns_file, max_drift=0.0)
    assert strict.any_critical is True  # any score >= 0.0 trips it


def test_analyze_drift_missing_patterns_raises(graph_db: str, tmp_path: Path) -> None:
    with pytest.raises(Exception):
        analyze_drift(graph_db, str(tmp_path / "nope.yaml"))
```

NOTE for the quotient path: it is covered by the regression test in Task 4
(real `docs/ontology/patterns.yaml` has `project_level`); do not duplicate a
quotient fixture here unless trivial.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_drift_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cgis.query.drift_service'`

- [ ] **Step 3: Implement**

Create `src/cgis/query/drift_service.py` — this is the orchestration currently
inlined in `cli.py::drift` (lines ~833–862), moved verbatim:

```python
"""Drift-analysis orchestration shared by the CLI and the MCP server."""

from dataclasses import dataclass

from cgis.query.drift import DomainConfig, DriftReport, DriftScorer
from cgis.query.fingerprint import FingerprintExtractor
from cgis.query.quotient import build_quotient
from cgis.storage.sqlite_store import SQLiteStore


@dataclass(frozen=True)
class DriftAnalysis:
    """Full drift run: per-domain reports plus the quotient (k=1) layer."""

    reports: list[DriftReport]
    quotient: list[tuple[DomainConfig, DriftReport]]
    any_critical: bool


def analyze_drift(
    db_path: str, patterns_path: str, max_drift: float = 0.50
) -> DriftAnalysis:
    """Score every project domain (and the quotient level) against patterns.

    Raises on unreadable inputs — callers translate errors to their medium.
    ``any_critical`` counts quotient bindings only when they are enforced.
    """
    scorer = DriftScorer(patterns_path)
    domains = scorer.load_project_domains()

    reports: list[DriftReport] = []
    quotient: list[tuple[DomainConfig, DriftReport]] = []
    with SQLiteStore(db_path) as store:
        extractor = FingerprintExtractor(store)
        for domain in domains:
            reports.append(scorer.score(extractor.extract(domain.fqn_prefix), domain))
        level_bindings = scorer.load_project_level()
        if level_bindings:
            qnodes, qedges = build_quotient(
                store.get_all_nodes(), store.get_all_edges(), domains
            )
            q_extractor = FingerprintExtractor.from_graph(qnodes, qedges)
            quotient = [
                (b, scorer.score(q_extractor.extract(b.fqn_prefix), b))
                for b in level_bindings
            ]

    any_critical = any(r.drift_score >= max_drift for r in reports) or any(
        r.drift_score >= max_drift for b, r in quotient if b.enforce
    )
    return DriftAnalysis(reports=reports, quotient=quotient, any_critical=any_critical)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_drift_service.py -v` — all PASS.

- [ ] **Step 5: Gates + commit**

```bash
make format && make lint && make type-check
git add src/cgis/query/drift_service.py tests/unit/test_drift_service.py
git commit -m "feat(query): extract analyze_drift service from the CLI handler (#145)"
```

---

### Task 4: CLI `drift` refactor onto the service

**Files:**
- Modify: `src/cgis/cli.py` (the `drift` command body, ~lines 825–888; imports ~line 21–26)
- Test: `tests/unit/test_cli.py` (existing drift tests are the regression net; add a JSON-shape regression only if none exists — check `grep -n "drift" tests/unit/test_cli.py` first)

- [ ] **Step 1: Capture the current JSON output as the regression oracle**

Check existing coverage: `grep -n "drift" tests/unit/test_cli.py`. If a JSON
test exists, keep it untouched. If not, add (using this file's existing
fixture style — it builds graphs via `SQLiteStore` and invokes `runner.invoke(app, [...])`):

```python
def test_drift_json_shape_unchanged(tmp_path: Path) -> None:
    """The --format json payload stays a flat list of report dicts."""
    db = str(tmp_path / "g.db")
    with SQLiteStore(db) as store:
        store.save_graph(
            [Node(id="cgis.extractors.a", type=NodeType.FUNCTION, name="a",
                  file_path="a.py", start_line=1, end_line=2)],
            [],
        )
    patterns = tmp_path / "patterns.yaml"
    patterns.write_text(
        'version: "1.0.0"\n'
        "drift_weights:\n  cycle_ratio: 1.0\n"
        "patterns:\n  pure_utility:\n    description: x\n"
        "    cycle_ratio: {max: 0.0}\n"
        "project_domains:\n"
        '  - name: extraction\n    fqn_prefix: "cgis.extractors"\n'
        "    expected_pattern: pure_utility\n    drift_tolerance: 0.15\n"
    )
    result = runner.invoke(
        app, ["drift", "--db", db, "--patterns", str(patterns), "--format", "json"]
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    assert payload[0]["fqn_prefix"] == "cgis.extractors"
```

Run: `uv run pytest tests/unit/test_cli.py -k drift -v` — must PASS **before**
the refactor (it pins current behavior).

- [ ] **Step 2: Refactor the command body**

In `src/cgis/cli.py`:
1. Add import: `from cgis.query.drift_service import analyze_drift` (keep
   `DriftReport` import — `_render_drift_table` uses it; drop
   `DomainConfig`, `FingerprintExtractor`, `build_quotient` imports if now
   unused — `make lint` will tell).
2. Replace the orchestration block (everything between the patterns-file
   guard and the output rendering, currently ~lines 833–862) with:

```python
    try:
        analysis = analyze_drift(db, patterns, max_drift=max_drift)
    except Exception as e:
        console.print(f"[bold red]❌ Error during drift analysis:[/bold red] {e}")
        raise typer.Exit(code=1) from e

    if output_format == DriftOutputFormat.JSON:
        payload = [dataclasses.asdict(r) for r in analysis.reports]
        payload += [
            {**dataclasses.asdict(r), "enforce": b.enforce}
            for b, r in analysis.quotient
        ]
        typer.echo(_json.dumps(payload, indent=2))
        if analysis.any_critical:
            raise typer.Exit(code=1)
        return

    _render_drift_table(analysis.reports, max_drift)

    for b, qr in analysis.quotient:
        marker = "" if b.enforce else " [dim](observe-only)[/dim]"
        console.print(
            f"Quotient k=1 \\[{b.name}] vs {qr.expected_pattern}: "
            f"drift={qr.drift_score:.2f}{marker}"
        )

    if analysis.any_critical:
        console.print("[bold red]❌ One or more domains exceed the drift threshold.[/bold red]")
        raise typer.Exit(code=1)

    console.print("[bold green]✅ All domains within tolerance.[/bold green]")
```

- [ ] **Step 3: Run the full test suite**

Run: `uv run pytest tests/unit/test_cli.py tests/unit/test_drift_service.py -v`
Expected: PASS, including the self-drift ratchet suite:
`uv run pytest tests/self_parsing/ -v` (drift values must not move — the
service is a verbatim extraction).

- [ ] **Step 4: Gates + commit**

```bash
make format && make lint && make type-check
git add src/cgis/cli.py tests/unit/test_cli.py
git commit -m "refactor(cli): drift command delegates to analyze_drift service (#145)"
```

---

### Task 5: `cgis_drift` MCP tool

**Files:**
- Modify: `src/cgis/api/mcp_server.py`
- Test: `tests/unit/test_mcp_server.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_mcp_server.py` (add `import json` and the new tool
import at the top; reuse the `repo_with_calls` fixture pattern):

```python
_PATTERNS_YAML = """\
version: "1.0.0"
drift_weights:
  cycle_ratio: 1.0
patterns:
  pure_utility:
    description: "x"
    cycle_ratio: {max: 0.0}
project_domains:
  - name: "modroot"
    fqn_prefix: "mod"
    expected_pattern: pure_utility
    drift_tolerance: 0.15
"""


def test_cgis_drift_returns_json(repo_with_calls: tuple[Path, Path]) -> None:
    repo, db = repo_with_calls
    cgis_ingest(str(repo), str(db))
    patterns = repo / "patterns.yaml"
    patterns.write_text(_PATTERNS_YAML, encoding="utf-8")

    result = cgis_drift(str(db), str(patterns))

    payload = json.loads(result)
    assert payload["any_critical"] is False
    assert payload["domains"][0]["fqn_prefix"] == "mod"
    assert payload["quotient"] == []
    assert payload["max_drift"] == 0.50


def test_cgis_drift_missing_db_returns_error(tmp_path: Path) -> None:
    result = cgis_drift(str(tmp_path / "no.db"), str(tmp_path / "p.yaml"))
    assert "❌" in result


def test_cgis_drift_missing_patterns_returns_error(
    repo_with_calls: tuple[Path, Path],
) -> None:
    repo, db = repo_with_calls
    cgis_ingest(str(repo), str(db))
    result = cgis_drift(str(db), str(repo / "missing.yaml"))
    assert "❌" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_mcp_server.py -k drift -v`
Expected: FAIL — `ImportError: cannot import name 'cgis_drift'`

- [ ] **Step 3: Implement**

In `src/cgis/api/mcp_server.py`, add imports
(`import dataclasses`, `import json`,
`from cgis.query.drift_service import analyze_drift`) and the tool:

```python
@mcp.tool()
def cgis_drift(
    db_path: str = _DEFAULT_DB,
    patterns_path: str = "docs/ontology/patterns.yaml",
    max_drift: float = 0.50,
) -> str:
    """Report per-domain architectural drift against declared ideal patterns.

    Returns JSON: ``any_critical`` verdict, per-domain reports and the
    observe-only quotient layer. Call after ``cgis_ingest`` to learn whether
    your edits pushed a domain past its drift tolerance.
    """
    if not Path(db_path).exists():
        return f"❌ Database not found at: {db_path}. Run cgis_ingest first."
    if not Path(patterns_path).exists():
        return f"❌ Patterns file not found: {patterns_path}"
    try:
        analysis = analyze_drift(db_path, patterns_path, max_drift=max_drift)
    except Exception as exc:
        return f"❌ {exc}"
    payload = {
        "any_critical": analysis.any_critical,
        "max_drift": max_drift,
        "domains": [dataclasses.asdict(r) for r in analysis.reports],
        "quotient": [
            {**dataclasses.asdict(r), "enforce": b.enforce}
            for b, r in analysis.quotient
        ],
    }
    return json.dumps(payload, indent=2)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_mcp_server.py -v` — all PASS.

- [ ] **Step 5: Gates + commit**

```bash
make format && make lint && make type-check
git add src/cgis/api/mcp_server.py tests/unit/test_mcp_server.py
git commit -m "feat(mcp): cgis_drift tool — JSON drift report over MCP (#145)"
```

---

### Task 6: `cgis_validate` MCP tool

**Files:**
- Modify: `src/cgis/api/mcp_server.py`
- Test: `tests/unit/test_mcp_server.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
def test_cgis_validate_returns_json(repo_with_calls: tuple[Path, Path]) -> None:
    repo, db = repo_with_calls
    cgis_ingest(str(repo), str(db))

    result = cgis_validate(str(db))

    payload = json.loads(result)
    assert payload["total"] > 0
    assert payload["threshold"] == 0.30
    assert isinstance(payload["healthy"], bool)
    assert isinstance(payload["top_unresolved"], list)
    for name, _count in payload["top_unresolved"]:
        assert not name.startswith("raw_call:")


def test_cgis_validate_missing_db_returns_error(tmp_path: Path) -> None:
    result = cgis_validate(str(tmp_path / "no.db"))
    assert "❌" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_mcp_server.py -k validate -v`
Expected: FAIL — `ImportError: cannot import name 'cgis_validate'`

- [ ] **Step 3: Implement**

Add to imports: `from cgis.storage.sqlite_store import RAW_CALL_PREFIX, SQLiteStore`
(the store import already exists — extend it). Add the tool:

```python
@mcp.tool()
def cgis_validate(db_path: str = _DEFAULT_DB, threshold: float = 0.30) -> str:
    """Report graph integrity as JSON: edge resolution stats + health verdict.

    Check this before trusting ``cgis_analyze_impact`` output — a high
    unresolved ratio means callers are missing from the graph.
    """
    if not Path(db_path).exists():
        return f"❌ Database not found at: {db_path}. Run cgis_ingest first."
    try:
        with SQLiteStore(db_path) as store:
            stats = store.get_edge_stats()
    except Exception as exc:
        return f"❌ {exc}"
    payload = {
        "total": stats.total,
        "resolved": stats.resolved,
        "stdlib": stats.stdlib,
        "external": stats.external,
        "unresolved": stats.unresolved,
        "unresolved_ratio": stats.unresolved_ratio,
        "top_unresolved": [
            [t.removeprefix(RAW_CALL_PREFIX), c] for t, c in stats.top_unresolved
        ],
        "threshold": threshold,
        "healthy": stats.unresolved_ratio <= threshold,
    }
    return json.dumps(payload, indent=2)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_mcp_server.py -v` — all PASS.

- [ ] **Step 5: Gates + commit**

```bash
make format && make lint && make type-check
git add src/cgis/api/mcp_server.py tests/unit/test_mcp_server.py
git commit -m "feat(mcp): cgis_validate tool — graph integrity JSON over MCP (#145)"
```

---

### Task 7: suffix resolution in the MCP nav tools

**Files:**
- Modify: `src/cgis/api/mcp_server.py` (`cgis_trace_flow`, `cgis_analyze_impact`, `cgis_get_structure`)
- Test: `tests/unit/test_mcp_server.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
def test_cgis_analyze_impact_resolves_suffix(repo_with_calls: tuple[Path, Path]) -> None:
    repo, db = repo_with_calls
    cgis_ingest(str(repo), str(db))

    result = cgis_analyze_impact("callee", str(db), depth=3)  # bare name

    assert "```mermaid" in result
    assert "Resolved 'callee'" in result
    assert "mod.callee" in result


def test_cgis_trace_flow_ambiguous_lists_candidates(tmp_path: Path) -> None:
    (tmp_path / "one.py").write_text("def fn(): pass\n", encoding="utf-8")
    (tmp_path / "two.py").write_text("def fn(): pass\n", encoding="utf-8")
    db = tmp_path / "graph.db"
    cgis_ingest(str(tmp_path), str(db))

    result = cgis_trace_flow("fn", str(db))

    assert "❌ Ambiguous FQN 'fn'" in result
    assert "one.fn" in result
    assert "two.fn" in result


def test_cgis_get_structure_exact_fqn_has_no_note(
    repo_with_calls: tuple[Path, Path],
) -> None:
    repo, db = repo_with_calls
    cgis_ingest(str(repo), str(db))

    result = cgis_get_structure("mod.caller", str(db))

    assert "```mermaid" in result
    assert "Resolved" not in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_mcp_server.py -k "suffix or ambiguous or no_note" -v`
Expected: FAIL — suffix call returns `❌ FQN not found`.

- [ ] **Step 3: Implement**

Add import `from cgis.query.fqn import resolve_fqn` and a module helper:

```python
def _resolution_error(fqn: str, candidates: list[str]) -> str:
    """Render a not-found / ambiguous FQN error for tool output."""
    if candidates:
        listing = "\n".join(f"- {c}" for c in candidates)
        return f"❌ Ambiguous FQN '{fqn}'. Candidates:\n{listing}"
    return f"❌ FQN not found in graph: {fqn}"
```

Rework each nav tool on the same template (shown for `cgis_trace_flow`; apply
identically to `cgis_analyze_impact` and `cgis_get_structure`, keeping each
tool's own heading text):

```python
    if not Path(db_path).exists():
        return f"❌ Database not found at: {db_path}. Run cgis_ingest first."
    try:
        with SQLiteStore(db_path) as store:
            res = resolve_fqn(store, fqn)
            if res.resolved is None:
                return _resolution_error(fqn, res.candidates)
            nodes, edges = QueryEngine(store).get_flow_graph(res.resolved, max_depth=depth)
    except Exception as exc:
        return f"❌ {exc}"

    if not nodes:
        return f"❌ FQN not found in graph: {fqn}"

    note = f"> Resolved '{fqn}' → '{res.resolved}'\n\n" if res.via_suffix else ""
    diagram = MermaidCompiler().compile(nodes, edges)
    return f"{note}### Execution flow for `{res.resolved}`:\n\n```mermaid\n{diagram}\n```"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_mcp_server.py -v` — all PASS (including
the pre-existing unknown-FQN tests, whose message is unchanged).

- [ ] **Step 5: Gates + commit**

```bash
make format && make lint && make type-check
git add src/cgis/api/mcp_server.py tests/unit/test_mcp_server.py
git commit -m "feat(mcp): suffix FQN resolution in trace/impact/structure tools (#145)"
```

---

### Task 8: suffix resolution in the CLI commands

**Files:**
- Modify: `src/cgis/cli.py` — `trace` (~line 303), `impact` (~line 425), `structure` (~line 565)
- Test: `tests/unit/test_cli.py` (append)

- [ ] **Step 1: Write the failing tests**

Append (reuse this file's graph-building style; `runner` exists at module level):

```python
def _fqn_db(tmp_path: Path) -> str:
    """A db with one caller→callee pair under a deep module path."""
    db = str(tmp_path / "g.db")
    nodes = [
        Node(id="src.app.mod.caller", type=NodeType.FUNCTION, name="caller",
             file_path="mod.py", start_line=1, end_line=2),
        Node(id="src.app.mod.callee", type=NodeType.FUNCTION, name="callee",
             file_path="mod.py", start_line=3, end_line=4),
    ]
    edges = [Edge(id="e", source="src.app.mod.caller",
                  target="src.app.mod.callee", type=EdgeType.CALLS)]
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)
    return db


def test_impact_resolves_suffix_with_note(tmp_path: Path) -> None:
    db = _fqn_db(tmp_path)
    result = runner.invoke(app, ["impact", "callee", "--db", db])
    assert result.exit_code == 0
    assert "Resolved 'callee'" in result.stdout
    assert "src.app.mod.caller" in result.stdout


def test_trace_ambiguous_exits_with_candidates(tmp_path: Path) -> None:
    db = str(tmp_path / "g.db")
    nodes = [
        Node(id="a.fn", type=NodeType.FUNCTION, name="fn",
             file_path="a.py", start_line=1, end_line=2),
        Node(id="b.fn", type=NodeType.FUNCTION, name="fn",
             file_path="b.py", start_line=1, end_line=2),
    ]
    with SQLiteStore(db) as store:
        store.save_graph(nodes, [])
    result = runner.invoke(app, ["trace", "fn", "--db", db])
    assert result.exit_code == 1
    assert "Ambiguous" in result.stdout
    assert "a.fn" in result.stdout
    assert "b.fn" in result.stdout


def test_structure_resolves_suffix(tmp_path: Path) -> None:
    db = _fqn_db(tmp_path)
    result = runner.invoke(app, ["structure", "mod.caller", "--db", db])
    assert result.exit_code == 0
    assert "src.app.mod.caller" in result.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_cli.py -k "suffix or ambiguous" -v`
Expected: FAIL — exit code 1 with "not found in graph".

- [ ] **Step 3: Implement**

Add import `from cgis.query.fqn import resolve_fqn` and a module helper in
`cli.py`:

```python
def _resolve_cli_fqn(store: SQLiteStore, target: str, kind: str) -> str:
    """Resolve a possibly-partial FQN for a CLI command or exit with code 1."""
    resolution = resolve_fqn(store, target)
    if resolution.resolved is None:
        if resolution.candidates:
            console.print(f"[bold red]❌ Ambiguous FQN:[/bold red] {target}")
            for candidate in resolution.candidates:
                console.print(f"  [dim]- {candidate}[/dim]")
        else:
            console.print(f"[bold red]❌ {kind} not found in graph:[/bold red] {target}")
        raise typer.Exit(code=1)
    if resolution.via_suffix:
        console.print(f"[dim]Resolved '{target}' → '{resolution.resolved}'[/dim]")
    return resolution.resolved
```

Then in each command, replace the lookup block. `trace` (current lines 303–306):

```python
    with SQLiteStore(db) as store:
        start = _resolve_cli_fqn(store, start, "Start entity")
        start_node = store.get_node(start)
        if not start_node:  # pragma: no cover — resolved FQNs always exist
            raise typer.Exit(code=1)
```

`impact` (current lines 425–428): same with `target` / `"Target entity"`.
`structure` (current lines 565–568): same with `target` / `"Node"` — note the
file-path→FQN normalization above it stays as-is (it runs before resolution).

The `if not X_node` guard stays only for mypy narrowing (get_node returns
`Node | None`); mark `# pragma: no cover`.

- [ ] **Step 4: Run the full unit suite**

Run: `uv run pytest tests/unit/ -v`
Expected: all PASS — including pre-existing trace/impact/structure tests
(exact-FQN behavior and the not-found message wording for the no-candidates
case are unchanged).

- [ ] **Step 5: Gates + commit**

```bash
make format && make lint && make type-check
git add src/cgis/cli.py tests/unit/test_cli.py
git commit -m "feat(cli): suffix FQN resolution in trace/impact/structure (#145)"
```

---

### Task 9: full verification

- [ ] **Step 1: Run every gate**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
```

Expected: all green; doc coverage ≥90% (new modules `fqn.py` /
`drift_service.py` are fully docstringed per the code above).

- [ ] **Step 2: Self-parsing / drift ratchet check**

Run: `uv run pytest tests/self_parsing/ -v`
Expected: PASS. If the architecture test flags `drift_service` or `fqn` (new
modules in domain counts), update the expectation the way the test file
documents — do NOT loosen ratchet values.

- [ ] **Step 3: Dogfood smoke (manual, no commit)**

```bash
uv run cgis ingest src/cgis --output /tmp/cgis-145.db
uv run cgis impact tv_distance --db /tmp/cgis-145.db          # suffix resolve note
uv run cgis drift --db /tmp/cgis-145.db --format json | head  # unchanged shape
/bin/rm -f /tmp/cgis-145.db
```

- [ ] **Step 4: Commit any stragglers, push, PR**

```bash
git push -u origin feat/issue-145-mcp-drift-validate
gh pr create --title "feat(mcp): drift/validate tools + suffix FQN resolution (closes #145)" --body "<summary + test plan>"
```
