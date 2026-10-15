"""The Go extractor: tree-sitter-go AST -> Nodes and Edges (step 7b).

A package is a directory, so definitions are named `<package scope>.<Name>` and
shared by every file of the package, while each file's FILE node is
`<scope>.<stem>_go` and declares the scope in its metadata (step 7, H1/H2). A
method hangs under its receiver type wherever that type is declared; a call
through the receiver is emitted as `raw_call:self.<name>` (H6).

Module-level handlers over explicit `nodes`/`edges` lists, as the TypeScript
extractor does: a walker class grows into a god object, and its
`self.edges.append` calls into unresolvable `self.*` placeholders.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import override

import tree_sitter_go as tsgo
from tree_sitter import Language, Parser
from tree_sitter import Node as TSNode

from cgis.core.extractor import BaseExtractor
from cgis.core.models import Edge, EdgeType, Node, NodeNamespace, NodeType
from cgis.languages.go._syntax import (
    UNNAMED_IMPORTS,
    base_type,
    call_name,
    declared_names,
    dotted,
    imports,
    literal_type,
    text,
    type_fqn,
    type_reference,
)
from cgis.languages.go.naming import GO_LANG, file_node_id, is_test, scope_of

_RAW_CALL = "raw_call:"
_RAW_CLASS = "raw_class:"


@dataclass(frozen=True)
class _File:
    """What every handler reads about the file being walked."""

    path: str
    node_id: str
    scope: str
    is_test: bool
    import_map: Mapping[str, str]
    own_types: frozenset[str]


class GoExtractor(BaseExtractor):
    """Extracts a Go file's package, declarations, imports and calls."""

    def __init__(self, source_roots: list[str] | None = None) -> None:
        """Initialise the tree-sitter-go parser."""
        super().__init__(source_roots=source_roots)
        self._parser = Parser(Language(tsgo.language()))

    @override
    def parse(self, code: str, file_path: str) -> tuple[list[Node], list[Edge]]:
        """Extract the nodes and edges of one Go file."""
        root = self._parser.parse(code.encode("utf-8")).root_node
        source_root = self._pick_source_root(file_path)
        package = text(_child(_child(root, "package_clause"), "package_identifier"))
        ctx = _File(
            path=file_path,
            node_id=file_node_id(file_path, source_root),
            scope=_package_scope(scope_of(file_path, source_root), package),
            is_test=is_test(file_path),
            import_map={
                name: dotted(path) for name, path in imports(root) if name not in UNNAMED_IMPORTS
            },
            own_types=frozenset(_declared_types(root)),
        )
        nodes: list[Node] = []
        edges: list[Edge] = []
        _file(ctx, root, nodes, edges)
        return nodes, edges


def _package_scope(directory: str, package: str) -> str:
    """Where a file's definitions live: its directory, or the clause's name at the root.

    An external test package (`package server_test` beside `package server`) is
    another package in the same directory, so it gets `<directory>_test` — one
    scope would let its `Helper` replace the production one (7b review I3).
    """
    if not directory:
        return package  # A root file's definitions take the package clause's name.
    return f"{directory}_test" if package.endswith("_test") else directory


def _child(node: TSNode | None, kind: str) -> TSNode | None:
    """The first child of `kind`, or None."""
    return next((c for c in node.children if c.type == kind), None) if node else None


def _declared_types(root: TSNode) -> list[str]:
    """The names of the types this file declares (aliases are not types of their own)."""
    return [
        text(spec.child_by_field_name("name"))
        for declaration in root.children
        if declaration.type == "type_declaration"
        for spec in declaration.children
        if spec.type == "type_spec"
    ]


def _file(ctx: _File, root: TSNode, nodes: list[Node], edges: list[Edge]) -> None:
    """The FILE node, the imports, then every top-level declaration."""
    name = ctx.path.replace("\\", "/").rsplit("/", 1)[-1]
    metadata: dict[str, object] = {
        "scope": ctx.scope,
        "import_map": dict(ctx.import_map),
        # As written: a dotted path cannot say whether its first segment was a domain.
        "imports": sorted({path for _, path in imports(root)}),
    }
    nodes.append(_node(ctx, ctx.node_id, name, NodeType.FILE, root, metadata))
    for _, path in imports(root):
        target = dotted(path)
        edges.append(
            _edge(ctx.node_id, target, EdgeType.IMPORTS, f"{ctx.node_id}->import:{target}")
        )
    for declaration in root.children:
        if declaration.type == "type_declaration":
            _types(ctx, declaration, nodes, edges)
        elif declaration.type == "function_declaration":
            _function(ctx, declaration, nodes, edges)
        elif declaration.type == "method_declaration":
            _method(ctx, declaration, nodes, edges)


def _types(ctx: _File, declaration: TSNode, nodes: list[Node], edges: list[Edge]) -> None:
    """A CLASS per `type_spec`, with its embeddings and interface methods."""
    for spec in declaration.children:
        if spec.type != "type_spec":
            continue
        name = text(spec.child_by_field_name("name"))
        fqn = f"{ctx.scope}.{name}"
        nodes.append(_node(ctx, fqn, name, NodeType.CLASS, spec))
        edges.append(_edge(ctx.node_id, fqn, EdgeType.CONTAINS))
        body = spec.child_by_field_name("type")
        edges.extend(
            _edge(fqn, f"{_RAW_CLASS}{reference}", EdgeType.EXTENDS)
            for reference in _embedded(body)
        )
        if body is not None and body.type == "interface_type":
            for elem in body.children:
                if elem.type == "method_elem":
                    _declare(ctx, fqn, elem, nodes, edges)


def _embedded(body: TSNode | None) -> list[str]:
    """The types a struct or interface embeds, as written: `Base`, `pkg.Other`."""
    if body is None:
        return []
    if body.type == "struct_type":
        fields = _child(body, "field_declaration_list")
        types = [
            f.child_by_field_name("type")
            for f in (fields.children if fields else [])
            if f.type == "field_declaration" and f.child_by_field_name("name") is None
        ]
    elif body.type == "interface_type":
        types = [c.named_children[0] for c in body.children if c.type == "type_elem"]
    else:
        return []
    return [r for r in map(type_reference, types) if r]


def _declare(
    ctx: _File, type_fqn_: str, elem: TSNode, nodes: list[Node], edges: list[Edge]
) -> None:
    """An interface method: a METHOD under the interface, with its DECLARES edge."""
    name = text(elem.child_by_field_name("name"))
    fqn = f"{type_fqn_}.{name}"
    nodes.append(_node(ctx, fqn, name, NodeType.METHOD, elem))
    edges.append(_edge(type_fqn_, fqn, EdgeType.DECLARES))


def _function(ctx: _File, node: TSNode, nodes: list[Node], edges: list[Edge]) -> None:
    """A FUNCTION in the package scope; `init` once per file, as Go allows several."""
    name = text(node.child_by_field_name("name"))
    if name == "init":
        fqn = f"{ctx.scope}.init_{ctx.node_id.rsplit('.', 1)[-1]}"
    else:
        fqn = f"{ctx.scope}.{name}"
    edges.append(_edge(ctx.node_id, fqn, EdgeType.CONTAINS))
    _callable(ctx, fqn, NodeType.FUNCTION, node, None, nodes, edges)


def _method(ctx: _File, node: TSNode, nodes: list[Node], edges: list[Edge]) -> None:
    """A method under its receiver's type, wherever that type is declared."""
    receiver = _child(node.child_by_field_name("receiver"), "parameter_declaration")
    if receiver is None:
        return
    type_name = text(base_type(receiver.child_by_field_name("type")))
    owner = f"{ctx.scope}.{type_name}"
    fqn = f"{owner}.{text(node.child_by_field_name('name'))}"
    edges.append(_edge(owner, fqn, EdgeType.DECLARES))
    if type_name not in ctx.own_types:
        # The type lives in another file: this file still holds the method.
        edges.append(_edge(ctx.node_id, fqn, EdgeType.CONTAINS))
    names = declared_names(receiver)
    _callable(ctx, fqn, NodeType.METHOD, node, names[0] if names else None, nodes, edges)


def _callable(
    ctx: _File,
    fqn: str,
    node_type: NodeType,
    node: TSNode,
    receiver: str | None,
    nodes: list[Node],
    edges: list[Edge],
) -> None:
    """The node, with its parameter and local types, and the CALLS edges of its body."""
    types: dict[str, str] = {}
    parameters = node.child_by_field_name("parameters")
    for declaration in parameters.children if parameters else []:
        if declaration.type == "parameter_declaration":
            _record(
                ctx, types, declared_names(declaration), declaration.child_by_field_name("type")
            )
    body = node.child_by_field_name("body")
    if body is not None:
        _body(ctx, body, fqn, receiver, types, edges)
    metadata: dict[str, object] = {"local_types": types} if types else {}
    nodes.append(_node(ctx, fqn, text(node.child_by_field_name("name")), node_type, node, metadata))


def _record(ctx: _File, types: dict[str, str], names: list[str], type_node: TSNode | None) -> None:
    """Note each of `names` as having the type `type_node` names, when it is a named type."""
    fqn = type_fqn(type_node, ctx.scope, ctx.import_map)
    if fqn:
        types.update(dict.fromkeys(names, fqn))


def _body(
    ctx: _File,
    node: TSNode,
    source: str,
    receiver: str | None,
    types: dict[str, str],
    edges: list[Edge],
) -> None:
    """CALLS edges and local variable types, through nested blocks and closures."""
    if node.type == "call_expression":
        function = node.child_by_field_name("function")
        name = call_name(function, receiver) if function is not None else ""
        if name:
            edges.append(_call(source, name, node))
    elif node.type == "var_spec":
        _record(ctx, types, declared_names(node), node.child_by_field_name("type"))
    elif node.type == "short_var_declaration":
        _short_var(ctx, node, types)
    for child in node.children:
        _body(ctx, child, source, receiver, types, edges)


def _short_var(ctx: _File, node: TSNode, types: dict[str, str]) -> None:
    """`x := T{}` / `x := &T{}`: x has type T. Anything else says nothing."""
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    if left is None or right is None:
        return
    for target, value in zip(left.named_children, right.named_children, strict=False):
        _record(ctx, types, [text(target)], literal_type(value))


def _call(source: str, name: str, node: TSNode) -> Edge:
    """An unresolved CALLS edge, its id carrying the call's position."""
    target = f"{_RAW_CALL}{name}"
    position = f"{node.start_point[0]}:{node.start_point[1]}"
    return Edge(
        id=f"{source}->{target}@{position}",
        source=source,
        target=target,
        type=EdgeType.CALLS,
        confidence=0.1,
    )


def _node(
    ctx: _File,
    fqn: str,
    name: str,
    node_type: NodeType,
    ts_node: TSNode,
    metadata: dict[str, object] | None = None,
) -> Node:
    """A Go node: language, namespace and the file's own test flag set explicitly."""
    return Node(
        id=fqn,
        name=name,
        type=node_type,
        file_path=ctx.path,
        start_line=ts_node.start_point[0] + 1,
        end_line=ts_node.end_point[0] + 1,
        language=GO_LANG,
        namespace=NodeNamespace.INTERNAL,
        is_test=ctx.is_test,
        metadata=metadata or {},
    )


def _edge(source: str, target: str, edge_type: EdgeType, edge_id: str | None = None) -> Edge:
    """An edge with a deterministic id."""
    return Edge(id=edge_id or f"{source}->{target}", source=source, target=target, type=edge_type)
