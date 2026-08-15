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
import hashlib
from collections.abc import Callable
from pathlib import Path, PureWindowsPath

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


def resolve_active_providers(finder_provider: str, skeptic_provider: str | None) -> frozenset[str]:
    """The union of the finder's and (if present) the skeptic's provider names.

    Spec §3.3: a skeptic on a different provider from the finder genuinely
    shapes the review and belongs in the digest; a skeptic on the same
    provider or no skeptic at all contributes nothing beyond the finder.

    Extracted (final review, #375) because `scripts/guardian_review.py` and
    `scripts/guardian_martian.py` each computed this set inline with the same
    expression — a duplication that drifting apart would make the two paths
    hash different sets for what should be the same reviewer.
    """
    return frozenset({finder_provider} | ({skeptic_provider} if skeptic_provider else set()))


def _module_path(module: str, read: ReadFile) -> str | None:
    """The repo-relative file for a module name, or None when it is not one."""
    stem = module.replace(".", "/")
    for candidate in (f"src/{stem}.py", f"src/{stem}/__init__.py"):
        if read(candidate) is not None:
            return candidate
    return None


def _is_pruned_provider(module: str, active: frozenset[str], known: frozenset[str]) -> bool:
    """True for a concrete, *known* provider module that did not produce this review.

    `known` — the provider names whose module actually exists (`gemini`,
    `mistral`, `ollama`) — gates the prune. Without it, any non-provider module
    placed under `providers/` (a retry policy, a usage accumulator, a prompt
    adapter shared by every provider) reads as an unselected provider and is
    silently dropped, even though every review reads it. Consulting `known`
    means only a module whose *leaf* names an actual provider can be pruned; a
    shared helper's leaf never does, so it always survives. A newly added
    provider module joins everyone's closure — widening, the acceptable
    direction — until it is selected, and fails loud via `UnknownProviderError`
    if `active` ever names a provider absent from `known` (#375 final review).
    """
    if not module.startswith(f"{_PROVIDER_PACKAGE}."):
        return False
    if module == _PROVIDER_BASE:
        return False
    leaf = module[len(_PROVIDER_PACKAGE) + 1 :].split(".", maxsplit=1)[0]
    return leaf in known and leaf not in active


def _resolve_relative_module(package: str, level: int, module: str | None) -> str | None:
    """Absolute dotted name for a relative import, or None if it escapes the tree.

    Mirrors `importlib._bootstrap._resolve_name`: `level=1` means `package`
    itself, `level=2` its parent, and so on. `package` must already be the
    *importing package* — see `_imported_modules` for the module-vs-package
    distinction that feeds into it — not the importing module.
    """
    bits = package.rsplit(".", level - 1)
    if len(bits) < level:
        return None
    base = bits[0]
    return f"{base}.{module}" if module else base


def _imported_modules(source: bytes, module: str, is_package: bool) -> list[str]:
    """Every `cgis.*` module named by an import in this source.

    Submodule attributes are included as candidates (`from cgis.x import y`
    yields `cgis.x.y`); `_module_path` discards the ones that are not modules.

    `module` is the dotted name of the file this source came from, and
    `is_package` says whether that file is an `__init__.py`. Both feed
    relative-import resolution: a package's own name *is* the package a
    `level=1` import counts from, while a plain module's package is its name
    minus its own last component — the same asymmetry `importlib` applies at
    runtime. Without this, `from .base import X` or `from ..core import Y`
    would silently vanish instead of resolving, because a relative import's
    `node.module` never starts with `"cgis"`.
    """
    package = module if is_package else module.rsplit(".", 1)[0]
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names if alias.name.startswith("cgis"))
        elif isinstance(node, ast.ImportFrom):
            resolved = (
                _resolve_relative_module(package, node.level, node.module)
                if node.level
                else node.module
            )
            if resolved is None or not resolved.startswith("cgis"):
                continue
            found.append(resolved)
            found.extend(f"{resolved}.{alias.name}" for alias in node.names)
    return found


def walk_closure(read: ReadFile, active_providers: frozenset[str]) -> list[str]:
    """Sorted repo-relative paths of every module a review can read.

    `active_providers` is the union of the finder's and the skeptic's provider
    names. Unselected providers are pruned **during** the walk, not filtered
    afterwards: `runner.py` imports all three at module level, so a full
    traversal minus a set would still have reached what an unselected provider
    imports.
    """
    known = frozenset(
        name
        for name in ("gemini", "mistral", "ollama")
        if _module_path(f"{_PROVIDER_PACKAGE}.{name}", read) is not None
    )
    unknown = active_providers - known
    if unknown:
        _msg = f"Active provider names no module: {sorted(unknown)}. Known: {sorted(known)}."
        raise UnknownProviderError(_msg)

    seen: set[str] = set()
    paths: set[str] = set()
    queue = list(SEEDS)
    while queue:
        module = queue.pop()
        if module in seen or _is_pruned_provider(module, active_providers, known):
            continue
        seen.add(module)
        path = _module_path(module, read)
        if path is None:
            continue
        source = read(path)
        if source is None:  # pragma: no cover - _module_path just read it
            continue
        paths.add(path)
        is_package = path.endswith("/__init__.py")
        queue.extend(_imported_modules(source, module, is_package))
        # Python executes every ancestor package's __init__.py on import, not
        # only the modules an `import` statement names. A hand-maintained
        # exclusion of this would be the unrecoverable silent-merge direction
        # (#375 final review): code that runs on every review must move the
        # digest, and `cgis/__init__.py` (an `importlib.metadata.version`
        # lookup) and `cgis/guardian/__init__.py` both do.
        parts = module.split(".")
        queue.extend(".".join(parts[:i]) for i in range(1, len(parts)))
    return sorted(paths)


#: The scheme, including its truncation width. The two are one unit: identical
#: code under a different width emits a value that looks different with nothing
#: in the record to say why, so changing either requires bumping this string.
SCHEME = b"cgis-review-fingerprint/v1\0"

#: Hex characters kept. 48 bits: collision probability N^2/2^49, about 2e-7 at
#: ten thousand distinct review-path states against a realistic N in the dozens.
DIGEST_CHARS = 12


class CarriageReturnError(RuntimeError):
    """Raised when a closure file contains CRLF.

    Refused rather than folded to LF. Folding is a normaliser and its way of
    being wrong is to merge — a source file whose string literal legitimately
    carries CRLF would hash as though it did not. A stray line ending is a
    misconfigured checkout, and repairing it silently lets that checkout go on
    producing subtly different artefacts elsewhere (spec §4.1.1).
    """


class BrokenReaderError(RuntimeError):
    """Raised when a reader serves `None` for a path the walk already vouched for.

    `walk_closure` reads every path it returns before returning it, so a
    `None` here is not a missing file — it is a reader that failed between
    the walk and the hash (a transient `git show` failure, for one). Skipping
    it would silently drop a behaviourally significant file out of the
    preimage, merging two reviewers that actually differ into one identity;
    refusing is the same choice this module already makes for `CarriageReturnError`.
    """


def disk_reader(root: Path) -> ReadFile:
    """A reader over a working tree rooted at `root`.

    `root` is explicit and never the CWD: a review spends most of its time
    standing in a checkout of somebody else's repository, where a relative read
    would return a wrong value that looks entirely right.

    An absolute `path` is refused rather than answered. `root / path` in
    pathlib silently discards `root` for one, so the reader would read a real
    file from somewhere else entirely — and `None`, its word for "absent",
    cannot carry that meaning. Every path here is built internally from a
    module name and is therefore relative by construction, so a caller passing
    an absolute one has made a mistake this must not absorb (#385).

    The check judges the string as both a POSIX and a Windows path rather than
    asking the host. `Path(path).is_absolute()` is host-dependent: on Linux it
    reads `C:/x` as relative, so a drive-letter path would slip past on the very
    machine this runs on, while the same string on Windows would discard `root`.
    A guard whose coverage depends on where it executes is the shape this
    codebase keeps finding and removing (#386).
    """

    def read(path: str) -> bytes | None:
        if path.startswith(("/", "\\")) or PureWindowsPath(path).is_absolute():
            _msg = f"Reader paths are repo-relative; {path!r} is absolute."
            raise ValueError(_msg)
        target = root / path
        return target.read_bytes() if target.is_file() else None

    return read


def compute_fingerprint(read: ReadFile, active_providers: frozenset[str]) -> str:
    """The digest over everything this review could read.

    Raises `BrokenReaderError` if `read` returns `None` for a path
    `walk_closure` already read successfully, and `CarriageReturnError` if a
    closure file contains CRLF.
    """
    digest = hashlib.sha256()
    digest.update(SCHEME)
    for path in walk_closure(read, active_providers):
        content = read(path)
        if content is None:
            _msg = f"Reader returned no content for {path}, though the closure walk read it."
            raise BrokenReaderError(_msg)
        if b"\r\n" in content:
            _msg = f"CRLF line endings in {path}; the checkout must use LF."
            raise CarriageReturnError(_msg)
        digest.update(path.encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()[:DIGEST_CHARS]
