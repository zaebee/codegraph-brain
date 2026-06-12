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

## `cgis_context`

Compile an agent-facing GraphRAG context package for a focal FQN (#19).

    Returns an XML-tagged prompt — the focal node's source, its enclosing class,
    its architectural domain boundary, direct callers (upstream ripple) and
    callees (downstream dependencies) — meant to be injected into your context
    window in place of raw file dumps. Far more token-efficient than reading
    whole files, and structured so boundaries stay unambiguous.

    Use ``cgis_ingest`` first if the database does not exist. ``source_root``
    locates source files on disk when the graph was ingested from a
    sub-directory (e.g. ``"src"`` after ``cgis ingest ./src``); without it the
    ``<source>`` block degrades gracefully to "unavailable".

| Argument | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `fqn` | `string` | ✓ |  |
| `db_path` | `string` |  |  |
| `depth` | `integer` |  |  |
| `source_root` | `string` |  |  |

---

## `cgis_drift`

Report per-domain architectural drift against declared ideal patterns.

    Returns JSON: ``any_critical`` verdict, per-domain reports and the
    observe-only quotient layer. Call after ``cgis_ingest`` to learn whether
    your edits pushed a domain past its drift tolerance.

    ``profile``: when set, score only domains with this profile (plus
    profile-less ones). Use when your patterns.yaml mixes languages but the
    graph holds one language — avoids false EMPTY reports for other-language
    domains that would otherwise fail the gate.

| Argument | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `db_path` | `string` |  |  |
| `patterns_path` | `string` |  |  |
| `max_drift` | `number` |  |  |
| `profile` | `any` |  |  |

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

| Argument | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `project_path` | `string` | ✓ |  |
| `db_path` | `string` |  |  |

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
