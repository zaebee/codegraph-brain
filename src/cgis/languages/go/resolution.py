"""The Go pack's resolution rules (spec: Go pilot, resolution).

An import path names a package directory of a module, which only this pack can
map to node ids: strip the module path (or a replace), then the ingest root's
offset under the module, then find the package directory — whose scope is the
package clause's at the module root (7b review I1, I2). The same mapping serves
the import edge and every reference through it (`reconciles_references`).
"""

from typing import cast

from cgis.core.models import Node, NodeType
from cgis.core.resolution import AFTER_IMPORT_MAP, ResolutionRules, ResolutionView
from cgis.languages.go._syntax import (
    ANY_OF,
    BUILTIN_PACKAGE,
    BUILTINS,
    CALL_RESULT,
    ELEM,
    FIELD,
    GLOBAL,
    TAKE,
)
from cgis.languages.go.dispatch import interface_dispatch
from cgis.languages.go.index import GoIndexFacts
from cgis.languages.go.naming import GO_LANG
from cgis.languages.go.profile import INVOKERS


def _strip(fqn: str, prefix: str) -> str | None:
    """`fqn` with `prefix` and its dot removed; '' when equal; None when not under it."""
    if fqn == prefix:
        return ""
    return fqn[len(prefix) + 1 :] if prefix and fqn.startswith(f"{prefix}.") else None


def _ingest_relative(fqn: str, facts: GoIndexFacts) -> str | None:
    """The part of a dotted import path below the ingest root; None for anyone else's."""
    rest = _strip(fqn, facts.module) if facts.module else None
    if rest is not None:
        return rest if not facts.offset else _strip(rest, facts.offset)
    for module, directory in facts.replaces.items():
        rest = _strip(fqn, module)
        if rest is not None:
            return ".".join(p for p in (directory, rest) if p)
    return None


def reconcile_import(fqn: str, _source_file: str | None, view: ResolutionView) -> str | None:
    """A package path -> its first production FILE (H5); a path to a symbol -> the symbol.

    A Go package has no node of its own, so an import edge names a
    representative file: the first by id, a production file when there is one.
    """
    facts = view.facts.get(GO_LANG)
    if facts is None:
        return None
    go = cast("GoIndexFacts", facts)
    relative = _ingest_relative(fqn, go)
    if relative is None:
        return None
    parts = relative.split(".") if relative else []
    for cut in range(len(parts), -1, -1):
        scope = go.package_dirs.get(".".join(parts[:cut]))
        if scope is None:
            continue
        if cut == len(parts):
            files = go.package_files.get(scope, ())
            return files[0] if files else None
        symbol = ".".join([scope, *parts[cut:]])
        return symbol if symbol in view.nodes else None
    return None


def builtin_call(name: str, file_path: str, view: ResolutionView) -> str | None:
    """`len`, `append`, `string(...)`: the language's own, before any global symbol (I4).

    Before the global index, which would otherwise hand a Go `make` to a `make`
    of another package. A function of the caller's own package with the builtin's
    name shadows it, as in Go: left to the same-scope tiebreak. Checked for
    shadowing, it is a resolved call and scores 1.0, unlike a Python or TS builtin,
    which is matched by name alone and keeps the unresolved 0.8 (#674 review).
    The target is qualified, `builtin.len`, after Go's own pseudo-package, as a TS
    builtin is `js_builtins.<name>`: a bare `len` would be shared with an unresolved
    call to a local closure named `len`, and the two edges would merge (#661).
    A module with a real top-level package `builtin` (GOROOT's own stubs) would see
    these calls join it; accepted, as TS accepts it for `js_builtins`.
    """
    if name not in BUILTINS:
        return None
    facts = view.facts.get(GO_LANG)
    scope = cast("GoIndexFacts", facts).file_scopes.get(file_path, "") if facts else ""
    return None if f"{scope}.{name}" in view.nodes else f"{BUILTIN_PACKAGE}.{name}"


#: How deep one marker may expand: they nest only as far as the extractor bounds
#: them, but a package variable initialised from another one could loop.
_MAX_EXPANSION = 32


def call_result(type_fqn: str, view: ResolutionView) -> str:
    """The type a Go marker names, or the marker itself when it names none (#608, #643).

    The extractor records what its file alone cannot know as a marker:
    - `go-returns:<i>:<callee>`: result i of a call. The callee is an FQN, or
      `<marker>|<method>` for a method on another marker's type; its node's
      `returns` says.
    - `go-take:<elem|key>:<value>`: an element received, indexed or ranged from a
      value whose type is a `go-elem:` container.
    - `go-field:<f1.f2>:<value>`: a field chain, through each struct's `self_types`.
    - `go-global:<id>`: a package variable, through its node's `var_type`.
    - `go-any:<t1>;<t2>`: the one type every binding agrees on.
    Anything else passes through untouched. A marker left unexpanded resolves to
    nothing.
    """
    return _expand(type_fqn, view, _MAX_EXPANSION, [])


def type_origin(type_fqn: str, view: ResolutionView) -> str | None:
    """The file whose declaration gave the type a marker expands to; None for no marker.

    That file qualified the type with its own imports, so its imports, not the
    caller's, say whether the type is a library's (#688).
    """
    files: list[str] = []
    expanded = _expand(type_fqn, view, _MAX_EXPANSION, files)
    if not files or expanded == type_fqn or expanded.startswith("go-"):
        return None  # nothing expanded, or only as far as another marker
    return files[-1]


#: Files of the nodes an expansion read, in order: the last one gave the result.
_Trace = list[str]


def _expand(type_fqn: str, view: ResolutionView, budget: int, trace: _Trace) -> str:
    if budget <= 0 or not type_fqn.startswith("go-"):
        return type_fqn
    kind, _, rest = type_fqn.partition(":")
    expand = _EXPANDERS.get(f"{kind}:")
    return expand(type_fqn, rest, view, budget - 1, trace) if expand else type_fqn


def _returned(marker: str, rest: str, view: ResolutionView, budget: int, trace: _Trace) -> str:
    position, _, callee = rest.partition(":")
    if "|" in callee:
        held, _, method = callee.rpartition("|")
        callee = f"{_expand(held, view, budget, trace)}.{method}"
    node = _node_of(callee, view)
    if node is not None and node.type is NodeType.CLASS:
        # `T(x)` names a type, so it is a conversion, not a call: its value is a T
        # (#670, #689). The name came from the caller's file, which stays the origin.
        return node.id
    returns: list[str] = node.metadata.get("returns") or [] if node else []
    index = int(position) if position.isdigit() else len(returns)
    result = next(iter(returns[index : index + 1]), "")  # past the end: no such result
    if not result:
        return marker
    _read(node, trace)
    return _expand(result, view, budget, trace)


def _read(node: Node | None, trace: _Trace) -> None:
    """Record that `node`'s metadata gave the type expanded so far."""
    if node is not None:
        trace.append(node.file_path)


def _taken(marker: str, rest: str, view: ResolutionView, budget: int, trace: _Trace) -> str:
    taken, _, source = rest.partition(":")
    container = _expand(source, view, budget, trace)
    if not container.startswith(ELEM):
        return marker
    kind, _, element = container.removeprefix(ELEM).partition(":")
    value, _, key = element.partition(",")
    if kind == "chan" or (taken == "elem" and kind == "seq"):
        return element
    if kind == "map":
        return (value if taken == "elem" else key) or marker
    return marker


def _fielded(marker: str, rest: str, view: ResolutionView, budget: int, trace: _Trace) -> str:
    fields, _, source = rest.partition(":")
    current = _expand(source, view, budget, trace)
    for name in fields.split("."):
        declared = _field_type(current, name, view, trace)
        if declared is None:
            return marker
        current = _expand(declared, view, budget, trace)
    return current


def _field_type(type_fqn: str, name: str, view: ResolutionView, trace: _Trace) -> str | None:
    """A struct's field `name`, its own or promoted, shallowest first; None when none."""
    level, seen = [type_fqn], {type_fqn}
    while level:
        found, below = _level_fields(level, name, view)
        if found:
            if len(found) != 1:
                return None
            ((declared, file_path),) = found.items()
            trace.append(file_path)
            return declared
        below = [t for t in below if t not in seen]
        seen.update(below)
        level = below
    return None


def _level_fields(
    level: list[str], name: str, view: ResolutionView
) -> tuple[dict[str, str], list[str]]:
    """The types of field `name` on one depth's structs, each with the file declaring
    it, and the structs they embed."""
    found: dict[str, str] = {}
    below: list[str] = []
    for current in level:
        node = _node_of(current, view)
        fields: dict[str, str] = node.metadata.get("self_types") or {} if node else {}
        if node is not None and name in fields:
            found.setdefault(fields[name], node.file_path)
        # An embedded field is named after its type (#656).
        below += [t for f, t in fields.items() if t.rsplit(".", 1)[-1] == f]
    return found, below


def _global(marker: str, rest: str, view: ResolutionView, budget: int, trace: _Trace) -> str:
    node = _node_of(rest, view)
    declared = node.metadata.get("var_type") if node is not None else None
    if not isinstance(declared, str):
        return marker
    _read(node, trace)
    return _expand(declared, view, budget, trace)


def _node_of(fqn: str, view: ResolutionView) -> Node | None:
    """The node an FQN names, mapped through the module like any Go reference."""
    node_id = fqn if fqn in view.nodes else reconcile_import(fqn, None, view)
    return view.nodes.get(node_id) if node_id else None


def _agreed(type_fqn: str, rest: str, view: ResolutionView, budget: int, trace: _Trace) -> str:
    """The one type every binding of a `go-any:` name resolves to; else the marker itself."""
    resolved = {_expand(t, view, budget, trace) for t in rest.split(";")}
    (only,) = resolved if len(resolved) == 1 else (type_fqn,)
    return type_fqn if only.startswith("go-") else only


_EXPANDERS = {
    CALL_RESULT: _returned,
    TAKE: _taken,
    FIELD: _fielded,
    GLOBAL: _global,
    ANY_OF: _agreed,
}


RULES = ResolutionRules(
    type_normalizers=(call_result,),
    type_origin=type_origin,
    steps=((AFTER_IMPORT_MAP, builtin_call),),
    reconcile_import=reconcile_import,
    reconciles_references=True,
    dispatch=interface_dispatch,
    shallowest_member=True,
    locals_shadow=True,
    invokers=INVOKERS,
)
