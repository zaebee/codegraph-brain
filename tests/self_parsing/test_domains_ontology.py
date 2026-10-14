"""The repository's own domains.yaml tags its own layout (language-packs step 5).

`cgis ingest ./src --domains docs/ontology/domains.yaml` is the documented
self-ingest. When a module moves, the heuristics must move with it: no CI gate
consumes these tags, so a stale pattern only shows up as an untagged
orchestrator or a dual-tagged module and a domain edge that is not there.
"""

from pathlib import Path

import pytest

from cgis.languages.python.extractor import PythonExtractor
from cgis.pipeline import IngestionPipeline
from cgis.storage.sqlite_store import SQLiteStore

_REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def tagged(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[str]]:
    db = tmp_path_factory.mktemp("domains") / "graph.db"
    pipeline = IngestionPipeline(
        {".py": PythonExtractor()}, domains_config=str(_REPO / "docs/ontology/domains.yaml")
    )
    with SQLiteStore(str(db)) as store:
        pipeline.run(str(_REPO / "src"), store=store)
        return {n.id: list(n.domains) for n in store.get_all_nodes()}


def test_the_pipeline_package_is_the_pipeline_domain(tagged: dict[str, list[str]]) -> None:
    assert tagged["cgis.pipeline"] == ["Pipeline"]
    assert tagged["cgis.pipeline.IngestionPipeline"] == ["Pipeline"]


def test_uplift_is_a_pipeline_pass_not_the_semantic_engine(tagged: dict[str, list[str]]) -> None:
    """Step 5 moved uplift into the build plane; one domain, not two."""
    assert tagged["cgis.pipeline.uplift"] == ["Pipeline"]
    assert tagged["cgis.pipeline.uplift.SemanticUpliftEngine"] == ["Pipeline"]


def test_the_resolver_is_still_the_semantic_engine(tagged: dict[str, list[str]]) -> None:
    assert tagged["cgis.resolver.engine.ResolverEngine"] == ["SemanticEngine"]
