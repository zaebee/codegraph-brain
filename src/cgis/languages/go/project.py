"""The Go pack's project facts: what `go.mod` says (spec: Go pilot, project facts)."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import override

from cgis.core.project import ProjectFacts, ProjectReader

#: The `ingest_state` key the facts are stored under — a pack-owned key (step 7, H3).
GO_MODULE_KEY = "go_module"


@dataclass(frozen=True)
class GoFacts(ProjectFacts):
    """The module a walk's `go.mod` declares; empty without one."""

    module: dict[str, object] = field(default_factory=dict)

    @override
    def state(self) -> dict[str, object]:
        """The module under its key; nothing without a `go.mod`, so a Go-free graph is unchanged.

        A key that later vanishes still calls for a rebuild: the collector compares
        the whole set of pack keys (`SQLiteStore.pack_state`).
        """
        return {GO_MODULE_KEY: dict(self.module)} if self.module else {}


class GoProjectReader(ProjectReader):
    """Reads the `go.mod` at, or nearest above, the ingest root."""

    def __init__(self, root: Path) -> None:
        """Collect for the project at `root`."""
        self._root = root

    @override
    def note(self, path: Path) -> bool:
        """Take a file the walk passed, if it is this pack's config."""
        return False

    @override
    def note_above(self, directories: list[Path], bounded: bool) -> None:
        """Read configs above the ingest root, nearest first."""

    @override
    def facts(self) -> GoFacts:
        """What the walk's `go.mod` says, once it is done."""
        return GoFacts()
