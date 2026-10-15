"""The Go pack's resolution rules (spec: Go pilot, resolution).

An import path names a package directory of a module, which only this pack can
map to node ids: strip the module path (or a replace), then the ingest root's
offset under the module, then find the package directory — whose scope is the
package clause's at the module root (7b review I1, I2). The same mapping serves
the import edge and every reference through it (`reconciles_references`).
"""

from typing import cast

from cgis.core.resolution import AFTER_IMPORT_MAP, ResolutionRules, ResolutionView
from cgis.languages.go._syntax import BUILTINS
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


RULES = ResolutionRules(
    steps=((AFTER_IMPORT_MAP, builtin_call),),
    reconcile_import=reconcile_import,
    reconciles_references=True,
)
