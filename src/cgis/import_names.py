"""The names imports resolve through, collected from the configs around a walk.

Four sources, read the same way and handed to the resolver together: workspace
packages and declared npm dependencies from `package.json` (#504, #556) and
`compilerOptions.paths` aliases from `tsconfig.json` (#508), which the walk passes
or which sit above the ingest root (#555), and the dependencies a Python project
declares (#495), read from the manifests at or above the ingest root.
Kept out of `IngestionPipeline`, which only says which files it walked past and
when the result must be stored.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from cgis.extractors.base import BaseExtractor
from cgis.npm_dependencies import NpmDependencies
from cgis.python_dependencies import declared_import_roots
from cgis.tsconfig_paths import TSCONFIG, TsconfigPaths
from cgis.workspaces import PACKAGE_MANIFEST, WorkspacePackages

if TYPE_CHECKING:
    from cgis.storage.sqlite_store import SQLiteStore

logger = structlog.getLogger(__name__)

#: What marks a repository boundary the upward search never crosses.
_REPOSITORY_MARKER = ".git"


@dataclass(frozen=True)
class ImportNames:
    """What one walk's configs say TypeScript import names mean."""

    #: Dotted package name -> the directory FQN it lives at.
    workspace_packages: dict[str, str]
    #: Project directory -> dotted alias pattern -> dotted target FQNs.
    path_aliases: dict[str, dict[str, list[str]]]
    #: Import roots of the declared Python dependencies, sorted.
    python_dependencies: list[str]
    #: Declared npm package names as written, less our workspace packages, sorted.
    npm_dependencies: list[str]

    def differ_from(self, store: "SQLiteStore") -> bool:
        """Whether the stored graph was resolved against different names.

        A renamed or moved package, or an edited alias, changes what unchanged
        files' imports mean, and an incremental run re-resolves only the files
        that changed — so a difference calls for a rebuild (#504, #508). An added
        or dropped dependency likewise changes what a colliding root means (#495),
        or whether a TypeScript import names a library (#556).
        A graph that predates recording any of them reads as having had none.
        """
        return (
            (store.get_workspace_packages() or {}) != self.workspace_packages
            or (store.get_tsconfig_paths() or {}) != self.path_aliases
            or (store.get_python_dependencies() or []) != self.python_dependencies
            or (store.get_npm_dependencies() or []) != self.npm_dependencies
        )

    def record(self, store: "SQLiteStore") -> None:
        """Record these names on the stored graph, for the next run's `differ_from`."""
        store.record_workspace_packages(self.workspace_packages)
        store.record_tsconfig_paths(self.path_aliases)
        store.record_python_dependencies(self.python_dependencies)
        store.record_npm_dependencies(self.npm_dependencies)


def _enclosing_directories(root: Path) -> Iterator[Path]:
    """The directories above `root`, nearest first, up to the one holding the repository.

    None when `root` is itself a repository's top: a checkout nested in another
    must not read its parent's configs.
    """
    if (root / _REPOSITORY_MARKER).exists():
        return
    for directory in root.parents:
        yield directory
        if (directory / _REPOSITORY_MARKER).exists():
            return


class ImportNameCollector:
    """Takes the `package.json` and `tsconfig.json` files a walk passes."""

    def __init__(self, workspace_root: Path, extractors: Mapping[str, BaseExtractor]) -> None:
        """Collect for `workspace_root`, naming modules with the TypeScript extractor's rule."""
        self._root = workspace_root
        self._packages = WorkspacePackages(workspace_root, extractors)
        self._tsconfigs = TsconfigPaths(workspace_root, extractors)
        self._npm = NpmDependencies()
        self._note_configs_above()

    def _note_configs_above(self) -> None:
        """Read the configs above the ingest root that its files still belong to (#555).

        `cgis ingest app/src` never walks past `app/tsconfig.json` or
        `app/package.json`, yet the compiler gives each file the nearest tsconfig
        above it and Node finds packages in every `node_modules` up the tree. So
        the nearest tsconfig above counts — unless the root has its own, which is
        nearer — and every package.json above declares dependencies. The search
        stops at the repository boundary, as `python_dependencies` does (#495).
        """
        found_tsconfig = (self._root / TSCONFIG).is_file()
        for directory in _enclosing_directories(self._root):
            manifest = directory / PACKAGE_MANIFEST
            if manifest.is_file():
                self._npm.note(manifest)
            config = directory / TSCONFIG
            if not found_tsconfig and config.is_file():
                logger.info("Reading tsconfig.json above the ingest root", path=str(config))
                self._tsconfigs.note(config)
                found_tsconfig = True

    def note(self, path: Path) -> None:
        """Hand a file the walk passed to whichever reader takes it; any other is ignored."""
        if path.name == PACKAGE_MANIFEST:
            self._packages.note(path)
            self._npm.note(path)
        elif path.name == TSCONFIG:
            self._tsconfigs.note(path)

    def collect(self) -> ImportNames:
        """The names, once the walk is done.

        Aliases are read last: a tsconfig can extend a shared config that lives in
        a workspace package, which only the finished package map can locate.
        """
        return ImportNames(
            workspace_packages=self._packages.unambiguous(),
            path_aliases=self._tsconfigs.aliases(self._packages.directories()),
            python_dependencies=sorted(declared_import_roots(self._root)),
            npm_dependencies=self._npm.declared(excluding=self._packages.names()),
        )
