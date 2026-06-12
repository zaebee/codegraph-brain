# Symbol-Level Import Edges (#161 slice 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `from X import Y` produces a symbol-level `IMPORTS_SYMBOL` edge (module → symbol node), resolved via the existing index or dropped — closing #161 gap 3.

**Architecture:** Per `docs/specs/2026-06-11-symbol-import-edges-design.md`. Third instance of the raw-prefix convention: extractor emits `raw_import:<fqn>` at confidence 0.1; `ResolverEngine` gains one branch (placed BEFORE the passthrough catch-all — load-bearing, §2.4) that resolves via `SymbolIndex.map_to_node_fqn` (conf 1.0) or drops. No new `SymbolResolver` method (9-method ceiling, #183). Census reads only IMPORTS/CALLS → all drift ratchets unaffected by construction.

**Tech Stack:** Python 3.12, tree-sitter, pytest, mypy strict, ruff, interrogate ≥90%.

**Branch:** `feat/issue-161-slice2-impl`

**Hard rules for every task:**
- After implementing, run the FULL unit suite (`uv run pytest tests/unit/ -q`), never just the task's test file.
- All new code needs docstrings (interrogate ≥90% gate).
- NEVER change drift_tolerance values; NEVER add `_KNOWN_GOD_OBJECTS` entries — BLOCKED + report instead.
- This PR closes #161 (gaps 1-2 closed by #166).

---

### Task 1: `EdgeType.IMPORTS_SYMBOL` + ontology entries

**Files:**
- Modify: `src/cgis/core/models.py` (EdgeType, Structural group ~line 55)
- Modify: `docs/ontology/core.yaml` (edge_types.structural + conventions block)

- [ ] **Step 1: Add the enum member**

In `src/cgis/core/models.py`, the `EdgeType` Structural group currently reads:

```python
    # Structural
    CONTAINS = "CONTAINS"
    DECLARES = "DECLARES"
    IMPORTS = "IMPORTS"
```

Add one line after `IMPORTS`:

```python
    IMPORTS_SYMBOL = "IMPORTS_SYMBOL"
```

- [ ] **Step 2: Run the ontology gate to verify it fails**

Run: `uv run pytest tests/unit/test_ontology_compliance.py -q`
Expected: FAIL — `IMPORTS_SYMBOL` is in the enum but missing from `docs/ontology/core.yaml` (this is the slice-1 lesson working as designed).

- [ ] **Step 3: Update `docs/ontology/core.yaml`**

a) In the `edge_types:` structural list (where `- IMPORTS` lives), add:

```yaml
    - IMPORTS_SYMBOL    # Module imports a specific symbol (from X import Y); resolved to the symbol node or dropped
```

b) In the `conventions:` block, after the existing `unresolved_call_example:` line, add:

```yaml
  # Speculative raw prefixes (slice 1 #166 backfill + slice 2 #161): candidates
  # that the resolver either resolves to an existing node or DROPS entirely —
  # unlike raw_call:, these never appear in output.
  unresolved_dep_prefix: "raw_dep:"
  unresolved_import_prefix: "raw_import:"
```

- [ ] **Step 4: Run the gate + full suite**

Run: `uv run pytest tests/unit/test_ontology_compliance.py -q && uv run pytest tests/unit/ -q && make type-check`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/cgis/core/models.py docs/ontology/core.yaml
git commit -m "feat(model): EdgeType.IMPORTS_SYMBOL + ontology prefix conventions (#161 slice 2 task 1)"
```

---

### Task 2: Extractor emission (TDD)

**Files:**
- Modify: `src/cgis/extractors/python_extractor.py` (`_process_import_from_statement`, the `for local_name, sym in symbols:` loop)
- Test: `tests/unit/test_python_extractor.py` (append; existing tests untouched)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_python_extractor.py` (the file already has the `extractor` fixture and imports `EdgeType`, `pytest`; add `Edge` to the existing `cgis.core.models` import for the helper annotation):

```python
# ---------------------------------------------------------------------------
# IMPORTS_SYMBOL emission (#161 slice 2)
# ---------------------------------------------------------------------------


def _symbol_edges(edges: list[Edge]) -> list[Edge]:
    """Filter IMPORTS_SYMBOL edges (slice-2 helper)."""
    return [e for e in edges if e.type == EdgeType.IMPORTS_SYMBOL]


def test_from_import_emits_symbol_edge(extractor: PythonExtractor) -> None:
    """`from a.b import X` emits IMPORTS_SYMBOL with raw_import: target at conf 0.1."""
    _, edges = extractor.parse("from a.b import X\n", "src/pkg/mod.py")
    sym = _symbol_edges(edges)
    assert len(sym) == 1
    assert sym[0].source == "src.pkg.mod"
    assert sym[0].target == "raw_import:a.b.X"
    assert sym[0].confidence == pytest.approx(0.1)


def test_from_import_alias_symbol_edge_keeps_real_name(extractor: PythonExtractor) -> None:
    """`from a import X as Y` — the local alias maps, but the edge targets the real symbol."""
    _, edges = extractor.parse("from a import X as Y\n", "mod.py")
    assert _symbol_edges(edges)[0].target == "raw_import:a.X"


def test_relative_from_import_symbol_edge(extractor: PythonExtractor) -> None:
    """`from .sib import X` inside pkg/mod.py resolves the dots to the sibling module."""
    _, edges = extractor.parse("from .sib import X\n", "pkg/mod.py")
    assert _symbol_edges(edges)[0].target == "raw_import:pkg.sib.X"


def test_plain_import_emits_no_symbol_edge(extractor: PythonExtractor) -> None:
    """`import os` stays module-level only — the imported name IS the module."""
    _, edges = extractor.parse("import os\n", "mod.py")
    assert _symbol_edges(edges) == []


def test_wildcard_import_emits_no_symbol_edge(extractor: PythonExtractor) -> None:
    """`from a.b import *` has no symbol list — nothing is emitted (spec §2.3)."""
    _, edges = extractor.parse("from a.b import *\n", "mod.py")
    assert _symbol_edges(edges) == []


def test_multi_symbol_import_one_edge_each(extractor: PythonExtractor) -> None:
    """`from a import X, Y` emits one IMPORTS_SYMBOL edge per imported name."""
    _, edges = extractor.parse("from a import X, Y\n", "mod.py")
    targets = {e.target for e in _symbol_edges(edges)}
    assert targets == {"raw_import:a.X", "raw_import:a.Y"}


def test_module_imports_edge_coexists_with_symbol_edges(extractor: PythonExtractor) -> None:
    """The module-level IMPORTS edge is untouched — both granularities coexist."""
    _, edges = extractor.parse("from a.b import X\n", "mod.py")
    module_edges = [e for e in edges if e.type == EdgeType.IMPORTS]
    assert len(module_edges) == 1
    assert module_edges[0].target == "a.b"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_python_extractor.py -q -k "symbol_edge or no_symbol or coexists or one_edge_each"`
Expected: the 5 emission tests FAIL (no IMPORTS_SYMBOL edges yet); the two `no_symbol` tests PASS vacuously — that's fine.

- [ ] **Step 3: Implement the emission**

In `src/cgis/extractors/python_extractor.py`, `_process_import_from_statement`, replace:

```python
        for local_name, sym in symbols:
            import_map[local_name] = f"{base_module}.{sym}" if base_module else sym
```

with:

```python
        for local_name, sym in symbols:
            target_fqn = f"{base_module}.{sym}" if base_module else sym
            import_map[local_name] = target_fqn
            # Symbol-level import edge (#161 slice 2): raw_import: candidates are
            # resolved to an existing node by the ResolverEngine or DROPPED —
            # they never leak into output (spec §2.2). Literal prefix mirrors
            # the raw_dep:/raw_call: convention used elsewhere in this file.
            edges.append(
                Edge(
                    id=f"{module_fqn}:imports_symbol:{target_fqn}",
                    type=EdgeType.IMPORTS_SYMBOL,
                    source=module_fqn,
                    target=f"raw_import:{target_fqn}",
                    confidence=0.1,
                    file_path=file_path,
                )
            )
```

Nothing else in the function changes (the module-level IMPORTS edge emission below stays as-is).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_python_extractor.py -q`
Expected: all pass (existing + 7 new).

- [ ] **Step 5: Full unit suite + type check**

Run: `uv run pytest tests/unit/ -q && make type-check`
Expected: all pass. NOTE: if any resolver/acceptance test fails here because `raw_import:` targets now flow through the passthrough branch — STOP and check: Task 3 not landed yet means raw_import edges currently leak through `resolve()`'s passthrough. If `tests/unit/test_resolver.py` or `test_di_acceptance.py` fail, report DONE_WITH_CONCERNS naming the failures (Task 3 fixes the dispatch) — do NOT modify those tests.

- [ ] **Step 6: Commit**

```bash
git add src/cgis/extractors/python_extractor.py tests/unit/test_python_extractor.py
git commit -m "feat(extractor): emit raw_import: IMPORTS_SYMBOL edges for from-imports (#161 slice 2 task 2)"
```

---

### Task 3: Resolver branch — placement is load-bearing (TDD)

**Files:**
- Modify: `src/cgis/resolver/engine.py` (`RAW_IMPORT_PREFIX` constant, `resolve()` dispatch, new `_resolved_import_edge`)
- Test: `tests/unit/test_resolver.py` (append; existing tests untouched)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_resolver.py` (it already imports `Edge`, `EdgeType`, `Node`, `NodeType`, `ResolverEngine`):

```python
# ---------------------------------------------------------------------------
# raw_import: resolution (#161 slice 2)
# ---------------------------------------------------------------------------


def _sym_node(fqn: str, file_path: str = "defs.py") -> Node:
    """Minimal FUNCTION node for symbol-import tests."""
    return Node(
        id=fqn,
        type=NodeType.FUNCTION,
        name=fqn.rsplit(".", maxsplit=1)[-1],
        file_path=file_path,
        start_line=1,
        end_line=2,
    )


def _raw_import_edge(source: str, fqn: str) -> Edge:
    """IMPORTS_SYMBOL candidate edge as the extractor emits it."""
    return Edge(
        id=f"{source}:imports_symbol:{fqn}",
        source=source,
        target=f"raw_import:{fqn}",
        type=EdgeType.IMPORTS_SYMBOL,
        confidence=0.1,
        file_path="consumer.py",
    )


def test_raw_import_resolves_exact_node() -> None:
    """A raw_import: target that names an existing node resolves at confidence 1.0."""
    nodes = [_sym_node("defs.Router")]
    edges = [_raw_import_edge("consumer", "defs.Router")]
    resolved, virtual = ResolverEngine(nodes, edges).resolve()
    assert len(resolved) == 1
    assert resolved[0].target == "defs.Router"
    assert resolved[0].type == EdgeType.IMPORTS_SYMBOL
    assert resolved[0].confidence == pytest.approx(1.0)
    assert virtual == []


def test_raw_import_resolves_via_suffix_map() -> None:
    """A src/-layout prefix mismatch resolves through the suffix map."""
    nodes = [_sym_node("src.defs.Router")]
    edges = [_raw_import_edge("consumer", "defs.Router")]
    resolved, _ = ResolverEngine(nodes, edges).resolve()
    assert resolved[0].target == "src.defs.Router"


def test_raw_import_unknown_symbol_is_dropped() -> None:
    """External symbols (no node) drop the edge entirely — and mint NO virtual node."""
    edges = [_raw_import_edge("consumer", "fastapi.Depends")]
    resolved, virtual = ResolverEngine([], edges).resolve()
    assert resolved == []
    assert virtual == []


def test_raw_import_never_leaks_into_output() -> None:
    """No-leak negative: no output edge may carry the raw_import: prefix.

    Regression guard for the §2.4 dispatch-placement constraint — if the
    raw_import: branch lands after the passthrough catch-all, this fails.
    """
    nodes = [_sym_node("defs.Router")]
    edges = [
        _raw_import_edge("consumer", "defs.Router"),
        _raw_import_edge("consumer", "totally.unknown.Symbol"),
    ]
    resolved, virtual = ResolverEngine(nodes, edges).resolve()
    assert all(not e.target.startswith("raw_import:") for e in resolved)
    assert all(not v.id.startswith("raw_import:") for v in virtual)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_resolver.py -q -k raw_import`
Expected: FAIL — today the passthrough branch keeps `raw_import:` targets and mints virtual nodes (exactly the leak the spec forbids).

- [ ] **Step 3: Implement**

In `src/cgis/resolver/engine.py`:

a) Next to `RAW_DEP_PREFIX`, add:

```python
RAW_IMPORT_PREFIX = "raw_import:"
```

b) In `resolve()`, insert the new branch BETWEEN the `RAW_DEP_PREFIX` branch and the passthrough (`elif not edge.target.startswith("raw_call:")`) — **this placement is load-bearing** (spec §2.4): the passthrough would otherwise swallow `raw_import:` targets, leaking them AND minting virtual nodes:

```python
            elif edge.target.startswith(RAW_IMPORT_PREFIX):
                import_edge = self._resolved_import_edge(edge)
                if import_edge is not None:
                    resolved_edges.append(import_edge)
                # no _ensure_virtual_node: target exists on hit, edge dies on miss
```

c) Add the method (after `_resolved_dep_edge`):

```python
    def _resolved_import_edge(self, edge: Edge) -> Edge | None:
        """Resolve a raw_import: symbol edge, or None when it must be dropped.

        Reuses SymbolIndex.map_to_node_fqn (exact / suffix / strip-prefix). An
        external or unknown symbol drops the edge: the module-level IMPORTS
        edge already captures the coupling — raw_import: never leaks into
        output and never mints a virtual node (spec §2.2/§2.4).
        """
        imported_fqn = edge.target.removeprefix(RAW_IMPORT_PREFIX)
        node_fqn = self._index.map_to_node_fqn(imported_fqn)
        if node_fqn is None:
            return None
        return edge.model_copy(update={"target": node_fqn, "confidence": 1.0})
```

`ResolverEngine` goes 8 methods — still below the God-Object threshold; `SymbolResolver` untouched.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_resolver.py -q`
Expected: all pass (existing + 4 new).

- [ ] **Step 5: Full unit suite + gates**

Run: `uv run pytest tests/unit/ -q && make type-check && make lint`
Expected: all pass — including anything Task 2 flagged as pending-Task-3.

- [ ] **Step 6: Commit**

```bash
git add src/cgis/resolver/engine.py tests/unit/test_resolver.py
git commit -m "feat(resolver): resolve-or-drop raw_import: branch before the passthrough (#161 slice 2 task 3)"
```

---

### Task 4: Acceptance e2e + self-parse pin + drift measurement

**Files:**
- Create: `tests/unit/test_import_acceptance.py`
- Modify: `tests/self_parsing/test_self_parse.py` (one new test + one no-leak assertion)

- [ ] **Step 1: Write the acceptance test**

First READ `tests/unit/test_di_acceptance.py` and mirror its pipeline→store→query mechanics exactly (tmp_path fixture, `IngestionPipeline`, `SQLiteStore`, `QueryEngine`). The fixture and assertions:

```python
"""End-to-end acceptance for #161 slice 2 — symbol-level import edges.

Distilled repro of the issue's gap 3: a module that IMPORTS a symbol but
never calls it must appear in the symbol's impact set.
"""

# defs.py
_DEFS = '''
class Router:
    """A routing table."""

    def add(self) -> None:
        """Register a route."""
'''

# consumer.py — imports the symbol, never calls it
_CONSUMER = '''
from defs import Router

__all__ = ["Router"]
'''
```

Test body (adapt the pipeline/store/query setup lines from `test_di_acceptance.py` verbatim):

1. Write `defs.py` and `consumer.py` into `tmp_path`, ingest with the same pipeline construction `test_di_acceptance.py` uses, persist to a SQLite store in `tmp_path`.
2. Locate the `Router` node id via `find_nodes_by_suffix("defs.Router", limit=2)` asserting exactly one match (same `_find_id` helper pattern as the DI acceptance file).
3. `get_impact_graph(router_id, max_depth=2)` → assert the `consumer` module node id is among the impact nodes, and the connecting edge has `type == EdgeType.IMPORTS_SYMBOL` with `confidence == 1.0`.
4. Negative: assert no edge in the stored graph has a target starting with `raw_import:`.

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/unit/test_import_acceptance.py -q`
Expected: PASS (Tasks 1-3 are in). If `consumer` is missing from impact: debug the chain extractor→resolver→store before touching anything else; do NOT weaken assertions.

- [ ] **Step 3: Self-parse pin + no-leak**

In `tests/self_parsing/test_self_parse.py`, next to `test_pipeline_run_calls_resolver`, add (mirror its style; `_fqn("resolver/engine.py")` with no symbol args yields the module FQN — verify against the helper's signature and existing usage):

```python
def test_engine_imports_symbolresolver_symbol_level(
    graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """resolver.engine must carry an IMPORTS_SYMBOL edge to SymbolResolver (#161 slice 2 pin)."""
    store, _, _ = graph_data
    engine_module = _fqn("resolver/engine.py")
    symbol_resolver = _fqn("resolver/symbols.py", "SymbolResolver")
    edges = store.get_outgoing_edges(engine_module)
    assert any(
        e.target == symbol_resolver and e.type == EdgeType.IMPORTS_SYMBOL for e in edges
    ), f"Missing IMPORTS_SYMBOL pin; outgoing: {sorted({e.target for e in edges})}"


def test_no_raw_import_leak_in_self_graph(
    graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """raw_import: must never survive resolution (#161 slice 2 no-leak, spec §3)."""
    _, _, edges = graph_data
    leaked = [e for e in edges if e.target.startswith("raw_import:")]
    assert leaked == [], f"raw_import: leaked: {[(e.source, e.target) for e in leaked]}"
```

If `EdgeType` is not yet imported in that file, add it to the existing models import.

- [ ] **Step 4: Run the full self-parsing suite (ratchets included)**

Run: `uv run pytest tests/self_parsing/ -q`
Expected: ALL pass. If a drift ratchet fails — STOP, report BLOCKED with the measured score (census is type-blind so this would mean something unexpected; do not touch tolerances).

- [ ] **Step 5: Record measured drift**

```bash
uv run cgis ingest src --source-root src -o /tmp/cgis161s2.db
uv run cgis drift --db /tmp/cgis161s2.db --patterns docs/ontology/patterns.yaml
```

Capture the full table for the PR description. Expected: scores unchanged vs main (census never sees IMPORTS_SYMBOL). If `resolution` moved at all — investigate, don't tune.

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_import_acceptance.py tests/self_parsing/test_self_parse.py
git commit -m "test: symbol-import acceptance e2e + self-parse pin + no-leak guard (#161 slice 2 task 4)"
```

---

### Task 5: Full gates + final verification

- [ ] **Step 1: All gates**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
```

Expected: all green.

- [ ] **Step 2: Acceptance criteria**

```bash
grep -c "    def " src/cgis/resolver/engine.py        # expect 8
grep -c "    def " src/cgis/resolver/symbols.py       # expect 9 (untouched)
git diff main -- docs/ontology/patterns.yaml          # expect EMPTY (no tolerance moved)
uv run pytest tests/unit/test_resolver.py -q          # all pass, k=raw_import included
```

Report exact outputs.

- [ ] **Step 3: Commit any formatting deltas**

```bash
git add -A -- src/ tests/ && git diff --cached --quiet || git commit -m "chore: formatting (#161 slice 2 task 5)"
```

---

## Final checklist (controller, before PR)

- [ ] Drift table identical to main; ratchets untouched
- [ ] No `raw_import:` in any output (unit no-leak + self-graph no-leak both green)
- [ ] Self-parse pin proves the mechanism on cgis itself
- [ ] PR body: `Closes #161`, link spec, include drift table + honest §5 scoping note (test_routes.py-via-router → slice 3)
