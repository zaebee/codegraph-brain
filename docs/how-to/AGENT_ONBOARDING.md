# 🤖 Agent Onboarding Guide

## What CGIS gives your AI agent

Without CGIS, an LLM agent navigates your codebase by reading raw text files — it guesses connections, hallucinates call chains, and bloats its context window with entire modules to answer a single question.

With CGIS, the agent queries a deterministic semantic graph. It gets precise, scoped answers:

| Without CGIS | With CGIS |
| :--- | :--- |
| Read 10 files to find callers | `cgis_analyze_impact` returns exact upstream nodes |
| Guess if a rename is safe | `cgis_analyze_impact` shows full blast radius |
| Paste whole file for context | `cgis_get_structure` returns only the relevant subgraph |

---

## Step 1: Build the knowledge graph

```bash
cgis ingest ./src --output graph.db
```

Point `--output` at a `.db` file. For large repos, add `--incremental` to skip unchanged files on subsequent runs.

---

## Step 2: Connect via MCP

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "cgis": {
      "command": "uv",
      "args": ["run", "cgis-mcp"],
      "env": {
        "CGIS_DB_PATH": "/absolute/path/to/graph.db"
      }
    }
  }
}
```

### Cursor (`~/.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "cgis": {
      "command": "uv",
      "args": ["--directory", "/path/to/codegraph-brain", "run", "cgis-mcp"]
    }
  }
}
```

---

## Step 3: Available MCP tools

| Tool | Use when |
| :--- | :--- |
| `cgis_ingest` | Initialise or refresh the graph after code changes |
| `cgis_trace_flow` | "Show me what `X` calls, 3 levels deep" |
| `cgis_analyze_impact` | "What breaks if I change `X`?" |
| `cgis_get_structure` | "Show me the layout of class `X`" |

---

## Effective prompts

```
Use cgis_analyze_impact on "cgis.resolver.engine.ResolverEngine.resolve"
to find every caller, then list the files that would need updating if
the return type changed.
```

```
Use cgis_trace_flow on "cgis.pipeline.IngestionPipeline.run" with depth 3
and generate a Mermaid diagram. Then identify the two most complex call
paths and suggest where to add logging.
```

```
First call cgis_ingest on ./src to refresh the graph, then use
cgis_get_structure on "cgis.storage.sqlite_store.SQLiteStore"
to understand its public interface before writing a new method.
```

---

## Why this beats vector RAG for architecture questions

A vector search on "save graph to database" returns the top-k semantically similar chunks — likely multiple files with vague relevance. A CGIS `cgis_trace_flow` call on `IngestionPipeline.run` returns exactly the nodes and edges involved in saving, with file paths and line numbers, in a single round-trip.

Token cost comparison for "what does `save_graph` call?":
- Vector RAG: ~3,000–8,000 tokens (full file chunks)
- CGIS subgraph (depth 2): ~200–400 tokens (nodes + edges only)
