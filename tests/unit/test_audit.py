"""Unit tests for the reachability/coverage audit primitive (#172)."""

from pathlib import Path

import pytest

from cgis.core.models import Edge, EdgeType, Node, NodeNamespace, NodeType
from cgis.query.context.audit import NoAuditSourcesError, audit_reachability
from cgis.storage.sqlite_store import SQLiteStore


def _node(node_id: str, node_type: NodeType, line: int = 1) -> Node:
    return Node(
        id=node_id,
        type=node_type,
        name=node_id.rsplit(".", maxsplit=1)[-1],
        file_path="app/routes.py",
        start_line=line,
        end_line=line + 1,
    )


def _store(tmp_path: Path, nodes: list[Node], edges: list[Edge]) -> str:
    db = str(tmp_path / "g.db")
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)
    return db


def _graph() -> tuple[list[Node], list[Edge]]:
    """3 route handlers + a DI handler; only h3 never reaches the ownership check."""
    nodes = [
        _node("app.routes.h1", NodeType.ROUTE_HANDLER, 10),
        _node("app.routes.h2", NodeType.ROUTE_HANDLER, 20),
        _node("app.routes.h3", NodeType.ROUTE_HANDLER, 30),  # the IDOR gap
        _node("app.routes.h4", NodeType.ROUTE_HANDLER, 40),  # reaches via FastAPI DI
        _node("app.verify_owner", NodeType.FUNCTION, 50),
        _node("app.svc", NodeType.FUNCTION, 60),
        _node("app.storage", NodeType.FUNCTION, 70),
    ]
    edges = [
        Edge(id="e1", source="app.routes.h1", target="app.verify_owner", type=EdgeType.CALLS),
        Edge(id="e2", source="app.routes.h2", target="app.svc", type=EdgeType.CALLS),
        Edge(id="e3", source="app.svc", target="app.verify_owner", type=EdgeType.CALLS),
        Edge(id="e4", source="app.routes.h3", target="app.storage", type=EdgeType.CALLS),
        Edge(id="e5", source="app.routes.h4", target="app.verify_owner", type=EdgeType.DEPENDS_ON),
    ]
    return nodes, edges


def test_audit_splits_covered_and_gaps(tmp_path: Path) -> None:
    """Handlers that reach the checkpoint are covered; the one that doesn't is a gap."""
    db = _store(tmp_path, *_graph())
    with SQLiteStore(db) as store:
        result = audit_reachability(
            store, target_fqn="app.verify_owner", from_type=NodeType.ROUTE_HANDLER
        )

    covered = {r.fqn for r in result.covered}
    gaps = {r.fqn for r in result.gaps}
    assert covered == {"app.routes.h1", "app.routes.h2", "app.routes.h4"}  # direct, transitive, DI
    assert gaps == {"app.routes.h3"}


def test_audit_gap_carries_file_and_line(tmp_path: Path) -> None:
    """A gap surfaces enough to jump to the handler (fqn/file/line)."""
    db = _store(tmp_path, *_graph())
    with SQLiteStore(db) as store:
        result = audit_reachability(
            store, target_fqn="app.verify_owner", from_type=NodeType.ROUTE_HANDLER
        )
    gap = result.gaps[0]
    assert gap.fqn == "app.routes.h3"
    assert gap.file == "app/routes.py"
    assert gap.line == 30


def test_audit_di_path_requires_behavioral_edges(tmp_path: Path) -> None:
    """The DI (DEPENDS_ON) path counts — authz via Depends() must be reachable."""
    db = _store(tmp_path, *_graph())
    with SQLiteStore(db) as store:
        result = audit_reachability(
            store, target_fqn="app.verify_owner", from_type=NodeType.ROUTE_HANDLER
        )
    assert "app.routes.h4" in {r.fqn for r in result.covered}


def test_audit_respects_max_depth(tmp_path: Path) -> None:
    """A transitive path longer than max_depth is reported as a gap."""
    db = _store(tmp_path, *_graph())
    with SQLiteStore(db) as store:
        result = audit_reachability(
            store, target_fqn="app.verify_owner", from_type=NodeType.ROUTE_HANDLER, max_depth=1
        )
    # h2 reaches verify only at depth 2 (h2→svc→verify), so at depth 1 it's a gap
    assert "app.routes.h2" in {r.fqn for r in result.gaps}
    assert "app.routes.h1" in {r.fqn for r in result.covered}  # direct, depth 1


def test_audit_from_prefix_selects_sources(tmp_path: Path) -> None:
    """Sources can be selected by FQN prefix instead of node type."""
    db = _store(tmp_path, *_graph())
    with SQLiteStore(db) as store:
        result = audit_reachability(store, target_fqn="app.verify_owner", from_prefix="app.routes")
    assert {r.fqn for r in result.gaps} == {"app.routes.h3"}


@pytest.mark.parametrize(
    ("selector", "named"),
    [
        ({"from_prefix": "app.rou"}, "from_prefix='app.rou'"),  # partial dot-segment
        ({"from_prefix": "nope"}, "from_prefix='nope'"),
        ({"from_type": NodeType.API_ENDPOINT}, "from_type=API_ENDPOINT"),
        ({"from_type": NodeType.ROUTE_HANDLER, "from_prefix": "app.svc"}, "from_prefix='app.svc'"),
    ],
)
def test_audit_selecting_no_sources_raises_instead_of_an_empty_result(
    tmp_path: Path, selector: dict[str, object], named: str
) -> None:
    """Zero audited sources is not a clean audit — `gaps == []` would pass a CI gate (#467)."""
    db = _store(tmp_path, *_graph())
    with SQLiteStore(db) as store, pytest.raises(NoAuditSourcesError) as excinfo:
        audit_reachability(store, target_fqn="app.verify_owner", **selector)  # type: ignore[arg-type]
    message = str(excinfo.value)
    assert "nothing was audited" in message
    assert named in message


def test_audit_no_sources_error_suggests_whole_segment_prefixes(tmp_path: Path) -> None:
    """A partial last segment names the dot-complete prefixes that would have matched."""
    db = _store(tmp_path, *_graph())
    with (
        SQLiteStore(db) as store,
        pytest.raises(NoAuditSourcesError, match=r"did you mean: app\.routes\?"),
    ):
        audit_reachability(store, target_fqn="app.verify_owner", from_prefix="app.rou")


@pytest.mark.parametrize(
    ("selector", "hint"),
    [
        # Whole segment, wrong type: the fix is the type, not the prefix.
        (
            {"from_type": NodeType.ROUTE_HANDLER, "from_prefix": "app.svc"},
            "none of type ROUTE_HANDLER",
        ),
        # Whole segment naming the checkpoint: nothing else lives there.
        ({"from_prefix": "app.verify_owner"}, "Only the checkpoint itself"),
    ],
)
def test_audit_no_sources_error_never_suggests_the_prefix_back(
    tmp_path: Path, selector: dict[str, object], hint: str
) -> None:
    """A suggestion equal to the input is noise; say why the whole segment selected nothing."""
    db = _store(tmp_path, *_graph())
    with SQLiteStore(db) as store, pytest.raises(NoAuditSourcesError) as excinfo:
        audit_reachability(store, target_fqn="app.verify_owner", **selector)  # type: ignore[arg-type]
    message = str(excinfo.value)
    assert "did you mean" not in message
    assert hint in message


def test_audit_no_sources_suggestions_respect_type_and_namespace(tmp_path: Path) -> None:
    """Suggest only prefixes that would select sources: same type, INTERNAL only."""
    nodes, edges = _graph()
    nodes.append(
        Node(
            id="app.roulette.spin",
            type=NodeType.ROUTE_HANDLER,
            name="spin",
            file_path="<stdlib>",
            start_line=0,
            end_line=0,
            namespace=NodeNamespace.STDLIB,
        )
    )
    nodes.append(_node("app.robots.index", NodeType.FUNCTION, 80))
    db = _store(tmp_path, nodes, edges)
    with SQLiteStore(db) as store, pytest.raises(NoAuditSourcesError) as excinfo:
        audit_reachability(
            store,
            target_fqn="app.verify_owner",
            from_type=NodeType.ROUTE_HANDLER,
            from_prefix="app.ro",
        )
    assert "did you mean: app.routes?" in str(excinfo.value)


def test_audit_trailing_dot_suggests_the_prefix_without_it(tmp_path: Path) -> None:
    """`app.routes.` meant `app.routes`, not a list of its children."""
    db = _store(tmp_path, *_graph())
    with SQLiteStore(db) as store, pytest.raises(NoAuditSourcesError) as excinfo:
        audit_reachability(store, target_fqn="app.verify_owner", from_prefix="app.routes.")
    assert "did you mean: app.routes?" in str(excinfo.value)


def test_audit_selecting_only_the_target_raises(tmp_path: Path) -> None:
    """The checkpoint is never its own source, so a selection of just it audits nothing."""
    db = _store(tmp_path, *_graph())
    with SQLiteStore(db) as store, pytest.raises(NoAuditSourcesError):
        audit_reachability(store, target_fqn="app.verify_owner", from_prefix="app.verify_owner")


def test_audit_empty_prefix_is_treated_as_unset(tmp_path: Path) -> None:
    """A whitespace-only from_prefix is treated as unset — error if it's the only selector."""
    db = _store(tmp_path, *_graph())
    with SQLiteStore(db) as store, pytest.raises(ValueError, match="from_type"):
        audit_reachability(store, target_fqn="app.verify_owner", from_prefix="   ")


def test_audit_reference_only_is_a_gap_not_covered(tmp_path: Path) -> None:
    """A REFERENCES/IMPORT link (not CALLS/DEPENDS_ON) is NOT coverage — sound for authz (#236)."""
    nodes = [
        _node("app.routes.ref", NodeType.ROUTE_HANDLER, 5),
        _node("app.verify_owner", NodeType.FUNCTION, 50),
    ]
    edges = [
        Edge(id="r", source="app.routes.ref", target="app.verify_owner", type=EdgeType.REFERENCES),
    ]
    db = _store(tmp_path, nodes, edges)
    with SQLiteStore(db) as store:
        result = audit_reachability(
            store, target_fqn="app.verify_owner", from_type=NodeType.ROUTE_HANDLER
        )
    # A reference-only link must not be treated as coverage.
    assert {r.fqn for r in result.gaps} == {"app.routes.ref"}
    assert result.covered == []


def test_audit_empty_edge_types_disables_traversal(tmp_path: Path) -> None:
    """An explicit empty allowed_edge_types is target-only — not silently the default."""
    db = _store(tmp_path, *_graph())
    with SQLiteStore(db) as store:
        result = audit_reachability(
            store,
            target_fqn="app.verify_owner",
            from_type=NodeType.ROUTE_HANDLER,
            allowed_edge_types=frozenset(),
        )
    # No edge types → no traversal → no handler reaches the guard.
    assert result.covered == []
    assert len(result.gaps) == 4
