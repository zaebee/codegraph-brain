"""Data keys in the core (DK-1..DK-3): ids, matching, and the resolver's key nodes."""

from pathlib import Path

from cgis.core.keys import key_id, keys_match, parse_key_id
from cgis.core.models import VIRTUAL_FILE_PATH, Edge, EdgeType, Node, NodeType
from cgis.query.engine import BEHAVIORAL_EDGE_TYPES, QueryEngine
from cgis.resolver.engine import ResolverEngine
from cgis.storage.sqlite_store import SQLiteStore


def _func(fqn: str, file_path: str = "svc.py") -> Node:
    return Node(
        id=fqn,
        type=NodeType.FUNCTION,
        name=fqn.rsplit(".", maxsplit=1)[-1],
        file_path=file_path,
        start_line=1,
        end_line=5,
        language="python",
    )


def _file(
    fqn: str,
    file_path: str,
    constants: dict[str, object] | None = None,
    imports: dict[str, str] | None = None,
) -> Node:
    """A FILE node carrying its module-level string constants and its import map."""
    metadata: dict[str, object] = {"import_map": imports or {}}
    if constants is not None:
        metadata["str_constants"] = constants
    return Node(
        id=fqn,
        type=NodeType.FILE,
        name=file_path,
        file_path=file_path,
        start_line=1,
        end_line=1,
        language="python",
        metadata=metadata,
    )


def _key_edge(raw: str, edge_type: EdgeType = EdgeType.READS_KEY, source: str = "svc.f") -> Edge:
    return Edge(
        id=f"{source}->{raw}",
        source=source,
        target=f"raw_key:{raw}",
        type=edge_type,
        confidence=1.0,
        file_path="svc.py",
        line_number=3,
    )


def test_key_ids_round_trip() -> None:
    """An id parses back to its parts, including an unscoped key and a dotted path."""
    for namespace, path in [("events", "details.brand"), ("", "picture"), ("a", "b|c")]:
        assert parse_key_id(key_id(namespace, path)) == (namespace, path)
    assert key_id("events", "brand") != key_id("vehicles", "brand")
    assert key_id("", "brand") != key_id("events", "brand")


def test_keys_match_rule() -> None:
    """Equal paths match when the namespaces are equal or either is unscoped."""
    assert keys_match(key_id("events", "brand"), key_id("events", "brand"))
    assert keys_match(key_id("events", "brand"), key_id("", "brand"))
    assert keys_match(key_id("", "brand"), key_id("vehicles", "brand"))
    assert keys_match(key_id("", "brand"), key_id("", "brand"))
    assert not keys_match(key_id("events", "brand"), key_id("vehicles", "brand"))
    assert not keys_match(key_id("events", "brand"), key_id("events", "model"))


def test_raw_key_edges_become_key_nodes() -> None:
    """A raw key edge points at one shared virtual DATA_KEY node per (namespace, path)."""
    nodes = [_func("svc.f"), _func("svc.g", "other.py")]
    edges = [
        _key_edge("events|details.brand", EdgeType.WRITES_KEY),
        _key_edge("events|details.brand", EdgeType.READS_KEY, source="svc.g"),
        _key_edge("|picture", EdgeType.DECLARES_KEY),
    ]
    resolved, virtual = ResolverEngine(nodes, edges).resolve()
    scoped, unscoped = key_id("events", "details.brand"), key_id("", "picture")
    assert {(e.type, e.target) for e in resolved} == {
        (EdgeType.WRITES_KEY, scoped),
        (EdgeType.READS_KEY, scoped),
        (EdgeType.DECLARES_KEY, unscoped),
    }
    by_id = {n.id: n for n in virtual}
    assert set(by_id) == {scoped, unscoped}
    node = by_id[scoped]
    assert node.type is NodeType.DATA_KEY
    assert node.name == "details.brand"
    assert node.file_path == VIRTUAL_FILE_PATH
    assert node.metadata == {"namespace": "events", "path": "details.brand"}


def test_a_constant_namespace_resolves_through_the_files_constants() -> None:
    """`@NAME` becomes the namespace held by the module-level constant NAME of its file."""
    nodes = [_func("svc.f"), _file("svc", "svc.py", {"EVENTS_INDEX": "events"})]
    resolved, virtual = ResolverEngine(nodes, [_key_edge("@EVENTS_INDEX|brand")]).resolve()
    assert [e.target for e in resolved] == [key_id("events", "brand")]
    assert virtual[0].metadata == {"namespace": "events", "path": "brand"}


def test_an_imported_constant_names_the_namespace() -> None:
    """`from consts import INDEX`: the value comes from the file that defines INDEX."""
    nodes = [
        _func("svc.f"),
        _file("svc", "svc.py", imports={"INDEX": "consts.INDEX"}),
        _file("consts", "consts.py", {"INDEX": "vehicles"}),
        # Another file's same-named constant does not decide it.
        _file("other", "other.py", {"INDEX": "events"}),
    ]
    resolved, _ = ResolverEngine(nodes, [_key_edge("@INDEX|brand")]).resolve()
    assert [e.target for e in resolved] == [key_id("vehicles", "brand")]


def test_an_unresolvable_namespace_is_unscoped() -> None:
    """A missing constant, one that is not imported, or an import of nothing known: unscoped."""
    nodes = [
        _func("svc.f"),
        _file("svc", "svc.py", {}, imports={"GONE": "nowhere.GONE"}),
        _file("other", "other.py", {"ELSEWHERE": "events"}),
    ]
    edges = [_key_edge("@MISSING|brand"), _key_edge("@ELSEWHERE|brand"), _key_edge("@GONE|brand")]
    resolved, virtual = ResolverEngine(nodes, edges).resolve()
    assert {e.target for e in resolved} == {key_id("", "brand")}
    assert [n.id for n in virtual] == [key_id("", "brand")]


def test_key_nodes_do_not_count_as_unresolved(tmp_path: Path) -> None:
    """Key edges are neither unresolved nor resolved calls: the ratio is unchanged."""
    nodes = [_func("svc.f"), _func("svc.h")]
    call = Edge(
        id="c1",
        source="svc.f",
        target="raw_call:h",
        type=EdgeType.CALLS,
        confidence=0.5,
        file_path="svc.py",
    )
    unknown = call.model_copy(update={"id": "c2", "target": "raw_call:nowhere.thing"})

    def stats(edges: list[Edge]):  # noqa: ANN202 - EdgeStats is a storage detail
        resolved, virtual = ResolverEngine(nodes, edges).resolve()
        with SQLiteStore(str(tmp_path / f"g{len(edges)}.db")) as store:
            store.save_graph([*nodes, *virtual], resolved)
            return store.get_edge_stats()

    before = stats([call, unknown])
    after = stats([call, unknown, _key_edge("events|brand"), _key_edge("|picture")])
    assert (after.total, after.resolved, after.unresolved) == (
        before.total,
        before.resolved,
        before.unresolved,
    )
    assert after.unresolved_ratio == before.unresolved_ratio


def test_key_edges_stay_out_of_flow_and_impact(tmp_path: Path) -> None:
    """A flow from a function, or the impact of one, lists no DATA_KEY node (DK-1..DK-3)."""
    nodes = [_func("svc.f"), _func("svc.h")]
    call = Edge(id="c", source="svc.f", target="svc.h", type=EdgeType.CALLS, file_path="svc.py")
    edges = [call, _key_edge("events|brand"), _key_edge("|id", EdgeType.WRITES_KEY, "svc.h")]
    resolved, virtual = ResolverEngine(nodes, edges).resolve()
    with SQLiteStore(str(tmp_path / "g.db")) as store:
        store.save_graph([*nodes, *virtual], resolved)
        engine = QueryEngine(store)
        flow, _ = engine.get_flow_graph("svc.f", allowed_edge_types=BEHAVIORAL_EDGE_TYPES)
        impact, _ = engine.get_impact_graph("svc.h", allowed_edge_types=BEHAVIORAL_EDGE_TYPES)
    assert {n.id for n in flow} == {"svc.f", "svc.h"}
    assert {n.id for n in impact} == {"svc.f", "svc.h"}
    assert not [n for n in [*flow, *impact] if n.type is NodeType.DATA_KEY]
