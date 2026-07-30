# 📑 MCP Tools Reference Manual

*Auto-compiled from FastMCP docstrings and type annotations.*
*Do not edit manually — regenerate with `python scripts/generate_mcp_ref.py`.*

---

## `cgis_analyze_impact`

Analyse transitive upstream callers of a specific FQN.

    Answers "what breaks if I change X?". ``output_format="mermaid"`` (default)
    returns a diagram; ``"json"`` returns a joinable ``{root, nodes, edges}``
    payload with real FQNs — letting an agent compute set differences (e.g.
    "which route handlers never reach ``verify_ownership``?") directly.

| Argument | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `fqn` | `string` | ✓ |  |
| `db_path` | `string` |  |  |
| `depth` | `integer` |  |  |
| `output_format` | `string` |  |  |

---

## `cgis_audit_reachability`

Reachability/authorization audit — which sources never reach a checkpoint (#172).

    The headline use is **IDOR/authz coverage**: list every route handler that does
    NOT transitively reach an ownership check. Reachability follows behavioral edges
    (CALLS *and* FastAPI ``Depends()`` DEPENDS_ON), so a guard wired via DI counts.

    Select sources with ``from_type`` (a NodeType like ``ROUTE_HANDLER`` /
    ``API_ENDPOINT`` / ``FUNCTION``) and/or ``from_prefix`` (FQN prefix) — at least
    one is required. Returns JSON ``{target, covered, gaps}`` where each gap carries
    ``fqn``/``file``/``line``. Generalizes to validators, event tracking, or
    service-layer-boundary rules by pointing ``target`` at the required node.

| Argument | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `target` | `string` | ✓ |  |
| `db_path` | `string` |  |  |
| `from_type` | `any` |  |  |
| `from_prefix` | `any` |  |  |
| `depth` | `integer` |  |  |

---

## `cgis_context`

Compile an agent-facing GraphRAG context package for a focal FQN (#19).

    Returns an XML-tagged prompt — the focal node's source, its enclosing class,
    its architectural domain boundary, direct callers (upstream ripple) and
    callees (downstream dependencies) — meant to be injected into your context
    window in place of raw file dumps. Far more token-efficient than reading
    whole files, and structured so boundaries stay unambiguous.

    Use ``cgis_ingest`` first if the database does not exist. ``source_root``
    locates source files on disk when the graph was ingested from a
    sub-directory (e.g. ``"src"`` after ``cgis ingest ./src``); it is safe to
    pass even when the stored paths already start with that segment (#228).
    When no candidate exists the ``<source>`` block degrades gracefully to
    "unavailable".

| Argument | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `fqn` | `string` | ✓ |  |
| `db_path` | `string` |  |  |
| `depth` | `integer` |  |  |
| `source_root` | `string` |  |  |

---

## `cgis_drift`

Report per-domain architectural drift against declared ideal patterns.

    Returns JSON: ``any_critical`` verdict, per-domain reports (each carrying a
    ``fit`` block — nearest alphabet template + residual + good/weak/none band),
    the observe-only quotient layer, and ``coverage`` (graph prefixes bound by no
    domain). Call after ``cgis_ingest`` to learn whether your edits pushed a
    domain past its drift tolerance.

    ``max_drift`` is now the default tolerance only for domains that omit
    ``drift_tolerance`` — it no longer caps domains that declare their own
    (see #170).

    ``profile``: when set, score only domains with this profile (plus
    profile-less ones). Use when your patterns.yaml mixes languages but the
    graph holds one language — avoids false EMPTY reports for other-language
    domains that would otherwise fail the gate.

    ``max_residual``: a domain whose nearest template is farther than this gets
    ``fit.band = "none"`` ("no template fits") — a grab-bag module or an
    alphabet gap, independent of drift tolerance (#177).

| Argument | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `db_path` | `string` |  |  |
| `patterns_path` | `string` |  |  |
| `max_drift` | `number` |  |  |
| `profile` | `any` |  |  |
| `max_residual` | `number` |  |  |

---

## `cgis_find_symbol`

Resolve a partial symbol name to candidate FQNs (substring match, ranked).

    Call this BEFORE ``cgis_trace_flow`` / ``cgis_analyze_impact`` /
    ``cgis_get_structure`` when you know a short name (e.g.
    ``get_reservation_prices``) but not its full FQN — it removes the
    read-the-file-first guesswork. Returns JSON ``[{fqn, name, type, file,
    line}]`` ranked exact > prefix > substring. ``kind`` filters by node type
    (FUNCTION / METHOD / CLASS / …); ``fqn_prefix`` scopes the search.

| Argument | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `query` | `string` | ✓ |  |
| `db_path` | `string` |  |  |
| `kind` | `any` |  |  |
| `fqn_prefix` | `any` |  |  |
| `limit` | `integer` |  |  |

---

## `cgis_fractal`

Report the motif census across the repository's structural tiers.

    Coarsens the graph along its own structure — symbol, class, module, then
    directory levels trimmed from the leaf end — and measures the 13-triad
    census at every rung. Returns JSON: one entry per layer (IMPORTS, CALLS)
    with the full per-rung curve (groups, triads, entropy in bits, dominant
    motif, tangle ratio) and the fit.

    ``verdict`` is the sign of ``slope`` (entropy bits per halving of the group
    count) outside a ``2 * std_error`` dead-band: ``hierarchical`` means
    coarsening ADDS motif diversity, ``flat`` means it destroys it,
    ``scale_invariant`` means the mix is the same at every scale, and
    ``no_signal`` means fewer than three rungs carried enough triads to fit.

    Read the curve, not just the verdict — the fit is a lossy summary of a
    non-linear curve. Observe-only: this tool enforces nothing and no gate
    reads it. Call after ``cgis_ingest``.

| Argument | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `db_path` | `string` |  |  |

---

## `cgis_get_structure`

Show the structural layout (CONTAINS/DECLARES) of a module or class.

    Traverses only containment edges — no call-graph noise — matching the CLI
    ``structure`` command. ``output_format="mermaid"`` (default) returns a
    diagram of the hierarchy rooted at the given FQN; ``"json"`` returns the
    joinable ``{root, nodes, edges}`` payload with real FQNs.

| Argument | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `fqn` | `string` | ✓ |  |
| `db_path` | `string` |  |  |
| `depth` | `integer` |  |  |
| `output_format` | `string` |  |  |

---

## `cgis_ingest`

Scan a local directory, extract all symbols, resolve links, and build the graph DB.

    Use this to initialise or refresh the code knowledge graph for a project.
    Paths are normalised relative to the workspace root so the database is
    portable across machines.

    By default the ingest is **incremental**: only changed/new files are
    re-scanned, and the summary reports both what changed this run and the
    whole-graph total. Set ``full_rebuild=True`` to re-scan every file and
    overwrite the database from scratch — use this to drop nodes for files that
    were deleted or renamed, which an incremental run leaves behind.

| Argument | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `project_path` | `string` | ✓ |  |
| `db_path` | `string` |  |  |
| `full_rebuild` | `boolean` |  |  |

---

## `cgis_init_ontology`

Propose a starter patterns.yaml from the measured graph (read-only).

    Returns the YAML text — save it yourself (e.g. to patterns.yaml), review
    the proposed labels, then run ``cgis_drift`` with it. Tolerances are the
    measured scores plus ``margin``: a baseline to ratchet down, not a verdict.

    No files are written; the caller decides where to persist the output.

| Argument | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `db_path` | `string` |  |  |
| `margin` | `number` |  |  |
| `min_nodes` | `integer` |  |  |
| `depth` | `any` |  |  |

---

## `cgis_metrics`

Whole-graph architectural metrics — coupling bottlenecks + God classes (#16).

    Returns JSON ``{bottlenecks, god_classes, critical}`` computed with vectorized
    DuckDB aggregations over the whole graph (fan-in/fan-out coupling,
    declared-member counts, PageRank) — the global "what are the hotspots?" view
    that complements the node-local trace/impact/context tools. Requires the
    optional ``duckdb`` extra; an unavailable dependency is reported as a normal
    ❌ message.

    ``exclude`` drops any node whose FQN contains one of the given dot-segments
    (e.g. ``["tests"]`` removes both ``tests.*`` and ``domains.*.tests.*``) so
    test/vendor scaffolding stays out of the rankings.

| Argument | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `db_path` | `string` |  |  |
| `limit` | `integer` |  |  |
| `exclude` | `any` |  |  |

---

## `cgis_suggest_packages`

Suggest sub-package boundaries for a package from its dependency communities.

    Returns JSON: modularity_q, divergence, direction (under/over/matched),
    verdict (split/consolidate/aligned/leave/borderline/no_signal), the detected
    communities (id + member files), the cross-community bridge edges (cost of
    splitting), and the thresholds used. Default layer is IMPORTS; set
    ``with_calls`` for the combined import+call graph. Run ``cgis_ingest`` first.

    A mis-rooted graph (import targets resolve to no internal file) returns
    ``no_signal`` with a diagnostic note rather than a silent clean verdict.

| Argument | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `db_path` | `string` |  |  |
| `prefix` | `any` |  |  |
| `with_calls` | `boolean` |  |  |
| `min_q` | `number` |  |  |

---

## `cgis_trace_flow`

Trace the execution call-graph starting from a specific FQN downwards.

    ``output_format="mermaid"`` (default) returns a human-readable diagram;
    ``"json"`` returns a joinable ``{root, nodes, edges}`` payload with real
    FQNs (not display hashes) for agent/CI use. Use ``cgis_ingest`` first if
    the database does not exist yet.

| Argument | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `fqn` | `string` | ✓ |  |
| `db_path` | `string` |  |  |
| `depth` | `integer` |  |  |
| `output_format` | `string` |  |  |

---

## `cgis_validate`

Report graph integrity as JSON: edge resolution stats + health verdict.

    Check this before trusting ``cgis_analyze_impact`` output — a high
    unresolved ratio means callers are missing from the graph.

| Argument | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `db_path` | `string` |  |  |
| `threshold` | `number` |  |  |

---
