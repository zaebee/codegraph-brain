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
    does not (`app.gone`). Two of its import values reach a node, which is what
    makes `app` a corroborated layout prefix rather than a third-party root.
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
        # A second import value that reaches a node: one is not evidence that a
        # root is ours, and stripping an unproven one wired `import boto3.config`
        # into a project's own config.py (#498 review).
        Node(
            id="core.config.settings",
            type=NodeType.VARIABLE,
            name="settings",
            file_path="core/config.py",
            start_line=1,
            end_line=1,
        ),
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


def test_one_matching_import_is_not_enough_to_strip_a_root() -> None:
    """A third-party module sharing a path with ours must not make its root strippable.

    `from boto3.utils.helpers import x` against a project that has `utils/helpers.py`
    made `boto3` first-party; stripping it then resolved `import boto3.config` into
    the project's own `config.py` at full confidence (#498 review).
    """
    nodes = [
        _file("utils.helpers"),
        _file("config"),
        _file("a", {"x": "boto3.utils.helpers.x"}),
        _file("b", {}),
    ]
    edges = [
        Edge(id="i9", source="b", target="boto3.config", type=EdgeType.IMPORTS, confidence=1.0)
    ]

    resolved, virtual = ResolverEngine(nodes, edges).resolve()

    edge = next(e for e in resolved if e.id == "i9")
    assert edge.target == "boto3.config"
    assert {node.id: node.namespace for node in virtual}["boto3.config"] is not (
        NodeNamespace.INTERNAL
    )


def test_a_typescript_import_does_not_use_a_python_prefix() -> None:
    """The prefixes come from Python import maps; a TS path alias is not one (#454).

    `import { User } from "app/models"` in a mixed repo would otherwise wire a
    TypeScript file into a Python package of the same name.
    """
    nodes, edges = _graph()
    nodes.append(
        Node(
            id="web.page",
            type=NodeType.FILE,
            name="web.page",
            file_path="web/page.ts",
            start_line=1,
            end_line=1,
            metadata={"import_map": {}},
        )
    )
    edges.append(
        Edge(
            id="i8",
            source="web.page",
            target="app.models",
            type=EdgeType.IMPORTS,
            confidence=1.0,
            file_path="web/page.ts",
        )
    )

    resolved, _virtual = ResolverEngine(nodes, edges).resolve()

    assert next(e for e in resolved if e.id == "i8").target == "app.models"


def test_symbol_imports_never_reach_the_layout_path() -> None:
    """`IMPORTS_SYMBOL` carries a raw_import: target and is resolved elsewhere.

    Pins the blast radius: only already-final targets take the new branch.
    """
    nodes, edges = _graph()
    edges.append(
        Edge(
            id="i7",
            source="seed",
            target="raw_import:User",
            type=EdgeType.IMPORTS_SYMBOL,
            confidence=0.5,
            file_path="seed.py",
        )
    )

    resolved, _virtual = ResolverEngine(nodes, edges).resolve()

    assert next(e for e in resolved if e.id == "i7").target == "models.User"
