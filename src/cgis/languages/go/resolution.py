"""The Go pack's resolution rules (spec: Go pilot, resolution).

An import path names a package directory of a module, which only this pack can
map to node ids: strip the module path (or a replace), then the ingest root's
offset under the module, then find the package directory — whose scope is the
package clause's at the module root (7b review I1, I2). The same mapping serves
the import edge and every reference through it (`reconciles_references`).
"""

from typing import cast

from cgis.core.resolution import AFTER_IMPORT_MAP, ResolutionRules, ResolutionView
from cgis.languages.go._syntax import ANY_OF, BUILTINS, CALL_RESULT
from cgis.languages.go.dispatch import interface_dispatch
from cgis.languages.go.index import GoIndexFacts
from cgis.languages.go.naming import GO_LANG


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

    Before the global index, which would otherwise hand a Go `make` to a Python
    `make` in the same repository. A function of the caller's own package with
    the builtin's name shadows it, as in Go: left to the same-scope tiebreak.
    """
    if name not in BUILTINS:
        return None
    facts = view.facts.get(GO_LANG)
    scope = cast("GoIndexFacts", facts).file_scopes.get(file_path, "") if facts else ""
    return None if f"{scope}.{name}" in view.nodes else name


def call_result(type_fqn: str, view: ResolutionView) -> str:
    """The type a call's result marker names: the callee's recorded result (#608).

    The extractor records `x := f()` as `go-returns:<i>:<callee>`, the callee an
    FQN or `<marker>|<method>` for a method on another call's result, since its
    file alone cannot know what `f` returns. Here the callee's node — mapped
    through the module like any Go reference — says, in its `returns`. Anything
    else passes through untouched; an unknown callee leaves the marker, which
    resolves to nothing.
    """
    if type_fqn.startswith(ANY_OF):
        return _agreed(type_fqn, view)
    if not type_fqn.startswith(CALL_RESULT):
        return type_fqn
    position, _, callee = type_fqn.removeprefix(CALL_RESULT).partition(":")
    if "|" in callee:
        held, _, method = callee.rpartition("|")
        callee = f"{call_result(held, view)}.{method}"
    node_id = callee if callee in view.nodes else reconcile_import(callee, None, view)
    node = view.nodes.get(node_id) if node_id else None
    returns: list[str] = node.metadata.get("returns") or [] if node else []
    index = int(position) if position.isdigit() else len(returns)
    result = next(iter(returns[index : index + 1]), "")  # past the end: no such result
    return result or type_fqn


def _agreed(type_fqn: str, view: ResolutionView) -> str:
    """The one type every binding of a `go-any:` name resolves to; else the marker itself."""
    resolved = {call_result(t, view) for t in type_fqn.removeprefix(ANY_OF).split(";")}
    (only,) = resolved if len(resolved) == 1 else (type_fqn,)
    return type_fqn if only.startswith((CALL_RESULT, ANY_OF)) else only


RULES = ResolutionRules(
    type_normalizers=(call_result,),
    steps=((AFTER_IMPORT_MAP, builtin_call),),
    reconcile_import=reconcile_import,
    reconciles_references=True,
    dispatch=interface_dispatch,
    shallowest_member=True,
)
