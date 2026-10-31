# Symbol-Level Import Edges (#161 slice 2)

**Date:** 2026-06-11
**Issue:** #161 — gap 3: "Import edges are module→module, not symbol-level"
**Status:** Draft
**Prior slices:** slice 1 (DEPENDS_ON + DI alias nodes) merged as PR #166; gaps 1-2 closed.

## 1. Problem

`from app.api.dependencies.owner import PublishedOwnerDep` produces only a
module→module `IMPORTS` edge (`routes → app.api.dependencies.owner`). The
graph has no edge to the *symbol*, so `analyze_impact` on a symbol (or on
anything that resolves to it) misses importers that never call it — in the
#161 repro, `test_routes_extended.py` surfaced but `test_routes.py` (which
imports the router symbol) did not. Developers reason in symbols; the graph
should too.

## 2. Design

### 2.1 Model: new edge type, census-blind by construction

`EdgeType.IMPORTS_SYMBOL = "IMPORTS_SYMBOL"` — Structural group, next to
`IMPORTS` (`src/cgis/core/models.py`), plus a `- IMPORTS_SYMBOL` entry under
`edge_types.structural` in `docs/ontology/core.yaml` (slice-1 lesson:
`test_ontology_compliance` gates this).

The ontology `conventions:` block documents `unresolved_call_prefix` but was
never updated for slice 1's `raw_dep:` — the implementation adds both missing
prefix conventions (guardian catch on the spec PR):

```yaml
  unresolved_dep_prefix: "raw_dep:"        # slice 1 backfill — dropped on miss
  unresolved_import_prefix: "raw_import:"  # this slice — dropped on miss
```

Why a new type instead of extending `IMPORTS` semantics:

- The drift census (`query/fingerprint.py`) filters explicitly on
  `EdgeType.IMPORTS` / `EdgeType.CALLS`. A new type is invisible to every
  triad census and every ratchet — **drift-safe by construction**, the same
  mechanism that kept slice 1 safe. Extending `IMPORTS` would multiply the
  IMPORTS-layer edge count and silently re-shape all measured domains.
- Query traversal needs no changes: `BEHAVIORAL_EDGE_TYPES` in
  `query/engine.py` is computed as "every EdgeType except CONTAINS/DECLARES",
  so the new type is traversed by impact/flow automatically.

### 2.2 Raw-target convention

Third instance of the established prefix-tells-the-policy convention:

| prefix | emitted by | resolved target must be | on miss |
|---|---|---|---|
| `raw_call:` | call sites | FUNCTION/CLASS/METHOD | keep unresolved (conf 0.8) |
| `raw_dep:` | typed params (slice 1) | VARIABLE (DI alias) | **drop** |
| `raw_import:` | **this slice** | any node in the graph | **drop** |

`RAW_IMPORT_PREFIX = "raw_import:"` is defined in `resolver/engine.py` next
to `RAW_DEP_PREFIX` (same visibility rationale: it is the public contract
named by this spec).

Drop rationale (decided with the user): external symbols
(`fastapi.Depends`, `typing.Any`, …) would otherwise mint thousands of
virtual nodes per real-world repo while adding nothing the module-level
`IMPORTS` edge doesn't already say. The module edge remains the coarse
fallback for external coupling; symbol edges exist only between nodes the
graph actually knows.

### 2.3 Extractor: one emission point

`_process_import_from_statement` already computes everything needed — its
symbol loop writes `import_map[local_name] = f"{base_module}.{sym}"`. The
change adds one edge per imported symbol in the same loop:

```python
for local_name, sym in symbols:
    target_fqn = f"{base_module}.{sym}" if base_module else sym
    import_map[local_name] = target_fqn
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

Scope of emission:

- Only `from X import a, b as c` forms (this function). Plain `import X`
  stays module-level only — the imported name IS the module, already covered.
- Relative imports reuse the existing `_resolve_relative_module` result
  (`base_module`), no new logic.
- `from X import *`: nothing is emitted — **verified**:
  `_collect_imported_symbols` explicitly skips `wildcard_import` nodes (the
  loop handles only `dotted_name`/`identifier`/`aliased_import`), so the
  symbol list is empty and the loop body never runs. No guard needed; the §4
  test locks it.
- Submodule-as-symbol (`from app import services`): emitted like any other
  symbol — `raw_import:app.services` resolves to the submodule's FILE node
  (FILE ids are module FQNs). This is **complementary**, not duplicate: the
  module-level edge targets `app`, the symbol edge targets `app.services` —
  finer information, intended.
- The module-level `IMPORTS` edge emission is untouched — both edges coexist.

Edge id embeds the target FQN, so multiple symbols from one statement get
distinct ids; re-importing the same symbol elsewhere in the file dedups at
the id level exactly like today's `IMPORTS` ids.

### 2.4 Resolution: engine branch, no resolver growth

`ResolverEngine.resolve()` gets a `raw_import:` branch (checked alongside the
existing prefix dispatch) delegating to a new method:

```python
def _resolved_import_edge(self, edge: Edge) -> Edge | None:
    """Resolve a raw_import: symbol edge, or None when it must be dropped."""
    imported_fqn = edge.target.removeprefix(RAW_IMPORT_PREFIX)
    node_fqn = self._index.map_to_node_fqn(imported_fqn)
    if node_fqn is None:
        # External or unknown symbol: the module-level IMPORTS edge already
        # captures the coupling — raw_import: must never leak into output.
        return None
    return edge.model_copy(update={"target": node_fqn, "confidence": 1.0})
```

- Reuses `SymbolIndex.map_to_node_fqn` (exact / suffix-map / strip-prefix) —
  **no new `SymbolResolver` method**; the resolver stays at 9 methods
  (zero-headroom constraint recorded in #183).
- `ResolverEngine` goes 7 → 8 methods, still below the God-Object threshold
  (≥10), no `_KNOWN_GOD_OBJECTS` change.
- No virtual node is ever created for this edge type (drop on miss; on hit
  the target exists by definition).
- **Placement is load-bearing, NOT cosmetic** (colleague-review catch): the
  third branch of today's chain is a passthrough catch-all
  (`elif not edge.target.startswith("raw_call:")`) that would swallow a
  `raw_import:` target — appending it unresolved (leak) AND minting a virtual
  node, violating both §2.2 and §3. The `raw_import:` branch MUST precede the
  passthrough. The resulting chain, with drop-on-None handling:

```python
for edge in self.edges:
    if edge.target.startswith(RAW_CLASS_PREFIX):
        ...
    elif edge.target.startswith(RAW_DEP_PREFIX):
        ...
    elif edge.target.startswith(RAW_IMPORT_PREFIX):
        import_edge = self._resolved_import_edge(edge)
        if import_edge is not None:
            resolved_edges.append(import_edge)
        # no _ensure_virtual_node: target exists on hit, edge dies on miss
    elif not edge.target.startswith("raw_call:"):
        ...  # passthrough — unchanged
    else:
        ...  # raw_call — unchanged
```

  The no-leak negative test (§4.2) is the regression guard for this exact
  mistake.

### 2.5 Known accepted limitations

- **Re-exports through `__init__.py`**: `from pkg import X` where `pkg/__init__.py`
  re-exports `X` from `pkg.mod` computes `pkg.X`, which has no node →
  dropped. Honest under current modeling (the symbol is not defined in
  `pkg`); the module-level edge keeps the coupling visible. Revisit only if
  #182's passthrough work gives the resolver re-export awareness.
- **Ambiguous suffix matches** drop (map_to_node_fqn returns None on
  ambiguity) — conservative, consistent with the index's existing contract.
- The strip-prefix branch of `map_to_node_fqn` has a known uniqueness gap
  (#183); this slice inherits, not fixes, it.

## 3. Drift and self-graph safety

Unlike slice 1 (cgis has no FastAPI → 0 DEPENDS_ON in the self-graph), cgis
**will** gain IMPORTS_SYMBOL edges from its own `from cgis.x import Y`
statements. Safety still holds:

- Census reads only IMPORTS/CALLS → all triad vectors and every
  `drift_tolerance` ratchet are unaffected **by construction**.
- `unresolved_ratio` hygiene: `raw_import:` never leaks (drop policy), and
  resolved edges have confidence 1.0.
- Self-parse gains a free acceptance pin: the edge
  `resolver.engine → resolver.symbols.SymbolResolver` (type IMPORTS_SYMBOL)
  must exist in the self-graph — added as a new test next to
  `test_pipeline_run_calls_resolver`.
- A no-leak negative test asserts no `raw_import:` target survives in output
  (mirror of slice 1's raw_dep test) — also the regression guard for the §2.4
  dispatch-placement constraint.
- Cost note: symbol edges add roughly one edge per imported name on top of
  the module edge (owner-api ≈37k edges today), and impact/flow traverse
  them. Census cost is zero (type-blind); BFS fan-out grows proportionally —
  acceptable at current scales, revisit alongside #185 if traversal ever
  measures hot.

## 4. Testing

1. **Extractor unit tests** (`tests/unit/test_python_extractor.py`):
   `from a.b import X` emits IMPORTS_SYMBOL with `raw_import:a.b.X`;
   `as`-alias maps local name but target keeps the real symbol; relative
   `from .sib import X` resolves the dots; plain `import x` emits NO
   IMPORTS_SYMBOL; `from x import *` emits none; multi-symbol statement
   emits one edge per symbol; module-level IMPORTS edge still present.
2. **Resolver unit tests** (`tests/unit/test_resolver.py` additions —
   existing tests untouched): resolves exact / suffix-prefixed node (conf
   1.0); unknown external symbol → edge absent from output; no virtual node
   minted; no-leak negative test.
3. **Acceptance** (`tests/unit/test_import_acceptance.py`): two-module
   fixture — `defs.py` defines `Router`, `consumer.py` has
   `from defs import Router` but never calls it; pipeline → store →
   `get_impact_graph(Router)` includes `consumer` module. This is the
   distilled #161 repro (test_routes.py case).
4. **Self-parsing**: new pin from §3; full suite + ratchets green.
5. Gates: `make format && make lint && make type-check && make pytest &&
   make doc-coverage`.

## 5. Acceptance (live, post-merge)

Re-run the #161 MCP scenario on owner-api: `analyze_impact` on
`PublishedOwnerDep` must now show `routes` as a direct importer via the
symbol-level edge (`routes —IMPORTS_SYMBOL→ PublishedOwnerDep`) — today it
appears only transitively through the DEPENDS_ON chain.

**Honest scoping of the §1 test_routes.py case** (colleague-review catch):
`test_routes.py` imports the **`router` symbol**, and `router = APIRouter()`
is a plain (non-DI) module-level assignment — slice 1 deliberately does not
index those (#166 spec §6), so there is no `router` node and that symbol
edge **drops**. Surfacing test_routes.py via the router import therefore
requires indexing non-DI module-level assignments — a separate decision with
its own node-count and drift implications (candidate slice 3), NOT promised
by this slice. What IS promised live: imports of functions, classes, and DI
aliases gain symbol edges (covers the issue's gap-3 example verbatim).

## 6. Out of scope

- `from X import *` modeling; `import X.Y` sub-module symbol granularity.
- Indexing non-DI module-level assignments (`router = APIRouter()`) — needed
  for the §5 test_routes.py-via-router case; candidate slice 3 with its own
  node-count/drift analysis.
- Re-export resolution through `__init__.py` chains (see §2.5, #182).
- TypeScript extractor parity (#18).
- Fixing the inherited `map_to_node_fqn` uniqueness gap (#183).
- Any change to module-level `IMPORTS` semantics or the drift census; #182
  passthrough detection is a separate effort.
- Closing #161: this slice completes the issue's gap 3 — the issue closes
  with this PR (gaps 1-2 closed by #166).
