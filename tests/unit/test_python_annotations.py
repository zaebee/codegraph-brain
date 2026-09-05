"""Tests for the annotation type-name collector (D9)."""

import tree_sitter_python as tspython
from tree_sitter import Language, Parser
from tree_sitter import Node as BaseNode

from cgis.extractors._python_annotations import collect_type_names
from cgis.extractors._python_ast import enclosing_class_fqn

_PARSER = Parser(Language(tspython.language()))


def _annotation(source: str) -> tuple[BaseNode, bytes]:
    """Parse `x: <source>` and return the annotation node plus the code bytes."""
    code = f"x: {source}".encode()
    root = _PARSER.parse(code).root_node
    assignment = root.named_children[0].named_children[0]
    type_node = assignment.child_by_field_name("type")
    assert type_node is not None
    return type_node, code


def test_bare_name() -> None:
    node, code = _annotation("SearchClient")
    assert collect_type_names(node, code) == ["SearchClient"]


def test_generic_keeps_both_container_and_argument() -> None:
    node, code = _annotation("list[Node]")
    assert collect_type_names(node, code) == ["list", "Node"]


def test_nested_generic_reaches_every_argument() -> None:
    node, code = _annotation("dict[str, list[Edge]]")
    assert collect_type_names(node, code) == ["dict", "str", "list", "Edge"]


def test_union_keeps_every_member() -> None:
    node, code = _annotation("Node | Edge | None")
    assert collect_type_names(node, code) == ["Node", "Edge", "None"]


def test_dotted_name_is_kept_whole() -> None:
    node, code = _annotation("models.BaseModel")
    assert collect_type_names(node, code) == ["models.BaseModel"]


def test_string_annotation_is_parsed() -> None:
    node, code = _annotation('"list[Node]"')
    assert collect_type_names(node, code) == ["list", "Node"]


def test_duplicates_collapse_preserving_first_position() -> None:
    node, code = _annotation("dict[Node, Node]")
    assert collect_type_names(node, code) == ["dict", "Node"]


def test_optional_unwraps_to_inner_type() -> None:
    node, code = _annotation("Optional[Node]")
    assert collect_type_names(node, code) == ["Node"]


def test_union_wrapper_keeps_every_member() -> None:
    node, code = _annotation("Union[Node, Edge]")
    assert collect_type_names(node, code) == ["Node", "Edge"]


def test_annotated_keeps_only_first_argument() -> None:
    node, code = _annotation("Annotated[Session, Depends(get_db)]")
    assert collect_type_names(node, code) == ["Session"]


def test_optional_of_generic_reaches_inner_arguments() -> None:
    node, code = _annotation("Optional[list[Node]]")
    assert collect_type_names(node, code) == ["list", "Node"]


def test_union_of_generics_reaches_every_argument() -> None:
    node, code = _annotation("Union[list[A], dict[str, B]]")
    assert collect_type_names(node, code) == ["list", "A", "dict", "str", "B"]


def test_annotated_generic_first_argument_reaches_its_arguments() -> None:
    node, code = _annotation("Annotated[list[X], Depends(f)]")
    assert collect_type_names(node, code) == ["list", "X"]


def test_qualified_optional_unwraps_to_inner_type() -> None:
    """`typing.Optional[X]` unwraps the same as bare `Optional[X]` (#194)."""
    node, code = _annotation("typing.Optional[Session]")
    assert collect_type_names(node, code) == ["Session"]


def test_qualified_union_keeps_every_member() -> None:
    """`typing.Union[A, B]` unwraps the same as bare `Union[A, B]` (#194)."""
    node, code = _annotation("typing.Union[A, B]")
    assert collect_type_names(node, code) == ["A", "B"]


def test_qualified_annotated_keeps_only_first_argument() -> None:
    """`typing.Annotated[T, ...]` unwraps the same as bare `Annotated[T, ...]` (#194)."""
    node, code = _annotation("typing.Annotated[Session, Depends(get_db)]")
    assert collect_type_names(node, code) == ["Session"]


def test_bare_annotated_without_subscript_is_kept_as_a_name() -> None:
    """`Annotated` with no subscript at all is just a plain name reference."""
    node, code = _annotation("Annotated")
    assert collect_type_names(node, code) == ["Annotated"]


def _first_assignment_in(code: str) -> tuple[BaseNode, bytes]:
    """Parse a module and return its first `assignment` node, depth-first."""
    code_bytes = code.encode()
    stack = [_PARSER.parse(code_bytes).root_node]
    while stack:
        node = stack.pop(0)
        if node.type == "assignment":
            return node, code_bytes
        stack.extend(node.named_children)
    msg = "no assignment node in source"
    raise AssertionError(msg)


def test_enclosing_class_fqn_inside_a_method() -> None:
    node, code = _first_assignment_in("class A:\n    def __init__(self):\n        self.x = 1\n")
    assert enclosing_class_fqn(node, code, "pkg/mod.py", None) == "pkg.mod.A"


def test_enclosing_class_fqn_in_a_class_body() -> None:
    node, code = _first_assignment_in("class A:\n    x: int\n")
    assert enclosing_class_fqn(node, code, "pkg/mod.py", None) == "pkg.mod.A"


def test_enclosing_class_fqn_is_none_at_module_level() -> None:
    node, code = _first_assignment_in("x = 1\n")
    assert enclosing_class_fqn(node, code, "pkg/mod.py", None) is None


def test_enclosing_class_fqn_picks_the_nearest_class() -> None:
    node, code = _first_assignment_in("class Outer:\n    class Inner:\n        x: int\n")
    assert enclosing_class_fqn(node, code, "pkg/mod.py", None) == "pkg.mod.Outer.Inner"


def test_enclosing_class_fqn_distinguishes_a_class_local_to_a_method() -> None:
    """A class defined inside a method must not collide with a same-named sibling.

    `class Outer.Local` (CLASS node id pkg.mod.Outer.Local) and the `Local`
    class nested inside `Outer.m` (CLASS node id pkg.mod.Outer.m.Local) must
    resolve to two different FQNs: the key `self_types` records must equal
    the CLASS node's own id, or lookups by node id would never find it.
    """
    code = (
        "class Outer:\n"
        "    class Local:\n"
        "        a: int\n"
        "    def m(self):\n"
        "        class Local:\n"
        "            b: int\n"
    )
    code_bytes = code.encode()
    stack = [_PARSER.parse(code_bytes).root_node]
    assignments = []
    while stack:
        node = stack.pop(0)
        if node.type == "assignment":
            assignments.append(node)
        stack.extend(node.named_children)
    assert len(assignments) == 2
    outer_local_a, inner_local_b = assignments
    assert (
        enclosing_class_fqn(outer_local_a, code_bytes, "pkg/mod.py", None) == "pkg.mod.Outer.Local"
    )
    assert (
        enclosing_class_fqn(inner_local_b, code_bytes, "pkg/mod.py", None)
        == "pkg.mod.Outer.m.Local"
    )


def test_prefixed_string_annotation_is_read_from_its_content() -> None:
    """A string prefix must not reach the re-parse: r"Node" is Node, not r"Node.

    Quote-stripping cannot see a prefix, so the body is taken from the literal's
    `string_content` child instead. Before that, the prefixed forms only resolved
    because the parser recovered from a malformed re-parse — right answer, wrong
    reason, and one grammar change away from silently returning nothing.
    """
    for prefix in ("", "r", "u", "f", "R", "U"):
        node, code = _annotation(f'{prefix}"Node"')
        assert collect_type_names(node, code) == ["Node"], prefix


def test_triple_quoted_string_annotation() -> None:
    """A triple-quoted forward reference resolves like any other."""
    node, code = _annotation('"""Node"""')
    assert collect_type_names(node, code) == ["Node"]


def test_qualified_annotated_drops_non_call_metadata() -> None:
    """`typing.Annotated[X, thing]` names X only, whatever shape the metadata takes.

    A `call` is skipped by the node-type rule, but a bare identifier or a string
    is not — only the Annotated argument slice keeps them out. Pinned because the
    slice depends on tree-sitter listing subscript arguments as siblings; were a
    grammar change to wrap them in one node, the slice would quietly become a
    no-op and metadata would start being collected as types.
    """
    for meta in ("Depends(get_db)", "some_validator", '"a doc"', "Gt"):
        node, code = _annotation(f"typing.Annotated[Session, {meta}]")
        assert collect_type_names(node, code) == ["Session"], meta


def test_bare_annotated_drops_non_call_metadata() -> None:
    """The unqualified `Annotated[X, thing]` spelling parses differently — same result."""
    for meta in ("Depends(get_db)", "some_validator", '"a doc"', "Gt"):
        node, code = _annotation(f"Annotated[Session, {meta}]")
        assert collect_type_names(node, code) == ["Session"], meta


def test_non_wrapper_generic_keeps_every_argument() -> None:
    """The Annotated slice must not leak into ordinary generics."""
    node, code = _annotation("dict[str, Edge]")
    assert collect_type_names(node, code) == ["dict", "str", "Edge"]
    node, code = _annotation("typing.Dict[str, Edge]")
    assert collect_type_names(node, code) == ["typing.Dict", "str", "Edge"]
