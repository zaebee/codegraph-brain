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
    ANY_OF,
    BUILTINS,
    CALL_RESULT,
    UNNAMED_IMPORTS,
    base_type,
    call_name,
    declared_names,
    dotted,
    imports,
    literal_type,
    result_types,
    selector_chain,
    text,
    type_fqn,
    type_reference,
)
from cgis.languages.go.naming import GO_LANG, file_node_id, is_test, scope_of

_RAW_CALL = "raw_call:"
#: A binding whose type cannot be named (`x := m[k]`, a range variable): one per
#: name makes it untyped, since one dict serves every block (#608 review).
_UNKNOWN = "?"
#: Hops of `x := y.M()` a call-result marker follows; past it the name stays
#: untyped, so markers stay short and expanding one never recurses deep (I2).
_MAX_CHAIN = 8
_RAW_CLASS = "raw_class:"


@dataclass(frozen=True)
class _File:
    """What every handler reads about the file being walked."""

    path: str
    node_id: str
    #: The package directory, dotted, relative to the ingest root; empty at the root.
    directory: str
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
        directory = scope_of(file_path, source_root)
        ctx = _File(
            path=file_path,
            node_id=file_node_id(file_path, source_root),
            directory=directory,
            scope=_package_scope(directory, package),
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
        # What an import path names: the scope is the clause's at the root (7b review I1).
        "package_dir": ctx.directory,
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
        body = spec.child_by_field_name("type")
        fields = _field_types(ctx, body)
        nodes.append(
            _node(ctx, fqn, name, NodeType.CLASS, spec, {"self_types": fields} if fields else None)
        )
        edges.append(_edge(ctx.node_id, fqn, EdgeType.CONTAINS))
        edges.extend(
            _edge(fqn, f"{_RAW_CLASS}{reference}", EdgeType.EXTENDS)
            for reference in _embedded(body)
        )
        if body is not None and body.type == "interface_type":
            for elem in body.children:
                if elem.type == "method_elem":
                    _declare(ctx, fqn, elem, nodes, edges)


def _field_types(ctx: _File, body: TSNode | None) -> dict[str, str]:
    """A struct's named fields of a named type: its `self_types`, as Python's (#608)."""
    fields = _child(body, "field_declaration_list") if body and body.type == "struct_type" else None
    types: dict[str, str] = {}
    for field in fields.children if fields else []:
        fqn = type_fqn(field.child_by_field_name("type"), ctx.scope, ctx.import_map)
        if field.type == "field_declaration" and fqn:
            types.update(dict.fromkeys(declared_names(field), fqn))
    return types


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
        types = [
            c.named_children[0] for c in body.children if c.type == "type_elem" and c.named_children
        ]
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
    if not type_name:
        return  # incomplete code: a method with no type to hang under
    owner = f"{ctx.scope}.{type_name}"
    fqn = f"{owner}.{text(node.child_by_field_name('name'))}"
    edges.append(_edge(owner, fqn, EdgeType.DECLARES))
    if type_name not in ctx.own_types:
        # The type lives in another file: this file still holds the method.
        edges.append(_edge(ctx.node_id, fqn, EdgeType.CONTAINS))
    names = declared_names(receiver)
    receiver_name = names[0] if names else None
    _callable(ctx, fqn, NodeType.METHOD, node, receiver_name, nodes, edges, owner)


Bindings = dict[str, list[str]]
"""Every type each local name is bound to in one function, in order (#608 review)."""


def _callable(
    ctx: _File,
    fqn: str,
    node_type: NodeType,
    node: TSNode,
    receiver: str | None,
    nodes: list[Node],
    edges: list[Edge],
    owner: str | None = None,
) -> None:
    """The node, with its parameter, local and result types, and its body's CALLS edges."""
    # The receiver's type is its method's owner: `s.Parser()` qualifies like `x.M()`.
    bindings: Bindings = {receiver: [owner]} if receiver and owner else {}
    _parameters(ctx, node.child_by_field_name("parameters"), bindings)
    body = node.child_by_field_name("body")
    if body is not None:
        _body(ctx, body, fqn, receiver, bindings, edges)
    settled = _settled(bindings)
    metadata: dict[str, object] = {"local_types": settled} if settled else {}
    returns = result_types(node, ctx.scope, ctx.import_map)
    if returns:
        metadata["returns"] = returns
    nodes.append(_node(ctx, fqn, text(node.child_by_field_name("name")), node_type, node, metadata))


def _parameters(ctx: _File, parameters: TSNode | None, bindings: Bindings) -> None:
    for declaration in parameters.children if parameters else []:
        if declaration.type == "parameter_declaration":
            _record(
                ctx, bindings, declared_names(declaration), declaration.child_by_field_name("type")
            )


def _settled(bindings: Bindings) -> dict[str, str]:
    """One type per name: its only binding, or `go-any:` of all when every one is known.

    A name with any binding of no nameable type records nothing — unresolved
    rather than lent a type from a sibling block.
    """
    return {
        name: types[0] if len(types) == 1 else ANY_OF + ";".join(types)
        for name, types in bindings.items()
        if _UNKNOWN not in types
    }


def _record(ctx: _File, bindings: Bindings, names: list[str], type_node: TSNode | None) -> None:
    """Bind each of `names` to the named type `type_node` gives, or to unknown."""
    fqn = type_fqn(type_node, ctx.scope, ctx.import_map) or _UNKNOWN
    for name in names:
        _bind(bindings, name, fqn)


def _bind(bindings: Bindings, name: str, fqn: str) -> None:
    """Add a binding of `name`; the blank identifier binds nothing."""
    if name != "_" and fqn not in bindings.setdefault(name, []):
        bindings[name].append(fqn)


def _held(bindings: Bindings, name: str) -> str | None:
    """The one known type `name` holds while walking, or None when it has several or none."""
    types = bindings.get(name, [])
    return types[0] if len(types) == 1 and types[0] != _UNKNOWN else None


def _unknown(bindings: Bindings, names: TSNode | None) -> None:
    """Bind every identifier under `names` (a range's or a type switch's left side) to unknown."""
    for name in names.named_children if names else []:
        if name.type == "identifier":
            _bind(bindings, text(name), _UNKNOWN)


def _body(
    ctx: _File,
    node: TSNode,
    source: str,
    receiver: str | None,
    bindings: Bindings,
    edges: list[Edge],
) -> None:
    """CALLS edges and local variable types, through nested blocks and closures."""
    if node.type == "call_expression":
        function = node.child_by_field_name("function")
        name = call_name(function, receiver) if function is not None else ""
        if name:
            edges.append(_call(source, name, node))
    elif node.type == "var_spec" and node.child_by_field_name("type") is not None:
        _record(ctx, bindings, declared_names(node), node.child_by_field_name("type"))
    elif node.type == "var_spec":
        _assigned(ctx, declared_names(node), node.child_by_field_name("value"), bindings)
    elif node.type == "short_var_declaration":
        left = node.child_by_field_name("left")
        names = [text(t) for t in left.named_children] if left else []
        _assigned(ctx, names, node.child_by_field_name("right"), bindings)
    elif node.type == "range_clause":
        _unknown(bindings, node.child_by_field_name("left"))
    elif node.type == "type_switch_statement":
        _unknown(bindings, node.child_by_field_name("alias"))
    elif node.type == "func_literal":
        # A closure's parameters are bindings like any other: `t.Run(..., func(t
        # *testing.T) {...})` binds `t` to the same type, and so keeps it.
        _parameters(ctx, node.child_by_field_name("parameters"), bindings)
    for child in node.children:
        _body(ctx, child, source, receiver, bindings, edges)


def _assigned(ctx: _File, names: list[str], values: TSNode | None, bindings: Bindings) -> None:
    """`x := T{}` gives T; `x := f()` gives f's result, `a, err := f()` each its own (#608)."""
    right = values.named_children if values is not None else []
    if len(right) == 1 and right[0].type == "call_expression" and len(names) > 1:
        for index, name in enumerate(names):
            _hold_result(ctx, bindings, name, right[0], index)
        return
    for position, name in enumerate(names):
        value = right[position] if position < len(right) else None
        if value is not None and value.type == "call_expression":
            _hold_result(ctx, bindings, name, value, 0)
        else:
            _record(ctx, bindings, [name], literal_type(value) if value is not None else None)


def _hold_result(ctx: _File, bindings: Bindings, name: str, call: TSNode, index: int) -> None:
    """Bind `name` to result `index` of `call`, or to unknown when the callee has no name."""
    callee = _callee(ctx, call.child_by_field_name("function"), bindings)
    _bind(bindings, name, f"{CALL_RESULT}{index}:{callee}" if callee else _UNKNOWN)


def _callee(ctx: _File, function: TSNode | None, bindings: Bindings) -> str | None:
    """What a call names, qualified: `f`, `pkg.F`, or `M` on a variable of known type."""
    chain = selector_chain(function) if function is not None else []
    if len(chain) == 1:
        return None if chain[0] in BUILTINS else f"{ctx.scope}.{chain[0]}"
    if len(chain) != 2:
        return None
    head, method = chain
    if head not in bindings:  # a local shadows an import, as the resolver reads it (M1)
        return f"{ctx.import_map[head]}.{method}" if head in ctx.import_map else None
    held = _held(bindings, head)
    if held is None or held.count("|") >= _MAX_CHAIN:
        return None
    return f"{held}|{method}" if held.startswith(CALL_RESULT) else f"{held}.{method}"


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
