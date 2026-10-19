---
name: cgis
description: Use when a question is about how code connects rather than what a single file says — finding every caller of a symbol, judging whether a rename or signature change is safe, tracing an execution path, mapping an unfamiliar module, or checking architectural drift. Reach for it before grepping across files or reading whole modules for context.
---

# CGIS — query the graph instead of guessing

Reading files to answer a structural question is the slow, lossy path: it burns context and still leaves you inferring connections. CGIS holds a deterministic graph of the codebase — every function, method, class and the resolved edges between them — and answers those questions exactly.

## When to reach for it

| Question | Tool | Instead of |
| :--- | :--- | :--- |
| "Where do I even start?" | `cgis_overview` | opening files to find the shape of the repo |
| "What calls this?" | `cgis_analyze_impact` | grepping the name and hoping |
| "Is this rename safe?" | `cgis_analyze_impact` | reading every plausible caller |
| "What does this end up calling?" | `cgis_trace_flow` | following imports by hand |
| "How is this class laid out?" | `cgis_get_structure` | reading the whole file |
| "Give me context on this symbol" | `cgis_context` | pasting several files |
| "Where is the symbol named X?" | `cgis_find_symbol` | a broad grep |
| "What's coupled or god-classed?" | `cgis_metrics` | intuition |
| "Did this change drift the architecture?" | `cgis_drift` | nothing — there is no manual equivalent |
| "Does every handler reach its authz guard?" | `cgis_audit_reachability` | auditing routes one by one |

A depth-2 trace or impact of a function or method costs a few hundred tokens: median about 300, and 9 in 10 under 1,600. Measured over every function and method of two real graphs (700 and 5,800 symbols), in the default mermaid format and in json. The equivalent in file chunks costs thousands and is less precise. A module or a heavily used symbol costs more — the worst symbol on the larger graph returns 36k tokens, so raise `depth` deliberately and prefer `cgis_context` for a single symbol. Containment, stdlib/third-party nodes and unresolved call targets are off by default; `include_structure` and `include_external` add them when that is the question.

## First run in a repository

The graph lives in `graph.db` in the project root, and every tool resolves that path relative to the working directory. If a tool answers `Database not found`, the graph has not been built yet:

```
cgis_ingest(project_path="./src")
```

Point it at the source root, not the repository root — ingesting `node_modules` or a virtualenv wastes time and pollutes the graph. `/cgis:ingest` does this for you and picks the right path.

Re-run `cgis_ingest` after any substantial change; it is incremental by default and skips unchanged files. A stale graph is worse than no graph, because it answers confidently and wrongly.

## Reading the answers honestly

CGIS classifies each edge as **internal**, **stdlib**, **external**, or **unresolved**. Unresolved edges are not bugs — they are calls on objects whose type is decided at runtime (`response.json`, `logger.info`, an injected client). No static analyser resolves those without executing the code.

This matters when you act on the output: **an empty impact result means "no static callers found", not "safe to delete."** A symbol reached only through dynamic dispatch, a registry, or a framework decorator will not appear. In Python, a function handed to `map`, `sorted(key=)`, `threading.Thread(target=)`, an executor or the event loop, or to a project function that calls that parameter, has an edge from the code that hands it over, as does one held in a local and called; one stored for a framework to call has no caller. Check `cgis_validate` if the unresolved share looks high for the area you are touching, and say so rather than asserting safety you cannot see.

## Coverage

Extractors exist for **Python** (`.py`), **TypeScript** (`.ts`, `.tsx`) and **Go** (`.go`: structure and metrics only. Go impact answers are a lead, not a list — confirm callers by reading the code; a function value held in a local and called through it has an edge (20 of 20 held-out calls, cgis #695); one passed to another function that calls it has an edge only if that function is on cgis's invoker list (`sync.Once.Do`, `time.AfterFunc`, `sort.Slice` and similar ones are), which is barely measured; a callback stored for a framework to call (a handler, cobra's `RunE`) has no caller, so treat it as an entry point; some interface dispatch is missing. Measured in exp4 on one library, golang/crypto, mostly its ssh package; not measured on applications). **JSON and YAML** (`.json`, `.yaml`, `.yml`) files contribute data key declarations only when data keys are enabled (`--data-keys`, off by default), with no symbols, calls or impact: an OpenSearch mapping declares each property and each subfield under its `fields` (`event_type.keyword` exists only through a `keyword` subfield), any other document its dotted key paths. Vue, Astro, Rust and everything else are invisible to the graph — in a mixed repository, fall back to reading files for those parts and say which parts of the answer the graph could not cover.

## Not covered here

Guardian, the graph-aware LLM reviewer, is a CI tool rather than an interactive one — it reviews pull requests where no agent is present. Inside a Claude Code session you are already the reviewer, and you now have the graph. See [Guardian in the README](https://github.com/zaebee/codegraph-brain#-guardian-graph-aware-code-review) to wire it into CI.
