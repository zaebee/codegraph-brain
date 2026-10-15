"""The Go pack's resolution rules (spec: Go pilot, resolution).

Package-qualified calls need no rule of their own: the import map gives
`example.com.app.internal.server.Run` and the module path's layout prefixes
take it to `internal.server.Run` (H4). What is left is the import edge itself.
"""

from typing import cast

from cgis.core.resolution import ResolutionRules, ResolutionView
from cgis.languages.go.index import GoIndexFacts
from cgis.languages.go.naming import GO_LANG


def _package_scope(fqn: str, facts: GoIndexFacts) -> str | None:
    """The directory scope an import of ours names: module path or a replace stripped."""
    for module, directory in ((facts.module, ""), *facts.replaces.items()):
        if module and (fqn == module or fqn.startswith(f"{module}.")):
            rest = fqn[len(module) + 1 :]
            return ".".join(p for p in (directory, rest) if p)
    return None


def reconcile_import(fqn: str, _source_file: str | None, view: ResolutionView) -> str | None:
    """A package import -> the package's first FILE node (H5); None for anyone else's.

    A Go package has no node of its own, so the edge names a representative
    file: the lexicographically first, which is deterministic and keeps the
    package-to-package topology. Approximate, and documented as such.
    """
    facts = view.facts.get(GO_LANG)
    if facts is None:
        return None
    go = cast("GoIndexFacts", facts)
    scope = _package_scope(fqn, go)
    files = go.package_files.get(scope) if scope is not None else None
    return files[0] if files else None


RULES = ResolutionRules(reconcile_import=reconcile_import)
