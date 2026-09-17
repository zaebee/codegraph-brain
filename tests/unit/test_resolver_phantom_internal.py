"""An import of a symbol the project does not have must stay unresolved (#459).

`from pkg.a import foo` where `pkg.a` has no `foo` resolves through the import map
to `pkg.a.foo`, and the fallback keeps that target even though nothing bears the
name. The boundary node minted for it was classified INTERNAL — so a call to a
function that does not exist read as a *resolved internal call*, and dropped out
of `unresolved_ratio` entirely.

That is the failure this repository has hit twice before under different names
(#414 self-dispatch, #454 stdlib-in-TypeScript): a number that measures honesty
improving because the graph stopped noticing something.
"""

from pathlib import Path

import pytest

from cgis.core.models import Edge, EdgeType, Node, NodeNamespace, NodeType
from cgis.extractors.python_extractor import PythonExtractor
from cgis.pipeline import IngestionPipeline
from cgis.resolver.engine import ResolverEngine
from cgis.storage.sqlite_store import SQLiteStore


def _file_node(path: str, import_map: dict[str, str]) -> Node:
    return Node(
        id=path.replace("/", ".").removesuffix(".py"),
        type=NodeType.FILE,
        name=path,
        file_path=path,
        start_line=1,
        end_line=1,
        metadata={"import_map": import_map},
    )


def _func(fqn: str, file_path: str) -> Node:
    return Node(
        id=fqn,
        type=NodeType.FUNCTION,
        name=fqn.rsplit(".", maxsplit=1)[-1],
        file_path=file_path,
        start_line=2,
        end_line=3,
    )


def _resolve(nodes: list[Node], edges: list[Edge]) -> dict[str, Node]:
    _resolved, virtual = ResolverEngine(nodes, edges).resolve()
    return {node.id: node for node in virtual}


def test_a_missing_internal_symbol_is_unknown_not_internal() -> None:
    """`pkg.a` exists, `pkg.a.foo` does not: the call is unresolved, and says so."""
    nodes = [
        _file_node("pkg/a.py", {}),
        _func("pkg.a.foo2", "pkg/a.py"),
        _file_node("pkg/b.py", {"foo": "pkg.a.foo"}),
        _func("pkg.b.bar", "pkg/b.py"),
    ]
    edges = [
        Edge(
            id="e1",
            source="pkg.b.bar",
            target="raw_call:foo",
            type=EdgeType.CALLS,
            confidence=0.5,
            file_path="pkg/b.py",
        )
    ]

    virtual = _resolve(nodes, edges)

    assert virtual["pkg.a.foo"].namespace is NodeNamespace.UNKNOWN


@pytest.mark.parametrize(
    ("import_target", "expected"),
    [
        ("rich.console.Console", NodeNamespace.EXTERNAL),
        ("json.dumps", NodeNamespace.STDLIB),
    ],
)
def test_third_party_and_stdlib_targets_keep_their_namespace(
    import_target: str, expected: NodeNamespace
) -> None:
    """The fallback is load-bearing outside the project — only internal roots change."""
    nodes = [
        _file_node("pkg/b.py", {"thing": import_target}),
        _func("pkg.b.bar", "pkg/b.py"),
    ]
    edges = [
        Edge(
            id="e1",
            source="pkg.b.bar",
            target="raw_call:thing",
            type=EdgeType.CALLS,
            confidence=0.5,
            file_path="pkg/b.py",
        )
    ]

    virtual = _resolve(nodes, edges)

    assert virtual[import_target].namespace is expected


def test_the_missing_call_is_counted_as_unresolved(tmp_path: Path) -> None:
    """End to end: `cgis validate` must see the gap, which is the whole point (#459).

    Before this fix the same graph reported a 0.0 unresolved ratio, because the
    phantom node made the call look internal.
    """
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "a.py").write_text("def foo2():\n    return 1\n", encoding="utf-8")
    (tmp_path / "pkg" / "b.py").write_text(
        "from pkg.a import foo\n\n\ndef bar():\n    return foo()\n", encoding="utf-8"
    )
    db = str(tmp_path / "graph.db")

    with SQLiteStore(db) as store:
        IngestionPipeline({".py": PythonExtractor()}).run(str(tmp_path), store=store)
        stats = store.get_edge_stats()
        phantom = store.get_node("pkg.a.foo")

    assert phantom is not None, "the target is kept, so the missing symbol is namable"
    assert phantom.namespace is NodeNamespace.UNKNOWN
    assert stats.unresolved >= 1
    assert stats.unresolved_ratio > 0.0
