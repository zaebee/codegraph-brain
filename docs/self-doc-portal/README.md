# Self‑Documenting Knowledge Portal

This documentation is **self‑generated** from the CodeGraph Intelligence System (CGIS). The graph of the codebase is automatically injected below.

<!-- START_CGIS_GRAPH -->
```mermaid

<!-- END_CGIS_GRAPH -->

## Quickstart
```bash
# Ingest the source code into a graph database
cgis ingest ./src --output graph.db

# Show the call‑graph of the main pipeline
cgis trace "src.cgis.pipeline.IngestionPipeline.run"
```
