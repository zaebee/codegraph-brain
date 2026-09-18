"""Module imports must reconcile the layout prefix, like every other target (#494).

`cgis ingest app/` gives nodes ids without the `app.` their imports carry, so
`from app.models import User` produced an edge to `app.models` — a name nothing
bears. Only 31 of owner-api's 6,293 IMPORTS edges landed on a real node; the rest
minted boundary nodes, and after #459 they are all counted unresolved.

The prefix is not a guess: `IndexBuilder` already classifies `app` as a
first-party layout prefix from the deeper imports (`app.core.config` strips to
`core.config`). This is that knowledge applied to the import edges themselves.
"""

from cgis.core.models import Edge, EdgeType, Node, NodeNamespace, NodeType
from cgis.resolver.engine import ResolverEngine


def _file(node_id: str, import_map: dict[str, str] | None = None) -> Node:
    return Node(
        id=node_id,
        type=NodeType.FILE,
        name=node_id,
        file_path=f"{node_id.replace('.', '/')}.py",
        start_line=1,
        end_line=1,
        metadata={"import_map": import_map or {}},
    )


def _graph() -> tuple[list[Node], list[Edge]]:
    """Ingested at `app/`: ids carry no `app.`, while the imports written in the code do.

    `seed` imports two modules that exist (`models`, `core.config`) and one that
    does not (`app.gone`). The deep one is what makes `app` a known first-party
    prefix rather than a third-party root.
    """
    nodes = [
        _file("models"),
        # The symbol an import names: `app.models.User` strips to `models.User`,
        # which is how IndexBuilder learns `app` is this project's own prefix.
        Node(
            id="models.User",
            type=NodeType.CLASS,
            name="User",
            file_path="models.py",
            start_line=2,
            end_line=3,
        ),
        _file("core.config"),
        _file(
            "seed",
            {
                "User": "app.models.User",
                "settings": "app.core.config.settings",
                "gone": "app.gone.thing",
            },
        ),
    ]
    edges = [
        Edge(id="i1", source="seed", target="app.models", type=EdgeType.IMPORTS, confidence=1.0),
        Edge(
            id="i2", source="seed", target="app.core.config", type=EdgeType.IMPORTS, confidence=1.0
        ),
        Edge(id="i3", source="seed", target="app.gone", type=EdgeType.IMPORTS, confidence=1.0),
    ]
    return nodes, edges


def _resolve() -> tuple[dict[str, Edge], dict[str, Node]]:
    resolved, virtual = ResolverEngine(*_graph()).resolve()
    return {edge.id: edge for edge in resolved}, {node.id: node for node in virtual}


def test_an_import_resolves_through_the_layout_prefix() -> None:
    """`app.models` is the graph's `models` — one segment left after the prefix."""
    edges, _virtual = _resolve()

    assert edges["i1"].target == "models"
    assert edges["i1"].confidence == 1.0


def test_a_deeper_import_resolves_too() -> None:
    """The rule is the prefix, not the depth: `app.core.config` → `core.config`."""
    edges, _virtual = _resolve()

    assert edges["i2"].target == "core.config"


def test_an_import_of_something_absent_stays_unresolved() -> None:
    """Stripping the prefix must not invent a node: `app.gone` has none (#459)."""
    edges, virtual = _resolve()

    assert edges["i3"].target == "app.gone"
    assert virtual["app.gone"].namespace is NodeNamespace.UNKNOWN


def test_a_third_party_import_is_left_alone() -> None:
    """`rich` is not a layout prefix of this project, so nothing is stripped."""
    nodes, edges = _graph()
    nodes.append(_file("other", {"Console": "rich.console.Console"}))
    edges.append(
        Edge(
            id="i4",
            source="other",
            target="rich.console",
            type=EdgeType.IMPORTS,
            confidence=1.0,
        )
    )

    resolved, virtual = ResolverEngine(nodes, edges).resolve()

    edge = next(e for e in resolved if e.id == "i4")
    assert edge.target == "rich.console"
    assert {node.id: node.namespace for node in virtual}["rich.console"] is NodeNamespace.EXTERNAL


def test_a_prefix_collision_does_not_resolve_to_an_unrelated_module() -> None:
    """Only the known layout prefix is stripped, never an arbitrary leading segment.

    `config` exists as a module of this project; `pydantic.config` must not become
    it, or every symbol of a library sharing a name with one of our modules would
    resolve into the project.
    """
    nodes, edges = _graph()
    nodes.append(_file("config"))
    nodes.append(_file("user", {"cfg": "pydantic.config"}))
    edges.append(
        Edge(
            id="i5",
            source="user",
            target="pydantic.config",
            type=EdgeType.IMPORTS,
            confidence=1.0,
        )
    )

    resolved, _virtual = ResolverEngine(nodes, edges).resolve()

    assert next(e for e in resolved if e.id == "i5").target == "pydantic.config"
