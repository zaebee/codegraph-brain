"""An identity for a reviewer, derived from what a review can read (#375).

`guardian_sha` pins the whole repository, so it moves on commits a review never
sees. This module derives the set that matters instead of declaring it: the
transitive import closure of the review entry points. A declared list is
hand-maintained, and its failure mode is the unrecoverable one — a module joins
the review path, nobody updates the list, and the digest stops moving while
behaviour changes.

Nothing inside the closure may import this module. If it did, the hasher would
enter its own hashed set and editing it would split identities without changing
a single review. `tests/unit/test_review_fingerprint_contract.py` enforces that.
"""

import ast
from collections.abc import Callable

#: Repo-relative path in, file contents out, None when the path does not exist.
#: Injected rather than assumed so the live run (disk) and the backfill
#: (`git show <sha>:<path>`) share one traversal instead of two implementations
#: kept equal by hand.
ReadFile = Callable[[str], bytes | None]

#: The entry points of a review. `runner` is a seed even though it drags in the
#: output side (github_poster, metrics, recording, render): excluding those needs
#: a hand-maintained exclusion list, and a wrong exclusion is a silent merge.
#: Measured cost, 3 commits of 275 (spec §3.2).
SEEDS: tuple[str, ...] = (
    "cgis.guardian.core",
    "cgis.guardian.collector",
    "cgis.guardian.axes",
    "cgis.guardian.chunked",
    "cgis.guardian.runner",
)

_PROVIDER_PACKAGE = "cgis.guardian.providers"

#: Always in: every provider imports it, and it holds the retry and usage
#: behaviour shared by all of them.
_PROVIDER_BASE = f"{_PROVIDER_PACKAGE}.base"


class UnknownProviderError(RuntimeError):
    """Raised when an active provider names no module.

    Refused rather than ignored: an unknown name would silently prune the whole
    provider layer out of the closure and return a confident wrong digest.
    """


def _module_path(module: str, read: ReadFile) -> str | None:
    """The repo-relative file for a module name, or None when it is not one."""
    stem = module.replace(".", "/")
    for candidate in (f"src/{stem}.py", f"src/{stem}/__init__.py"):
        if read(candidate) is not None:
            return candidate
    return None


def _is_pruned_provider(module: str, active: frozenset[str]) -> bool:
    """True for a concrete provider module that did not produce this review."""
    if not module.startswith(f"{_PROVIDER_PACKAGE}."):
        return False
    if module == _PROVIDER_BASE:
        return False
    leaf = module[len(_PROVIDER_PACKAGE) + 1 :].split(".", maxsplit=1)[0]
    return leaf not in active


def _imported_modules(source: bytes) -> list[str]:
    """Every `cgis.*` module named by an import in this source.

    Submodule attributes are included as candidates (`from cgis.x import y`
    yields `cgis.x.y`); `_module_path` discards the ones that are not modules.
    """
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names if alias.name.startswith("cgis"))
        elif isinstance(node, ast.ImportFrom):
            if node.module is None or not node.module.startswith("cgis"):
                continue
            found.append(node.module)
            found.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def walk_closure(read: ReadFile, active_providers: frozenset[str]) -> list[str]:
    """Sorted repo-relative paths of every module a review can read.

    `active_providers` is the union of the finder's and the skeptic's provider
    names. Unselected providers are pruned **during** the walk, not filtered
    afterwards: `runner.py` imports all three at module level, so a full
    traversal minus a set would still have reached what an unselected provider
    imports.
    """
    known = {
        name
        for name in ("gemini", "mistral", "ollama")
        if _module_path(f"{_PROVIDER_PACKAGE}.{name}", read) is not None
    }
    unknown = active_providers - known
    if unknown:
        _msg = f"Active provider names no module: {sorted(unknown)}. Known: {sorted(known)}."
        raise UnknownProviderError(_msg)

    seen: set[str] = set()
    paths: set[str] = set()
    queue = list(SEEDS)
    while queue:
        module = queue.pop()
        if module in seen or _is_pruned_provider(module, active_providers):
            continue
        seen.add(module)
        path = _module_path(module, read)
        if path is None:
            continue
        source = read(path)
        if source is None:  # pragma: no cover - _module_path just read it
            continue
        paths.add(path)
        queue.extend(_imported_modules(source))
    return sorted(paths)
