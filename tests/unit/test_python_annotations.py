"""Tests for the annotation type-name collector (D9)."""

import tree_sitter_python as tspython
from tree_sitter import Language, Parser
from tree_sitter import Node as BaseNode

from cgis.extractors._python_annotations import collect_type_names

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
