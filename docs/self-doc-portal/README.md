# Self‑Documenting Knowledge Portal

This documentation is **self‑generated** from the CodeGraph Intelligence System (CGIS). The graph of the codebase is automatically injected below.

<!-- START_CGIS_GRAPH -->
```mermaid

graph TD
    src.cgis.pipeline.IngestionPipeline.run --> src.cgis.pipeline.IngestionPipeline._process_file
    src.cgis.pipeline.IngestionPipeline.run --> src.cgis.pipeline.IngestionPipeline._get_extractor
    src.cgis.pipeline.IngestionPipeline._process_file --> src.cgis.pipeline.IngestionPipeline._compute_hash
    src.cgis.pipeline.IngestionPipeline._process_file --> src.cgis.pipeline.IngestionPipeline._persist_incremental
    src.cgis.pipeline.IngestionPipeline._persist_incremental --> src.cgis.pipeline.IngestionPipeline._make_virtual_node
    src.cgis.pipeline.IngestionPipeline._get_extractor --> src.cgis.extractors.python_extractor.PythonExtractor.parse
    src.cgis.extractors.python_extractor.PythonExtractor.parse --> src.cgis.resolver.engine.ResolverEngine.resolve
```
<!-- END_CGIS_GRAPH -->
