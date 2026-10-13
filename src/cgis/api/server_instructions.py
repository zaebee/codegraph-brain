"""What the MCP server tells a host in `initialize`, before any tool is listed (#542).

Guidance on when to query the graph used to live only in the Claude plugin skill
(`plugin/skills/cgis/SKILL.md`), so a host wiring the bare server — Cursor, Codex,
a custom harness — got the tool schemas and no hint of when to use them. This is
that skill, distilled: when the graph beats grep, how to read what comes back,
and where it is blind. Keep the two in step; the skill may say more.
"""

SERVER_INSTRUCTIONS = """\
cgis holds a deterministic graph of a codebase: every function, method and class, and \
the resolved calls, imports, inheritance and references between them. Use it for \
questions about how code connects, before grepping across files or reading whole \
modules for context.

When to use which tool:
- Unfamiliar repository, where to start: cgis_overview.
- What calls X / is changing or renaming X safe: cgis_analyze_impact.
- What does X end up calling: cgis_trace_flow.
- How a module or class is laid out: cgis_get_structure.
- One symbol with its source and neighbours, before editing it: cgis_context.
- Where is the symbol named X: cgis_find_symbol.
- Coupling, drift, reachability of a guard: cgis_metrics, cgis_drift, \
cgis_audit_reachability.

First run: if a tool answers "Database not found", build the graph with \
cgis_ingest(project_path=<source root>) — the source root, not the repository root, so \
virtualenvs and node_modules stay out. Prefer an absolute db_path: a relative one \
resolves against the server's working directory, not yours.

Trust the answer for what it covers; do not re-grep to confirm a resolved edge. Read \
it honestly:
- freshness: when files changed since the last ingest, answers carry a freshness \
note. A stale graph answers confidently and wrongly — re-run cgis_ingest (incremental) \
before relying on it.
- coverage / top_unresolved (json): calls on values whose type is decided at runtime \
(an injected client, logger.info) resolve to nothing. An empty impact result means \
"no static callers found", not "safe to delete": dispatch through a registry, a \
decorator or a framework is invisible. Name the gap rather than assert safety.
- cgis_analyze_impact leaves out test callers (counted in excluded_tests) and caps the \
answer at max_nodes, nearest callers first; a cut is reported in truncated.

Coverage: Python (.py) and TypeScript (.ts, .tsx) only. Other languages are not in the \
graph — read those files, and say which part of the answer the graph could not see.
"""
