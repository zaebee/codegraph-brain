# MCP drift/validate tools + suffix FQN resolution (issue #145)

**Status**: approved design, pre-implementation
**Issue**: #145
**Date**: 2026-06-11

## 1. Problem

The MCP server (`src/cgis/api/mcp_server.py`) exposes navigation tools only
(`cgis_ingest`, `cgis_get_structure`, `cgis_analyze_impact`, `cgis_trace_flow`).
Two gaps, both confirmed by dogfooding during the PR #144 review session:

1. The highest-value signal for an agent — *"did my changes drift the
   architecture?"* (`cgis drift`) and *"can I trust this graph?"*
   (`cgis validate`) — is CLI-only.
2. FQN lookups fail on prefix mismatch: MCP ingest of `src` produces `cgis.*`
   FQNs while CLI ingest from the repo root produces `src.cgis.*`. An agent
   calling `cgis_analyze_impact("src.cgis.query.triads.tv_distance")` gets a
   bare "FQN not found" and has to guess prefixes.

## 2. Decisions (user-confirmed)

- **Fuzzy FQN**: suffix-resolve *inside* the existing tools (no separate
  `cgis_find_fqn` tool — avoids an extra agent round-trip). Unambiguous suffix
  match auto-resolves with a note; ambiguous match returns the candidate list
  in the error.
- **Scope**: both MCP tools *and* the matching CLI commands
  (`trace`, `impact`, `structure`) get suffix resolution — one resolver, one
  behavior, one test suite.
- **Drift logic sharing**: extract a service function used by both CLI and
  MCP (approach A). No subprocess shelling, no duplicated orchestration.
- **Output**: new MCP tools return JSON strings (agents parse them); the
  existing Mermaid-returning tools keep their format.

## 3. Components

### 3.1 `SQLiteStore.find_nodes_by_suffix` (storage layer)

```python
def find_nodes_by_suffix(self, name: str, limit: int = 10) -> list[Node]:
    """Find nodes whose FQN ends with `.name` at a dot boundary."""
```

- **Pure suffix search** — the id itself is NOT its own dot-boundary suffix.
  Exact-match policy lives in `resolve_fqn` (query layer), not here.
- Suffix match **on a dot boundary**:
  `SELECT * FROM nodes WHERE id LIKE '%.name' ORDER BY id LIMIT ?`
  with `name` escaped for LIKE wildcards (`%`, `_` are literal characters in
  FQNs — escape with `ESCAPE '\'`). The dot boundary means `tv_distance`
  matches `src.cgis.query.triads.tv_distance` but **not**
  `...triads.my_tv_distance`.
- `name` may itself be dotted (`triads.tv_distance`) — the same LIKE pattern
  handles it.
- Deterministic: `ORDER BY id`. `limit` caps pathological fan-out.
- Raises `RuntimeError` when the store is not connected (same `_error_message`
  convention as every other store method).
- **Design note (self-drift guardrail, #145):** the original design had an
  exact-match short-circuit (`exact = self.get_node(name)`) inside this method.
  During implementation, the self-parsing drift test (`tests/self_parsing/
  test_drift.py`) caught a real coupling smell: the intra-storage CALLS chain
  (`find_nodes_by_suffix` → `get_node`) added a 021D triad to the
  `cgis.storage` domain, pushing its score from ~0.17 to 0.22 — above the
  declared `pure_utility` tolerance of 0.20. The fix is a layering improvement,
  not a ratchet change: exact-match policy moved to the query layer.

### 3.2 `resolve_fqn` helper (`src/cgis/query/fqn.py`, new module)

```python
@dataclass(frozen=True)
class FqnResolution:
    """Outcome of resolving a possibly-partial FQN against the graph."""
    resolved: str | None        # full FQN when resolution succeeded
    candidates: list[str]       # suffix matches when ambiguous (or empty)
    via_suffix: bool            # True when resolved by suffix, not exact match

def resolve_fqn(store: SQLiteStore, fqn: str) -> FqnResolution: ...
```

**Exact-match policy lives here** (not in `find_nodes_by_suffix`): `resolve_fqn`
calls `store.get_node(fqn)` first; only on a miss does it call
`store.find_nodes_by_suffix(fqn)` for suffix candidates.

Resolution table:

| store result | FqnResolution |
|---|---|
| exact match | `resolved=fqn, via_suffix=False` |
| one suffix match | `resolved=<full FQN>, via_suffix=True` |
| 2+ suffix matches | `resolved=None, candidates=[ids...]` |
| nothing | `resolved=None, candidates=[]` |

### 3.3 Suffix resolution wiring

**MCP tools** (`cgis_trace_flow`, `cgis_analyze_impact`, `cgis_get_structure`):
after the DB-exists guard, call `resolve_fqn`. On `resolved is None`:
- with candidates: `❌ Ambiguous FQN '<input>'. Candidates:\n- a\n- b`
- without: keep the current `❌ FQN not found in graph: <input>` message.

On `via_suffix=True`, prepend a note line to the successful output:
`> Resolved '<input>' → '<full FQN>'` (agents see what actually ran).

**CLI commands** (`trace`, `impact`, `structure` in `cli.py`): same helper
before the existing traversal; ambiguous → print candidates and
`typer.Exit(code=1)`; resolved-via-suffix → dim console note. The traversal
code itself does not change — only the FQN it receives.

### 3.4 Drift service (`src/cgis/query/drift_service.py`, new module)

Move the orchestration currently inlined in `cli.py::drift` (lines ~833–862)
into:

```python
@dataclass(frozen=True)
class DriftAnalysis:
    """Full drift run: per-domain reports + observe-only quotient layer."""
    reports: list[DriftReport]
    quotient: list[tuple[DomainConfig, DriftReport]]   # (binding, report) pairs
    any_critical: bool                                  # honors binding.enforce

def analyze_drift(
    db_path: str,
    patterns_path: str,
    max_drift: float = 0.50,
) -> DriftAnalysis: ...
```

- Owns: `DriftScorer(patterns)`, `load_project_domains`, per-domain
  `FingerprintExtractor.extract`, `load_project_level`, `build_quotient`,
  quotient scoring, and the `any_critical` rule (quotient counts only when
  `binding.enforce`).
- Raises on missing files / scorer errors — callers translate to their medium
  (CLI: red message + exit 1; MCP: `❌` string).
- `cli.py::drift` becomes: option parsing → `analyze_drift(...)` → render
  (Rich table or the existing JSON payload, both built from `DriftAnalysis`).
  JSON shape stays byte-compatible with today's `--format json` output
  (list of report dicts, quotient entries carrying `enforce`).

### 3.5 `cgis_drift` MCP tool

```python
@mcp.tool()
def cgis_drift(
    db_path: str = "graph.db",
    patterns_path: str = "docs/ontology/patterns.yaml",
    max_drift: float = 0.50,
) -> str: ...
```

Returns a JSON string:

```json
{
  "any_critical": false,
  "max_drift": 0.5,
  "domains": [ { ...DriftReport asdict... } ],
  "quotient": [ { ...DriftReport asdict..., "enforce": false } ]
}
```

(Object wrapper rather than the CLI's bare list — the tool needs a top-level
`any_critical` verdict; the per-report dicts are identical `dataclasses.asdict`
output.) Errors: missing DB / missing patterns file / analysis failure →
`❌ <reason>` string, never an exception (STDIO server must not crash).

### 3.6 `cgis_validate` MCP tool

```python
@mcp.tool()
def cgis_validate(db_path: str = "graph.db", threshold: float = 0.30) -> str: ...
```

Thin wrapper over `store.get_edge_stats()` (no service layer — `EdgeStats` is
already structured). Returns a JSON string:

```json
{
  "total": 1234,
  "resolved": 1000,
  "stdlib": 100,
  "external": 50,
  "unresolved": 84,
  "unresolved_ratio": 0.068,
  "top_unresolved": [["name", 12], ...],
  "threshold": 0.30,
  "healthy": true
}
```

`healthy = unresolved_ratio <= threshold`. `top_unresolved` names carry the
`raw_call:` prefix stripped (same as CLI). Missing DB → `❌` string.

## 4. Error handling

- All MCP tools keep the established pattern: broad `except Exception` →
  `❌ {exc}` string; stdout stays JSON-RPC-clean; logging via structlog to
  stderr.
- CLI keeps its pattern: red console message + `typer.Exit(code=1)`.
- `find_nodes_by_suffix` with a connected store never raises on "not found" —
  empty list is a result, not an error.

## 5. Testing

All on real `SQLiteStore` fixtures (project convention; no mocked stores).

- `tests/unit/test_sqlite_store.py` — `find_nodes_by_suffix`: exact hit wins
  over suffix hits; dot-boundary (no mid-identifier match, `_` not treated as
  wildcard); dotted partial; ambiguous returns ≤ limit ordered by id; empty;
  closed-store RuntimeError.
- `tests/unit/test_fqn.py` (new) — `resolve_fqn`: all four table rows.
- `tests/unit/test_drift_service.py` (new) — `analyze_drift` on the existing
  drift test fixture graph: reports match direct scorer output, quotient
  observe-only does not flip `any_critical`, missing patterns file raises.
- `tests/unit/test_mcp_server.py` — extend: `cgis_drift` happy path (JSON
  parses, keys present), missing patterns → `❌`; `cgis_validate` happy path +
  missing DB; suffix-resolution through `cgis_analyze_impact` (short name →
  resolved note in output; ambiguous → candidate list).
- `tests/unit/test_cli.py` (CliRunner) — `impact` with bare function name on
  an ingested fixture resolves and prints the note; ambiguous exits 1 with
  candidates; `drift` output unchanged (regression: JSON byte-shape).

Gates: `make format && make lint && make type-check && make pytest &&
make doc-coverage` (mypy strict, interrogate ≥90%).

## 6. Out of scope

- `--source-root` multi-root ingest (the *real* fix for the prefix divergence;
  separate backlog item — suffix resolution makes the symptom livable).
- Separate `cgis_find_fqn` search tool (rejected: extra round-trip; revisit
  only if suffix-resolve proves insufficient).
- Mermaid output for drift/validate (agents need JSON, humans have the CLI).
- MCP write guards / CI enforcement (#40, #42 — `cgis_drift` is their
  interactive sibling, nothing more).
