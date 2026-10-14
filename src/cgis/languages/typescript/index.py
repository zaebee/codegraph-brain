"""The TypeScript pack's index facts and classification (spec: Index and classification).

`package_namespace` moved from `cgis.resolver.indices.bare_import_namespace` in
language-packs step 3.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import NamedTuple, cast

from cgis.core.index import ClassifyContext
from cgis.core.js_globals import (
    JS_BUILTINS_ROOT,
    NODE_BUILTIN_MODULES,
    NODE_SCHEME,
    specifier_fqn,
)
from cgis.core.models import Node, NodeNamespace, NodeType

#: The wildcard of a tsconfig `paths` alias, kept in its dotted spelling (#508).
_ALIAS_WILDCARD = "*"


class PathAlias(NamedTuple):
    """One tsconfig `paths` entry, dotted: `@lib.*` -> (`apps.web.lib.*`,) (#508)."""

    #: The pattern without its trailing `*`: what a matching import starts with.
    prefix: str
    #: Whether the pattern ended in `*`; otherwise it must equal the import.
    wildcard: bool
    #: Target FQNs in the order TypeScript tries them; a `*` takes the remainder.
    targets: tuple[str, ...]


@dataclass(frozen=True)
class TypeScriptIndexFacts:
    """Declared npm packages and each file's bare imports (#556)."""

    internal_roots: frozenset[str]
    layout_prefixes: frozenset[str]
    #: npm packages the manifests declare, as written, less our workspace packages.
    npm_dependencies: frozenset[str]
    #: normalized file_path -> the bare specifiers that file imports, as written.
    file_bare_imports: Mapping[str, frozenset[str]]
    #: dotted workspace package name -> the directory FQN it lives at, longest name
    #: first: npm names may contain dots, so `a.b` is both "package a, subpath b" and
    #: "package a.b" once `/` is dotted (#504).
    workspace_packages: Mapping[str, str]
    #: project directory -> its tsconfig `paths` aliases, exact patterns first and then
    #: wildcards longest prefix first — the order TypeScript picks one in (#508).
    path_aliases: Mapping[str, tuple[PathAlias, ...]]


def index_facts(nodes: Mapping[str, Node], state: Mapping[str, object]) -> TypeScriptIndexFacts:
    """Read the bare imports the extractor recorded and the declared npm packages."""
    bare = {
        os.path.normpath(n.file_path): frozenset(n.metadata["bare_imports"])
        for n in nodes.values()
        if n.type == NodeType.FILE and n.metadata.get("bare_imports")
    }
    packages = cast("dict[str, str]", state.get("workspace_packages", {}))
    aliases = cast("dict[str, dict[str, list[str]]]", state.get("tsconfig_paths", {}))
    return TypeScriptIndexFacts(
        internal_roots=frozenset(),
        layout_prefixes=frozenset(),
        npm_dependencies=frozenset(cast("list[str]", state.get("npm_dependencies", []))),
        file_bare_imports=MappingProxyType(bare),
        workspace_packages=MappingProxyType(
            dict(sorted(packages.items(), key=lambda i: -len(i[0])))
        ),
        path_aliases=MappingProxyType({scope: _ordered_aliases(r) for scope, r in aliases.items()}),
    )


def package_namespace(
    fqn: str, source_file: str | None, facts: "TypeScriptIndexFacts"
) -> NodeNamespace | None:
    """EXTERNAL or STDLIB when `fqn` lies under a package `source_file` imports, else None.

    Only TypeScript files record bare imports, so any other source — or none —
    is None without a language check here. Two things must hold (#556).

    The file imports a bare specifier `fqn` sits under — so `react.render` on a
    parameter named `react` in a file that never imports React stays
    unresolved, the mistake #454 fixed for Python names.
    And that specifier names a package: one a `package.json` declares, which
    is EXTERNAL, or a Node builtin, which is STDLIB. An import of anything else
    — an undeclared package, a broken alias — keeps its UNKNOWN, because that
    is a lost edge `top_unresolved` should show.

    A declared package wins over a builtin of the same name: a browser app
    that declares `buffer` bundles that package, not Node's.

    The specifier is matched to a package as written, `/` and all: dotted,
    `lodash.debounce` (a package of its own) would pass for a subpath of a
    declared `lodash`. When a target sits under several imported specifiers the
    longest decides, since it is the one that names the target's module.
    """
    imported = facts.file_bare_imports.get(source_file or "", frozenset())
    under = [
        s for s in imported if fqn == specifier_fqn(s) or fqn.startswith(f"{specifier_fqn(s)}.")
    ]
    if not under:
        return None
    specifier = max(under, key=len)
    # Each `/`-prefix of the specifier is a candidate package name: `@x/a/b/c`
    # tries `@x`, `@x/a`, … — a set lookup each, never a scan of the manifests.
    parts = specifier.split("/")
    if any("/".join(parts[:i]) in facts.npm_dependencies for i in range(1, len(parts) + 1)):
        return NodeNamespace.EXTERNAL
    if specifier.startswith(NODE_SCHEME) or specifier.split("/")[0] in NODE_BUILTIN_MODULES:
        return NodeNamespace.STDLIB
    return None


def classify(fqn: str, ctx: ClassifyContext) -> NodeNamespace:
    """A package the file imports, then our own roots, then the JS builtins; else UNKNOWN (#454)."""
    package = package_namespace(fqn, ctx.source_file, cast("TypeScriptIndexFacts", ctx.facts))
    if package is not None:
        return package
    root = fqn.split(".", maxsplit=1)[0]
    if root in ctx.internal_roots:
        return NodeNamespace.INTERNAL
    if root == JS_BUILTINS_ROOT:
        return NodeNamespace.STDLIB
    return NodeNamespace.UNKNOWN


def _ordered_aliases(rules: Mapping[str, list[str]]) -> tuple[PathAlias, ...]:
    """One project's aliases as `PathAlias`es, in the order TypeScript tries patterns."""
    aliases = [
        PathAlias(
            prefix=pattern.removesuffix(_ALIAS_WILDCARD),
            wildcard=pattern.endswith(_ALIAS_WILDCARD),
            targets=tuple(targets),
        )
        for pattern, targets in rules.items()
    ]
    return tuple(sorted(aliases, key=lambda a: (a.wildcard, -len(a.prefix), a.prefix)))
