# CGIS — Code Graph Intelligence

Gives Claude a deterministic semantic graph of your codebase instead of flat text. Exact callers, real blast radius, and scoped context in a few hundred tokens rather than whole files.

```bash
/plugin marketplace add zaebee/codegraph-brain
/plugin install cgis@codegraph-brain
```

Then, in any repository, run `/cgis-ingest` once to build the graph.

## What it ships

| Component | What it does |
| :--- | :--- |
| MCP server `cgis` | 13 tools: impact analysis, flow tracing, structure, symbol search, metrics, architectural drift, authz reachability |
| Skill `cgis` | Teaches the agent *when* to query the graph instead of reading files |
| Command `/cgis-ingest` | Builds the graph on first use and reports what it found |

## What it does on your machine

**On first use it downloads a package.** The MCP server is the open-source [`codegraph-brain`](https://pypi.org/project/codegraph-brain/) package (MIT), fetched from PyPI with `uvx --from "codegraph-brain>=0.6.0" cgis-mcp` and cached by `uv` afterwards. That download is the only thing this plugin fetches.

**Everything else is local.** Your source code is parsed on your machine with Tree-sitter, and the resulting graph is written to `graph.db` in your project root. No source, no graph, and no usage data leaves your machine.

- **No hooks.** The plugin registers none, so it observes nothing outside its own tool calls.
- **No telemetry.** No analytics, usage pings, crash reporting or feature-flag fetches.
- **No credential access.** It reads source files under the path you point it at; nothing else.

`graph.db` should go in your `.gitignore`.

## Guardian is not part of this plugin

The repository also contains **Guardian**, a graph-aware LLM code reviewer that *does* call an external model provider (Gemini, Mistral, Cohere, or a local Ollama). It is a CI tool, it requires explicit configuration, and this plugin neither ships nor invokes it. See the [repository README](https://github.com/zaebee/codegraph-brain#-guardian-graph-aware-code-review) if you want it in your pipeline.

## Coverage

Extractors exist for **Python** and **TypeScript** (`.py`, `.ts`, `.tsx`). Other languages are invisible to the graph — see the [case study](https://github.com/zaebee/codegraph-brain/blob/main/docs/CASE_STUDY.md) for measured coverage on a real twelve-repository codebase, including what it misses.

## Links

- Source: [github.com/zaebee/codegraph-brain](https://github.com/zaebee/codegraph-brain)
- Package: [pypi.org/project/codegraph-brain](https://pypi.org/project/codegraph-brain/)
- Licence: MIT
