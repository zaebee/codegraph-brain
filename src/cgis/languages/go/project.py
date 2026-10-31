"""The Go pack's project facts: what `go.mod` says (spec: Go pilot, project facts).

A line parser for the three directives classification needs — `module`,
`require` and `replace`, single-line and block — with no Go toolchain and no
dependency. `go.work` (several modules) is out of the pilot's scope.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import override

import structlog

from cgis.core.project import ProjectFacts, ProjectReader, resolves_inside

logger = structlog.get_logger(__name__)

#: The `ingest_state` key the facts are stored under — a pack-owned key (step 7, H3).
GO_MODULE_KEY = "go_module"
GO_MOD = "go.mod"

_BLOCK_DIRECTIVES = ("require", "replace")


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


def _directives(text: str) -> list[tuple[str, list[str]]]:
    """(directive, fields) per entry, comments dropped and blocks flattened."""
    entries: list[tuple[str, list[str]]] = []
    block: str | None = None
    for raw in text.splitlines():
        fields = raw.split("//", 1)[0].split()
        if not fields:
            continue
        if block is not None:
            if fields == [")"]:
                block = None
            else:
                entries.append((block, fields))
        elif fields[0] in _BLOCK_DIRECTIVES and fields[1:] == ["("]:
            block = fields[0]
        else:
            entries.append((fields[0], fields[1:]))
    return entries


def _internal_replace(target: list[str], go_mod: Path, root: Path) -> str | None:
    """The replacement's directory relative to `root`, when it is a local path inside it."""
    if len(target) != 1 or not target[0].startswith((".", "/")):
        return None  # `=> module version` replaces with another module
    directory = (go_mod.parent / target[0]).resolve()
    try:
        return directory.relative_to(root.resolve()).as_posix()
    except ValueError:
        return None  # outside the repository: not ours


def parse_go_mod(text: str, go_mod: Path, root: Path) -> dict[str, object]:
    """The module path, required module paths, and replaces that point inside `root`.

    `offset` is where `root` sits under the module, dotted — `internal` for
    `cgis ingest internal/` — since node ids are relative to `root` while import
    paths are relative to the module (7b review I2).
    """
    path = ""
    requires: set[str] = set()
    replaces: dict[str, str] = {}
    for directive, fields in _directives(text):
        if directive == "module" and fields:
            path = fields[0].strip('"')
        elif directive == "require" and fields:
            requires.add(fields[0])
        elif directive == "replace" and "=>" in fields:
            arrow = fields.index("=>")
            local = _internal_replace(fields[arrow + 1 :], go_mod, root)
            if arrow and local is not None:
                replaces[fields[0]] = local
    try:
        offset = root.resolve().relative_to(go_mod.parent.resolve()).as_posix()
    except ValueError:
        offset = "."
    return {
        "path": path,
        "requires": sorted(requires),
        "replaces": replaces,
        "offset": "" if offset == "." else offset.replace("/", "."),
    }


class GoProjectReader(ProjectReader):
    """Reads the `go.mod` at the ingest root, or the nearest above it."""

    def __init__(self, root: Path) -> None:
        """Collect for the project at `root`."""
        self._root = root
        self._go_mod: Path | None = None

    @override
    def note(self, path: Path) -> bool:
        """Claim every `go.mod`; only the root's is this module's (a nested one is another).

        The root's file is read only when it resolves there: a PR can commit a
        `go.mod` as a symlink, and the ingest must not read a file the checkout does
        not hold (#732).
        """
        if path.name != GO_MOD:
            return False
        if path.parent.resolve() == self._root.resolve():
            if resolves_inside(path, self._root):
                self._go_mod = path
            else:
                logger.warning("go.mod outside the ingest root, skipping", path=str(path))
        return True

    @override
    def note_above(self, directories: list[Path], bounded: bool) -> None:
        """The nearest `go.mod` above, unless the root has its own: Go takes the nearest.

        Only the nearest is ever read, so an unbounded walk adds no risk.
        """
        if self._go_mod is not None or (self._root / GO_MOD).is_file():
            return
        self._go_mod = next((d / GO_MOD for d in directories if (d / GO_MOD).is_file()), None)
        if self._go_mod is not None:
            logger.info("Reading go.mod above the ingest root", path=str(self._go_mod))

    @override
    def facts(self) -> GoFacts:
        """What the module's `go.mod` says, once the walk is done."""
        if self._go_mod is None or not self._go_mod.is_file():
            return GoFacts()
        text = self._go_mod.read_text(encoding="utf-8", errors="replace")
        return GoFacts(parse_go_mod(text, self._go_mod, self._root))
