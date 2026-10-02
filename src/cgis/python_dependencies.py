"""The third-party import roots a Python project declares in its manifests (#495).

The graph alone cannot tell `from alembic import op` from the project's own
`app/alembic/` package: both roots are internal (a directory of that name exists)
and both look external (imports under them fail to reach a node). The manifest is
the evidence that decides it — `alembic>=1.12.1` is a declared dependency, while
the project's `scripts` package is not.

Distribution names are mapped to import names by normalisation plus a short table
of well-known exceptions (`python-dateutil` -> `dateutil`). That is approximate
without the installed environment; a miss leaves a root exactly as it was before.
"""

import re
import tomllib
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import structlog

logger = structlog.getLogger(__name__)

#: The manifest a modern Python project declares its dependencies in.
PYPROJECT = "pyproject.toml"
#: The pip requirement files read beside it: `requirements.txt`, `requirements-dev.txt`, …
_REQUIREMENTS_GLOB = "requirements*.txt"
#: What marks a repository boundary the upward search never crosses.
_REPOSITORY_MARKER = ".git"

#: A PEP 508 requirement's distribution name, at the start of the string.
_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
#: Where a requirement's leading token ends: whitespace, extras, a marker or a specifier.
_REQUIREMENT_TOKEN_END = re.compile(r"[\s;@\[<>=!~,]")
#: Characters no distribution name holds, but a path or URL does: `sub/pkg`, `C:\\libs`.
_PATH_CHARACTERS = frozenset("/\\:")
#: Runs PEP 503 folds together, spelled with `_` because that is how imports spell them.
_NAME_SEPARATORS = re.compile(r"[-_.]+")

#: Distributions whose import name is not their normalised distribution name.
_IMPORT_NAMES: dict[str, str] = {
    "beautifulsoup4": "bs4",
    "gitpython": "git",
    "grpcio": "grpc",
    "opencv_python": "cv2",
    "opencv_python_headless": "cv2",
    "pillow": "PIL",
    "protobuf": "google",
    "psycopg2_binary": "psycopg2",
    "pyjwt": "jwt",
    "pymupdf": "fitz",
    "python_dateutil": "dateutil",
    "python_dotenv": "dotenv",
    "python_jose": "jose",
    "python_multipart": "multipart",
    "pyyaml": "yaml",
    "scikit_learn": "sklearn",
}


def declared_import_roots(workspace_root: Path) -> frozenset[str]:
    """The import roots of the dependencies declared for the project at `workspace_root`.

    The manifests are those of the nearest directory, from the ingest root upwards,
    that has any: the root is often a subdirectory of the project (`cgis ingest
    ownima-backend/app` against `ownima-backend/pyproject.toml`). The search stops
    at a repository boundary, so a checkout nested in another never reads its
    parent's dependencies. No manifest means no roots, which changes nothing.
    """
    directory = _manifest_directory(workspace_root)
    if directory is None:
        return frozenset()
    names: set[str] = set()
    pyproject = directory / PYPROJECT
    if pyproject.is_file():
        names.update(_pyproject_requirements(pyproject))
    for requirements in sorted(directory.glob(_REQUIREMENTS_GLOB)):
        names.update(_requirements_file(requirements))
    return frozenset(import_name(name) for name in names)


def import_name(distribution: str) -> str:
    """The top-level import name a distribution most likely provides."""
    normalised = _NAME_SEPARATORS.sub("_", distribution).lower()
    return _IMPORT_NAMES.get(normalised, normalised)


def _manifest_directory(workspace_root: Path) -> Path | None:
    """The nearest directory at or above `workspace_root` holding a Python manifest."""
    for directory in (workspace_root, *workspace_root.parents):
        if (directory / PYPROJECT).is_file() or any(directory.glob(_REQUIREMENTS_GLOB)):
            return directory
        if (directory / _REPOSITORY_MARKER).exists():
            return None
    return None


def _pyproject_requirements(path: Path) -> Iterator[str]:
    """Distribution names from PEP 621, PEP 735 and Poetry tables of a `pyproject.toml`.

    An unreadable file is skipped with a warning: one bad manifest must not stop
    the ingest, and without it the classification is what it was before #495.
    """
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        logger.warning("Skipping unreadable pyproject.toml", path=str(path), error=str(exc))
        return
    project = _table(data.get("project"))
    yield from _requirement_names(project.get("dependencies"))
    for extra in _table(project.get("optional-dependencies")).values():
        yield from _requirement_names(extra)
    for group in _table(data.get("dependency-groups")).values():
        yield from _requirement_names(group)
    poetry = _table(_table(data.get("tool")).get("poetry"))
    yield from _poetry_names(poetry.get("dependencies"))
    yield from _poetry_names(poetry.get("dev-dependencies"))
    for group in _table(poetry.get("group")).values():
        yield from _poetry_names(_table(group).get("dependencies"))


def _requirements_file(path: Path) -> Iterator[str]:
    """Distribution names from a pip requirements file; options and paths are skipped."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Skipping unreadable requirements file", path=str(path), error=str(exc))
        return
    for line in lines:
        requirement = line.split("#", maxsplit=1)[0].strip()
        # `-r other.txt`, `-e .`, `--index-url …`, and bare paths or URLs name no package;
        # `pkg @ https://…` does, so only the leading token is checked for a path.
        token = _REQUIREMENT_TOKEN_END.split(requirement, maxsplit=1)[0]
        if token and not token.startswith(("-", ".")) and not _PATH_CHARACTERS & set(token):
            yield from _requirement_names([requirement])


def _requirement_names(requirements: object) -> Iterator[str]:
    """The distribution name of each PEP 508 string in a list; anything else is skipped."""
    if not isinstance(requirements, list):
        return
    for requirement in requirements:
        if isinstance(requirement, str) and (match := _REQUIREMENT_NAME.match(requirement)):
            yield match.group(1)


def _poetry_names(dependencies: object) -> Iterable[str]:
    """The keys of a Poetry dependency table, minus the interpreter constraint."""
    return (name for name in _table(dependencies) if name.lower() != "python")


def _table(value: object) -> dict[str, Any]:
    """`value` when it is a TOML table, else empty: a malformed manifest reads as silent."""
    return value if isinstance(value, dict) else {}
