"""What the MCP server tells a host in `initialize`, before any tool is listed (#542).

Guidance on when to query the graph used to live only in the Claude plugin skill
(`plugin/skills/cgis/SKILL.md`), so a host wiring the bare server — Cursor, Codex,
a custom harness — got the tool schemas and no hint of when to use them. This is
that skill, distilled: when the graph beats grep, how to read what comes back,
and where it is blind. Keep the two in step; the skill may say more.

The coverage line is built from the language registry, so a new pack is announced
the moment it ships — it said "Python and TypeScript only" for as long as Go had.
"""

from cgis.languages import MANIFESTS

#: Said by the tool's own description and by the instructions (final review I6): the
#: tool is on by default, and its key items were measured noisy before it shipped.
DIFF_CHECK_NOISE = (
    "caller and subclass items are leads to read; data-key items are noisy — measured "
    "on one application (owner-api), about a quarter of listed items were worth a look "
    "(docs/lab-notes/2026-10-19-data-key-diff-check.md)."
)


def _coverage() -> str:
    """`Python (.py), TypeScript (.ts, .tsx) and Go (.go) only; JSON and YAML files (...)
    contribute data-key declarations only.`, from the registry.

    A pack whose files name no code (`names_code=False`) is no language of the graph's
    symbols: it is said apart, or the line reads as a fourth language.
    """
    named = [f"{m.title} ({', '.join(m.extensions)})" for m in MANIFESTS if m.names_code]
    code = named[0] if len(named) == 1 else f"{', '.join(named[:-1])} and {named[-1]}"
    data = "".join(
        f"; {m.title} files ({', '.join(m.extensions)}) contribute data-key declarations only"
        for m in MANIFESTS
        if not m.names_code
    )
    return f"{code} only{data}."


def _caveats() -> str:
    """One line per language whose impact answers need confirming, from the registry."""
    return "".join(f"\n{m.impact_caveat}" for m in MANIFESTS if m.impact_caveat)


# An f-string for the coverage line: a literal brace in the prose must be doubled.
SERVER_INSTRUCTIONS = f"""\
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
- Before review or merge, what else a diff may have broken: cgis_diff_check.

First run: if a tool answers "Database not found", build the graph with \
cgis_ingest(project_path=<source root>) — the source root, not the repository root, so \
virtualenvs and node_modules stay out. Prefer an absolute db_path: a relative one \
resolves against the server's working directory, not yours.

Trust the answer for what it covers; do not re-grep to confirm a resolved edge. Read \
it honestly:
- freshness: when files changed since the last ingest, or other cgis code built the \
graph, a graph answer says so — a freshness key in json, a note above a diagram \
(cgis_find_symbol carries none). A stale graph answers confidently and wrongly — \
re-run cgis_ingest (incremental) before relying on it.
- coverage / top_unresolved (json): calls on values whose type is decided at runtime \
(an injected client, logger.info) resolve to nothing. An empty impact result means \
"no static callers found", not "safe to delete": dispatch through a registry, a \
decorator or a framework is invisible. In Python, a function handed to map, \
sorted(key=), threading.Thread(target=), an executor or the event loop, or to a project \
function that calls that parameter, has an edge from the code that hands it over, as \
does one held in a local and called; one stored for a framework to call has no caller. \
Each unresolved name comes with where it is \
made ("at <caller> <file:line>" in text, coverage.sites in json). When one could reach \
the symbol you asked about (impact) or lead past the answer (flow), read that line and \
follow it before answering; naming the gap is not enough.
- cgis_analyze_impact and cgis_trace_flow leave out test code (counted) and cap the \
answer at max_nodes, nearest first; a cut is reported. Ask for output_format="text" to \
read an answer — an indented tree, a fifth the size of json; use json to compute with it.
- cgis_diff_check: {DIFF_CHECK_NOISE}

Coverage: {_coverage()} Other languages are not in the graph — read those \
files, and say which part of the answer the graph could not see.{_caveats()}
"""
