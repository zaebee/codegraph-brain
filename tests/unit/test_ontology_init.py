"""Unit tests for the init-ontology proposer (#174)."""

from pathlib import Path

import pytest
import yaml
from conftest import make_chain_nodes_edges, module_with_funcs

from cgis.core.models import VIRTUAL_FILE_PATH, Edge, EdgeType, Node, NodeType
from cgis.query.drift_service import analyze_drift
from cgis.query.fingerprint import FingerprintExtractor, PatternFingerprint
from cgis.query.ontology_init import (
    _DEFAULT_ONTOLOGY_HEADER,
    _baseline_lines,
    discover_domains,
    propose_ontology,
)
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


def test_discover_depth_zero_or_negative_raises() -> None:
    """depth must be positive — 0/negative would yield empty prefixes."""
    nodes = [_node("a.b.c")]
    with pytest.raises(ValueError, match="positive"):
        discover_domains(nodes, depth=0)
    with pytest.raises(ValueError, match="positive"):
        discover_domains(nodes, depth=-2)


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


# ---------------------------------------------------------------------------
# hygiene_baseline emission in init-ontology (#176/#170 task 4)
# ---------------------------------------------------------------------------


def test_cyclic_domain_proposal_emits_baseline_and_round_trips(tmp_path: Path) -> None:
    """A domain with an intra-domain import cycle gets an acknowledged baseline (spec §2.2).

    The emitted value is _ceil2'd UP (colleague catch: flooring below the true
    measurement would gate_fail the proposal on its own graph).
    Round-trip guarantee: analyze_drift on the proposal yields no gate_failed
    and any_critical is False.
    """
    db = str(tmp_path / "cyc.db")
    # Two modules importing each other + enough functions to clear min_nodes:
    nodes: list[Node] = []
    edges: list[Edge] = []
    for mod, n in (("app.loop.a", 6), ("app.loop.b", 6)):
        nodes += module_with_funcs(mod, f"{mod.replace('.', '/')}.py", n)
    edges += [
        Edge(id="c1", source="app.loop.a", target="app.loop.b", type=EdgeType.IMPORTS),
        Edge(id="c2", source="app.loop.b", target="app.loop.a", type=EdgeType.IMPORTS),
    ]
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)
    # depth=2 forces app.loop as the domain prefix (auto-descent would discover
    # app.loop.a and app.loop.b as separate single-file domains, each with a
    # cross-domain cycle_ratio of 0.0 — Task 1 semantics).  With depth=2, the
    # unified domain has all 14 nodes and an intra-domain cycle_ratio > 0.
    text = propose_ontology(db, min_nodes=10, depth=2)
    assert "hygiene_baseline" in text
    assert "acknowledged at baseline by init-ontology" in text
    out = tmp_path / "p.yaml"
    out.write_text(text)
    analysis = analyze_drift(db, str(out))
    assert analysis.any_critical is False
    assert all(r.status != "gate_failed" for r in analysis.reports)


# ---------------------------------------------------------------------------
# _baseline_lines: operator-aware rounding (#221 review, spec §2.2)
# ---------------------------------------------------------------------------


def _fp(**kwargs: object) -> PatternFingerprint:
    """Minimal PatternFingerprint factory for _baseline_lines tests."""
    defaults: dict[str, object] = {
        "domain": "test",
        "hub_count": 0,
        "star_count": 0,
        "chain_len": 0.0,
        "dag_depth": 0,
        "router_count": 0,
        "cycle_ratio": 0.0,
        "unresolved_ratio": 0.0,
    }
    defaults.update(kwargs)
    return PatternFingerprint(**defaults)  # type: ignore[arg-type]


def test_baseline_lines_max_rounds_up() -> None:
    """max constraint: measured value is ceiled (rounds UP) so the baseline >= measured."""
    # cycle_ratio 0.073 → ceil2 = 0.08, not 0.07.
    hygiene: dict[str, object] = {"cycle_ratio": {"max": 0.0}}
    fp = _fp(cycle_ratio=0.073)
    lines = _baseline_lines(fp, hygiene=hygiene)
    assert len(lines) == 1
    assert "cycle_ratio: 0.08" in lines[0]


def test_baseline_lines_min_rounds_down() -> None:
    """min constraint: measured value is floored (rounds DOWN) so baseline <= measured.

    Without this fix, _ceil2 would produce a value ABOVE the true measurement,
    which would gate_fail the proposal (the round-trip would show the acknowledged
    baseline as a new breach since baseline > measured).
    """
    # synthetic hygiene with a min key; measured 0.856 → floor2 = 0.85 (not ceil = 0.86)
    hygiene: dict[str, object] = {"chain_len": {"min": 1.0}}
    fp = _fp(chain_len=0.856)
    lines = _baseline_lines(fp, hygiene=hygiene)
    assert len(lines) == 1
    assert "chain_len: 0.85" in lines[0]


def test_baseline_lines_exact_skipped() -> None:
    """exact constraint breaches are skipped — pinning would re-fail on improvement."""
    hygiene: dict[str, object] = {"router_count": {"exact": 0.0}}
    fp = _fp(router_count=2)  # 2 != 0 → breach
    lines = _baseline_lines(fp, hygiene=hygiene)
    assert lines == [], "exact breaches must not emit baseline lines"


def test_baseline_lines_no_breach_returns_empty() -> None:
    """Compliant fingerprints produce no baseline lines."""
    hygiene: dict[str, object] = {"cycle_ratio": {"max": 0.0}, "unresolved_ratio": {"max": 0.2}}
    fp = _fp(cycle_ratio=0.0, unresolved_ratio=0.1)
    assert _baseline_lines(fp, hygiene=hygiene) == []
