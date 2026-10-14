"""The TypeScript pack's project reader: workspace packages, npm dependencies, tsconfig aliases.

Four sources read the same way (#504, #556, #508, #562): `package.json` and
`tsconfig.json` files the walk passes or that sit above the ingest root (#555).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import override

import structlog

from cgis.core.extractor import BaseExtractor
from cgis.core.project import ProjectFacts, ProjectReader
from cgis.languages.typescript.npm import NpmDependencies
from cgis.languages.typescript.tsconfig import TSCONFIG, TsconfigPaths
from cgis.languages.typescript.workspaces import (
    PACKAGE_MANIFEST,
    PNPM_WORKSPACE,
    WorkspacePackages,
    pnpm_member_names,
    workspace_member_names,
)

logger = structlog.getLogger(__name__)


@dataclass(frozen=True)
class TypeScriptFacts(ProjectFacts):
    """What one walk's configs say TypeScript import names mean."""

    #: Dotted package name -> the directory FQN it lives at.
    workspace_packages: dict[str, str]
    #: Project directory -> dotted alias pattern -> dotted target FQNs.
    path_aliases: dict[str, dict[str, list[str]]]
    #: Declared npm package names as written, less our workspace packages, sorted.
    npm_dependencies: list[str]

    @override
    def state(self) -> dict[str, object]:
        """Stored as `workspace_packages`, `tsconfig_paths` and `npm_dependencies`."""
        return {
            "workspace_packages": self.workspace_packages,
            "tsconfig_paths": self.path_aliases,
            "npm_dependencies": self.npm_dependencies,
        }


class TypeScriptProjectReader(ProjectReader):
    """Takes the `package.json` and `tsconfig.json` files a walk passes, and those above it."""

    def __init__(self, root: Path, extractors: Mapping[str, BaseExtractor]) -> None:
        """Read for `root`, naming modules with the walk's own `.ts` extractor (S4)."""
        self._root = root
        self._packages = WorkspacePackages(root, extractors)
        self._tsconfigs = TsconfigPaths(root, extractors)
        self._npm = NpmDependencies()
        # Workspace packages named by a manifest above the root, outside the walk.
        self._siblings: set[str] = set()

    @override
    def note(self, path: Path) -> bool:
        """`package.json` feeds packages and npm names; `tsconfig.json` feeds aliases."""
        if path.name == PACKAGE_MANIFEST:
            self._packages.note(path)
            self._npm.note(path)
            return True
        if path.name == TSCONFIG:
            self._tsconfigs.note(path)
            return True
        return False

    @override
    def note_above(self, directories: list[Path], bounded: bool) -> None:
        """Read the configs above the ingest root that its files still belong to (#555).

        `cgis ingest app/src` never walks past `app/tsconfig.json` or
        `app/package.json`, yet the compiler gives each file the nearest tsconfig
        above it and Node finds packages in every `node_modules` up the tree. So
        the nearest tsconfig above counts — unless the root has its own, which is
        nearer — and every package.json above declares dependencies, less the
        workspace packages a monorepo manifest there names.
        """
        manifests = [d / PACKAGE_MANIFEST for d in directories if (d / PACKAGE_MANIFEST).is_file()]
        # Without a repository the walk up reaches `$HOME` or `/`: a stray manifest
        # there would add packages, so only the nearest is trusted (#560 review).
        for manifest in manifests if bounded else manifests[:1]:
            self._npm.note(manifest)
            self._siblings |= workspace_member_names(manifest)
        # pnpm names its workspace in its own file, beside the root manifest (#562).
        pnpm = [d / PNPM_WORKSPACE for d in directories if (d / PNPM_WORKSPACE).is_file()]
        for workspace_file in pnpm if bounded else pnpm[:1]:
            self._siblings |= pnpm_member_names(workspace_file)
        if (self._root / TSCONFIG).is_file():
            return
        config = next((d / TSCONFIG for d in directories if (d / TSCONFIG).is_file()), None)
        if config is not None:
            logger.info("Reading tsconfig.json above the ingest root", path=str(config))
            self._tsconfigs.note(config)

    @override
    def facts(self) -> TypeScriptFacts:
        """Aliases last: a tsconfig can extend a shared config in a workspace package."""
        return TypeScriptFacts(
            workspace_packages=self._packages.unambiguous(),
            path_aliases=self._tsconfigs.aliases(self._packages.directories()),
            npm_dependencies=self._npm.declared(excluding=self._packages.names() | self._siblings),
        )
