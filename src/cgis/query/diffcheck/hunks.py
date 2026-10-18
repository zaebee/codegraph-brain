"""The files and lines a git range changed, read from `git diff -U0`."""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

#: `@@ -12,3 +14 @@` -> old start/count, new start/count (a count left out means 1).
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class DiffError(Exception):
    """Git could not produce the diff; the message is git's own."""


@dataclass(frozen=True)
class FileChange:
    """One file of the diff: where it was, where it is, and which lines moved.

    `old_path` is None for an added file, `new_path` None for a deleted one.
    `old_lines` are line numbers in the base text that were removed or rewritten,
    `new_lines` those in the head text that were added or rewritten.
    """

    old_path: str | None
    new_path: str | None
    old_lines: frozenset[int]
    new_lines: frozenset[int]


def _git(repo: Path, *args: str) -> str:
    """Run git in `repo` and return stdout; raise `DiffError` with git's message."""
    try:
        done = subprocess.run(
            ["git", "-c", "core.quotepath=off", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise DiffError(str(exc)) from exc
    if done.returncode != 0:
        raise DiffError(done.stderr.strip() or f"git {args[0]} failed")
    return done.stdout


def read_at(repo: Path, rev: str, path: str) -> str:
    """The text of `path` at `rev` (`git show rev:path`)."""
    return _git(repo, "show", f"{rev}:{path}")


def _span(start: str, count: str | None) -> range:
    """The line numbers a hunk side covers; an empty range for a count of 0."""
    first = int(start)
    return range(first, first + (1 if count is None else int(count)))


def _strip(path: str) -> str | None:
    """A `--- a/x` / `+++ b/x` path without its prefix; None for /dev/null."""
    if path == "/dev/null":
        return None
    return path[2:]


class _Block:
    """The file section of the diff being read."""

    def __init__(self, header: str) -> None:
        """Start from the `diff --git` header's paths; later lines refine them."""
        self.old: str | None = None
        self.new: str | None = None
        self.old_lines: set[int] = set()
        self.new_lines: set[int] = set()
        # The header covers a pure rename or mode change, which has no ---/+++
        # lines; a space in a path makes the split ambiguous, so later lines win.
        parts = header.split(" b/", 1)
        if len(parts) == 2 and parts[0].startswith("a/"):
            self.old, self.new = parts[0][2:], parts[1]

    def feed(self, line: str) -> None:
        """Take one line of the section."""
        if line.startswith("--- "):
            self.old = _strip(line[4:])
        elif line.startswith("+++ "):
            self.new = _strip(line[4:])
        elif line.startswith("rename from "):
            self.old = line[len("rename from ") :]
        elif line.startswith("rename to "):
            self.new = line[len("rename to ") :]
        elif m := _HUNK.match(line):
            self.old_lines.update(_span(m.group(1), m.group(2)))
            self.new_lines.update(_span(m.group(3), m.group(4)))

    def done(self) -> FileChange:
        """The finished change."""
        return FileChange(self.old, self.new, frozenset(self.old_lines), frozenset(self.new_lines))


def changed_files(repo: Path, base: str, head: str) -> list[FileChange]:
    """Every file changed between `base` and `head`, with its changed line numbers.

    Renames are followed (`-M`). Raises `DiffError` with git's message when
    `repo` is not a repository or a revision does not exist.
    """
    text = _git(
        repo,
        "diff",
        "-M",
        "--unified=0",
        "--no-color",
        "--no-ext-diff",
        f"{base}..{head}",
    )
    blocks: list[_Block] = []
    for line in text.splitlines():
        if line.startswith("diff --git "):
            blocks.append(_Block(line[len("diff --git ") :]))
        elif blocks:
            blocks[-1].feed(line)
    return [block.done() for block in blocks]
