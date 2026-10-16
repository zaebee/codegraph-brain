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

import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
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
    CONTAINERS,
    ELEM,
    FIELD,
    GLOBAL,
    PREDECLARED,
    TAKE,
    UNNAMED_IMPORTS,
    arity,
    base_type,
    call_name,
    container_type,
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
        elif declaration.type == "var_declaration":
            _variables(ctx, declaration, nodes, edges)


def _variables(ctx: _File, declaration: TSNode, nodes: list[Node], edges: list[Edge]) -> None:
    """A VARIABLE per package-level var, typed as a local would be (#643 B6).

    A call through it (`DefaultRegisterer.Register()`) is typed by the function's
    `go-global:` binding of the name, which the resolver expands to this type.
    """
    lists = [c for c in declaration.children if c.type == "var_spec_list"]
    candidates = [*declaration.children, *(c for group in lists for c in group.children)]
    for spec in (c for c in candidates if c.type == "var_spec"):
        bindings = Bindings()
        _bindings_of(ctx, spec, bindings)
        settled = _settled(bindings)
        for name in declared_names(spec):
            if name == "_":
                continue
            fqn = f"{ctx.scope}.{name}"
            metadata: dict[str, object] | None = (
                {"var_type": settled[name]} if name in settled else None
            )
            nodes.append(_node(ctx, fqn, name, NodeType.VARIABLE, spec, metadata))
            edges.append(_edge(ctx.node_id, fqn, EdgeType.CONTAINS))


def _types(ctx: _File, declaration: TSNode, nodes: list[Node], edges: list[Edge]) -> None:
    """A CLASS per `type_spec`, with its embeddings and interface methods."""
    for spec in declaration.children:
        if spec.type != "type_spec":
            continue
        name = text(spec.child_by_field_name("name"))
        fqn = f"{ctx.scope}.{name}"
        body = spec.child_by_field_name("type")
        is_interface = body is not None and body.type == "interface_type"
        params = _type_parameters(spec)
        # An interface is satisfied, not inherited: the dispatch rule needs to know which
        # types are interfaces (#609). A struct carries its fields instead.
        metadata: dict[str, object] | None = (
            {"interface": True} if is_interface else _struct_metadata(ctx, body, params)
        )
        nodes.append(_node(ctx, fqn, name, NodeType.CLASS, spec, metadata))
        edges.append(_edge(ctx.node_id, fqn, EdgeType.CONTAINS))
        edges.extend(
            _edge(fqn, f"{_RAW_CLASS}{reference}", EdgeType.EXTENDS)
            for reference in _embedded(body)
        )
        if is_interface and body is not None:
            for elem in body.children:
                if elem.type == "method_elem":
                    _declare(ctx, fqn, elem, nodes, edges, params)


def _struct_metadata(
    ctx: _File, body: TSNode | None, params: frozenset[str] = frozenset()
) -> dict[str, object] | None:
    """A struct's field types, and every field's name: a field shadows a promoted method.

    Go keeps fields and methods in one namespace per depth, so `Log func()` beside
    an embedded type with a `Log` method is what `s.Log()` calls (#656 review).
    """
    types = {
        field: fqn
        for field, fqn in _field_types(ctx, body).items()
        if not _names_parameter(fqn, ctx.scope, params)
    }
    names = sorted(_field_names(body))
    metadata: dict[str, object] = {"self_types": types} if types else {}
    if names:
        metadata["fields"] = names
    return metadata or None


def _field_names(body: TSNode | None) -> set[str]:
    """Every field a struct declares, an embedded one by its type's name."""
    fields = _child(body, "field_declaration_list") if body and body.type == "struct_type" else None
    names: set[str] = set()
    for field in fields.children if fields else []:
        if field.type == "field_declaration":
            reference = type_reference(field.child_by_field_name("type"))
            names.update(declared_names(field) or [reference.rsplit(".", 1)[-1]])
    names.discard("")
    return names


def _field_types(ctx: _File, body: TSNode | None) -> dict[str, str]:
    """A struct's fields of a named type: its `self_types`, as Python's (#608).

    An embedded field is named after its type (`*pkg.Metric` is `Metric`), so
    `s.Metric.Write()` reads it like any other field (#643 A4).
    """
    fields = _child(body, "field_declaration_list") if body and body.type == "struct_type" else None
    types: dict[str, str] = {}
    for field in fields.children if fields else []:
        type_node = field.child_by_field_name("type")
        fqn = type_fqn(type_node, ctx.scope, ctx.import_map) or container_type(
            type_node, ctx.scope, ctx.import_map
        )
        if field.type != "field_declaration" or not fqn:
            continue
        reference = type_reference(type_node)
        names = declared_names(field) or [reference.rsplit(".", 1)[-1]]
        types.update(dict.fromkeys(names, fqn))
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
    ctx: _File,
    type_fqn_: str,
    elem: TSNode,
    nodes: list[Node],
    edges: list[Edge],
    params: frozenset[str] = frozenset(),
) -> None:
    """An interface method: a METHOD under the interface, with its DECLARES edge."""
    name = text(elem.child_by_field_name("name"))
    fqn = f"{type_fqn_}.{name}"
    metadata: dict[str, object] = {"arity": arity(elem)}
    returns = _unparameterised(result_types(elem, ctx.scope, ctx.import_map), ctx.scope, params)
    if returns:
        metadata["returns"] = returns  # `s, _ := fsm.Snapshot()` types `s` (#643 B7)
    nodes.append(_node(ctx, fqn, name, NodeType.METHOD, elem, metadata))
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


#: Type names share the scope stack with values, under this prefix (no identifier has it).
_TYPE_KEY = "type "


def _type_parameters(node: TSNode) -> frozenset[str]:
    """`[A any, B comparable]` of a function or type; a method's from its receiver `Box[T]`."""
    names: set[str] = set()
    declared = node.child_by_field_name("type_parameters")
    for parameter in declared.named_children if declared else []:
        names.update(text(n) for n in parameter.children_by_field_name("name"))
    receiver = _child(node.child_by_field_name("receiver"), "parameter_declaration")
    generic = receiver.child_by_field_name("type") if receiver else None
    while generic is not None and generic.type == "pointer_type":
        generic = generic.named_children[0] if generic.named_children else None
    arguments = generic.child_by_field_name("type_arguments") if generic else None
    for argument in arguments.named_children if arguments else []:
        names.add(text(argument))
    return frozenset(names)


def _names_parameter(fqn: str, scope: str, params: frozenset[str]) -> bool:
    """Whether a type or marker names one of `params` as if it were the package's type."""
    names = {f"{scope}.{p}" for p in params}
    return any(part in names for part in re.split(r"[:,]", fqn))


def _unparameterised(types: list[str], scope: str, params: frozenset[str]) -> list[str]:
    """Results with any type parameter blanked: `Idt[A](x A) A` returns no nameable type."""
    return ["" if _names_parameter(t, scope, params) else t for t in types]


class Bindings:
    """A function's local names: every type each is bound to, and the one in scope now.

    `every` is what `local_types` settles (#608 review). The scope stack follows
    Go's blocks as the walk goes, so a call's receiver takes the binding it can
    see: a range variable does not type the import it shadows after the loop, and
    each `select` case keeps its own `c` (#643 B8).
    """

    def __init__(self, initial: Mapping[str, str] | None = None) -> None:
        """Bindings with the receiver (or nothing) bound in the function's own scope."""
        self.every: dict[str, list[str]] = {}
        #: Call position -> (receiver name, its type there), for `site_types`.
        self.sites: dict[str, tuple[str, str]] = {}
        self._scopes: list[dict[str, str]] = [{}]
        for name, fqn in (initial or {}).items():
            self.bind(name, fqn)

    def __contains__(self, name: object) -> bool:
        """Whether `name` is a local in scope here."""
        return any(name in scope for scope in self._scopes)

    def bind(self, name: str, fqn: str) -> None:
        """Bind `name` in the innermost scope; the blank identifier binds nothing."""
        if name == "_":
            return
        types = self.every.setdefault(name, [])
        if fqn not in types:
            types.append(fqn)
        self._scopes[-1][name] = fqn

    def note(self, name: str, fqn: str) -> None:
        """Record a type for `name` without declaring it: a package variable's use."""
        types = self.every.setdefault(name, [])
        if fqn not in types:
            types.append(fqn)

    def shadowed(self, name: str) -> bool:
        """Whether a nested block rebinds `name` over the function's own binding of it."""
        return any(name in scope for scope in self._scopes[1:])

    def bind_type(self, name: str, fqn: str) -> None:
        """Declare a type name in the innermost scope: an alias's target, or "" for none."""
        self._scopes[-1][f"{_TYPE_KEY}{name}"] = fqn

    def type_here(self, name: str) -> str | None:
        """A local type name's target ("" when it has none to name); None when not local."""
        return self.here(f"{_TYPE_KEY}{name}")

    def here(self, name: str) -> str | None:
        """The type `name` is bound to in the innermost scope that binds it."""
        return next((s[name] for s in reversed(self._scopes) if name in s), None)

    @contextmanager
    def scope(self) -> Iterator[None]:
        """A nested block for the walk's duration."""
        self._scopes.append({})
        try:
            yield
        finally:
            self._scopes.pop()


#: Nodes that open a block in Go: a name bound inside is gone after them.
_SCOPES = frozenset(
    {
        "block",
        "func_literal",
        "for_statement",
        "if_statement",
        "expression_switch_statement",
        "type_switch_statement",
        "select_statement",
        "expression_case",
        "default_case",
        "type_case",
        "communication_case",
    }
)


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
    bindings = Bindings({receiver: owner} if receiver and owner else None)
    params = _type_parameters(node)
    for param in params:
        bindings.bind_type(param, "")  # `[A any]`: never the package's `A` (#662 review)
    _parameters(ctx, node.child_by_field_name("parameters"), bindings)
    _parameters(ctx, node.child_by_field_name("result"), bindings)  # named results bind too
    body = node.child_by_field_name("body")
    if body is not None:
        _body(ctx, body, fqn, receiver, bindings, edges)
    settled = _settled(bindings)
    metadata: dict[str, object] = {"local_types": settled} if settled else {}
    # Where a call's receiver is not what `local_types` says: another block's binding
    # of the name, or none — an import, a package variable ("" reads as not local).
    sites = {
        position: held
        for position, (name, held) in bindings.sites.items()
        if settled.get(name, "") != held
    }
    if sites:
        metadata["site_types"] = sites
    returns = _unparameterised(result_types(node, ctx.scope, ctx.import_map), ctx.scope, params)
    if returns:
        metadata["returns"] = returns
    if node_type == NodeType.METHOD:
        metadata["arity"] = arity(node)  # what an interface it satisfies must match (#646)
    nodes.append(_node(ctx, fqn, text(node.child_by_field_name("name")), node_type, node, metadata))


def _parameters(ctx: _File, parameters: TSNode | None, bindings: Bindings) -> None:
    for declaration in parameters.children if parameters else []:
        if declaration.type == "parameter_declaration":
            _record(
                ctx, bindings, declared_names(declaration), declaration.child_by_field_name("type")
            )
        elif declaration.type == "variadic_parameter_declaration":
            # `opts ...Option` is a []Option inside the function (#643 B).
            element = _typed(ctx, declaration.child_by_field_name("type"), bindings)
            for name in declared_names(declaration):
                bindings.bind(name, f"{ELEM}seq:{element}" if element else _UNKNOWN)


def _settled(bindings: Bindings) -> dict[str, str]:
    """One type per name: its only binding, or `go-any:` of all when every one is known.

    A name with any binding of no nameable type records nothing — unresolved
    rather than lent a type from a sibling block.
    """
    return {
        name: types[0] if len(types) == 1 else ANY_OF + ";".join(types)
        for name, types in bindings.every.items()
        if _UNKNOWN not in types
    }


def _record(ctx: _File, bindings: Bindings, names: list[str], type_node: TSNode | None) -> None:
    """Bind each of `names` to the named type `type_node` gives, or to unknown.

    Of the predeclared types only `error` binds, by name: the one with a method a
    call can reach (`err.Error()`); `int` or `string` would type nothing.
    """
    predeclared = "error" if text(base_type(type_node)) == "error" else _UNKNOWN
    fqn = _typed(ctx, type_node, bindings) or predeclared
    for name in names:
        bindings.bind(name, fqn)


def _typed(ctx: _File, node: TSNode | None, bindings: Bindings) -> str | None:
    """A type written in a function: its FQN or container marker, after local type names.

    A type parameter or a type defined in the function names nothing; a local
    alias (`type A = B`) names its target (#662 review).
    """
    named = base_type(node)
    if named is not None and named.type == "type_identifier":
        local = bindings.type_here(text(named))
        if local is not None:
            return local or None
    container = node
    while container is not None and container.type == "pointer_type":
        container = container.named_children[0] if container.named_children else None
    if container is not None and container.type in CONTAINERS:
        # The element goes through the same local names: `xs []A` with `[A any]` is not
        # a slice of the package's `A` (#662 review).
        kind, field = CONTAINERS[container.type]
        element = _element(ctx, container.child_by_field_name(field), bindings)
        if kind == "map":
            key = _element(ctx, container.child_by_field_name("key"), bindings)
            return f"{ELEM}map:{element},{key}" if element or key else None
        return f"{ELEM}{kind}:{element}" if element else None
    return type_fqn(node, ctx.scope, ctx.import_map)


def _element(ctx: _File, node: TSNode | None, bindings: Bindings) -> str:
    """A container's element or key as a named type; "" for none or another container."""
    typed = _typed(ctx, node, bindings)
    return typed if typed and not typed.startswith("go-") else ""


def _held(bindings: Bindings, name: str) -> str | None:
    """The known type `name` holds at this point of the walk, or None."""
    held = bindings.here(name)
    return held if held != _UNKNOWN else None


def _body(
    ctx: _File,
    node: TSNode,
    source: str,
    receiver: str | None,
    bindings: Bindings,
    edges: list[Edge],
) -> None:
    """CALLS edges and local variable types, through nested blocks and closures."""
    if node.type in _SCOPES:
        with bindings.scope():
            _visit(ctx, node, source, receiver, bindings, edges)
    else:
        _visit(ctx, node, source, receiver, bindings, edges)


def _visit(
    ctx: _File,
    node: TSNode,
    source: str,
    receiver: str | None,
    bindings: Bindings,
    edges: list[Edge],
) -> None:
    """One node of `_body`: its call or its bindings, and its children.

    A declaration binds after its own expressions are walked: in `q := q.Get()`
    the call is still the import's (#660 review). A closure's parameters and a
    type case's alias bind first, as they scope over the node's own children.
    """
    declares = node.type in _DECLARATIONS
    if node.type == "call_expression":
        function = node.child_by_field_name("function")
        # A block that rebinds the receiver's name hides it: `x.Run()` there is the local's.
        own = receiver if receiver and not bindings.shadowed(receiver) else None
        name = call_name(function, own) if function is not None else ""
        if name and function is not None:
            edge = _call(source, name, node)
            edges.append(edge)
            _site(ctx, function, own, bindings, _site_key(edge))
            _global_head(ctx, function, bindings)
        elif function is not None and function.type == "selector_expression":
            _unnamed_receiver(ctx, source, function, node, bindings, edges)
    elif not declares:
        _bindings_of(ctx, node, bindings)
    for child in node.children:
        _body(ctx, child, source, receiver, bindings, edges)
    if declares:
        _bindings_of(ctx, node, bindings)


#: Statements whose names scope over what follows them, not over their own value.
_DECLARATIONS = frozenset(
    {"short_var_declaration", "var_spec", "range_clause", "receive_statement"}
)


def _site(
    ctx: _File, function: TSNode, receiver: str | None, bindings: Bindings, position: str
) -> None:
    """What the call's first name is at this point: a local's type, or not a local.

    A bare `f()` counts too: after a block that bound a local `f`, it is the
    package's `f` again (#660 review).
    """
    chain = selector_chain(function)
    if not chain or (len(chain) > 1 and chain[0] == receiver):
        return
    head = chain[0]
    if head in bindings:
        held = bindings.here(head) or _UNKNOWN  # a local, typed or not: never the import
    elif len(chain) == 1 or head in ctx.import_map or head in PREDECLARED:
        held = ""  # not a local here: `error.Error(e)` is the predeclared type's
    else:
        held = f"{GLOBAL}{ctx.scope}.{head}"
    bindings.sites[position] = (head, held)


def _bindings_of(ctx: _File, node: TSNode, bindings: Bindings) -> None:
    """The names `node` binds, if it is a statement that binds any."""
    binder = _BINDERS.get(node.type)
    if binder is not None:
        binder(ctx, node, bindings)


def _var_spec(ctx: _File, node: TSNode, bindings: Bindings) -> None:
    """`var x T` binds T; `var x = v` what v gives."""
    declared = node.child_by_field_name("type")
    if declared is not None:
        _record(ctx, bindings, declared_names(node), declared)
    else:
        _assigned(ctx, declared_names(node), node.child_by_field_name("value"), bindings)


def _short_var(ctx: _File, node: TSNode, bindings: Bindings) -> None:
    """`a, b := v, w`."""
    _assigned(
        ctx, _names(node.child_by_field_name("left")), node.child_by_field_name("right"), bindings
    )


def _received(ctx: _File, node: TSNode, bindings: Bindings) -> None:
    """`case v, ok := <-ch:` (its right is the receive itself)."""
    right = node.child_by_field_name("right")
    received = _value_type(ctx, right, bindings) if right is not None else None
    for name, typed in zip(
        _names(node.child_by_field_name("left")), (received, None), strict=False
    ):
        bindings.bind(name, typed or _UNKNOWN)


def _closure(ctx: _File, node: TSNode, bindings: Bindings) -> None:
    """A closure's parameters and named results bind their declared types, so
    `t.Run(..., func(t *testing.T) {...})` keeps `t`."""
    _parameters(ctx, node.child_by_field_name("parameters"), bindings)
    _parameters(ctx, node.child_by_field_name("result"), bindings)


def _default_case(ctx: _File, node: TSNode, bindings: Bindings) -> None:
    """A type switch's `default:` binds its alias; an expression switch's binds nothing."""
    if node.parent is not None and node.parent.type == "type_switch_statement":
        _case_alias(ctx, node, bindings)


def _names(left: TSNode | None) -> list[str]:
    """The identifiers of a left-hand side."""
    return [text(t) for t in left.named_children] if left else []


def _assigned(ctx: _File, names: list[str], values: TSNode | None, bindings: Bindings) -> None:
    """`x := T{}` gives T; `x := f()` gives f's result, `a, err := f()` each its own (#608).

    `v, ok := <-ch`, `m[k]` or `w.(T)`: the first name takes the value; the rest,
    past the values, a bool, which types nothing.
    """
    right = values.named_children if values is not None else []
    if len(right) == 1 and right[0].type == "call_expression" and len(names) > 1:
        for index, name in enumerate(names):
            _hold_result(ctx, bindings, name, right[0], index)
        return
    for position, name in enumerate(names):
        _bind_value(ctx, bindings, name, right[position] if position < len(right) else None)


def _bind_value(ctx: _File, bindings: Bindings, name: str, value: TSNode | None) -> None:
    """Bind `name` to one value: a call's first result, or what the expression gives."""
    if value is not None and value.type == "call_expression":
        _hold_result(ctx, bindings, name, value, 0)
        return
    typed = _value_type(ctx, value, bindings) if value is not None else None
    bindings.bind(name, typed or _UNKNOWN)


def _ranged(ctx: _File, clause: TSNode, bindings: Bindings) -> None:
    """`for k, v := range x`: `v` is x's element; `k` is, only when x is a channel (#643 B2)."""
    left = clause.child_by_field_name("left")
    names = [text(t) for t in left.named_children] if left else []
    right = clause.child_by_field_name("right")
    source = _operand(ctx, right, bindings) if right is not None else None
    for name, taken in zip(names, ("key", "elem"), strict=False):
        bindings.bind(name, _take(taken, source) or _UNKNOWN)


def _case_alias(ctx: _File, case: TSNode, bindings: Bindings) -> None:
    """`switch v := x.(type)`: in a one-type case `v` is that type, else x's own (#643 B4)."""
    switch = case.parent
    alias = switch.child_by_field_name("alias") if switch is not None else None
    names = [text(n) for n in alias.named_children] if alias else []
    if not names or switch is None:
        return
    types = case.children_by_field_name("type")
    if len(types) == 1:
        typed = _typed(ctx, types[0], bindings)
    else:  # several types, or `default`: the value as the switch saw it
        value = switch.child_by_field_name("value")
        typed = _operand(ctx, value, bindings) if value is not None else None
    bindings.bind(names[0], typed or _UNKNOWN)


def _local_types(ctx: _File, node: TSNode, bindings: Bindings) -> None:
    """`type A = B` in a function names B there; `type A struct{...}` names nothing we know."""
    for spec in node.children:
        name = text(spec.child_by_field_name("name"))
        if spec.type == "type_alias" and name:
            bindings.bind_type(name, _typed(ctx, spec.child_by_field_name("type"), bindings) or "")
        elif spec.type == "type_spec" and name:
            bindings.bind_type(name, "")


_BINDERS: dict[str, Callable[[_File, TSNode, Bindings], None]] = {
    "var_spec": _var_spec,
    "short_var_declaration": _short_var,
    "range_clause": _ranged,
    "receive_statement": _received,
    "type_case": _case_alias,
    "default_case": _default_case,
    "func_literal": _closure,
    "type_declaration": _local_types,
}
"""Statements that bind names, by node type."""


def _value_type(ctx: _File, value: TSNode, bindings: Bindings) -> str | None:
    """The type a value expression has, as a type or a marker; None when it has no name.

    A receive and an index take the operand's element; an assertion names its
    type; a literal its own; a name or a field chain what it holds (#643 B).
    """
    if value.type == "parenthesized_expression" and value.named_children:
        return _value_type(ctx, value.named_children[0], bindings)
    operand = value.child_by_field_name("operand")
    if value.type == "unary_expression" and text(value.child_by_field_name("operator")) == "<-":
        return _take("elem", _operand(ctx, operand, bindings)) if operand else None
    if value.type == "index_expression":
        return _take("elem", _operand(ctx, operand, bindings)) if operand else None
    if value.type == "type_assertion_expression":
        asserted = value.child_by_field_name("type")
        return _typed(ctx, asserted, bindings)
    literal = literal_type(value)
    if literal is not None:
        return _typed(ctx, literal, bindings)
    if value.type in ("identifier", "selector_expression"):
        return _operand(ctx, value, bindings)
    return None


def _operand(ctx: _File, node: TSNode, bindings: Bindings) -> str | None:
    """What a name, a field chain or a call holds, as a type or a marker; else None."""
    if node.type == "parenthesized_expression" and node.named_children:
        return _operand(ctx, node.named_children[0], bindings)
    operand = node.child_by_field_name("operand")
    if node.type == "unary_expression" and text(node.child_by_field_name("operator")) == "*":
        return _operand(ctx, operand, bindings) if operand else None  # pointers are transparent
    if node.type == "call_expression":
        callee = _callee(ctx, node.child_by_field_name("function"), bindings)
        return _bounded(f"{CALL_RESULT}0:{callee}") if callee else None
    chain = selector_chain(node)
    if not chain:
        return None
    head, fields = _holder(ctx, chain, bindings)
    if head is None:
        return None
    return _bounded(f"{FIELD}{'.'.join(fields)}:{head}") if fields else head


def _holder(ctx: _File, chain: list[str], bindings: Bindings) -> tuple[str | None, list[str]]:
    """The type a chain's head holds, and the fields after it.

    A local holds its binding; an import names a package, whose next segment is
    its variable; any other name is a variable of this package (`go-global:`).
    """
    head, rest = chain[0], chain[1:]
    if head in bindings:
        return _held(bindings, head), rest
    if head in ctx.import_map:
        return (f"{GLOBAL}{ctx.import_map[head]}.{rest[0]}", rest[1:]) if rest else (None, [])
    if head in BUILTINS:
        return None, []
    return f"{GLOBAL}{ctx.scope}.{head}", rest


def _take(taken: str, source: str | None) -> str | None:
    """`go-take:<taken>:<source>`, or None when the source has no type."""
    return _bounded(f"{TAKE}{taken}:{source}") if source else None


def _bounded(marker: str) -> str | None:
    """The marker, unless it nests past `_MAX_CHAIN`: expanding one never recurses deep."""
    return marker if marker.count("go-") <= _MAX_CHAIN else None


def _global_head(ctx: _File, function: TSNode | None, bindings: Bindings) -> None:
    """`V.M()` with `V` neither local nor an import: `V` is a package variable (#643 B6)."""
    chain = selector_chain(function) if function is not None else []
    head = chain[0] if len(chain) > 1 else ""
    if head and head not in bindings and head not in ctx.import_map and head not in BUILTINS:
        bindings.note(head, f"{GLOBAL}{ctx.scope}.{head}")


def _hold_result(ctx: _File, bindings: Bindings, name: str, call: TSNode, index: int) -> None:
    """Bind `name` to result `index` of `call`, or to unknown when the callee has no name.

    `make(chan T)` and `new(T)` name their type in the first argument (#643 B).
    """
    function = call.child_by_field_name("function")
    builtin = text(function) if function is not None else ""
    if builtin in ("make", "new") and builtin not in bindings:
        arguments = call.child_by_field_name("arguments")
        first = arguments.named_children[0] if arguments and arguments.named_children else None
        typed = _type_argument(ctx, first, bindings) if index == 0 else None
        bindings.bind(name, typed or _UNKNOWN)
        return
    callee = _callee(ctx, function, bindings)
    bindings.bind(name, f"{CALL_RESULT}{index}:{callee}" if callee else _UNKNOWN)


def _type_argument(ctx: _File, node: TSNode | None, bindings: Bindings) -> str | None:
    """A type written where an expression may stand: `make(chan *T)`, `new(T)`, `new(pkg.T)`."""
    if node is None:
        return None
    if node.type == "identifier":
        name = text(node)
        local = bindings.type_here(name)
        if local is not None:
            return local or None
        return None if name in PREDECLARED else f"{ctx.scope}.{name}"
    chain = selector_chain(node)
    if len(chain) == 2 and chain[0] in ctx.import_map:
        return f"{ctx.import_map[chain[0]]}.{chain[1]}"
    return _typed(ctx, node, bindings)


def _callee(ctx: _File, function: TSNode | None, bindings: Bindings) -> str | None:
    """What a call names, qualified: `f`, `pkg.F`, or `M` on a value of known type.

    The value may be a variable, a field chain on one (`r.snapshots.Create`) or a
    package variable; a method on a marker is written `<marker>|<method>`.
    """
    chain = selector_chain(function) if function is not None else []
    if len(chain) == 1:
        # A local func value (`newT := newB; newT()`) is not the package's `newT` (#662 review).
        return None if chain[0] in BUILTINS or chain[0] in bindings else f"{ctx.scope}.{chain[0]}"
    if not chain:
        return _chained_callee(ctx, function, bindings) if function is not None else None
    *path, method = chain
    if len(path) == 1 and path[0] not in bindings and path[0] in ctx.import_map:
        # A local shadows an import, as the resolver reads it (M1).
        return f"{ctx.import_map[path[0]]}.{method}"
    holder, fields = _holder(ctx, path, bindings)
    held = _bounded(f"{FIELD}{'.'.join(fields)}:{holder}") if holder and fields else holder
    if held is None or held.count("|") >= _MAX_CHAIN:
        return None
    return f"{held}|{method}" if held.startswith("go-") else f"{held}.{method}"


def _chained_callee(ctx: _File, function: TSNode, bindings: Bindings) -> str | None:
    """`M` on a value no name holds — `a.B().M`, `x.(T).M`: `<its type>|M` (#643 C)."""
    if function.type != "selector_expression":
        return None
    root, names = _selector_parts(function)
    *fields, method = names
    held = _root_type(ctx, root, bindings)
    if held and fields:
        held = _bounded(f"{FIELD}{'.'.join(fields)}:{held}")
    if held is None or held.count("|") >= _MAX_CHAIN:
        return None
    return f"{held}|{method}" if held.startswith("go-") else f"{held}.{method}"


def _unnamed_receiver(
    ctx: _File,
    source: str,
    function: TSNode,
    call: TSNode,
    bindings: Bindings,
    edges: list[Edge],
) -> None:
    """A call on a value no name holds gets an edge too, named after the value (#643 C).

    `c.Leader().AddVoter()` was no edge at all, so no name reached the coverage
    count. It is now `Leader().AddVoter`, its receiver typed at its position in
    `site_types` — or untyped ("?"), which the resolver leaves unresolved rather
    than matching `AddVoter` against the package's symbols.
    """
    root, names = _selector_parts(function)
    label = _label(root)
    edge = _call(source, ".".join([label, *names]), call)
    edges.append(edge)
    held = _root_type(ctx, root, bindings)
    bindings.sites[_site_key(edge)] = (label, held or _UNKNOWN)


def _selector_parts(function: TSNode) -> tuple[TSNode, list[str]]:
    """`a.B().c.M` as (the `a.B()` node, ['c', 'M']): the value and the names after it."""
    names: list[str] = []
    node = function
    while node.type == "selector_expression":
        names.insert(0, text(node.child_by_field_name("field")))
        operand = node.child_by_field_name("operand")
        if operand is None:
            break
        node = operand
    return node, names


def _root_type(ctx: _File, root: TSNode, bindings: Bindings) -> str | None:
    """The type of the value a chain starts from: a call's result, an assertion, ..."""
    while root.type == "parenthesized_expression" and root.named_children:
        root = root.named_children[0]
    if root.type == "call_expression":
        return _operand(ctx, root, bindings)
    return _value_type(ctx, root, bindings)


def _label(node: TSNode) -> str:
    """A short, dot-free name for a value no name holds: `Leader()`, `(T)`, `m[]`."""
    if node.type == "identifier":
        return text(node)
    chain = selector_chain(node)
    if chain:
        return chain[-1]
    operand = node.child_by_field_name("operand")
    if node.type == "call_expression":
        function = node.child_by_field_name("function")
        return f"{_label(function) if function is not None else '?'}()"
    if node.type == "type_assertion_expression":
        reference = type_reference(node.child_by_field_name("type"))
        return f"({reference.rsplit('.', 1)[-1] or '?'})"
    if node.type == "index_expression":
        return f"{_label(operand) if operand is not None else '?'}[]"
    if node.type == "parenthesized_expression" and node.named_children:
        return f"({_label(node.named_children[0])})"
    if node.type == "unary_expression" and operand is not None:
        return f"{text(node.child_by_field_name('operator'))}{_label(operand)}"
    if node.type == "selector_expression":  # a field of a call's result: `a.B().f`
        # `:` keeps each link distinct and dot-free: `a.Get().Get()` is `Get():Get()`,
        # so two links named alike never share an edge id (#662 review).
        field = text(node.child_by_field_name("field"))
        return f"{_label(operand)}:{field}" if operand is not None else field
    return "?"


def _site_key(edge: Edge) -> str:
    """`<raw name>@<row>:<col>`: a chain's calls share a start, never a name there too."""
    return edge.id.partition(_RAW_CALL)[2]


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
