"""Workspace packages a TypeScript monorepo names in its `package.json` files (#504).

A monorepo imports its own packages by name: `@calcom/lib/hooks/useLocale` means
`packages/lib/hooks/useLocale.ts`. The resolver can map such an import onto the
module only when told which directory each name lives in, and this collects that
from the manifests the pipeline walks past.
"""

import json
from collections.abc import Iterable, Mapping
from functools import cached_property
from pathlib import Path

import structlog
import yaml

from cgis.core.extractor import BaseExtractor, ModuleNamer

logger = structlog.getLogger(__name__)

#: The manifest a workspace package is named in.
PACKAGE_MANIFEST = "package.json"
#: Where pnpm declares its workspace packages; it ignores `workspaces` in package.json.
PNPM_WORKSPACE = "pnpm-workspace.yaml"


def workspace_member_names(manifest: Path) -> set[str]:
    """The package names a monorepo manifest's `workspaces` globs reach, as written.

    For a manifest above the ingest root (#555): its workspace packages lie
    outside the walk, so `WorkspacePackages` never sees them, yet an app inside
    the walk may depend on one with a plain version range. They are ours, and
    must not read as npm packages (#560 review). Both spellings are read — a list,
    or Yarn's `{"packages": [...]}`; a `!` exclusion is skipped. Unreadable
    manifests contribute nothing.
    """
    data = _read_manifest(manifest)
    workspaces = data.get("workspaces") if data else None
    if isinstance(workspaces, dict):
        workspaces = workspaces.get("packages")
    if not isinstance(workspaces, list):
        return set()
    return _member_names(manifest.parent, workspaces)


def pnpm_member_names(config: Path) -> set[str]:
    """The package names a `pnpm-workspace.yaml`'s `packages` globs reach (#562).

    pnpm reads its workspace from this file, not from `workspaces` in
    package.json, so in a pnpm monorepo `workspace_member_names` finds nothing
    and a sibling depended on with a plain version range read as EXTERNAL.
    Unreadable or malformed files contribute nothing.
    """
    try:
        data = yaml.safe_load(config.read_text(encoding="utf-8"))
    # Not UTF-8 is a ValueError, a deeply nested file a RecursionError: either
    # crashed the whole ingest (#591 review).
    except (OSError, ValueError, yaml.YAMLError, RecursionError):
        return set()
    packages = data.get("packages") if isinstance(data, dict) else None
    return _member_names(config.parent, packages) if isinstance(packages, list) else set()


def _member_names(base: Path, patterns: Iterable[object]) -> set[str]:
    """Names of the packages whose directories `patterns` match under `base`.

    A `!` pattern subtracts what it matches, and nothing under `node_modules`
    is a member — as pnpm and npm read their workspaces (#591 review): with
    `packages/**` an installed copy of a dependency read as a sibling, and a
    real library then read as our own unresolved code.
    """
    included: set[Path] = set()
    excluded: set[Path] = set()
    for pattern in patterns:
        if not isinstance(pattern, str):
            continue
        if pattern.startswith("!"):
            excluded |= _manifests(base, pattern.removeprefix("!"))
        else:
            included |= _manifests(base, pattern)
    names: set[str] = set()
    for member in sorted(included - excluded):
        name = (_read_manifest(member) or {}).get("name")
        if isinstance(name, str) and name.strip():
            names.add(name)
    return names


def _manifests(base: Path, pattern: str) -> set[Path]:
    """The package.json files a workspace glob reaches under `base`, none in node_modules."""
    try:
        found = base.glob(f"{pattern.rstrip('/')}/{PACKAGE_MANIFEST}")
        return {m for m in found if "node_modules" not in m.relative_to(base).parts}
    # An absolute or otherwise unglobbable pattern names nothing, rather than
    # stopping the ingest (#591 review).
    except (NotImplementedError, ValueError):
        return set()


def _read_manifest(manifest: Path) -> dict[str, object] | None:
    """A `package.json` as a dict, or None when it cannot be read as one."""
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


class WorkspacePackages:
    """Collects package names during a walk and hands the resolver the unambiguous ones."""

    def __init__(self, workspace_root: Path, extractors: Mapping[str, BaseExtractor]) -> None:
        """Collect for `workspace_root`, naming directories with the TypeScript extractor's rule.

        The extractor is the one registered for `.ts`, chosen by extension rather
        than by class, so this module imports no language's extractor (#506). With
        none configured, or one that cannot name modules, every manifest is ignored.
        The extractor is looked up on first use, so a repository with no
        package.json never builds it.
        """
        self._root = workspace_root
        self._extractors = extractors
        # package name as written -> every directory that claims it
        self._claims: dict[str, set[Path]] = {}

    @cached_property
    def _extractor(self) -> ModuleNamer | None:
        """The `.ts` extractor if it can name modules — fetched only when a manifest needs it."""
        extractor = self._extractors.get(".ts") or self._extractors.get(".tsx")
        return extractor if isinstance(extractor, ModuleNamer) else None

    def note(self, manifest: Path) -> None:
        """Record which directory a `package.json` names.

        Never the repository root: its manifest names the monorepo itself, and
        mapping it would send an import of that name to the empty directory FQN.
        An unreadable or nameless manifest is skipped: one bad file must not stop
        the ingest.
        """
        if self._extractor is None:
            return
        directory = manifest.resolve().parent
        try:
            relative = directory.relative_to(self._root).as_posix()
        except ValueError:
            return
        if relative in ("", "."):
            return
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("Skipping unreadable package.json", path=str(manifest), error=str(exc))
            return
        name = data.get("name") if isinstance(data, dict) else None
        if not isinstance(name, str) or not name.strip():
            return
        self._claims.setdefault(name, set()).add(directory)

    def names(self) -> set[str]:
        """Every package name a manifest claims, as written — ambiguous ones included.

        A name claimed twice is not resolved, but it is still ours: an import of it
        must never read as a third-party package (#556 review).
        """
        return set(self._claims)

    def directories(self) -> dict[str, Path]:
        """Package names as written, claimed by exactly one directory, to that directory.

        What a tsconfig `extends` of a shared config package needs (#508): a
        monorepo checkout without `node_modules` still has the package itself.
        """
        return {
            name: next(iter(found))
            for name, found in sorted(self._claims.items())
            if len(found) == 1
        }

    def unambiguous(self) -> dict[str, str]:
        """Dotted package names claimed by exactly one directory; a name claimed twice is dropped.

        Two manifests with one name cannot both be what an import means, and
        choosing one would wire every importer to a package it may not use. The
        directory FQN comes from the extractor's own `module_fqn`, so it is spelled
        exactly as that package's node ids are.
        """
        if not self._claims or self._extractor is None:
            return {}
        claims: dict[str, set[str]] = {}
        for name, found in sorted(self._claims.items()):
            for directory in found:
                relative = directory.relative_to(self._root).as_posix()
                fqn = self._extractor.module_fqn(f"{relative}/index.ts")
                claims.setdefault(name.replace("/", "."), set()).add(fqn)
        packages: dict[str, str] = {}
        for name, directories in sorted(claims.items()):
            if len(directories) == 1:
                packages[name] = next(iter(directories))
            else:
                logger.warning(
                    "Package name claimed by several directories; not resolving it",
                    name=name,
                    directories=sorted(directories),
                )
        return packages
