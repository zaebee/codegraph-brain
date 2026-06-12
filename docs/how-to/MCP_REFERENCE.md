# 📑 MCP Tools Reference Manual

*Auto-compiled from FastMCP docstrings and type annotations.*
*Do not edit manually — regenerate with `python scripts/generate_mcp_ref.py`.*

---

## `cgis_analyze_impact`

Analyse transitive upstream callers of a specific FQN.

    Returns a Mermaid.js diagram showing what would be impacted if this
    function changed. Answers "what breaks if I change X?".

| Argument | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `fqn` | `string` | ✓ |  |
| `db_path` | `string` |  |  |
| `depth` | `integer` |  |  |

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

## `cgis_get_structure`

Show the class/module layout of a component by tracing outgoing edges.

    Returns a Mermaid.js diagram of the immediate call structure rooted at
    the given FQN. Useful for understanding how a class is organised.

| Argument | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `fqn` | `string` | ✓ |  |
| `db_path` | `string` |  |  |
| `depth` | `integer` |  |  |

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

## `cgis_trace_flow`

Trace the execution call-graph starting from a specific FQN downwards.

    Returns a Mermaid.js diagram showing what the given function calls.
    Use ``cgis_ingest`` first if the database does not exist yet.

| Argument | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `fqn` | `string` | ✓ |  |
| `db_path` | `string` |  |  |
| `depth` | `integer` |  |  |

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
