"""Unit tests for SymbolIndex and IndexBuilder."""

import pytest

from cgis.core.models import Node, NodeNamespace, NodeType
from cgis.resolver.indices import IndexBuilder


def _node(
    fqn: str,
    node_type: NodeType = NodeType.FUNCTION,
    file_path: str = "mod.py",
    metadata: dict[str, object] | None = None,
) -> Node:
    """Build a minimal Node for index tests."""
    return Node(
        id=fqn,
        type=node_type,
        name=fqn.rsplit(".", maxsplit=1)[-1],
        file_path=file_path,
        start_line=1,
        end_line=2,
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# IndexBuilder.build
# ---------------------------------------------------------------------------


def test_build_indexes_global_symbols() -> None:
    """FUNCTION and CLASS nodes land in global_symbols and file_global_symbols."""
    index = IndexBuilder().build([_node("mod.f"), _node("mod.Cls", node_type=NodeType.CLASS)])
    assert index.global_symbols["f"] == ["mod.f"]
    assert index.global_symbols["Cls"] == ["mod.Cls"]
    assert index.file_global_symbols[("mod.py", "f")] == ["mod.f"]


def test_build_indexes_class_methods() -> None:
    """METHOD nodes are indexed under their enclosing class FQN."""
    index = IndexBuilder().build([_node("mod.Cls.m", node_type=NodeType.METHOD)])
    assert index.class_methods["mod.Cls"]["m"] == "mod.Cls.m"


def test_build_indexes_variables_separately() -> None:
    """VARIABLE nodes go to variable_symbols and NEVER to global_symbols (#161 invariant)."""
    index = IndexBuilder().build([_node("mod.Dep", node_type=NodeType.VARIABLE)])
    assert index.variable_symbols["Dep"] == ["mod.Dep"]
    assert index.file_variable_symbols[("mod.py", "Dep")] == ["mod.Dep"]
    assert "Dep" not in index.global_symbols


def test_build_indexes_file_import_map() -> None:
    """FILE nodes contribute their import_map keyed by normalized path."""
    file_node = _node(
        "mod",
        node_type=NodeType.FILE,
        file_path="./mod.py",
        metadata={"import_map": {"Dep": "other.Dep"}},
    )
    index = IndexBuilder().build([file_node])
    assert index.file_imports["mod.py"] == {"Dep": "other.Dep"}
    assert index.external_roots == {"other"}


# ---------------------------------------------------------------------------
# SymbolIndex.map_to_node_fqn
# ---------------------------------------------------------------------------


def test_map_to_node_fqn_exact() -> None:
    """An FQN that is already a node id maps to itself."""
    index = IndexBuilder().build([_node("cgis.mod.f")])
    assert index.map_to_node_fqn("cgis.mod.f") == "cgis.mod.f"


def test_map_to_node_fqn_suffix() -> None:
    """A node with an extra layout prefix is found via the suffix map."""
    index = IndexBuilder().build([_node("src.cgis.mod.f")])
    assert index.map_to_node_fqn("cgis.mod.f") == "src.cgis.mod.f"


def test_map_to_node_fqn_strips_import_prefix() -> None:
    """An import with an extra package prefix resolves by stripping segments."""
    index = IndexBuilder().build([_node("mod.f")])
    assert index.map_to_node_fqn("extra.mod.f") == "mod.f"


def test_map_to_node_fqn_ambiguous_suffix_is_none() -> None:
    """Two nodes sharing a suffix make the lookup ambiguous: None."""
    index = IndexBuilder().build([_node("a.cgis.mod.f"), _node("b.cgis.mod.f")])
    assert index.map_to_node_fqn("cgis.mod.f") is None


# ---------------------------------------------------------------------------
# SymbolIndex.classify_fqn
# ---------------------------------------------------------------------------


def test_classify_fqn_namespaces() -> None:
    """classify_fqn distinguishes INTERNAL, STDLIB, EXTERNAL, and UNKNOWN roots."""
    file_node = _node(
        "pkg",
        node_type=NodeType.FILE,
        metadata={"import_map": {"Depends": "fastapi.Depends"}},
    )
    index = IndexBuilder().build([file_node, _node("pkg.mod.f")])
    assert index.classify_fqn("pkg.mod.f") is NodeNamespace.INTERNAL
    assert index.classify_fqn("os.path.join") is NodeNamespace.STDLIB
    assert index.classify_fqn("fastapi.Depends") is NodeNamespace.EXTERNAL
    assert index.classify_fqn("zzz.unknown") is NodeNamespace.UNKNOWN
    # Changed by #414: a self.-prefixed FQN is an unresolved receiver, not a
    # symbol. Calling it INTERNAL made get_edge_stats score it as *resolved*,
    # so unresolved_ratio improved as a codebase adopted more dependency
    # injection. The old assertion encoded that bug.
    assert index.classify_fqn("self.method") is NodeNamespace.UNKNOWN
    assert index.classify_fqn(".relative") is NodeNamespace.INTERNAL


# ---------------------------------------------------------------------------
# SymbolIndex.is_variable_node / normalized_file_path
# ---------------------------------------------------------------------------


def test_is_variable_node() -> None:
    """True only for an existing VARIABLE node."""
    index = IndexBuilder().build([_node("mod.Dep", node_type=NodeType.VARIABLE), _node("mod.f")])
    assert index.is_variable_node("mod.Dep") is True
    assert index.is_variable_node("mod.f") is False
    assert index.is_variable_node("mod.missing") is False


def test_normalized_file_path_from_node_and_fallback() -> None:
    """Known FQN uses the node's path; unknown falls back to edge_file_path; else None."""
    index = IndexBuilder().build([_node("mod.f", file_path="./pkg/mod.py")])
    assert index.normalized_file_path("mod.f", None) == "pkg/mod.py"
    assert index.normalized_file_path("missing", "./x/y.py") == "x/y.py"
    assert index.normalized_file_path("missing", None) is None


# ---------------------------------------------------------------------------
# Index immutability and has_node (#183 items 5 and 7)
# ---------------------------------------------------------------------------


def test_index_mappings_reject_writes() -> None:
    """A stray write must raise, not silently corrupt resolution for every later lookup.

    The dataclass being frozen only stops field *rebinding*; before this the
    contained dicts were mutable and "never mutated after construction" was a
    convention held by comment alone.
    """
    index = IndexBuilder().build([_node("mod.fn")])

    for name in (
        "nodes",
        "global_symbols",
        "file_global_symbols",
        "class_methods",
        "variable_symbols",
        "file_variable_symbols",
        "file_imports",
        "suffix_map",
    ):
        mapping = getattr(index, name)
        with pytest.raises(TypeError):
            mapping["injected"] = "value"  # type: ignore[index]


def test_index_root_sets_are_frozen() -> None:
    """The two root sets are frozensets, so `.add()` is not available at all."""
    index = IndexBuilder().build([_node("mod.fn")])

    assert isinstance(index.internal_roots, frozenset)
    assert isinstance(index.external_roots, frozenset)


def test_index_still_reads_normally_through_the_views() -> None:
    """Read-only views must not change lookup behaviour."""
    index = IndexBuilder().build([_node("mod.fn")])

    assert index.nodes["mod.fn"].name == "fn"
    assert "mod.fn" in index.nodes
    assert index.nodes.get("absent") is None
    assert list(index.global_symbols) == ["fn"]


def test_has_node_reports_membership() -> None:
    """has_node replaces callers reaching into `.nodes` for a membership test."""
    index = IndexBuilder().build([_node("mod.fn")])

    assert index.has_node("mod.fn") is True
    assert index.has_node("mod.missing") is False


# ---------------------------------------------------------------------------
# self_types (spec D1) — written by the extractor, read by resolve_self_call
# ---------------------------------------------------------------------------


def test_self_types_is_indexed_by_class_fqn() -> None:
    """A CLASS node's self_types map reaches the index under the class's own FQN."""
    index = IndexBuilder().build(
        [
            _node(
                "pkg.mod.Adapter",
                NodeType.CLASS,
                metadata={"self_types": {"client": "pkg.client.SearchClient"}},
            )
        ]
    )
    assert index.self_types == {"pkg.mod.Adapter": {"client": "pkg.client.SearchClient"}}


def test_class_without_self_types_is_absent_rather_than_empty() -> None:
    """A class with no annotated attributes contributes no entry at all."""
    index = IndexBuilder().build([_node("pkg.mod.Plain", NodeType.CLASS)])
    assert "pkg.mod.Plain" not in index.self_types


def test_two_classes_keep_separate_self_types_maps() -> None:
    """Same attribute name, different classes, different types — no merging.

    This is the collision #414 reports from the other end: without per-class
    keying, PythonExtractor and TypeScriptExtractor would agree on what
    `self._parser` is.
    """
    index = IndexBuilder().build(
        [
            _node("pkg.mod.A", NodeType.CLASS, metadata={"self_types": {"parser": "pkg.a.Py"}}),
            _node("pkg.mod.B", NodeType.CLASS, metadata={"self_types": {"parser": "pkg.b.Ts"}}),
        ]
    )
    assert index.self_types["pkg.mod.A"]["parser"] == "pkg.a.Py"
    assert index.self_types["pkg.mod.B"]["parser"] == "pkg.b.Ts"


def test_self_types_ignores_the_metadata_of_a_non_class_node() -> None:
    """Only CLASS nodes contribute; a stray key elsewhere must not reach the map."""
    index = IndexBuilder().build(
        [_node("pkg.mod.fn", NodeType.FUNCTION, metadata={"self_types": {"x": "pkg.X"}})]
    )
    assert index.self_types == {}


# ---------------------------------------------------------------------------
# classify_fqn: a self. placeholder is not a resolved symbol (#414)
# ---------------------------------------------------------------------------


def test_relative_import_prefix_is_internal() -> None:
    """A leading dot is a relative import — genuinely internal. Must not change."""
    index = IndexBuilder().build([_node("pkg.mod.A", NodeType.CLASS)])
    assert index.classify_fqn(".sibling.thing") == NodeNamespace.INTERNAL


def test_self_placeholder_is_not_internal() -> None:
    """An unresolved self.<attr>.<method> placeholder must not count as resolved.

    get_edge_stats scores an INTERNAL target on the resolved side, so classifying
    these INTERNAL let unresolved_ratio *improve* as a codebase adopted more
    dependency injection — the metric moving the wrong way for exactly the reason
    #414 was filed. A green `validate` therefore said nothing about the gap.
    """
    index = IndexBuilder().build([_node("pkg.mod.A", NodeType.CLASS)])
    assert index.classify_fqn("self.client.search") == NodeNamespace.UNKNOWN


# ---------------------------------------------------------------------------
# First-party roots hidden in external_roots (#424)
# ---------------------------------------------------------------------------


def test_an_import_prefix_that_reaches_a_node_is_internal_not_external() -> None:
    """Ingesting at a subdirectory makes the package name look like a third party.

    `cgis ingest ownima-backend/app` roots its nodes at `domains`, `api`, … while
    the code imports `from app.models import X`. `app` then lands in
    external_roots, every `app.*` FQN without a node classifies EXTERNAL, and
    D7's library branch mints a boundary node for a class of ours that does not
    exist — a confident edge to nothing (#424).

    An import whose tail reaches a real node is project code, whatever its head.
    """
    file_node = _node(
        "domains.svc",
        node_type=NodeType.FILE,
        metadata={"import_map": {"Thing": "app.domains.models.Thing"}},
    )
    index = IndexBuilder().build([file_node, _node("domains.models.Thing", NodeType.CLASS)])
    assert index.classify_fqn("app.domains.models.Thing") is NodeNamespace.INTERNAL
    assert index.classify_fqn("app.api.deps.Missing") is NodeNamespace.INTERNAL


def test_a_real_third_party_root_stays_external() -> None:
    """The discriminator must not swallow genuine packages.

    Measured on owner-api: `app` had 3346 of 4885 import values reach a node,
    while pydantic, sqlalchemy, grpc, fastapi and httpx had zero. There is no
    grey zone between the two.
    """
    file_node = _node(
        "domains.svc",
        node_type=NodeType.FILE,
        metadata={"import_map": {"BaseModel": "pydantic.BaseModel"}},
    )
    index = IndexBuilder().build([file_node, _node("domains.models.Thing", NodeType.CLASS)])
    assert index.classify_fqn("pydantic.BaseModel") is NodeNamespace.EXTERNAL


def test_a_package_sharing_a_name_with_a_project_subpackage_stays_external() -> None:
    """`grpc` the library must not become internal because of `api/deps/grpc/`.

    The earlier per-call-path guard used map_to_node_fqn, which matches by
    suffix on purpose, and this exact collision made it stop resolving 56
    genuine gRPC calls on owner-api. The discriminator here asks whether the
    *import value* reaches a node, not whether some node ends with the name.
    """
    file_node = _node(
        "api.deps.grpc.client",
        node_type=NodeType.FILE,
        metadata={"import_map": {"Channel": "grpc.Channel"}},
    )
    index = IndexBuilder().build([file_node, _node("api.deps.grpc.client.Stub", NodeType.CLASS)])
    assert index.classify_fqn("grpc.Channel") is NodeNamespace.EXTERNAL


def test_a_library_submodule_sharing_a_top_level_name_stays_external() -> None:
    """`from pydantic import config` must not make pydantic first-party.

    Stripping the root of `pydantic.config` leaves `config`, and a project with a
    top-level config.py has a node by that name — so the whole of pydantic would
    classify INTERNAL and resolution for every symbol in it would break. `config`
    and `utils` are among the most common module names in Python, so this is a
    likely collision rather than a contrived one.

    Requiring two segments after the strip removes it: two cannot collide by
    accident, and a genuine first-party prefix always has deeper imports —
    measured on owner-api, 3346 values resolve and none needs a single-segment
    match.
    """
    consumer = _node(
        "svc",
        node_type=NodeType.FILE,
        file_path="svc.py",
        metadata={"import_map": {"config": "pydantic.config", "BaseModel": "pydantic.BaseModel"}},
    )
    index = IndexBuilder().build(
        [consumer, _node("config", NodeType.FILE, file_path="config.py"), _node("svc.go")]
    )
    assert index.classify_fqn("pydantic.BaseModel") is NodeNamespace.EXTERNAL
    assert "pydantic" not in index.internal_roots
