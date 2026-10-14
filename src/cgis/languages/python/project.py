"""The Python pack's project reader: the dependencies a project declares (#495)."""

from dataclasses import dataclass
from pathlib import Path
from typing import override

from cgis.core.project import ProjectFacts, ProjectReader
from cgis.languages.python.dependencies import declared_import_roots


@dataclass(frozen=True)
class PythonFacts(ProjectFacts):
    """Import roots of the declared dependencies, sorted."""

    dependencies: list[str]

    @override
    def state(self) -> dict[str, object]:
        """Stored as `python_dependencies`."""
        return {"python_dependencies": self.dependencies}


class PythonProjectReader(ProjectReader):
    """Reads the manifests at or above the root; the walk itself brings nothing."""

    def __init__(self, root: Path) -> None:
        """Read for the project at `root`."""
        self._root = root

    @override
    def note(self, path: Path) -> bool:
        """Never: the nearest manifest at or above the root is found in `facts` (S3)."""
        return False

    @override
    def note_above(self, directories: list[Path], bounded: bool) -> None:
        """Ignored: `declared_import_roots` searches from the root itself, up to `.git` (S3)."""

    @override
    def facts(self) -> PythonFacts:
        """The declared dependencies' import roots."""
        return PythonFacts(sorted(declared_import_roots(self._root)))
