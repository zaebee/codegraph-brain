"""Where the data-key measurement scripts find their inputs: no machine's paths in code.

Each location reads an environment variable first, then falls back to a default
derived from the home directory or from where this checkout lives:

- ``CGIS_DATA_KEYS_CORPUS``: exported trees, clones and scratch databases
  (default ``~/bench-corpora/data-keys``).
- ``CGIS_OWNER_API``: the owner-api checkout, read with ``git`` only, never checked out
  (default: ``owner-api`` beside this repository's main checkout, also when this runs from a
  linked worktree such as ``.claude/worktrees/<name>``).
"""

import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _from_env(name: str, default: Path) -> Path:
    """The path in environment variable `name`, else `default`."""
    value = os.environ.get(name)
    return Path(value).expanduser() if value else default


def checkout_root(root: Path) -> Path:
    """The main checkout of the repository at `root`: `root` itself, or, for a linked
    worktree, the checkout whose `.git/worktrees/<name>` its `.git` file points at."""
    marker = root / ".git"
    if not marker.is_file():
        return root
    gitdir = Path(marker.read_text(encoding="utf-8").removeprefix("gitdir:").strip())
    return gitdir.parent.parent.parent if gitdir.parent.name == "worktrees" else root


CORPUS = _from_env("CGIS_DATA_KEYS_CORPUS", Path.home() / "bench-corpora" / "data-keys")
OWNER_API = _from_env("CGIS_OWNER_API", checkout_root(REPO_ROOT).parent / "owner-api")


#: A GitHub repository as ``owner/name``: what ``gh --repo`` and ``repos/<repo>/…`` take.
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")

#: One argument to git or gh: a path, a sha, a revision. Never an option (no leading
#: ``-``), no whitespace or shell characters — a value from the command line that reached
#: a subprocess as ``--upload-pack=…`` would be a flag, not data (pythonsecurity:S8705,
#: as in ``scripts/check_test_count.py``).
_ARG = re.compile(r"(?!-)[A-Za-z0-9_./~^@:+=-]+")


def checked_repo(repo: str) -> str:
    """``repo`` unchanged when it is ``owner/name``; otherwise the run stops."""
    if not _REPO.fullmatch(repo):
        message = f"{repo!r} is not a GitHub repository as owner/name"
        raise SystemExit(message)
    return repo


def checked_arg(value: str) -> str:
    """``value`` unchanged when it cannot read as an option; otherwise the run stops."""
    if not _ARG.fullmatch(value):
        message = f"{value!r} could reach git as an option or hold shell characters; refused"
        raise SystemExit(message)
    return value
