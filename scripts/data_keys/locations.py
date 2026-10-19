"""Where the data-key measurement scripts find their inputs: no machine's paths in code.

Each location reads an environment variable first, then falls back to a default
derived from the home directory or from where this checkout lives:

- ``CGIS_DATA_KEYS_CORPUS``: exported trees, clones and scratch databases
  (default ``~/bench-corpora/data-keys``).
- ``CGIS_OWNER_API``: the owner-api checkout, read with ``git`` only, never checked out
  (default: ``owner-api`` beside this checkout).
"""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _from_env(name: str, default: Path) -> Path:
    """The path in environment variable `name`, else `default`."""
    value = os.environ.get(name)
    return Path(value).expanduser() if value else default


CORPUS = _from_env("CGIS_DATA_KEYS_CORPUS", Path.home() / "bench-corpora" / "data-keys")
OWNER_API = _from_env("CGIS_OWNER_API", REPO_ROOT.parent / "owner-api")
