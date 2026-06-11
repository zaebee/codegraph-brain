# Resolver Split: IndexBuilder + SymbolResolver (#115)

**Date:** 2026-06-11
**Issue:** #115 — refactor(resolver): split ResolverEngine into IndexBuilder + SymbolResolver
**Status:** Draft

## 1. Problem

`ResolverEngine` is a God Object: 23 methods (grown from 18 since the issue was
filed — the #161 DI sprint added five more), efferent coupling 46, flagged by
`cgis analyze` on the cgis codebase itself and grandfathered in
`tests/self_parsing/test_architecture.py::_KNOWN_GOD_OBJECTS`.

Three responsibilities are mixed in one class:

1. **Index build** — turning the node list into lookup dictionaries
2. **Symbol resolution** — mapping raw names to FQNs through a chain of strategies
3. **Edge finalization** — rewriting raw edges with resolved targets, confidence
   policy, and virtual-node creation

This is a pure structural refactor. **Observable behavior is preserved
bit-for-bit**: same resolved edges, same confidence values, same virtual nodes,
same iteration order.

## 2. Design

### 2.1 Module layout

```
src/cgis/resolver/
  indices.py   SymbolIndex (frozen dataclass) + IndexBuilder
  symbols.py   SymbolResolver
  engine.py    ResolverEngine — thin facade (public API unchanged)
```

`engine.py` keeps its path and class name: `IngestionPipeline` imports
`ResolverEngine` from `cgis.resolver.engine`, and the self-parsing guardrails
(`EXPECTED_NODES`, `test_pipeline_run_calls_resolver`) pin both the
`ResolverEngine` node and its `resolve` method to that file.

Constants:

- `RAW_DEP_PREFIX` **stays in `engine.py`** — it is the public contract named
  by the #161 spec/plan; moving it would break that contract for no gain.
- `_SELF_PREFIX`, `_BUILTINS` move to `indices.py` (used by `classify_fqn`);
  `engine.py` imports `_SELF_PREFIX` for the `self.`-dispatch in
  `_resolved_call_edge`. Single definition, no duplication.
- `_RAW_CLASS_PREFIX` is used by `engine.py` (resolve loop, `_resolved_class_edge`)
  and by `SymbolResolver.__init__` (EXTENDS targets) — it lives in `indices.py`
  next to the other shared constants and is imported by both consumers.

### 2.2 `SymbolIndex` — frozen dataclass (indices.py)

Ten data fields, all built once by `IndexBuilder` and never rebound:

| field | type | today's attribute |
|---|---|---|
| `nodes` | `dict[str, Node]` | `self.nodes` |
| `global_symbols` | `dict[str, list[str]]` | `_global_symbols` |
| `file_global_symbols` | `dict[tuple[str, str], list[str]]` | `_file_global_symbols` |
| `class_methods` | `dict[str, dict[str, str]]` | `_class_methods` |
| `variable_symbols` | `dict[str, list[str]]` | `_variable_symbols` |
| `file_variable_symbols` | `dict[tuple[str, str], list[str]]` | `_file_variable_symbols` |
| `file_imports` | `dict[str, dict[str, str]]` | `_file_imports` |
| `suffix_map` | `dict[str, list[str]]` | `_suffix_map` |
| `internal_roots` | `set[str]` | `_internal_roots` |
| `external_roots` | `set[str]` | `_external_roots` |

`@dataclass(frozen=True)` — rebinding prevented; the contained dicts remain
technically mutable (shallow immutability), which matches the project's
frozen-Pydantic convention. Nothing mutates them after construction.

Four **lookup methods** move here — they need only the index itself, and
placing them on the data removes four methods from the resolver:

| method | from |
|---|---|
| `map_to_node_fqn(imported_fqn) -> str \| None` | `_map_to_node_fqn` |
| `classify_fqn(fqn) -> NodeNamespace` | `_classify_fqn` |
| `is_variable_node(fqn) -> bool` | `_is_variable_node` |
| `normalized_file_path(source_fqn, edge_file_path) -> str \| None` | `_get_normalized_file_path` |

The dataclass-generated `__init__` is not a source-level `def`, so the
extractor counts exactly 4 methods. Method bodies are moved verbatim
(attribute renames only: `self._suffix_map` → `self.suffix_map` etc.).

### 2.3 `IndexBuilder` (indices.py) — nodes in, frozen index out

```python
class IndexBuilder:
    def build(self, nodes: list[Node]) -> SymbolIndex: ...
    def _add_node_to_suffix_map(...) -> None: ...   # mutates local dicts passed in
    def _build_external_roots(...) -> set[str]: ...
```

- Takes **only nodes** — no edges. The inheritance tree is deliberately NOT
  built here (see 2.4).
- Builds plain mutable dicts in local scope, constructs the frozen
  `SymbolIndex` as the last step.
- Loop body of today's `_build_indices` moves verbatim (FILE import maps,
  FUNCTION/CLASS globals, METHOD per-class, VARIABLE DI aliases, suffix map).
- ≤4 methods — far below the God-Object threshold.

### 2.4 `SymbolResolver` (symbols.py) — exactly 9 methods

```python
class SymbolResolver:
    def __init__(self, index: SymbolIndex, edges: list[Edge]) -> None: ...
```

`__init__` stores the index and builds `_inheritance_tree` inline (the body of
today's `_build_inheritance_tree`, ~5 lines over EXTENDS edges). This resolves
the hidden circularity in the issue's original sketch: the inheritance tree is
not an index — it is a *resolution product* (`_resolve_class_ref` consults the
import map and global symbols), so it belongs to the resolver, not the builder.
By the time `SymbolResolver.__init__` runs, the index is complete.

Method inventory (9 = below the ≥10 God-Object threshold):

| method | from | visibility change |
|---|---|---|
| `__init__` (+ inheritance tree) | `__init__` fragment + `_build_inheritance_tree` | — |
| `resolve_class_ref` | `_resolve_class_ref` | now public (engine calls it) |
| `resolve_self_call` | `_resolve_self_call` | now public |
| `resolve_global_call` | `_resolve_global_call` | now public |
| `resolve_dep_candidate` | `_resolve_dep_candidate` | now public |
| `_resolve_method_on_class_hierarchy` | same | private |
| `_resolve_via_import_map` | same | private |
| `_resolve_local_type_call` | same | private |
| `_resolve_via_global_symbols` | same | private |

Bodies move verbatim with two mechanical substitutions:

- index access: `self._global_symbols` → `self._index.global_symbols`, etc.
- helper calls that moved to the index: `self._map_to_node_fqn(...)` →
  `self._index.map_to_node_fqn(...)`, `self._get_normalized_file_path(...)` →
  `self._index.normalized_file_path(...)`, `self._is_variable_node(...)` →
  `self._index.is_variable_node(...)`.

Naming note: the four promoted-to-public methods drop their leading underscore;
no other renames. Resolution order, candidate preferences, and the raw_dep:
drop policy (#161 spec §3.3) are untouched.

### 2.5 `ResolverEngine` (engine.py) — facade, 7 methods

```python
class ResolverEngine:
    def __init__(self, nodes: list[Node], edges: list[Edge]) -> None:
        self.edges = edges
        self._index = IndexBuilder().build(nodes)
        self._resolver = SymbolResolver(self._index, edges)

    def resolve(self) -> tuple[list[Edge], list[Node]]: ...  # loop unchanged
```

Keeps **edge finalization** — confidence policy and `model_copy` rewrites are
graph-output concerns, not symbol lookup:

| method | change |
|---|---|
| `__init__` | rewritten as above |
| `resolve` | loop body unchanged |
| `_resolved_class_edge` | `self._resolve_class_ref(...)` → `self._resolver.resolve_class_ref(...)` |
| `_resolved_call_edge` | delegates to `resolve_self_call` / `resolve_global_call` |
| `_resolved_dep_edge` | delegates to `resolve_dep_candidate` |
| `_ensure_virtual_node` | `self.nodes` → `self._index.nodes`; `self._classify_fqn` → `self._index.classify_fqn` |
| `_make_virtual_node` | unchanged |

7 methods, Ce drops with the moved lookups → exits the God-Object set.

Compatibility note: `self.nodes` / public attribute access is not part of any
contract — `tests/unit/test_resolver.py` constructs the engine and calls
`resolve()` only (verified by grep); `IngestionPipeline.run` does the same.

## 3. Guardrails and drift safety

### 3.1 God-Object baseline (acceptance)

- Remove `"resolver.engine.ResolverEngine"` from `_KNOWN_GOD_OBJECTS` in
  `tests/self_parsing/test_architecture.py`.
- Detector thresholds: `methods ≥ 10 AND Ce ≥ 5`. Post-split counts:
  engine 7, resolver 9, index 4, builder ≤4 — no unit can be flagged
  regardless of coupling.

### 3.2 Self-parsing expectations (additive)

Add to `EXPECTED_NODES` in `test_self_parse.py`:

- `resolver/indices.py :: SymbolIndex`, `IndexBuilder`, `IndexBuilder.build`
- `resolver/symbols.py :: SymbolResolver`, `SymbolResolver.resolve_global_call`

Existing pins (`ResolverEngine`, `ResolverEngine.resolve`,
`test_pipeline_run_calls_resolver`) keep passing because the facade stays in
`resolver/engine.py`.

### 3.3 Drift (the real risk)

Domain `resolution` (`cgis.resolver`, pattern `pipeline_stage`): tolerance
0.40, measured ≈0.35 — headroom 0.05. Today the IMPORTS layer in this domain
is **empty and therefore excluded** from the score; the split creates it
(`engine → symbols → indices`, `symbols → indices`), so the layer enters the
score for the first time. The expected shape is a 021C-dominant chain, which
is exactly what `pipeline_stage` rewards — but this must be **measured, not
assumed**:

- A dedicated early task runs `uv run cgis drift` + the self-parsing ratchet
  suite immediately after the structural move, before any polish.
- If the score exceeds tolerance, the structure is rearranged (e.g., engine
  imports only `symbols`, which re-exports what the facade needs) —
  **the ratchet value is never raised** (project rule).
- `uplift.py` already lives in `cgis.resolver` and is unaffected.

## 4. Testing

1. **Existing suite unchanged**: `tests/unit/test_resolver.py` passes with
   zero edits (acceptance criterion) — it exercises everything through
   `ResolverEngine(nodes, edges).resolve()`.
2. **New smoke tests** (`tests/unit/test_resolver_indices.py`): direct tests
   for the promoted `SymbolIndex` methods — `map_to_node_fqn` (exact / suffix /
   strip-prefix / ambiguous→None), `classify_fqn` (INTERNAL / STDLIB /
   EXTERNAL / UNKNOWN), `is_variable_node`, `normalized_file_path` fallback.
   These paths are currently tested only indirectly.
3. **God-Object regression**: covered by the existing
   `test_god_object_baseline_not_exceeded` once `ResolverEngine` is removed
   from the baseline — a new violation in any of the four units fails CI.
4. **Full gates**: `make format && make lint && make type-check && make pytest
   && make doc-coverage` (docstrings on every new module/class/method —
   interrogate ≥90%).

## 5. Acceptance criteria (from #115, restated)

- [ ] `ResolverEngine` below God-Object threshold (7 methods)
- [ ] `tests/unit/test_resolver.py` passes **unchanged**
- [ ] `test_god_object_baseline_not_exceeded` passes with `ResolverEngine`
      removed from `_KNOWN_GOD_OBJECTS`
- [ ] mypy strict + ruff clean, interrogate ≥90%
- [ ] drift ratchets untouched and green (self-parsing suite)

## 6. Out of scope

- Any behavior change: resolution order, confidence values, drop policies,
  virtual-node creation are moved verbatim.
- TypeScript resolver, new resolution strategies, performance work.
- Moving `RAW_DEP_PREFIX` out of `engine.py` (public contract from #161).
- `uplift.py` and `resolver/__init__.py` re-export changes beyond what the
  new modules need.
- Slice 2 of #161 (symbol-level imports) — separate effort.
