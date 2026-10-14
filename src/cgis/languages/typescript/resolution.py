"""The TypeScript pack's resolution rules (spec: Resolution; language-packs step 4).

Moved from `cgis.resolver.indices` with their docstrings: what a TypeScript import
means once tsconfig aliases, workspace packages and declaration files are read.
"""

import os
import posixpath
from collections.abc import Mapping
from typing import cast

from cgis.core.models import Node
from cgis.core.resolution import AFTER_IMPORT_MAP, ResolutionRules, ResolutionView
from cgis.languages.typescript.builtins import js_builtin_target
from cgis.languages.typescript.index import (
    _ALIAS_WILDCARD,
    PathAlias,
    TypeScriptIndexFacts,
    package_namespace,
)

#: What a declaration file's module FQN ends in: `Calendar.d.ts` -> `Calendar.d` (#507).
_DECLARATION_SUFFIX = ".d"


def _resolve_typescript_import(
    fqn: str,
    nodes: Mapping[str, Node],
    packages: Mapping[str, str],
    aliased: tuple[str, ...] = (),
) -> str | None:
    """Reconcile a TypeScript import against the node ids, or None to leave it alone.

    Each candidate is tried as written and then as a declaration file:
    `packages/types/Calendar.d.ts` is the module `packages.types.Calendar.d`,
    while code imports it with no `.d` (#507). That is the order TypeScript
    resolves in, so an implementation beside its declaration wins. The node ids
    are not changed instead: that would rename every declaration node in
    existing graphs and give `foo.ts` and `foo.d.ts` one id.

    The candidates are the target itself — a relative import, already made
    absolute by the extractor — then what a tsconfig `paths` alias of the
    importing project rewrites it to (#508), and then, for a workspace package,
    the module it names (#504). Aliases come before packages because TypeScript
    consults `paths` before `node_modules`, where a workspace package is linked.
    """
    for candidate in (fqn, *aliased, *_workspace_candidates(fqn, packages)):
        for spelled in (candidate, f"{candidate}{_DECLARATION_SUFFIX}"):
            if spelled in nodes:
                return spelled
    return None


def _workspace_candidates(fqn: str, packages: Mapping[str, str]) -> tuple[str, ...]:
    """The module FQNs a TypeScript import of a workspace package may name.

    `@calcom.lib.hooks.useLocale` becomes `packages.lib.hooks.useLocale` when the
    pipeline found a `package.json` named `@calcom/lib` in `packages/lib`; the
    package root is tried before `src/`, a common layout for a package's sources.

    The name must match on a segment boundary, so `@x/a` never claims
    `@x/a-b`. The first — longest — package that matches decides: falling back
    to a shorter one would read `a.b.util` as package `a` when package `a.b`
    was meant. No candidates for a name the map does not hold.
    """
    for name, directory in packages.items():
        if fqn != name and not fqn.startswith(name + "."):
            continue
        subpath = fqn[len(name) + 1 :]
        return tuple(
            f"{base}.{subpath}" if subpath else base for base in (directory, f"{directory}.src")
        )
    return ()


def _alias_candidates(
    fqn: str, source_file: str, scopes: Mapping[str, tuple[PathAlias, ...]]
) -> tuple[str, ...]:
    """The module FQNs the importing project's tsconfig `paths` rewrite `fqn` to.

    The project is the nearest directory above `source_file` that has a
    tsconfig; a nearer one without aliases shadows a farther one with them, as
    it does for the compiler. Within it the first pattern that matches decides,
    exact before wildcard and longer prefix before shorter — and, as in
    TypeScript, a pattern whose targets miss does not fall back to another.
    """
    if not scopes:
        return ()
    directory = posixpath.dirname(source_file.replace(os.sep, "/"))
    while directory not in scopes:
        if not directory:
            return ()
        directory = posixpath.dirname(directory)
    for alias in scopes[directory]:
        if not alias.wildcard:
            if fqn == alias.prefix:
                return alias.targets
            continue
        if fqn.startswith(alias.prefix):
            # The remainder may be empty, as in TypeScript (`jquery*` matches
            # `jquery`): the target is then its directory, so `lib.*` -> `lib`.
            rest = fqn[len(alias.prefix) :]
            substituted = (
                t.replace(_ALIAS_WILDCARD, rest).removesuffix(".") for t in alias.targets
            )
            return tuple(candidate for candidate in substituted if candidate)
    return ()


def reconcile_import(fqn: str, source_file: str | None, view: ResolutionView) -> str | None:
    """A TS import's node through tsconfig aliases, then workspace packages (#504, #507, #508)."""
    facts = cast("TypeScriptIndexFacts", view.facts["typescript"])
    aliased = _alias_candidates(fqn, source_file or "", facts.path_aliases)
    return _resolve_typescript_import(fqn, view.nodes, facts.workspace_packages, aliased)


def package_call(name: str, file_path: str, view: ResolutionView) -> str | None:
    """A TypeScript call on a name imported from a package: `useState` -> `react.useState`.

    Only when the binding lands in a package (#556). A binding into one of our
    own modules through an alias or workspace name is left to the lookups that
    already resolve those, so internal resolution is unchanged.

    Bindings are per file, not per scope: a parameter that shadows an imported
    name (`function f(map) { map() }` beside `import { map } from "lodash"`) is
    read as the import. Accepted — shadowing an import is rare, and scope
    tracking is a separate piece of work.
    """
    facts = cast("TypeScriptIndexFacts", view.facts["typescript"])
    bindings = facts.file_bare_bindings.get(file_path)
    if not bindings:
        return None
    head, _, rest = name.partition(".")
    bound = bindings.get(head)
    if bound is None:
        return None
    candidate = f"{bound}.{rest}" if rest else bound
    if package_namespace(candidate, file_path, facts) is None:
        return None
    return candidate


#: What a call rewritten to the JS runtime scores: the unresolved value a Python
#: builtin gets, not the 1.0 of a resolved call (#111).
_FALLBACK_CONFIDENCE = 0.8


def fallback(name: str, file_path: str, view: ResolutionView) -> tuple[str, float] | None:
    """A call on a JS runtime global the file does not shadow: `js_builtins.<name>` (#111)."""
    facts = cast("TypeScriptIndexFacts", view.facts["typescript"])
    shadowed = facts.file_shadowed_globals.get(file_path, frozenset())
    target = js_builtin_target(name, shadowed)
    return None if target is None else (target, _FALLBACK_CONFIDENCE)


RULES = ResolutionRules(
    steps=((AFTER_IMPORT_MAP, package_call),),
    fallback=fallback,
    reconcile_import=reconcile_import,
)
