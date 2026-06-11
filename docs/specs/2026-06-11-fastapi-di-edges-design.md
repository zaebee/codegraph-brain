# FastAPI DI modeling: DEPENDS_ON edges + alias nodes (issue #161, slice 1)

**Status**: approved design, pre-implementation
**Issue**: #161 (slice 1 of 2; symbol-level imports = slice 2, out of scope here)
**Date**: 2026-06-11

## 1. Problem

Dogfooding cgis MCP on the Ownima backend (FastAPI-heavy, 10K nodes / 37K edges)
showed that `analyze_impact` / `trace_flow` miss the dependency-injection
coupling layer entirely. A manual `grep` gave a strictly more complete picture.

Reproducible case (`app/api/dependencies/owner.py` + `app/domains/rider/routes.py`):

```python
# owner.py
async def resolve_owner(owner_service: OwnerServiceDep, owner_id: str = Path(...)) -> User: ...
ResolvedOwnerDep = Annotated[User, Depends(resolve_owner)]

async def resolve_published_owner(owner: ResolvedOwnerDep) -> User: ...
PublishedOwnerDep = Annotated[User, Depends(resolve_published_owner)]

# routes.py — 4 endpoints inject PublishedOwnerDep
async def get_owner_public_profile(owner: PublishedOwnerDep, ...): ...
async def search_vehicles(owner: PublishedOwnerDep, ...): ...
async def get_vehicle(owner: PublishedOwnerDep, ...): ...
async def calculate_pricing(owner: PublishedOwnerDep, ...): ...
```

Three observed failures, mapped to extractor mechanics:

1. **`Depends(fn)` never produces an edge to the provider.** In a parameter
   default, the `call` node emits `func —CALLS→ raw_call:Depends` (the callee),
   and the argument `fn` is never inspected. Inside `Annotated[...]` the
   metadata is discarded by `_clean_python_type_string` before resolution.
2. **Module-level assignments are invisible.** `_walk` processes `assignment`
   nodes only when `current_func_node` is set, so
   `PublishedOwnerDep = Annotated[...]` produces neither a node nor edges —
   hence `❌ FQN not found` (suffix resolution from #145 cannot help; the node
   does not exist).
3. Symbol-level imports (module→module IMPORTS only) — **slice 2, out of
   scope**: changing IMPORTS semantics moves the triad-census fingerprints and
   ratchets; it needs its own design.

## 2. Decisions (user-confirmed)

- **Detection**: hardcoded `_DI_CALL_NAMES = frozenset({"Depends", "Security"})`
  matched by call identifier, regardless of import style. No `Query()`/`Body()`
  noise, trivially extensible later. No config knob (YAGNI).
- **Alias node type**: reuse `NodeType.VARIABLE` (already mapped in the uplift
  ontology). No new NodeType. Nodes are emitted **only** for DI aliases (RHS
  contains a `Depends`/`Security` call); plain module-level constants stay
  unindexed — no graph bloat.
- **Alias-annotation linking (approach A)**: the extractor emits a *candidate*
  edge `raw_dep:<TypeName>` for every plain typed-parameter annotation; the
  resolver keeps the edge only when the target resolves to an existing
  VARIABLE node (a DI alias) and **drops it otherwise**. Rejected alternatives:
  name heuristic (`*Dep` — project-specific convention), second extraction
  pass (breaks the single-pass extract→resolve architecture).

## 3. Components

### 3.1 Models (`src/cgis/core/models.py`)

- New `EdgeType.DEPENDS_ON = "DEPENDS_ON"` in the Behavioral/Execution group.
  Distinct from the existing semantic `DOMAIN_DEPENDS_ON` (L3, emitted by the
  quotient builder between *domain* nodes): `DEPENDS_ON` is code-level DI
  wiring between functions/aliases. No consumer treats edge types by name
  pattern, so the similar names cannot collide in queries or Mermaid output.
- No NodeType changes.

New raw-target prefix constant `RAW_DEP_PREFIX = "raw_dep:"` lives next to the
existing prefix conventions (see §3.3 for ownership).

### 3.2 Extractor (`src/cgis/extractors/python_extractor.py`)

`_DI_CALL_NAMES: frozenset[str] = frozenset({"Depends", "Security"})`.

Three new emissions:

**a) Module-level DI alias nodes.** In `_walk`, an `assignment` reached with
`current_func_node is None` **at module level only** (assignments inside class
bodies are out of scope, §6 — FastAPI DI aliases are module-level by
convention) is scanned for a
`Depends`/`Security` call anywhere in its RHS subtree (covers both
`X = Annotated[User, Depends(fn)]` and `X = Depends(fn)`). When found:

- Emit a `VARIABLE` node: `id = f"{module_fqn}.{Name}"`, `name = <Name>`,
  `file_path`, `start_line`/`end_line` from the assignment node. The FQN is
  built directly from the module FQN plus the LHS identifier — **not** via
  `_get_id` (that helper reads `child_by_field_name("name")`, which an
  `assignment` node does not have).
- For each DI call with at least one positional argument, emit
  `alias —DEPENDS_ON→ raw_call:<arg>` where `<arg>` is the first positional
  argument's identifier or dotted name (`resolve_owner`,
  `services.get_owner_service`). `Depends()` with no arguments emits no edge;
  non-name arguments (lambdas, calls, subscripts) also emit no edge.
- LHS must be a single plain identifier; tuple targets and attribute targets
  are skipped.

Assignments whose RHS contains no DI call are skipped exactly as today.

**b) Direct DI in parameters — a single hook in `_process_call_node`.**
`_walk` already recurses into every child subtree with `current_func_node`
set, so any `Depends(fn)`/`Security(fn)` call inside a parameter default
(`param = Depends(fn)`), inside an `Annotated[...]` subscript, or under
further wrappers (`Annotated[X, Depends(fn)] | None`) reaches
`_process_call_node` as an ordinary `call` node. The implementation is one
addition there: when the callee identifier is in `_DI_CALL_NAMES`,
additionally emit `func —DEPENDS_ON→ raw_call:<first positional arg>` (same
name-extraction rules as §3.2a; no edge for argless or non-name arguments).
No separate annotation-walking code — subtree robustness comes from the
existing recursion.

The edge resolves through the standard `raw_call:` path (import map → global
symbols). `Depends(SomeClass)` is valid — the edge lands on a CLASS node.
Today these calls already emit `func —CALLS→ raw_call:Depends` via the same
hook; that edge keeps being emitted unchanged (external classification), the
DEPENDS_ON edge is *additional* signal. At module level `_process_call_node`
is gated on `current_func_node`, so alias RHS calls do not double-fire —
§3.2a is the sole emitter there.

**c) Annotation candidates (approach A).** For every
`typed_parameter` / `typed_default_parameter` whose cleaned annotation
(`_clean_python_type_string` output) is a plain or dotted name — i.e. the
existing `_collect_param_type` path — additionally emit
`func —DEPENDS_ON→ raw_dep:<TypeName>` with `confidence=0.1` (candidate).
This requires threading the `edges` list into `_collect_param_type` (its
current signature has no `edges` parameter) or emitting from the calling
`_walk` branch — an explicit signature change, not a drive-by.
No filtering at the extractor: builtins, classes, externals all emit
candidates; the resolver drops everything that is not a DI alias. When the
parameter also matched case (b) (an explicit `Depends` inside its
`Annotated`), the direct `raw_call:` edge from (b) is emitted and the
`raw_dep:` candidate for the *outer* annotation is still emitted too — it
will resolve to a CLASS/external and be dropped; no dedup logic needed.

### 3.3 Resolver (`src/cgis/resolver/engine.py`)

- `RAW_DEP_PREFIX = "raw_dep:"` defined here as a module-level constant,
  alongside `_RAW_CLASS_PREFIX`; the extractor emits the literal string
  (same layering as the existing `raw_call:` convention, where the extractor
  emits the literal and the resolver owns the matching logic).
- New index `_variable_symbols: dict[str, list[str]]` built in
  `_build_indices` from `NodeType.VARIABLE` nodes only, plus the same-file
  variant `_file_variable_symbols: dict[tuple[str, str], list[str]]`.
  VARIABLE nodes are **not** added to `_global_symbols` — call resolution
  behavior must not change.
- New branch in `resolve()` for `edge.target.startswith(RAW_DEP_PREFIX)`:
  1. Strip prefix → `name`.
  2. Resolve via the file's import map (`_resolve_via_import_map`); accept the
     result only when `_map_to_node_fqn` lands on an existing node of type
     VARIABLE.
  3. Else resolve via `_variable_symbols` with same-file preference (mirror
     of `_resolve_via_global_symbols` logic over the variable indices).
  4. **Resolved to a VARIABLE node** → append edge with
     `target=<alias FQN>, confidence=1.0`.
  5. **Anything else** (unresolved, or resolved to non-VARIABLE) → the edge is
     **dropped entirely** via an explicit `continue` — the literal
     `raw_dep:...` target must never leak into the resolved edge set (it was
     an ordinary type annotation, not DI).

  This drop policy intentionally differs from `raw_call:` (which keeps
  unresolved targets at low confidence): `raw_dep:` candidates are speculative
  by construction, and keeping them would flood the graph with one junk edge
  per typed parameter.

  Cost note: candidates add O(typed-params) transient edges and index lookups
  to the resolve pass (on a 10K-node FastAPI codebase: thousands of
  candidates, nearly all dropped). Each lookup is two dict probes — expected
  overhead is negligible next to extraction; the §7 live re-run will confirm
  at real scale.

### 3.4 Query / MCP / storage / pipeline

No changes. BFS traversal is edge-type-agnostic, Mermaid renders `edge.type`
labels as-is, SQLite schema stores `EdgeType` as text. Suffix-FQN resolution
(#145) covers alias nodes automatically (`PublishedOwnerDep` →
`...dependencies.owner.PublishedOwnerDep`).

Resulting graph for the acceptance case:

```
get_owner_public_profile —DEPENDS_ON→ PublishedOwnerDep (VARIABLE)
search_vehicles          —DEPENDS_ON→ PublishedOwnerDep
get_vehicle              —DEPENDS_ON→ PublishedOwnerDep
calculate_pricing        —DEPENDS_ON→ PublishedOwnerDep
PublishedOwnerDep        —DEPENDS_ON→ resolve_published_owner (FUNCTION)
resolve_published_owner  —DEPENDS_ON→ ResolvedOwnerDep (VARIABLE)
ResolvedOwnerDep         —DEPENDS_ON→ resolve_owner (FUNCTION)
```

- `analyze_impact(resolve_published_owner)` (incoming BFS) now surfaces the
  alias and all 4 endpoints.
- `trace_flow(resolve_published_owner)` (outgoing BFS) now includes
  `ResolvedOwnerDep → resolve_owner`, not just `HTTPException`.

## 4. Self-graph / drift safety

cgis itself uses no FastAPI: zero `Depends`/`Security` calls → zero new
DEPENDS_ON edges and zero VARIABLE nodes in the self-graph. `raw_dep:`
candidates are emitted (every typed parameter) but all drop at resolution
(no VARIABLE nodes exist to match). Triad census layers are IMPORTS/CALLS
only — DEPENDS_ON does not enter the fingerprint. Expected:
`tests/self_parsing` stays 40/40 and ratchet values do not move. If the drift
test moves, that is a design violation to investigate, not a tolerance to bump
(lesson of #145).

## 5. Testing

All on real fixtures (project convention; no mocked stores).

- `tests/unit/test_python_extractor.py`:
  - alias node + DEPENDS_ON edge from `X = Annotated[User, Depends(fn)]`;
  - alias node + edge from `X = Depends(fn)`;
  - plain module constant (`LIMIT = 10`, `NAMES = ["a"]`) → no node, no edge;
  - `Depends()` argless → node, no edge;
  - param default `= Depends(fn)` → direct DEPENDS_ON;
  - param `Annotated[X, Depends(fn)]` → direct DEPENDS_ON;
  - wrapped annotation `Annotated[X, Depends(fn)] | None` → direct DEPENDS_ON
    (subtree recursion robustness);
  - typed param → `raw_dep:` candidate edge with confidence 0.1;
  - tuple-target assignment with Depends in RHS → skipped;
  - class-body assignment with Depends in RHS → no node, no edge (out of
    scope, §6).
- `tests/unit/test_resolver.py` (extend):
  - `raw_dep:` → VARIABLE node (same file, no import) → kept, confidence 1.0;
  - `raw_dep:` cross-file via import map → kept;
  - `raw_dep:` resolves to CLASS → dropped;
  - `raw_dep:` unresolved → dropped;
  - negative assertion: no `raw_dep:`-prefixed target ever appears in the
    resolved edge set;
  - existing `raw_call:` behavior unchanged (regression guard).
- **Acceptance test (the issue's check, end-to-end)**: fixture replicating
  `owner.py` + `routes.py` (2 providers, 2 aliases, 4 endpoints) ingested
  through `IngestionPipeline` into a real `SQLiteStore`; assert via
  `QueryEngine`:
  - `get_impact_graph(resolve_published_owner)` node set ⊇ {alias, 4 endpoints};
  - `get_flow_graph(resolve_published_owner)` node set ⊇ {ResolvedOwnerDep,
    resolve_owner}.
- `tests/self_parsing`: existing suite must stay green with unchanged ratchets
  (§4).

Gates: `make format && make lint && make type-check && make pytest &&
make doc-coverage` (mypy strict, interrogate ≥90%).

## 6. Out of scope

- **Slice 2**: symbol-level import edges (drift-layer implications — separate
  design; likely a new edge type rather than changing IMPORTS semantics).
- `Security` scopes / keyword arguments of DI calls.
- Argless `Depends()` semantics (type-driven DI).
- Callable-instance dependencies (`__call__` objects), generator dependencies.
- Non-FastAPI DI frameworks; configurable DI-name list.
- Indexing non-DI module-level assignments (constants, plain type aliases).
- DI aliases defined inside class bodies (FastAPI aliases are module-level by
  convention; class-body assignments stay invisible as today).

## 7. Acceptance (live)

After merge, the issue author re-runs the exact MCP session from #161 against
the Ownima backend: `analyze_impact("...owner.resolve_published_owner")` must
list the 4 consuming endpoints; `analyze_impact("...PublishedOwnerDep")` must
resolve (alias node exists); `trace_flow("...resolve_published_owner")` must
include `resolve_owner`.
