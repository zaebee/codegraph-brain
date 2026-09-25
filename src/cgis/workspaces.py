"""Workspace packages a TypeScript monorepo names in its `package.json` files (#504).

A monorepo imports its own packages by name: `@calcom/lib/hooks/useLocale` means
`packages/lib/hooks/useLocale.ts`. The resolver can map such an import onto the
module only when told which directory each name lives in, and this collects that
from the manifests the pipeline walks past.
"""

import json
from collections.abc import Mapping
from pathlib import Path

import structlog

from cgis.extractors.base import BaseExtractor, ModuleNamer

logger = structlog.getLogger(__name__)

#: The manifest a workspace package is named in.
PACKAGE_MANIFEST = "package.json"


class WorkspacePackages:
    """Collects package names during a walk and hands the resolver the unambiguous ones."""

    def __init__(self, workspace_root: Path, extractors: Mapping[str, BaseExtractor]) -> None:
        """Collect for `workspace_root`, naming directories with the TypeScript extractor's rule.

        The extractor is the one registered for `.ts`, chosen by extension rather
        than by class, so this module imports no language's extractor (#506). With
        none configured, or one that cannot name modules, every manifest is ignored.
        """
        self._root = workspace_root
        extractor = extractors.get(".ts") or extractors.get(".tsx")
        self._extractor = extractor if isinstance(extractor, ModuleNamer) else None
        # dotted package name -> every directory FQN that claims it
        self._claims: dict[str, set[str]] = {}

    def note(self, manifest: Path) -> None:
        """Record which directory a `package.json` names.

        Never the repository root: its manifest names the monorepo itself, and
        mapping it would send an import of that name to the empty directory FQN.
        The directory FQN comes from the extractor's own `module_fqn`, so it is
        spelled exactly as that package's node ids are. An unreadable or nameless
        manifest is skipped: one bad file must not stop the ingest.
        """
        if self._extractor is None:
            return
        try:
            directory = manifest.resolve().parent.relative_to(self._root).as_posix()
        except ValueError:
            return
        if directory in ("", "."):
            return
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("Skipping unreadable package.json", path=str(manifest), error=str(exc))
            return
        name = data.get("name") if isinstance(data, dict) else None
        if not isinstance(name, str) or not name.strip():
            return
        directory_fqn = self._extractor.module_fqn(f"{directory}/index.ts")
        self._claims.setdefault(name.replace("/", "."), set()).add(directory_fqn)

    def unambiguous(self) -> dict[str, str]:
        """Dotted package names claimed by exactly one directory; a name claimed twice is dropped.

        Two manifests with one name cannot both be what an import means, and
        choosing one would wire every importer to a package it may not use.
        """
        packages: dict[str, str] = {}
        for name, directories in sorted(self._claims.items()):
            if len(directories) == 1:
                packages[name] = next(iter(directories))
            else:
                logger.warning(
                    "Package name claimed by several directories; not resolving it",
                    name=name,
                    directories=sorted(directories),
                )
        return packages
