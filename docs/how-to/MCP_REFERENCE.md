# 📑 MCP Tools Reference Manual

*Auto-compiled from FastMCP docstrings and type annotations.*
*Do not edit manually — regenerate with `python scripts/generate_mcp_ref.py`.*

---

## `cgis_analyze_impact`

Upstream subgraph of one FQN: everything that reaches it within ``depth`` hops.

    Every edge type counts — callers, but also importers, subclasses, type
    references, DI dependents and the enclosing class or file — so this answers
    "what breaks if I change X?". For what X depends on use
    ``cgis_trace_flow``; for only the members of a module or class,
    ``cgis_get_structure``; for a source-included brief to read before editing
    one symbol, ``cgis_context``.

    ``output_format="mermaid"`` (default)
    returns a diagram; ``"json"`` returns a joinable ``{root, nodes, edges,
    coverage}`` payload with real FQNs — letting an agent compute set
    differences (e.g. "which route handlers never reach ``verify_ownership``?")
    directly. ``coverage`` counts unresolved calls whose name matches a
    traversed function, method or class: callers that may be missing, named in
    ``top_unresolved``. It is an upper bound — a common name matches calls on
    unrelated objects, which the names make visible.

| Argument | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `fqn` | `string` | ✓ | Fully qualified name, e.g. pkg.module.Class.method. A unique dot-boundary suffix also resolves; an ambiguous one returns candidates. Use cgis_find_symbol to look a name up. |
| `db_path` | `string` |  | SQLite graph built by cgis_ingest. A relative path resolves against the MCP server's working directory, not the agent's — prefer an absolute path. |
| `depth` | `integer` |  | Maximum edge hops upstream. Every edge type counts as a hop — callers, importers, subclasses, type references, DI dependents and the enclosing class or file all appear alongside each other. |
| `output_format` | `string` |  | "mermaid" for a diagram, or "json" for a payload with real FQNs (case-insensitive). Any other value returns an error. |

---

## `cgis_audit_reachability`

Reachability/authorization audit — which sources never reach a checkpoint.

    The headline use is **IDOR/authz coverage**: list every route handler that does
    NOT transitively reach an ownership check. Reachability follows behavioral edges
    (CALLS *and* FastAPI ``Depends()`` DEPENDS_ON), so a guard wired via DI counts.

    Select sources with ``from_type`` (a NodeType like ``ROUTE_HANDLER`` /
    ``API_ENDPOINT`` / ``FUNCTION``) and/or ``from_prefix`` (FQN prefix) — at least
    one is required. Returns JSON ``{target, covered, gaps}`` where each gap carries
    ``fqn``/``file``/``line``. Generalizes to validators, event tracking, or
    service-layer-boundary rules by pointing ``target`` at the required node.

    A selection that matches no source returns a ❌ message, not an empty
    ``{covered: [], gaps: []}`` that would read as a passing audit (#467).

| Argument | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `target` | `string` | ✓ | FQN of the checkpoint every source must reach, e.g. an ownership check. A unique dot-boundary suffix also resolves. |
| `db_path` | `string` |  | SQLite graph built by cgis_ingest. A relative path resolves against the MCP server's working directory, not the agent's — prefer an absolute path. |
| `from_type` | `any` |  | NodeType of the sources to audit, e.g. ROUTE_HANDLER, API_ENDPOINT or FUNCTION (any case). Give this, from_prefix, or both. |
| `from_prefix` | `any` |  | Only audit sources at or under this FQN prefix, matched on whole dot-segments. A selection matching no source is an error naming the whole-segment prefixes it may have meant. Combined with from_type when both are given. |
| `depth` | `integer` |  | Maximum reachability depth; a longer path is reported as a gap. |

---

## `cgis_context`

Prompt-ready brief on one FQN: its source, class, direct callers and callees.

    Call this before editing a symbol, instead of reading its files. It follows
    calls only, one hop by default. Source is included when the file is found
    (see ``source_root``), and the domain when the graph was tagged with one.
    For a multi-hop subgraph
    over every edge type without source, use ``cgis_trace_flow`` (downstream) or
    ``cgis_analyze_impact`` (upstream).

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
| `fqn` | `string` | ✓ | Fully qualified name, e.g. pkg.module.Class.method. A unique dot-boundary suffix also resolves; an ambiguous one returns candidates. Use cgis_find_symbol to look a name up. |
| `db_path` | `string` |  | SQLite graph built by cgis_ingest. A relative path resolves against the MCP server's working directory, not the agent's — prefer an absolute path. |
| `depth` | `integer` |  | Call hops around the focal node; 1 means direct callers and callees. |
| `source_root` | `string` |  | Directory the graph's stored file paths are relative to — normally the project_path given to cgis_ingest; prefer an absolute path. Empty means the server's working directory, so source shows as unavailable when the server runs elsewhere. |

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
| `db_path` | `string` |  | SQLite graph built by cgis_ingest. A relative path resolves against the MCP server's working directory, not the agent's — prefer an absolute path. |
| `patterns_path` | `string` |  | patterns.yaml (.yaml or .yml) declaring each domain's expected pattern and tolerance, relative to the server's working directory. cgis_init_ontology proposes one. |
| `max_drift` | `number` |  | Drift tolerance for domains that declare no drift_tolerance of their own. |
| `profile` | `any` |  | Score only domains with this profile, plus profile-less ones — e.g. one language when patterns.yaml mixes several. |
| `max_residual` | `number` |  | Distance to the nearest template beyond which a domain's fit band is "none" (no template fits). |

---

## `cgis_find_orphans`

Classes nothing in production builds, extends or names — dead-code candidates.

    Finds classes that no test, type checker or linter flags, because each is
    still imported somewhere: a package re-export keeps a class importable long
    after its last real caller is gone. On one mid-sized backend this reported
    43 of 1 789 classes, and the hand-written equivalent's findings were all
    real and all deleted.

    Two filters decide the answer. **Tests are not users** — a class built only
    by its own test is exactly the shape being hunted. **A re-export is not a
    use** — ``IMPORTS_SYMBOL`` does not count, or nothing is ever reported. What
    counts is construction (``CALLS``), inheritance (``EXTENDS``) and being named
    (``REFERENCES`` — an annotation, or a class handed to a framework); the last
    keeps abstract ports and Protocols off the list.

    ``prefix`` narrows to one package on a dot boundary. ``include_tests`` counts
    test code as a user, turning the report into "unreachable from anywhere".

    Machine-generated classes are **hidden by default**, and ``include_generated``
    puts them back. The query is right about them — nothing constructs a
    betterproto stub — but nobody hand-deletes one either, so they are noise
    rather than a finding. Measured on owner-api at b7d02fe6, five of six
    reported orphans were generated entities and the sixth a nested pydantic
    ``Config``: the unfiltered report had no actionable row in it (#432).

    Returns JSON ``{orphans, considered, test_sources, generated_excluded}``;
    each orphan carries ``fqn``/``file``/``line``. **A listing is a candidate for
    deletion, not a proof** — a class named only inside a decorator (#429) or
    arriving through a star import is invisible here, so the sweep errs towards
    reporting a live class rather than hiding a dead one. ``test_sources: 0`` in a
    repository that has tests means the graph predates the ``is_test`` column:
    re-ingest. ``generated_excluded`` counts every generated class left out of
    the population under the same ``prefix``, referenced or not — so ``0`` on a
    repository with generated code means the same for ``is_generated``, which has
    no backfill: the marker is in the file header, not in the database.

| Argument | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `db_path` | `string` |  | SQLite graph built by cgis_ingest. A relative path resolves against the MCP server's working directory, not the agent's — prefer an absolute path. |
| `prefix` | `any` |  | Only consider classes under this FQN prefix, cut on a dot boundary. |
| `include_tests` | `boolean` |  | Count test code as a user, so the report means "unreachable from anywhere". |
| `include_generated` | `boolean` |  | Include machine-generated classes, which are hidden by default. |

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
| `query` | `string` | ✓ | Leaf symbol name to search for, without dots (e.g. get_flow_result) — not an FQN. Case-insensitive substring match, ranked exact > prefix > substring. |
| `db_path` | `string` |  | SQLite graph built by cgis_ingest. A relative path resolves against the MCP server's working directory, not the agent's — prefer an absolute path. |
| `kind` | `any` |  | Only return this node type, e.g. FUNCTION, METHOD or CLASS (any case). An unknown type matches nothing rather than raising an error. |
| `fqn_prefix` | `any` |  | Only return symbols at or under this FQN prefix, matched on whole dot-segments: app.svc does not match app.svc_alt, and a partial segment matches nothing. |
| `limit` | `integer` |  | Maximum number of candidates to return. |

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
| `db_path` | `string` |  | SQLite graph built by cgis_ingest. A relative path resolves against the MCP server's working directory, not the agent's — prefer an absolute path. |

---

## `cgis_get_structure`

Members of a module or class: the classes, functions and methods it contains.

    Follows containment (CONTAINS/DECLARES) only, so no call or import appears.
    For how the code connects use ``cgis_trace_flow`` (what it depends on) or
    ``cgis_analyze_impact`` (what depends on it).

    Matches the CLI ``structure`` command. ``output_format="mermaid"`` (default) returns a
    diagram of the hierarchy rooted at the given FQN; ``"json"`` returns the
    joinable ``{root, nodes, edges}`` payload with real FQNs.

| Argument | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `fqn` | `string` | ✓ | Fully qualified name, e.g. pkg.module.Class.method. A unique dot-boundary suffix also resolves; an ambiguous one returns candidates. Use cgis_find_symbol to look a name up. |
| `db_path` | `string` |  | SQLite graph built by cgis_ingest. A relative path resolves against the MCP server's working directory, not the agent's — prefer an absolute path. |
| `depth` | `integer` |  | Maximum containment levels to descend (module → class → method). |
| `output_format` | `string` |  | "mermaid" for a diagram, or "json" for a payload with real FQNs (case-insensitive). Any other value returns an error. |

---

## `cgis_ingest`

Scan a local directory, extract all symbols, resolve links, and build the graph DB.

    Use this to initialise or refresh the code knowledge graph for a project.
    Node FQNs are normalised relative to the workspace root so the graph is
    portable across machines.

    ``db_path`` must name a database — it has to end in ``.db``, ``.sqlite`` or
    ``.sqlite3``, live in a directory that already exists, and not point at an
    existing file that is not a SQLite database. cgis will not create parent
    directories.

    By default the ingest is **incremental**: only changed/new files are
    re-scanned, and the summary reports both what changed this run and the
    whole-graph total. When a change alters what other files resolve against — a
    renamed, removed or added symbol, a deleted or new file, a changed base class
    or re-export — the incremental run rebuilds the whole graph itself, so edges
    in unchanged files never point at symbols that no longer exist. Set
    ``full_rebuild=True`` to force a re-scan of every file from scratch.

| Argument | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `project_path` | `string` | ✓ | Root directory of the project to scan. A relative path resolves against the MCP server's working directory. |
| `db_path` | `string` |  | Where to write the graph: must end in .db, .sqlite or .sqlite3, in a directory that already exists, and must not be an existing non-SQLite file. A relative path resolves against the server's working directory. |
| `full_rebuild` | `boolean` |  | Re-scan every file from scratch instead of the incremental default. |

---

## `cgis_init_ontology`

Propose a starter patterns.yaml from the measured graph (read-only).

    Returns the YAML text — save it yourself (e.g. to patterns.yaml), review
    the proposed labels, then run ``cgis_drift`` with it. Tolerances are the
    measured scores plus ``margin``: a baseline to ratchet down, not a verdict.

    No files are written; the caller decides where to persist the output.

| Argument | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `db_path` | `string` |  | SQLite graph built by cgis_ingest. A relative path resolves against the MCP server's working directory, not the agent's — prefer an absolute path. |
| `margin` | `number` |  | Headroom added to each measured score to form the proposed tolerance. |
| `min_nodes` | `integer` |  | Domains with fewer nodes stay hygiene-only instead of getting a label. |
| `depth` | `any` |  | Fixed FQN segment depth for domain discovery (positive); omit to pick it automatically. |

---

## `cgis_metrics`

Whole-graph architectural metrics — coupling bottlenecks, God classes, PageRank.

    Returns JSON ``{bottlenecks, god_classes, critical}`` computed with vectorized
    DuckDB aggregations over the whole graph (fan-in/fan-out coupling,
    declared-member counts, PageRank) — the global "what are the hotspots?" view
    that complements the node-local trace/impact/context tools. Requires the
    optional ``duckdb`` extra; an unavailable dependency is reported as a normal
    ❌ message.

    ``exclude`` drops any node whose FQN contains one of the given dot-segments
    (e.g. ``["tests"]`` removes both ``tests.*`` and ``domains.*.tests.*``) so
    test/vendor scaffolding stays out of the rankings.

    ``scope`` is its complement: it keeps only nodes under one of the given
    dot-prefixes, anchored and cut on a dot boundary, so
    ``["domains.reservation"]`` is that subtree and not
    ``domains.reservation_archive``. Use it for a per-domain review. The two
    compose, and they differ where it matters for PageRank — ``exclude`` removes
    nodes from the propagation graph, ``scope`` filters the rows and lets rank
    propagate over the whole graph, so a scoped run reports how central the
    subtree is *globally*. Coupling in-degree likewise keeps counting callers
    from outside the scope, which is the ripple a domain review is after (#239).

| Argument | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `db_path` | `string` |  | SQLite graph built by cgis_ingest. A relative path resolves against the MCP server's working directory, not the agent's — prefer an absolute path. |
| `limit` | `integer` |  | Top-N rows returned per section. |
| `exclude` | `any` |  | Drop nodes whose FQN contains any of these dot-segments, e.g. ["tests"]; they are removed from PageRank propagation too. |
| `scope` | `any` |  | Keep only nodes under any of these dot-prefixes, e.g. ["domains.billing"]; rank still propagates over the whole graph. |

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
| `db_path` | `string` |  | SQLite graph built by cgis_ingest. A relative path resolves against the MCP server's working directory, not the agent's — prefer an absolute path. |
| `prefix` | `any` |  | FQN prefix of the package to analyse, e.g. cgis.query, matched on whole dot-segments. Needed in practice: without it the verdict is no_signal. |
| `with_calls` | `boolean` |  | Use the combined import + call graph instead of imports only. |
| `min_q` | `number` |  | Modularity threshold: at or above it, a package whose layout disagrees with its communities is flagged split (or consolidate, if over-split). |

---

## `cgis_trace_flow`

Downstream subgraph of one FQN: everything it reaches within ``depth`` hops.

    Every edge type counts — calls, imports, inheritance, DI dependencies,
    references, and containment (so from a module or class the first hop includes
    its own members) — so this answers "what does X depend on?". For what depends
    on X use
    ``cgis_analyze_impact``; for only the members of a module or class,
    ``cgis_get_structure``; for a source-included brief to read before editing
    one symbol, ``cgis_context``.

    ``output_format="mermaid"`` (default) returns a human-readable diagram;
    ``"json"`` returns a joinable ``{root, nodes, edges, coverage}`` payload
    with real FQNs (not display hashes) for agent/CI use. ``coverage`` counts
    the calls the traversed functions make that resolved to nothing, and
    ``top_unresolved`` names the most frequent. Read the names, not only the
    ratio: in Python most are methods on untyped locals (``logger.info``,
    ``items.append``), which cut nothing short. Use ``cgis_ingest`` first if
    the database does not exist yet.

| Argument | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `fqn` | `string` | ✓ | Fully qualified name, e.g. pkg.module.Class.method. A unique dot-boundary suffix also resolves; an ambiguous one returns candidates. Use cgis_find_symbol to look a name up. |
| `db_path` | `string` |  | SQLite graph built by cgis_ingest. A relative path resolves against the MCP server's working directory, not the agent's — prefer an absolute path. |
| `depth` | `integer` |  | Maximum edge hops downstream. Every edge type counts as a hop — calls, imports, inheritance, DI dependencies, references and containment — so from a module or class the first hop is mostly its own members and imports. |
| `output_format` | `string` |  | "mermaid" for a diagram, or "json" for a payload with real FQNs (case-insensitive). Any other value returns an error. |

---

## `cgis_validate`

Report graph integrity as JSON: edge resolution stats + health verdict.

    Check this before trusting ``cgis_analyze_impact`` output — a high
    unresolved ratio means callers are missing from the graph.

| Argument | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `db_path` | `string` |  | SQLite graph built by cgis_ingest. A relative path resolves against the MCP server's working directory, not the agent's — prefer an absolute path. |
| `threshold` | `number` |  | Highest unresolved-edge ratio (0-1) still reported as healthy. |

---
