"""The names TypeScript imports resolve through, collected from the configs a walk passes.

Two sources, read the same way and handed to the resolver together: workspace
packages from `package.json` (#504) and `compilerOptions.paths` aliases from
`tsconfig.json` (#508). Kept out of `IngestionPipeline`, which only says which
files it walked past and when the result must be stored.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from cgis.extractors.base import BaseExtractor
from cgis.tsconfig_paths import TSCONFIG, TsconfigPaths
from cgis.workspaces import PACKAGE_MANIFEST, WorkspacePackages

if TYPE_CHECKING:
    from cgis.storage.sqlite_store import SQLiteStore


@dataclass(frozen=True)
class ImportNames:
    """What one walk's configs say TypeScript import names mean."""

    #: Dotted package name -> the directory FQN it lives at.
    workspace_packages: dict[str, str]
    #: Project directory -> dotted alias pattern -> dotted target FQNs.
    path_aliases: dict[str, dict[str, list[str]]]

    def differ_from(self, store: "SQLiteStore") -> bool:
        """Whether the stored graph was resolved against different names.

        A renamed or moved package, or an edited alias, changes what unchanged
        files' imports mean, and an incremental run re-resolves only the files
        that changed — so a difference calls for a rebuild (#504, #508). A graph
        that predates recording either reads as having had none.
        """
        return (store.get_workspace_packages() or {}) != self.workspace_packages or (
            store.get_tsconfig_paths() or {}
        ) != self.path_aliases

    def record(self, store: "SQLiteStore") -> None:
        """Record these names on the stored graph, for the next run's `differ_from`."""
        store.record_workspace_packages(self.workspace_packages)
        store.record_tsconfig_paths(self.path_aliases)


class ImportNameCollector:
    """Takes the `package.json` and `tsconfig.json` files a walk passes."""

    def __init__(self, workspace_root: Path, extractors: Mapping[str, BaseExtractor]) -> None:
        """Collect for `workspace_root`, naming modules with the TypeScript extractor's rule."""
        self._packages = WorkspacePackages(workspace_root, extractors)
        self._tsconfigs = TsconfigPaths(workspace_root, extractors)

    def note(self, path: Path) -> None:
        """Hand a file the walk passed to whichever reader takes it; any other is ignored."""
        if path.name == PACKAGE_MANIFEST:
            self._packages.note(path)
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
        )
