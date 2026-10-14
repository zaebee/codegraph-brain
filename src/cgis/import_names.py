"""The names imports resolve through, collected from the configs around a walk.

Each language pack reads its own (`cgis.core.project`); this module only routes
the walk's files and the directories above the root to every pack's reader, and
merges what they read (language-packs step 2). Kept out of `IngestionPipeline`,
which only says which files it walked past and when the result must be stored.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from cgis.core.extractor import BaseExtractor
from cgis.core.project import ProjectFacts
from cgis.languages import project_readers

if TYPE_CHECKING:
    from cgis.storage.sqlite_store import SQLiteStore

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

    @classmethod
    def from_facts(cls, facts: Iterable[ProjectFacts]) -> "ImportNames":
        """Merge every pack's facts; a pack that read nothing contributes empty values.

        A key two packs both claim is an error, not a last-writer-wins: one pack's
        facts would vanish from resolution without a trace.
        """
        state: dict[str, object] = {}
        for fact in facts:
            contribution = fact.state()
            shared = state.keys() & contribution.keys()
            if shared:
                msg = f"two packs record the same ingest_state key(s): {sorted(shared)}"
                raise ValueError(msg)
            state.update(contribution)
        return cls(
            workspace_packages=cast("dict[str, str]", state.get("workspace_packages", {})),
            path_aliases=cast("dict[str, dict[str, list[str]]]", state.get("tsconfig_paths", {})),
            python_dependencies=cast("list[str]", state.get("python_dependencies", [])),
            npm_dependencies=cast("list[str]", state.get("npm_dependencies", [])),
        )

    def record(self, store: "SQLiteStore") -> None:
        """Record these names on the stored graph, for the next run's `differ_from`."""
        store.record_workspace_packages(self.workspace_packages)
        store.record_tsconfig_paths(self.path_aliases)
        store.record_python_dependencies(self.python_dependencies)
        store.record_npm_dependencies(self.npm_dependencies)


def _enclosing_directories(root: Path) -> tuple[list[Path], bool]:
    """The directories above `root`, nearest first, and whether a repository bounds them.

    Up to and including the one that holds the repository; none when `root` is
    itself a repository's top, since a checkout nested in another must not read
    its parent's configs. With no repository anywhere the list runs to `/`, and
    the flag says so, for the caller to trust only the nearest find.
    """
    if (root / _REPOSITORY_MARKER).exists():
        return [], True
    directories: list[Path] = []
    for directory in root.parents:
        directories.append(directory)
        if (directory / _REPOSITORY_MARKER).exists():
            return directories, True
    return directories, False


class ImportNameCollector:
    """Routes the configs a walk passes to every language pack's reader."""

    def __init__(self, workspace_root: Path, extractors: Mapping[str, BaseExtractor]) -> None:
        """Collect for `workspace_root`; `extractors` reach the readers that name modules (S4).

        The root is resolved first: the search above it walks `parents`, which a
        relative path does not have past `.` (#560 review). The pipeline already
        passes a resolved root, so for it nothing changes.
        """
        self._root = workspace_root.resolve()
        self._readers = project_readers(self._root, extractors)
        directories, bounded = _enclosing_directories(self._root)
        for reader in self._readers:
            reader.note_above(directories, bounded)

    def note(self, path: Path) -> None:
        """Hand a file the walk passed to every reader; each takes only its own."""
        for reader in self._readers:
            reader.note(path)

    def collect(self) -> ImportNames:
        """The names, once the walk is done."""
        return ImportNames.from_facts(reader.facts() for reader in self._readers)
