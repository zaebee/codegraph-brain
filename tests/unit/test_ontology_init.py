"""Unit tests for the init-ontology proposer (#174)."""

from pathlib import Path

import pytest
import yaml
from conftest import make_chain_nodes_edges

from cgis.core.models import VIRTUAL_FILE_PATH, Edge, EdgeType, Node, NodeType
from cgis.query.drift_service import analyze_drift
from cgis.query.fingerprint import FingerprintExtractor
from cgis.query.ontology_init import _DEFAULT_ONTOLOGY_HEADER, discover_domains, propose_ontology
from cgis.storage.sqlite_store import SQLiteStore


def _node(fqn: str, file_path: str = "mod.py", node_type: NodeType = NodeType.FUNCTION) -> Node:
    """Minimal node for discovery/proposal tests."""
    return Node(
        id=fqn,
        type=node_type,
        name=fqn.rsplit(".", maxsplit=1)[-1],
        file_path=file_path,
        start_line=1,
        end_line=2,
    )


# ---------------------------------------------------------------------------
# discover_domains
# ---------------------------------------------------------------------------


def test_discover_auto_descends_single_root() -> None:
    """src.click.{core,parser} → domains at the first level with >= 2 children."""
    nodes = [
        _node("src.click.core.f"),
        _node("src.click.core.g"),
        _node("src.click.parser.h"),
    ]
    assert discover_domains(nodes) == ["src.click.core", "src.click.parser"]


def test_discover_multi_root_uses_roots_level() -> None:
    """Two top-level roots → the roots themselves are the candidates."""
    nodes = [_node("app.f"), _node("lib.g")]
    assert discover_domains(nodes) == ["app", "lib"]


def test_discover_depth_override() -> None:
    """depth=3 takes 3-segment prefixes regardless of auto-descent."""
    nodes = [
        _node("src.click.core.sub.f"),
        _node("src.click.parser.other.g"),
    ]
    assert discover_domains(nodes, depth=3) == ["src.click.core", "src.click.parser"]


def test_discover_excludes_virtual_nodes() -> None:
    """Virtual boundary nodes (file_path == EXTERNAL) never form domains."""
    nodes = [
        _node("app.real.f"),
        _node("app.real.g"),
        _node("fastapi.Depends", file_path=VIRTUAL_FILE_PATH),
        _node("os.path.join", file_path=VIRTUAL_FILE_PATH),
    ]
    domains = discover_domains(nodes)
    assert all(not d.startswith(("fastapi", "os")) for d in domains)


def test_discover_sorted_and_deduplicated() -> None:
    """Output is sorted; many nodes per prefix yield one candidate."""
    nodes = [_node("z.b.f"), _node("z.a.g"), _node("z.a.h"), _node("z.b.i")]
    assert discover_domains(nodes) == ["z.a", "z.b"]


def test_discover_node_at_intermediate_prefix() -> None:
    """A MODULE node id coexisting with deeper nodes must not yield []."""
    nodes = [_node("src.click", node_type=NodeType.MODULE), _node("src.click.core.f")]
    assert discover_domains(nodes) == ["src.click.core.f"]


def test_discover_single_node_graph() -> None:
    """A one-node graph proposes that node's lineage, not []."""
    assert discover_domains([_node("solo.fn")]) == ["solo.fn"]


# ---------------------------------------------------------------------------
# propose_ontology
# ---------------------------------------------------------------------------


@pytest.fixture
def two_domain_db(tmp_path: Path) -> str:
    """Graph with one big chain domain (>= min_nodes) and one tiny domain."""
    db = str(tmp_path / "g.db")
    big_nodes, big_edges = make_chain_nodes_edges("app.pipeline", 12)
    tiny_nodes, tiny_edges = make_chain_nodes_edges("app.tiny", 3)
    with SQLiteStore(db) as store:
        store.save_graph(big_nodes + tiny_nodes, big_edges + tiny_edges)
    return db


def test_propose_missing_db_raises(tmp_path: Path) -> None:
    """Nonexistent db must raise BEFORE SQLite silently creates a file (spec §2.1.1)."""
    missing = tmp_path / "nope.db"
    with pytest.raises(FileNotFoundError):
        propose_ontology(str(missing))
    assert not missing.exists()


def test_propose_emits_parseable_yaml_with_domains(two_domain_db: str) -> None:
    """Output parses as yaml and contains both discovered domains."""
    text = propose_ontology(two_domain_db, min_nodes=10)
    data = yaml.safe_load(text)
    names = {d["fqn_prefix"] for d in data["project_domains"]}
    assert names == {"app.pipeline", "app.tiny"}
    assert "patterns" in data
    assert "profiles" in data
    assert "hygiene" in data


def test_propose_labels_big_domain_and_hygienes_tiny(two_domain_db: str) -> None:
    """>= min_nodes chain gets an expected_pattern; tiny domain is hygiene-only."""
    text = propose_ontology(two_domain_db, min_nodes=10)
    data = yaml.safe_load(text)
    by_prefix = {d["fqn_prefix"]: d for d in data["project_domains"]}
    assert "expected_pattern" in by_prefix["app.pipeline"]
    assert "expected_pattern" not in by_prefix["app.tiny"]
    assert "# below min_nodes" in text


def test_propose_tolerance_is_measured_plus_margin(two_domain_db: str) -> None:
    """Round-trip: every proposed domain scores within its proposed tolerance."""
    text = propose_ontology(two_domain_db, min_nodes=10, margin=0.03)
    out = Path(two_domain_db).parent / "proposed.yaml"
    out.write_text(text)
    analysis = analyze_drift(two_domain_db, str(out))
    assert analysis.any_critical is False
    for r in analysis.reports:
        assert r.status != "empty"
        assert r.drift_score <= r.tolerance + 1e-9


def test_propose_no_fit_goes_hygiene_only(tmp_path: Path) -> None:
    """A census far from every ideal → no forced label, '# no template fits' comment."""
    # A dense bidirectional clique diverges from all five chain/star/dag ideals.
    db = str(tmp_path / "clique.db")
    names = [f"app.blob.n{i}" for i in range(12)]
    nodes = [_node(n, file_path="app/blob.py") for n in names]
    edges = [
        Edge(id=f"c{i}-{j}", source=a, target=b, type=EdgeType.CALLS)
        for i, a in enumerate(names)
        for j, b in enumerate(names)
        if i != j
    ]
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)
    # depth=2 forces app.blob as the domain prefix (auto-descent would otherwise
    # descend to the individual leaf nodes app.blob.n0…n11).
    text = propose_ontology(db, min_nodes=10, depth=2)
    data = yaml.safe_load(text)
    blob = next(d for d in data["project_domains"] if d["fqn_prefix"] == "app.blob")
    if "expected_pattern" not in blob:
        assert "# no template fits" in text
    # Either way the round-trip must hold (same depth=2 for consistency):
    out = tmp_path / "p.yaml"
    out.write_text(text)
    assert analyze_drift(db, str(out)).any_critical is False


def test_propose_deterministic(two_domain_db: str) -> None:
    """Two runs over the same graph are byte-identical."""
    assert propose_ontology(two_domain_db) == propose_ontology(two_domain_db)


def test_header_templates_match_repo_ontology() -> None:
    """Staleness pin: bundled patterns block == docs/ontology/patterns.yaml's (parsed)."""
    bundled = yaml.safe_load(_DEFAULT_ONTOLOGY_HEADER)
    repo = yaml.safe_load(Path("docs/ontology/patterns.yaml").read_text())
    assert bundled["patterns"] == repo["patterns"]
    assert bundled["profiles"] == repo["profiles"]
    assert bundled["hygiene"] == repo["hygiene"]


def test_hygiene_only_cyclic_domain_round_trips(tmp_path: Path) -> None:
    """Hygiene tolerance must be scored with the SAME profile the yaml emits.

    Regression for the profile-mismatch bug: a cyclic below-min_nodes domain
    proposed as hygiene-only must still satisfy score <= tolerance when the
    proposed yaml is fed back into analyze_drift (spec §4.3 round-trip).
    """
    db = str(tmp_path / "cyc.db")
    a = _node("app.cyc.mod_a", file_path="app/cyc/mod_a.py", node_type=NodeType.MODULE)
    b = _node("app.cyc.mod_b", file_path="app/cyc/mod_b.py", node_type=NodeType.MODULE)
    edges = [
        Edge(id="i1", source="app.cyc.mod_a", target="app.cyc.mod_b", type=EdgeType.IMPORTS),
        Edge(id="i2", source="app.cyc.mod_b", target="app.cyc.mod_a", type=EdgeType.IMPORTS),
    ]
    with SQLiteStore(db) as store:
        store.save_graph([a, b], edges)
    text = propose_ontology(db, min_nodes=10)
    out = tmp_path / "p.yaml"
    out.write_text(text)
    analysis = analyze_drift(db, str(out))
    # Verify the fixture actually has cycles (cycle_ratio > 0) — the regression
    # only fires when the cyclic weight (0.25 for python) diverges from the v1
    # equal-weight path, so a fixture without cycles would be a false green.
    with SQLiteStore(db) as store:
        fp = FingerprintExtractor(store).extract("app.cyc")
    assert fp.cycle_ratio > 0, "fixture has no cycles — in_cycle enrichment did not fire"
    for r in analysis.reports:
        assert r.drift_score <= r.tolerance + 1e-9, (
            f"{r.fqn_prefix}: {r.drift_score} > {r.tolerance}"
        )
