"""The Python pack's index facts and classification (language-packs step 3).

Values are those the pre-step-3 `IndexBuilder`/`classify_fqn` produced for the
same nodes; the equivalence over real graphs is the identity snapshot's job.
"""

from types import MappingProxyType

from cgis.core.index import ClassifyContext
from cgis.core.models import Node, NodeNamespace, NodeType
from cgis.languages.python.index import PythonIndexFacts, classify, index_facts


def _node(
    fqn: str,
    node_type: NodeType = NodeType.FUNCTION,
    file_path: str | None = None,
    metadata: dict[str, object] | None = None,
) -> Node:
    return Node(
        id=fqn,
        type=node_type,
        name=fqn.rsplit(".", 1)[-1],
        file_path=file_path or f"{fqn.replace('.', '/')}.py",
        start_line=1,
        end_line=1,
        metadata=metadata or {},
    )


def _facts(nodes: list[Node], deps: list[str] | None = None) -> PythonIndexFacts:
    by_id = {n.id: n for n in nodes}
    return index_facts(MappingProxyType(by_id), {"python_dependencies": deps or []})


def _ctx(
    facts: PythonIndexFacts, nodes: list[Node], roots: set[str], source: str | None
) -> ClassifyContext:
    return ClassifyContext(
        source_file=source,
        internal_roots=frozenset(roots) | facts.internal_roots,
        nodes=MappingProxyType({n.id: n for n in nodes}),
        facts=facts,
    )


def test_import_roots_split_into_external_and_first_party() -> None:
    """#424: `app` reaches our nodes once stripped (twice: corroborated); `pydantic` never does."""
    file_node = _node(
        "domains.views",
        NodeType.FILE,
        file_path="domains/views.py",
        metadata={
            "import_map": {
                "A": "app.domains.models.A",
                "B": "app.domains.models.B",
                "C": "pydantic.BaseModel",
            }
        },
    )
    nodes = [file_node, _node("domains.models.A"), _node("domains.models.B")]
    facts = _facts(nodes)
    assert facts.external_roots == frozenset({"pydantic"})
    assert "app" in facts.internal_roots
    assert facts.layout_prefixes == frozenset({"app"})


def test_src_and_lib_layouts_add_their_second_segment() -> None:
    facts = _facts([_node("src.cgis.pipeline.run"), _node("lib.util.x")])
    assert {"cgis", "util"} <= facts.internal_roots


def test_only_colliding_dependencies_are_kept() -> None:
    """#495: a declared dependency matters only where an internal root shares its name."""
    nodes = [_node("alembic.env.run_migrations"), _node("app.main")]
    facts = _facts(nodes, deps=["alembic", "fastapi"])
    assert facts.dependency_roots == frozenset({"alembic"})
    assert facts.dependency_heads == frozenset({"alembic.env"})


def test_classification_chain() -> None:
    nodes = [_node("alembic.env.run_migrations"), _node("json.helpers.f")]
    facts = _facts(nodes, deps=["alembic"])
    ctx = _ctx(facts, nodes, {"alembic", "json"}, "app/x.py")
    assert classify("alembic.env.run_migrations", ctx) is NodeNamespace.INTERNAL
    assert classify("alembic.op.add_column", ctx) is NodeNamespace.EXTERNAL  # declared, not ours
    assert classify("json.helpers.f", ctx) is NodeNamespace.INTERNAL  # our package beats stdlib
    assert classify("os.path.join", ctx) is NodeNamespace.STDLIB
    assert classify("len", ctx) is NodeNamespace.STDLIB
    assert classify("js_builtins.Math", ctx) is NodeNamespace.STDLIB
    assert classify("nothing.known", ctx) is NodeNamespace.UNKNOWN
