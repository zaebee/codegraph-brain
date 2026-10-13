"""The npm packages a TypeScript project declares in its `package.json` files (#556).

`import { useState } from "react"` names a package no node of ours holds, and
without the manifest the resolver cannot tell it from a lost edge: on owner-admin
`react`, `lucide-react` and `useState` topped `top_unresolved` and `external` was 0.
The manifest is the evidence, as `pyproject.toml` is for Python (#495).

Every manifest the walk passes counts, the repository root's included: a monorepo
hoists shared dependencies to the root, and an app may import one it never declares
itself.
"""

import json
from collections.abc import Iterable
from pathlib import Path

import structlog

logger = structlog.getLogger(__name__)

#: The manifest fields that name a package the project imports.
_DEPENDENCY_FIELDS = (
    "dependencies",
    "devDependencies",
    "peerDependencies",
    "optionalDependencies",
)
#: Version protocols that point inside the repository: such a dependency is ours.
_LOCAL_PROTOCOLS = ("workspace:", "file:", "link:", "portal:")


class NpmDependencies:
    """Collects the declared package names from the manifests a walk passes."""

    def __init__(self) -> None:
        """Start with nothing declared."""
        self._names: set[str] = set()

    def note(self, manifest: Path) -> None:
        """Record the packages one `package.json` declares.

        A dependency resolved from inside the repository (`workspace:*`, `file:`,
        `link:`) is skipped: an import of it that does not reach a node is a lost
        edge of ours, not a call into a library. An unreadable manifest is skipped
        too — one bad file must not stop the ingest.
        """
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("Skipping unreadable package.json", path=str(manifest), error=str(exc))
            return
        if not isinstance(data, dict):
            return
        for field in _DEPENDENCY_FIELDS:
            declared = data.get(field)
            if not isinstance(declared, dict):
                continue
            for name, version in declared.items():
                if isinstance(version, str) and version.startswith(_LOCAL_PROTOCOLS):
                    continue
                self._names.add(name)

    def declared(self, excluding: Iterable[str] = ()) -> list[str]:
        """Declared package names as written, sorted, less `excluding`.

        `excluding` takes the workspace packages: a monorepo also lists its own
        packages as dependencies, often with a plain version range rather than
        `workspace:*`, and those are ours.

        Not dotted: `lodash.debounce` is a package of its own, not the `debounce`
        subpath of `lodash`, and only the written form tells them apart.
        """
        return sorted(self._names.difference(excluding))
