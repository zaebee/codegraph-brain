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


def resolve_rev(repo: Path, rev: str) -> str:
    """The commit sha `rev` names; `DiffError` when it names none.

    Revisions arrive from outside (the MCP tool), so nothing but a sha reaches
    another git command: `--end-of-options` keeps `rev` from being read as a flag.
    """
    try:
        return _git(repo, "rev-parse", "--verify", "--end-of-options", f"{rev}^{{commit}}").strip()
    except DiffError as exc:
        message = f"no commit named {rev!r}: {exc}"
        raise DiffError(message) from exc


def toplevel(repo: Path) -> Path:
    """The working tree root of the repository holding `repo`; `DiffError` outside one."""
    return Path(_git(repo, "rev-parse", "--show-toplevel").strip()).resolve()


def read_at(repo: Path, sha: str, path: str) -> str:
    """The text of `path` at the commit `sha` (`git show sha:path`)."""
    return _git(repo, "show", "--end-of-options", f"{sha}:{path}")


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
        self.in_hunks = False
        # The header covers a pure rename or mode change, which has no ---/+++
        # lines; a space in a path makes the split ambiguous, so later lines win.
        parts = header.split(" b/", 1)
        if len(parts) == 2 and parts[0].startswith("a/"):
            self.old, self.new = parts[0][2:], parts[1]

    def feed(self, line: str) -> None:
        """Take one line of the section."""
        if m := _HUNK.match(line):
            self.in_hunks = True
            self.old_lines.update(_span(m.group(1), m.group(2)))
            self.new_lines.update(_span(m.group(3), m.group(4)))
        elif self.in_hunks:
            return  # content: a removed "-- x" line reads as "--- x", and is not a header
        elif line.startswith("new file mode "):
            # An empty or binary file has no ---/+++ lines to say /dev/null.
            self.old = None
        elif line.startswith("deleted file mode "):
            self.new = None
        elif line.startswith("--- "):
            self.old = _strip(line[4:])
        elif line.startswith("+++ "):
            self.new = _strip(line[4:])
        elif line.startswith("rename from "):
            self.old = line[len("rename from ") :]
        elif line.startswith("rename to "):
            self.new = line[len("rename to ") :]

    def done(self) -> FileChange:
        """The finished change."""
        return FileChange(self.old, self.new, frozenset(self.old_lines), frozenset(self.new_lines))


def changed_files(repo: Path, base: str, head: str) -> list[FileChange]:
    """Every file changed between `base` and `head`, with its changed line numbers.

    Renames are followed (`-M`). An added or deleted file with no text hunks (empty,
    binary) has no ---/+++ lines; its `new file mode` / `deleted file mode` line
    clears the missing side. Raises `DiffError` with git's message when
    `repo` is not a repository or a revision does not exist.
    """
    text = _git(
        repo,
        "diff",
        "-M",
        "--unified=0",
        "--no-color",
        "--no-ext-diff",
        # The user's config must not reshape what is parsed: `diff.noprefix` or
        # `diff.mnemonicPrefix` would change the `a/`/`b/` that `_strip` cuts, and
        # a textconv filter would rewrite the lines the hunks number.
        "--src-prefix=a/",
        "--dst-prefix=b/",
        "--no-textconv",
        "--end-of-options",
        f"{resolve_rev(repo, base)}..{resolve_rev(repo, head)}",
    )
    blocks: list[_Block] = []
    for line in text.splitlines():
        if line.startswith("diff --git "):
            blocks.append(_Block(line[len("diff --git ") :]))
        elif blocks:
            blocks[-1].feed(line)
    return [block.done() for block in blocks]
