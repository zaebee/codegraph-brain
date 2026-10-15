"""The Go pack's index facts and classification (spec: Go pilot, classify).

Import paths are recorded dotted (`example.com/app/internal/server`, H4) and
mapped to node ids by the pack's own `reconcile_import`, through the module
path, the ingest root's offset and each package's directory. Classification of
a reference from a Go file follows the spec's order — own module first, then
the file's imports. cgo's pseudo-package needs no rule of its own: a file that
calls `C.malloc` imports "C", a path with no dot in its first segment, which
is the standard library's reading.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import cast

from cgis.core.index import ClassifyContext
from cgis.core.models import Node, NodeNamespace, NodeType
from cgis.languages.go._syntax import BUILTINS, dotted
from cgis.languages.go.naming import GO_LANG
from cgis.languages.go.project import GO_MODULE_KEY


@dataclass(frozen=True)
class GoIndexFacts:
    """What Go nodes and `go.mod` add to the language-free index.

    No layout prefixes: the module path's segments in the global set stripped
    Python imports too (#319's collision, 7b review I5). The Go pack maps its
    own import paths instead (`resolution.reconcile_import`).
    """

    internal_roots: frozenset[str] = frozenset()
    reexports: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    layout_prefixes: frozenset[str] = frozenset()
    #: The module path, dotted; empty without a `go.mod`.
    module: str = ""
    #: Where the ingest root sits under the module, dotted; empty at the module root.
    offset: str = ""
    #: Required module paths, as written.
    requires: frozenset[str] = frozenset()
    #: Replaced module path, dotted -> the directory, dotted, relative to the ingest root.
    replaces: Mapping[str, str] = field(default_factory=dict)
    #: normalized file_path -> {dotted import path: import path as written}.
    file_imports: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    #: package directory, dotted -> the scope of the package an import of it names.
    package_dirs: Mapping[str, str] = field(default_factory=dict)
    #: package scope -> its FILE node ids, production files first (step 7, H5).
    package_files: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: normalized file_path -> its package scope.
    file_scopes: Mapping[str, str] = field(default_factory=dict)


def _package_dirs(files: list[Node]) -> dict[str, str]:
    """Directory -> package scope; an external `_test` package never stands for its directory."""
    dirs: dict[str, str] = {}
    for file in sorted(files, key=lambda f: str(f.metadata.get("scope", "")).endswith("_test")):
        dirs.setdefault(
            str(file.metadata.get("package_dir", "")), str(file.metadata.get("scope", ""))
        )
    return dirs


def _package_files(files: list[Node]) -> dict[str, tuple[str, ...]]:
    """Scope -> FILE ids, production before test, then by id (H5's representative is first)."""
    packages: dict[str, list[Node]] = {}
    for file in files:
        packages.setdefault(str(file.metadata.get("scope", "")), []).append(file)
    return {
        scope: tuple(f.id for f in sorted(group, key=lambda f: (f.is_test, f.id)))
        for scope, group in packages.items()
    }


def index_facts(nodes: Mapping[str, Node], state: Mapping[str, object]) -> GoIndexFacts:
    """Go's facts over every node, given the merged project state."""
    module = cast("dict[str, object]", state.get(GO_MODULE_KEY) or {})
    replaces = cast("dict[str, str]", module.get("replaces") or {})
    files = [n for n in nodes.values() if n.type == NodeType.FILE and n.language == GO_LANG]
    return GoIndexFacts(
        module=dotted(str(module.get("path", ""))),
        offset=str(module.get("offset", "")),
        requires=frozenset(cast("list[str]", module.get("requires") or [])),
        replaces=MappingProxyType(
            {dotted(m): target.replace("/", ".") for m, target in replaces.items()}
        ),
        file_imports=MappingProxyType(
            {
                os.path.normpath(f.file_path): {
                    dotted(p): p for p in cast("list[str]", f.metadata.get("imports") or [])
                }
                for f in files
            }
        ),
        package_dirs=MappingProxyType(_package_dirs(files)),
        package_files=MappingProxyType(_package_files(files)),
        file_scopes=MappingProxyType(
            {os.path.normpath(f.file_path): str(f.metadata.get("scope", "")) for f in files}
        ),
    )


def _under(fqn: str, prefix: str) -> bool:
    return bool(prefix) and (fqn == prefix or fqn.startswith(f"{prefix}."))


def import_namespace(import_path: str, facts: GoIndexFacts) -> NodeNamespace:
    """What an import path names: the stdlib, a required module, or neither (UNKNOWN).

    A first segment with no dot is the standard library, as the go command
    decides. A domain path is EXTERNAL when `go.mod` requires its module — or
    when there is no `go.mod` to ask; an undeclared one stays UNKNOWN, a lost
    edge `top_unresolved` should show, as TypeScript does for npm (#556).
    """
    if "." not in import_path.split("/", 1)[0]:
        return NodeNamespace.STDLIB
    if not facts.module or any(
        import_path == m or import_path.startswith(f"{m}/") for m in facts.requires
    ):
        return NodeNamespace.EXTERNAL
    return NodeNamespace.UNKNOWN


def classify(fqn: str, ctx: ClassifyContext) -> NodeNamespace:
    """Own module, builtins, the source file's imports (cgo's `C` among them), our roots."""
    facts = cast("GoIndexFacts", ctx.facts)
    if _under(fqn, facts.module) or any(_under(fqn, m) for m in facts.replaces):
        return NodeNamespace.INTERNAL
    if fqn in BUILTINS:
        return NodeNamespace.STDLIB
    imported = facts.file_imports.get(ctx.source_file or "", {})
    under = [d for d in imported if _under(fqn, d)]
    if under:
        return import_namespace(imported[max(under, key=len)], facts)
    if fqn.split(".", maxsplit=1)[0] in ctx.internal_roots:
        return NodeNamespace.INTERNAL
    return NodeNamespace.UNKNOWN
