# Graph Freshness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let any cgis query tell its reader that the graph it answered from is out of date.

**Architecture:** An `ingest_state` table records the ingested root, the ingest time, and the file extensions that were ingested. A stat-only probe on `SQLiteStore` compares the files the graph was built from (`nodes.file_path`) against the tree as it is now, returning one of three states. CLI and MCP surface that state only when it is not `FRESH`.

**Tech Stack:** Python 3.12, SQLite (`sqlite3`), Pydantic v2, Typer, FastMCP, pytest, ruff, mypy strict.

**Spec:** `docs/specs/2026-09-09-graph-freshness-design.md`

## Global Constraints

- MyPy runs in **strict** mode. Every function needs full annotations including return types.
- Docstring coverage must stay ≥ 90% (`make doc-coverage`); every public function and class needs one.
- Line length 100 (`make lint`).
- Pydantic models in `core/models.py` are **frozen**; use `model_copy(update={...})`.
- The full gate before every commit: `make format && make lint && make type-check && make pytest && make doc-coverage`.
- The probe must never read file contents — `os.stat` only (spec D5).
- Tracked files come from `nodes.file_path`, never from `files_state` (spec D2, measurement basis).

---

### Task 1: The `Freshness` model and the `ingest_state` table

**Files:**
- Create: `src/cgis/core/freshness.py`
- Modify: `src/cgis/storage/sqlite_store.py` (schema in `_create_schema`, new read/write methods)
- Test: `tests/unit/test_freshness.py`, `tests/unit/test_sqlite_store.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `FreshnessState` (str enum: `FRESH`, `STALE`, `UNKNOWN`), `Freshness` (frozen Pydantic model with `state: FreshnessState`, `changed: int`, `missing: int`, `reason: str | None`), and on `SQLiteStore`: `record_ingest(root: str, extensions: Iterable[str]) -> None` and `get_ingest_state() -> tuple[str, float, tuple[str, ...]] | None`.

- [ ] **Step 1: Write the failing test for the model**

```python
# tests/unit/test_freshness.py
"""Unit tests for the graph-freshness value objects (#175)."""

from cgis.core.freshness import Freshness, FreshnessState


def test_fresh_reports_nothing_to_say() -> None:
    """A fresh graph carries no counts and no reason — nothing to print."""
    f = Freshness(state=FreshnessState.FRESH)
    assert f.changed == 0
    assert f.missing == 0
    assert f.reason is None


def test_unknown_carries_its_reason() -> None:
    """`UNKNOWN` is not `STALE`: the reader is told *why* it cannot be checked.

    Two causes need different remedies — a graph older than the table wants a
    re-ingest, a moved root wants `--root` — so the reason travels with it.
    """
    f = Freshness(state=FreshnessState.UNKNOWN, reason="graph predates the ingest_state table")
    assert f.state is FreshnessState.UNKNOWN
    assert "predates" in (f.reason or "")
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/unit/test_freshness.py -v --no-header`
Expected: FAIL — `ModuleNotFoundError: No module named 'cgis.core.freshness'`

- [ ] **Step 3: Write the model**

```python
# src/cgis/core/freshness.py
"""Whether the graph still matches the tree it was built from (#175).

A query answers from whatever `graph.db` holds, and a graph ingested before the
last edit answers confidently and wrongly. Two reports already carry hand-rolled
staleness counters (`OrphanReport.test_sources`, `.generated_excluded`) because
there was no way to ask the question directly; this is that way.
"""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class FreshnessState(str, Enum):
    """Whether the graph matches the tree, or whether that is even knowable."""

    FRESH = "FRESH"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class Freshness(BaseModel):
    """The probe's answer: a state, what it counted, and why it could not look.

    `UNKNOWN` is a state of its own rather than a pessimistic `STALE` or an
    optimistic `FRESH`. A signal that reads the same when all is well and when it
    could not look is exactly the `generated_excluded == 0` failure #441 found,
    where one number answered two questions and was wrong at the common one.
    """

    model_config = ConfigDict(frozen=True)

    state: FreshnessState
    changed: int = Field(default=0, ge=0)
    missing: int = Field(default=0, ge=0)
    reason: str | None = None
```

- [ ] **Step 4: Run the test and watch it pass**

Run: `uv run pytest tests/unit/test_freshness.py -v --no-header`
Expected: PASS (2 tests)

- [ ] **Step 5: Write the failing test for the store's ingest_state round trip**

```python
# append to tests/unit/test_sqlite_store.py
def test_ingest_state_round_trips(tmp_path: Path) -> None:
    """The store remembers the root, the time and the extensions it ingested (#175)."""
    db_path = str(tmp_path / "g.db")
    with SQLiteStore(db_path) as store:
        store.record_ingest("/abs/repo", [".py", ".ts"])

    with SQLiteStore(db_path) as store:
        recorded = store.get_ingest_state()

    assert recorded is not None
    root, ingested_at, extensions = recorded
    assert root == "/abs/repo"
    assert ingested_at > 0
    assert extensions == (".py", ".ts")


def test_ingest_state_absent_on_an_older_graph(tmp_path: Path) -> None:
    """A graph that predates the table reports nothing, rather than a default (#175).

    Returning a zero timestamp here would make every such graph look freshly
    ingested in 1970 — a `FRESH`-shaped answer to a question that cannot be
    answered. `None` is what forces the `UNKNOWN` branch.
    """
    db_path = str(tmp_path / "g.db")
    with SQLiteStore(db_path) as store:
        assert store.get_ingest_state() is None
```

- [ ] **Step 6: Run it and watch it fail**

Run: `uv run pytest tests/unit/test_sqlite_store.py -k ingest_state -v --no-header`
Expected: FAIL — `AttributeError: 'SQLiteStore' object has no attribute 'record_ingest'`

- [ ] **Step 7: Add the table to the schema**

In `src/cgis/storage/sqlite_store.py`, inside the `schema` string in `_create_schema`, after the `files_state` table:

```sql
        CREATE TABLE IF NOT EXISTS ingest_state (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
```

No entry in `_migrate` is needed or wanted: `CREATE TABLE IF NOT EXISTS` runs on every open, and unlike the `is_generated` column (#441) an absent row misreports nothing about the nodes, so `files_state` must not be touched here.

- [ ] **Step 8: Add the read and write methods**

```python
    def record_ingest(self, root: str, extensions: Iterable[str]) -> None:
        """Record what this graph was built from, for the freshness probe (#175).

        `root` is stored absolute because a relative path is meaningless to a
        later process with a different working directory. `extensions` is stored
        so the probe can reproduce the pipeline's walk without the caller
        threading the extractor registry through every query.
        """
        if not self._conn:
            raise RuntimeError(self._error_message)
        self._conn.executemany(
            "INSERT OR REPLACE INTO ingest_state (key, value) VALUES (?, ?)",
            [
                ("root", str(Path(root).resolve())),
                ("ingested_at", str(time.time())),
                ("extensions", ",".join(sorted(set(extensions)))),
            ],
        )
        self._conn.commit()

    def get_ingest_state(self) -> tuple[str, float, tuple[str, ...]] | None:
        """The recorded (root, ingested_at, extensions), or None on an older graph."""
        if not self._conn:
            raise RuntimeError(self._error_message)
        rows = {
            row["key"]: row["value"]
            for row in self._conn.execute("SELECT key, value FROM ingest_state")
        }
        if not {"root", "ingested_at", "extensions"} <= rows.keys():
            return None
        exts = tuple(e for e in rows["extensions"].split(",") if e)
        return rows["root"], float(rows["ingested_at"]), exts
```

Add `import time` and `from collections.abc import Iterable` to the module's imports if absent; `Path` is already imported.

- [ ] **Step 9: Run the tests and watch them pass**

Run: `uv run pytest tests/unit/test_sqlite_store.py -k ingest_state -v --no-header`
Expected: PASS (2 tests)

- [ ] **Step 10: Run the full gate and commit**

```bash
make format && make lint && make type-check && make doc-coverage
uv run pytest -q --no-header
git add src/cgis/core/freshness.py src/cgis/storage/sqlite_store.py tests/unit/test_freshness.py tests/unit/test_sqlite_store.py
git commit -m "feat(store): record what a graph was ingested from (#175)"
```

---

### Task 2: The probe

**Files:**
- Modify: `src/cgis/storage/sqlite_store.py` (add `freshness`)
- Test: `tests/unit/test_freshness.py`

**Interfaces:**
- Consumes: `Freshness`, `FreshnessState`, `SQLiteStore.get_ingest_state()` from Task 1.
- Produces: `SQLiteStore.freshness(root: str | None = None) -> Freshness`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/unit/test_freshness.py
import os
import time
from pathlib import Path

from cgis.core.models import Node, NodeType
from cgis.storage.sqlite_store import SQLiteStore


def _graph_of(tmp_path: Path, *names: str) -> tuple[str, Path]:
    """A repo of trivial modules and a graph built from it, both on disk."""
    repo = tmp_path / "repo"
    repo.mkdir()
    nodes = []
    for name in names:
        (repo / name).write_text("x = 1\n", encoding="utf-8")
        nodes.append(
            Node(
                id=name.removesuffix(".py"),
                type=NodeType.FILE,
                name=name,
                file_path=name,
                start_line=1,
                end_line=1,
            )
        )
    db = str(tmp_path / "g.db")
    with SQLiteStore(db) as store:
        store.save_graph(nodes, [])
        store.record_ingest(str(repo), [".py"])
    return db, repo


def test_an_untouched_tree_is_fresh(tmp_path: Path) -> None:
    """Nothing newer, nothing gone."""
    db, _repo = _graph_of(tmp_path, "a.py", "b.py")
    with SQLiteStore(db) as store:
        assert store.freshness().state is FreshnessState.FRESH


def test_a_touched_file_is_stale(tmp_path: Path) -> None:
    """An edit after the ingest is what the reader needs to know about."""
    db, repo = _graph_of(tmp_path, "a.py", "b.py")
    time.sleep(0.01)
    os.utime(repo / "a.py", (time.time() + 5, time.time() + 5))
    with SQLiteStore(db) as store:
        result = store.freshness()
    assert result.state is FreshnessState.STALE
    assert result.changed == 1
    assert result.missing == 0


def test_a_deleted_file_is_stale(tmp_path: Path) -> None:
    """A file the graph still describes but the tree no longer has."""
    db, repo = _graph_of(tmp_path, "a.py", "b.py")
    (repo / "b.py").unlink()
    with SQLiteStore(db) as store:
        result = store.freshness()
    assert result.state is FreshnessState.STALE
    assert result.missing == 1


def test_a_graph_without_ingest_state_is_unknown(tmp_path: Path) -> None:
    """Never FRESH: an unanswerable question must not read as a clean bill."""
    db = str(tmp_path / "old.db")
    with SQLiteStore(db) as store:
        store.save_graph(
            [Node(id="a", type=NodeType.FILE, name="a.py", file_path="a.py",
                  start_line=1, end_line=1)],
            [],
        )
        result = store.freshness()
    assert result.state is FreshnessState.UNKNOWN
    assert "predates" in (result.reason or "")


def test_a_moved_root_is_unknown_not_stale(tmp_path: Path) -> None:
    """A graph queried from elsewhere cannot be checked; say so, do not guess."""
    db, repo = _graph_of(tmp_path, "a.py")
    import shutil

    shutil.rmtree(repo)
    with SQLiteStore(db) as store:
        result = store.freshness()
    assert result.state is FreshnessState.UNKNOWN
    assert "root" in (result.reason or "")


def test_an_explicit_root_overrides_the_recorded_one(tmp_path: Path) -> None:
    """The moved-checkout escape hatch: point the probe at where the tree is now."""
    db, repo = _graph_of(tmp_path, "a.py")
    moved = tmp_path / "moved"
    repo.rename(moved)
    with SQLiteStore(db) as store:
        assert store.freshness(root=str(moved)).state is FreshnessState.FRESH


def test_the_probe_never_reads_file_contents(tmp_path: Path, monkeypatch) -> None:
    """Spec D5, asserted rather than reviewed.

    Reaching for content hashes would move the per-query cost from milliseconds
    to seconds without failing anything else, so the ban is a test.
    """
    db, _repo = _graph_of(tmp_path, "a.py", "b.py")
    opened: list[str] = []
    real_open = Path.open

    def spy(self: Path, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        opened.append(str(self))
        return real_open(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", spy)
    with SQLiteStore(db) as store:
        store.freshness()
    assert [p for p in opened if p.endswith(".py")] == []
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/unit/test_freshness.py -v --no-header`
Expected: the two Task-1 tests PASS; the seven new ones FAIL with `AttributeError: 'SQLiteStore' object has no attribute 'freshness'`

- [ ] **Step 3: Implement the probe**

```python
    def freshness(self, root: str | None = None) -> Freshness:
        """Does this graph still match the tree it was built from? (#175)

        Decided by `st_mtime` against the recorded ingest time, never by
        re-hashing: a stat-only walk is ~6 ms on an 800-file repository, which is
        noise next to a query, while hashing is seconds. `touch` on an unmodified
        file therefore reports `STALE` when nothing changed. That is the chosen
        direction — over-reporting costs a re-ingest, under-reporting returns a
        confident wrong answer, which is the failure this exists to remove.

        Tracked files come from `nodes.file_path`, not `files_state`: a
        non-incremental `cgis ingest` leaves that table empty, so a probe built
        on it would report "nothing missing" for every ordinary graph.
        """
        recorded = self.get_ingest_state()
        if recorded is None:
            return Freshness(
                state=FreshnessState.UNKNOWN,
                reason="graph predates the ingest_state table — re-ingest to enable the check",
            )
        recorded_root, ingested_at, extensions = recorded
        base = Path(root or recorded_root)
        if not base.is_dir():
            return Freshness(
                state=FreshnessState.UNKNOWN,
                reason=f"ingest root {recorded_root} no longer exists — pass an explicit root",
            )

        tracked = self.get_tracked_source_files()
        changed = 0
        missing = 0
        for rel in tracked:
            try:
                if (base / rel).stat().st_mtime > ingested_at:
                    changed += 1
            except OSError:
                missing += 1

        for path in base.rglob("*"):
            if path.suffix in extensions and path.is_file():
                rel_path = str(path.relative_to(base))
                if rel_path not in tracked and path.stat().st_mtime > ingested_at:
                    changed += 1

        if changed or missing:
            return Freshness(state=FreshnessState.STALE, changed=changed, missing=missing)
        return Freshness(state=FreshnessState.FRESH)

    def get_tracked_source_files(self) -> set[str]:
        """The real source files this graph was built from, as stored paths.

        `VIRTUAL_FILE_PATH` is excluded: resolver-minted boundary nodes have no
        file behind them and would read as permanently missing.
        """
        if not self._conn:
            raise RuntimeError(self._error_message)
        cursor = self._conn.execute(
            "SELECT DISTINCT file_path FROM nodes WHERE file_path != ?", (VIRTUAL_FILE_PATH,)
        )
        return {row["file_path"] for row in cursor.fetchall()}
```

Add `from cgis.core.freshness import Freshness, FreshnessState` to the module imports. `VIRTUAL_FILE_PATH` is already imported by `sqlite_store.py`.

- [ ] **Step 4: Run the tests and watch them pass**

Run: `uv run pytest tests/unit/test_freshness.py -v --no-header`
Expected: PASS (9 tests)

- [ ] **Step 5: Run the full gate and commit**

```bash
make format && make lint && make type-check && make doc-coverage
uv run pytest -q --no-header
git add src/cgis/storage/sqlite_store.py tests/unit/test_freshness.py
git commit -m "feat(store): stat-only freshness probe with an explicit UNKNOWN (#175)"
```

---

### Task 3: Ingest records the state

**Files:**
- Modify: `src/cgis/cli.py:224-237` (the ingest persistence block), `src/cgis/api/mcp_server.py` (`cgis_ingest`)
- Test: `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: `SQLiteStore.record_ingest` from Task 1, `SQLiteStore.freshness` from Task 2.
- Produces: every `.db` written by `cgis ingest` (both modes) carries `ingest_state`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_cli.py
def test_ingest_records_state_so_freshness_works(tmp_path: Path) -> None:
    """A graph straight out of `cgis ingest` reports FRESH, not UNKNOWN (#175)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "m.py").write_text("def f():\n    pass\n", encoding="utf-8")
    db = str(tmp_path / "g.db")

    result = runner.invoke(app, ["ingest", str(repo), "--output", db])
    assert result.exit_code == 0

    with SQLiteStore(db) as store:
        assert store.freshness().state is FreshnessState.FRESH


def test_incremental_ingest_records_state_too(tmp_path: Path) -> None:
    """Both ingest modes, or the mode you happen to use decides whether it works."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "m.py").write_text("def f():\n    pass\n", encoding="utf-8")
    db = str(tmp_path / "g.db")

    assert runner.invoke(app, ["ingest", str(repo), "--output", db, "-i"]).exit_code == 0

    with SQLiteStore(db) as store:
        assert store.freshness().state is FreshnessState.FRESH
```

Add `from cgis.core.freshness import FreshnessState` to the test module's imports.

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/unit/test_cli.py -k records_state -v --no-header`
Expected: FAIL — the assertion sees `FreshnessState.UNKNOWN`

- [ ] **Step 3: Record the state in both ingest paths**

In `src/cgis/cli.py`, replace the persistence block (currently lines 224-237):

```python
        if incremental:
            with SQLiteStore(output) as store:
                nodes, raw_edges, resolved_edges = pipeline.run(path, store=store)
                store.record_ingest(path, extractors.keys())
        else:
            nodes, raw_edges, resolved_edges = pipeline.run(path)

        if not nodes:
            console.print(
                "[bold yellow]⚠️  Warning: No nodes were extracted. "
                "Check your path or file extensions.[/bold yellow]"
            )
            return

        if not incremental:
            _write_graph_output(output, path, nodes, resolved_edges, domains)
            if output.endswith(".db"):
                with SQLiteStore(output) as store:
                    store.record_ingest(path, extractors.keys())
```

The `.db` guard matters: `--output graph.json` writes no database, and opening one to record state would create an empty file beside the JSON.

- [ ] **Step 4: Run the tests and watch them pass**

Run: `uv run pytest tests/unit/test_cli.py -k records_state -v --no-header`
Expected: PASS (2 tests)

- [ ] **Step 5: Do the same for the MCP ingest tool**

`cgis_ingest` in `src/cgis/api/mcp_server.py` runs the same pipeline; record the state on the same two paths, matching whatever branch structure that function already has. Verify by hand:

```bash
uv run python -c "
from cgis.api.mcp_server import cgis_ingest
from cgis.storage.sqlite_store import SQLiteStore
cgis_ingest('src', db_path='/tmp/mcp_fresh.db')
with SQLiteStore('/tmp/mcp_fresh.db') as s:
    print(s.freshness())
"
```

Expected: `state=FreshnessState.FRESH`

- [ ] **Step 6: Run the full gate and commit**

```bash
make format && make lint && make type-check && make doc-coverage
uv run pytest -q --no-header
git add src/cgis/cli.py src/cgis/api/mcp_server.py tests/unit/test_cli.py
git commit -m "feat(ingest): record ingest state on both ingest paths (#175)"
```

---

### Task 4: Surface it in the CLI

**Files:**
- Modify: `src/cgis/cli.py` — one helper plus one call in each of `trace` (369), `impact` (500), `validate` (568), `find` (645), `structure` (692), `analyze` (783), `context` (1184), `audit` (1366), `orphans` (1460)
- Test: `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: `SQLiteStore.freshness` from Task 2.
- Produces: `_warn_if_not_fresh(db: str) -> None` in `cli.py`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/unit/test_cli.py
def test_a_stale_graph_warns_before_the_answer(tmp_path: Path) -> None:
    """The answer still comes; the reader is told what it was computed from (#175)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "m.py").write_text("def f():\n    pass\n", encoding="utf-8")
    db = str(tmp_path / "g.db")
    runner.invoke(app, ["ingest", str(repo), "--output", db])
    os.utime(repo / "m.py", (time.time() + 5, time.time() + 5))

    result = runner.invoke(app, ["validate", "--db", db])

    assert "stale" in result.stdout.lower()
    assert "1 changed" in result.stdout


def test_a_fresh_graph_says_nothing_about_freshness(tmp_path: Path) -> None:
    """No note on the happy path, matching `_render_orphans`' warn-only idiom."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "m.py").write_text("def f():\n    pass\n", encoding="utf-8")
    db = str(tmp_path / "g.db")
    runner.invoke(app, ["ingest", str(repo), "--output", db])

    result = runner.invoke(app, ["validate", "--db", db])

    assert "stale" not in result.stdout.lower()
    assert "freshness" not in result.stdout.lower()
```

Add `import os` and `import time` to the test module's imports if absent.

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/unit/test_cli.py -k "stale_graph_warns or says_nothing" -v --no-header`
Expected: the first FAILs (no "stale" in output); the second PASSes vacuously — note that, it becomes meaningful once the helper exists

- [ ] **Step 3: Write the helper**

```python
def _warn_if_not_fresh(db: str) -> None:
    """Tell the reader when the graph no longer matches its tree (#175).

    Printed before the result and only when there is something to say — a fresh
    graph adds nothing, the same way `_render_orphans` warns only when
    `test_sources` is zero. Never raises: a freshness check failing must not take
    down the query the user actually asked for.
    """
    try:
        with SQLiteStore(db) as store:
            result = store.freshness()
    except Exception:  # noqa: BLE001 - a probe must never break the real query
        return
    if result.state is FreshnessState.STALE:
        console.print(
            f"[bold yellow]⚠  Graph is stale:[/bold yellow] {result.changed} changed, "
            f"{result.missing} missing since ingest. Re-ingest for a current answer."
        )
    elif result.state is FreshnessState.UNKNOWN:
        console.print(f"[dim]· Freshness unknowable: {escape(result.reason or '')}[/dim]")
```

Add `from cgis.core.freshness import FreshnessState` to `cli.py`'s imports.

- [ ] **Step 4: Call it from all nine read commands**

In each of `trace`, `impact`, `validate`, `find`, `structure`, `analyze`, `context`, `audit`, `orphans`: add `_warn_if_not_fresh(db)` immediately after the existing "database not found" guard and before the query runs. Every one of these commands already has that guard, so the insertion point is uniform.

- [ ] **Step 5: Run the tests and watch them pass**

Run: `uv run pytest tests/unit/test_cli.py -k "stale_graph_warns or says_nothing" -v --no-header`
Expected: PASS (2 tests)

- [ ] **Step 6: Run the full gate and commit**

```bash
make format && make lint && make type-check && make doc-coverage
uv run pytest -q --no-header
git add src/cgis/cli.py tests/unit/test_cli.py
git commit -m "feat(cli): warn when a query answers from a stale graph (#175)"
```

---

### Task 5: Surface it in the MCP tools

**Files:**
- Modify: `src/cgis/api/mcp_server.py` — one helper plus one call in each of `cgis_trace_flow`, `cgis_analyze_impact`, `cgis_get_structure`, `cgis_drift`, `cgis_suggest_packages`, `cgis_validate`, `cgis_find_symbol`, `cgis_context`, `cgis_metrics`, `cgis_find_orphans`, `cgis_audit_reachability`, `cgis_fractal`
- Test: `tests/unit/test_mcp_server.py`

**Interfaces:**
- Consumes: `SQLiteStore.freshness` from Task 2.
- Produces: `_freshness_note(db_path: str) -> str` in `mcp_server.py`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_mcp_server.py
def test_mcp_tools_flag_a_stale_graph(tmp_path: Path) -> None:
    """An agent must learn staleness from the answer, not by remembering to ask (#175)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "m.py").write_text("def f():\n    pass\n", encoding="utf-8")
    db = str(tmp_path / "g.db")
    cgis_ingest(str(repo), db_path=db)
    os.utime(repo / "m.py", (time.time() + 5, time.time() + 5))

    assert "stale" in cgis_validate(db).lower()


def test_mcp_tools_stay_quiet_on_a_fresh_graph(tmp_path: Path) -> None:
    """No note when there is nothing to say."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "m.py").write_text("def f():\n    pass\n", encoding="utf-8")
    db = str(tmp_path / "g.db")
    cgis_ingest(str(repo), db_path=db)

    assert "stale" not in cgis_validate(db).lower()
```

Add `import os`, `import time` and `cgis_ingest` to the test module's imports if absent.

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/unit/test_mcp_server.py -k "flag_a_stale or stay_quiet" -v --no-header`
Expected: the first FAILs; the second PASSes vacuously

- [ ] **Step 3: Write the helper**

```python
def _freshness_note(db_path: str) -> str:
    """A one-line staleness prefix for a tool's answer, or "" when fresh (#175).

    Prefixed rather than merged into each payload: the tools return a mix of JSON
    and markdown, and `cgis_find_symbol` already establishes the idiom
    (`return note + payload`). Never raises — a probe must not break the query.
    """
    try:
        with SQLiteStore(db_path) as store:
            result = store.freshness()
    except Exception:  # noqa: BLE001 - a probe must never break the real query
        return ""
    if result.state is FreshnessState.STALE:
        return (
            f"> ⚠ Graph is stale: {result.changed} changed, {result.missing} missing "
            "since ingest. Re-run cgis_ingest for a current answer.\n\n"
        )
    if result.state is FreshnessState.UNKNOWN:
        return f"> Freshness unknowable: {result.reason}\n\n"
    return ""
```

- [ ] **Step 4: Prefix it in each of the twelve reading tools**

For each tool listed under **Files**, change its `return <payload>` to `return _freshness_note(db_path) + <payload>`, leaving the early `❌ Database not found` returns untouched — those are not answers about the graph.

`cgis_find_symbol` already builds a `note` prefix; there, prepend to that existing note rather than adding a second concatenation, so the two notes stack in one block.

- [ ] **Step 5: Run the tests and watch them pass**

Run: `uv run pytest tests/unit/test_mcp_server.py -k "flag_a_stale or stay_quiet" -v --no-header`
Expected: PASS (2 tests)

- [ ] **Step 6: Fix any JSON-parsing tests the prefix breaks**

Run: `uv run pytest -q --no-header`

Existing tests that do `json.loads(cgis_x(...))` on a **fresh** graph keep passing, because a fresh graph prefixes nothing. Any that fail are asserting on a stale or ingest-state-less fixture; give the fixture a `record_ingest` call rather than weakening the assertion — a test that needs the note suppressed is a test whose fixture does not represent a real graph.

- [ ] **Step 7: Run the full gate and commit**

```bash
make format && make lint && make type-check && make doc-coverage
uv run pytest -q --no-header
git add src/cgis/api/mcp_server.py tests/unit/test_mcp_server.py
git commit -m "feat(mcp): prefix a staleness note to every graph answer (#175)"
```

---

### Task 6: Dogfood and document

**Files:**
- Modify: `docs/specs/2026-09-09-graph-freshness-design.md` (a measured-result line)
- Test: none new

- [ ] **Step 1: Measure on a real repository**

```bash
uv run cgis ingest /home/zaebee/projects/ownima/owner-api/ownima-backend/app -o /tmp/fresh.db
uv run cgis orphans --db /tmp/fresh.db          # expect: no freshness line
touch /home/zaebee/projects/ownima/owner-api/ownima-backend/app/main.py
uv run cgis orphans --db /tmp/fresh.db          # expect: "⚠ Graph is stale: 1 changed, 0 missing"
```

- [ ] **Step 2: Time the probe on that graph**

```bash
uv run python -c "
import time
from cgis.storage.sqlite_store import SQLiteStore
with SQLiteStore('/tmp/fresh.db') as s:
    t = time.perf_counter(); s.freshness(); print(f'{(time.perf_counter()-t)*1000:.1f} ms')
"
```

Expected: single-digit milliseconds, consistent with the spec's 5.6 ms measurement. A materially larger number means the probe is reading contents somewhere and Task 2's D5 test has a hole.

- [ ] **Step 3: Record the measured result in the spec and commit**

Add the observed timing and the stale/fresh output under the spec's "Measurement basis", then:

```bash
git add docs/specs/2026-09-09-graph-freshness-design.md
git commit -m "docs(spec): record the measured freshness probe cost (#175)"
```

---

## Self-Review

**Spec coverage:** D1 → Task 1 (table, three rows) and Task 3 (written on both ingest paths). D2 → Task 2 (three states, tracked set from `nodes.file_path`, explicit-root override). D3 → Task 2's docstring and the touch test. D4 → Tasks 4 and 5. D5 → Task 2's `test_the_probe_never_reads_file_contents` and Task 6's timing check. Testing table → Tasks 1, 2, 3, 4, 5. Out-of-scope items are absent from every task, as intended.

**Placeholders:** none. Task 3 step 5 and Task 5 step 4 describe mechanical repetition across an enumerated list of functions rather than pasting twelve near-identical diffs; the helper's code and the exact transformation are given.

**Type consistency:** `Freshness`/`FreshnessState` are defined in Task 1 and used unchanged in Tasks 2, 4, 5. `record_ingest(root, extensions)` and `get_ingest_state() -> tuple[str, float, tuple[str, ...]] | None` are defined in Task 1 and consumed in Tasks 2 and 3. `freshness(root=None) -> Freshness` is defined in Task 2 and consumed in Tasks 3, 4, 5. `get_tracked_source_files() -> set[str]` is introduced and used inside Task 2 only.
