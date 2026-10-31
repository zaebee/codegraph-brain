"""What a language pack reads from a project's configs (spec: Project facts).

Data, not a writer: a pack never touches storage (the `pack-isolation` gate), so
facts say *what* to store under which `ingest_state` key and the build plane's
collector (`cgis.import_names`) records and compares them (step 2, ruling S1).
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol


class ProjectFacts(Protocol):
    """Frozen, JSON-serialisable facts one pack read from a project."""

    def state(self) -> Mapping[str, object]:
        """Each fact under the `ingest_state` key it is stored as."""
        ...


class ProjectReader(Protocol):
    """Collects one pack's configs during a walk, and from the directories above it."""

    def note(self, path: Path) -> bool:
        """Take a file the walk passed, if it is one of this pack's configs."""
        ...

    def note_above(self, directories: list[Path], bounded: bool) -> None:
        """Read configs above the ingest root, nearest first (#555).

        `bounded` says a repository boundary ends the list; without one the list
        runs to `/`, and only the nearest find should be trusted.
        """
        ...

    def facts(self) -> ProjectFacts:
        """What the walk's configs say, once it is done."""
        ...


def resolves_inside(path: Path, root: Path) -> bool:
    """Whether `path`, with symlinks resolved, is still inside `root`.

    `IngestionPipeline` skips a source file whose resolved target lies outside the
    ingest root, but a project reader opens configs of its own: a PR can commit a
    `tsconfig.json`, a `package.json` or a `go.mod` as a symlink and have the
    ingest read a file the checkout does not hold (#732). This is the one
    containment a reader asks of a path the walk passed; a config above the root is
    a different case (#555) and is not judged by it.

    A path that cannot be resolved — a symlink loop — reads as outside: refusing
    the file is the safe answer.
    """
    try:
        return path.resolve().is_relative_to(root.resolve())
    except (OSError, RuntimeError):
        return False
