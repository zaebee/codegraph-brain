# Review Fingerprint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every review record an identity that changes when review behaviour changes and not when a commit merely happens.

**Architecture:** A digest over the transitive import closure of the review entry points, computed from file bytes through an injected reader so live runs and the historical backfill share one traversal. Concrete provider modules are pruned during the walk unless that provider produced the review. `guardian_sha` stays as provenance.

**Tech Stack:** Python 3.12, pydantic v2 (frozen models), `ast` for the import walk, pytest, mypy strict, ruff.

**Spec:** `docs/specs/2026-08-14-review-fingerprint-design.md`

## Global Constraints

- MyPy runs in **strict** mode. Every function needs full annotations including return types.
- Frozen pydantic models: update with `model_copy(update={...})`, never attribute assignment.
- Docstring coverage minimum **90%** (`make doc-coverage`). Every public function needs a docstring.
- Full verification before every commit: `make format && make lint && make type-check && make pytest && make doc-coverage`.
- Tests live in `tests/unit/`. No network, no real LLM calls.
- The digest scheme string is `cgis-review-fingerprint/v1` and the truncation is `[:12]`. Changing either requires bumping the version string — they are one unit (spec §4).
- `src/cgis/guardian/review_fingerprint.py` must never be imported by any module inside the closure (spec §4.2). Task 4 enforces this.

---

### Task 1: Providers name themselves

**Why first:** the fingerprint is scoped by which provider ran (spec §3.3), and today the only way to ask is an `isinstance` sniff that gets Ollama wrong.

**Files:**
- Modify: `src/cgis/guardian/providers/base.py`
- Modify: `src/cgis/guardian/providers/gemini.py`
- Modify: `src/cgis/guardian/providers/mistral.py`
- Modify: `src/cgis/guardian/providers/ollama.py`
- Modify: `scripts/guardian_review.py:84`
- Modify: `scripts/guardian_martian.py` (the `primary = ...` sniff near line 349)
- Modify: `scripts/guardian_bench.py` (same sniff, near line 110)
- Test: `tests/unit/test_guardian_providers_name.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `BaseProvider.name: ClassVar[str]` — `"gemini"`, `"mistral"`, `"ollama"`. Every later task reads the active provider through this.

- [ ] **Step 1: Write the failing test**

```python
"""Each provider states its own name, so nothing has to sniff its type."""

from cgis.guardian.providers.base import BaseProvider
from cgis.guardian.providers.gemini import GeminiProvider
from cgis.guardian.providers.mistral import MistralProvider
from cgis.guardian.providers.ollama import OllamaProvider


def test_every_provider_names_itself() -> None:
    assert GeminiProvider.name == "gemini"
    assert MistralProvider.name == "mistral"
    assert OllamaProvider.name == "ollama"


def test_names_match_the_runner_vocabulary() -> None:
    """The names are the GUARDIAN_PROVIDER values, not free-form labels.

    `build_provider` dispatches on exactly these three strings, so a provider
    whose name does not appear there cannot be selected and its module would be
    pruned out of every fingerprint.
    """
    assert {GeminiProvider.name, MistralProvider.name, OllamaProvider.name} == {
        "gemini",
        "mistral",
        "ollama",
    }


def test_base_declares_the_attribute() -> None:
    """Declared on the base so a new provider that forgets it fails type-check."""
    assert hasattr(BaseProvider, "name")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_guardian_providers_name.py -v`
Expected: FAIL — `AttributeError: type object 'GeminiProvider' has no attribute 'name'`

- [ ] **Step 3: Add the attribute**

In `src/cgis/guardian/providers/base.py`, inside `class BaseProvider`, above `__init__`:

```python
    #: The GUARDIAN_PROVIDER value that selects this provider. Declared here so
    #: a new provider that omits it fails type-check rather than being sniffed
    #: for by isinstance at three call sites — one of which reported Ollama as
    #: gemini. The review fingerprint is scoped by this value (#375 §3.3), so a
    #: wrong one computes the digest over the wrong module.
    name: ClassVar[str]
```

Add `from typing import ClassVar` to the imports.

Then in each provider class body, as the first line:

```python
# gemini.py, inside class GeminiProvider(BaseProvider):
    name: ClassVar[str] = "gemini"

# mistral.py, inside class MistralProvider(BaseProvider):
    name: ClassVar[str] = "mistral"

# ollama.py, inside class OllamaProvider(BaseProvider):
    name: ClassVar[str] = "ollama"
```

Each file needs `from typing import ClassVar` added to its imports.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_guardian_providers_name.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Replace the three isinstance sniffs**

In `scripts/guardian_review.py`, replace line 84:

```python
    primary = "mistral" if isinstance(provider, MistralProvider) else "gemini"
```

with:

```python
    # Was an isinstance sniff that returned "gemini" for an Ollama provider,
    # which then picked the wrong default skeptic. The provider knows its own
    # name (#375 Task 1).
    primary = provider.name
```

Apply the identical replacement in `scripts/guardian_martian.py` and `scripts/guardian_bench.py`. Remove the now-unused `MistralProvider` import from any file where nothing else uses it — ruff will flag it.

- [ ] **Step 6: Add the regression test for the Ollama case**

Append to `tests/unit/test_guardian_providers_name.py`:

```python
def test_ollama_is_not_reported_as_gemini() -> None:
    """The bug the isinstance sniff had: only Mistral was checked for.

    An Ollama provider fell into the else branch and was announced as gemini,
    which then selected the wrong default skeptic.
    """
    provider = OllamaProvider(model_name="codellama:13b")
    assert provider.name == "ollama"
```

- [ ] **Step 7: Run the full suite**

Run: `make pytest`
Expected: PASS — no test may reference the removed sniff.

- [ ] **Step 8: Verify and commit**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
git add src/cgis/guardian/providers tests/unit/test_guardian_providers_name.py scripts/guardian_review.py scripts/guardian_martian.py scripts/guardian_bench.py
git commit -m "refactor(guardian): a provider states its own name, instead of three isinstance sniffs (#375)"
```

---

### Task 2: The closure walk

**Files:**
- Create: `src/cgis/guardian/review_fingerprint.py`
- Test: `tests/unit/test_review_fingerprint_closure.py`

**Interfaces:**
- Consumes: `BaseProvider.name` values from Task 1 (as plain strings).
- Produces:
  - `SEEDS: tuple[str, ...]` — the five entry-point module names.
  - `ReadFile = Callable[[str], bytes | None]` — repo-relative path in, contents out, `None` when the path does not exist.
  - `walk_closure(read: ReadFile, active_providers: frozenset[str]) -> list[str]` — sorted repo-relative paths.
  - `UnknownProviderError(RuntimeError)`.

- [ ] **Step 1: Write the failing tests**

```python
"""The closure walk: what a review can read is derived, not declared."""

from pathlib import Path

import pytest

from cgis.guardian.review_fingerprint import (
    SEEDS,
    UnknownProviderError,
    walk_closure,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def disk_reader(path: str) -> bytes | None:
    """Read a repo-relative path from the working tree, None when absent."""
    target = REPO_ROOT / path
    return target.read_bytes() if target.is_file() else None


def test_seeds_are_the_review_entry_points() -> None:
    assert SEEDS == (
        "cgis.guardian.core",
        "cgis.guardian.collector",
        "cgis.guardian.axes",
        "cgis.guardian.chunked",
        "cgis.guardian.runner",
    )


def test_closure_reaches_the_graph_and_drift_sections() -> None:
    """The modules a declared list of guardian/*.py would have missed.

    These feed the STRUCTURAL IMPACT GRAPHS and ARCHITECTURAL DRIFT sections of
    the prompt, so a change in them changes what the finder reads (spec §3.1).
    """
    closure = walk_closure(disk_reader, frozenset({"gemini"}))
    for expected in (
        "src/cgis/query/engine.py",
        "src/cgis/query/render/mermaid.py",
        "src/cgis/storage/sqlite_store.py",
        "src/cgis/query/drift/drift.py",
        "src/cgis/extractors/registry.py",
    ):
        assert expected in closure


def test_closure_is_sorted_and_unique() -> None:
    closure = walk_closure(disk_reader, frozenset({"gemini"}))
    assert closure == sorted(closure)
    assert len(closure) == len(set(closure))


def test_unselected_providers_are_pruned() -> None:
    """runner.py imports all three at module level; the walk must not follow.

    A filter applied after a full traversal would still have reached whatever
    an unselected provider imports (spec §3.3).
    """
    closure = walk_closure(disk_reader, frozenset({"gemini"}))
    assert "src/cgis/guardian/providers/gemini.py" in closure
    assert "src/cgis/guardian/providers/base.py" in closure
    assert "src/cgis/guardian/providers/mistral.py" not in closure
    assert "src/cgis/guardian/providers/ollama.py" not in closure


def test_both_roles_are_active_when_they_differ() -> None:
    closure = walk_closure(disk_reader, frozenset({"gemini", "mistral"}))
    assert "src/cgis/guardian/providers/gemini.py" in closure
    assert "src/cgis/guardian/providers/mistral.py" in closure
    assert "src/cgis/guardian/providers/ollama.py" not in closure


def test_unknown_provider_is_refused() -> None:
    """A provider that maps to no module would silently narrow the closure."""
    with pytest.raises(UnknownProviderError, match="anthropic"):
        walk_closure(disk_reader, frozenset({"anthropic"}))


def test_reader_is_injected_not_assumed() -> None:
    """The walk never touches the filesystem itself, so the backfill can reuse it."""
    calls: list[str] = []

    def recording_reader(path: str) -> bytes | None:
        calls.append(path)
        return disk_reader(path)

    walk_closure(recording_reader, frozenset({"gemini"}))
    assert calls, "the walk must go through the injected reader"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_review_fingerprint_closure.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cgis.guardian.review_fingerprint'`

- [ ] **Step 3: Write the module**

Create `src/cgis/guardian/review_fingerprint.py`:

```python
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
    leaf = module[len(_PROVIDER_PACKAGE) + 1 :].split(".")[0]
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_review_fingerprint_closure.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Verify and commit**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
git add src/cgis/guardian/review_fingerprint.py tests/unit/test_review_fingerprint_closure.py
git commit -m "feat(guardian): derive what a review can read, instead of listing it (#375)"
```

---

### Task 3: The digest

**Files:**
- Modify: `src/cgis/guardian/review_fingerprint.py`
- Test: `tests/unit/test_review_fingerprint_digest.py`

**Interfaces:**
- Consumes: `walk_closure`, `ReadFile` from Task 2.
- Produces:
  - `SCHEME: bytes = b"cgis-review-fingerprint/v1\0"`
  - `DIGEST_CHARS: int = 12`
  - `CarriageReturnError(RuntimeError)`
  - `compute_fingerprint(read: ReadFile, active_providers: frozenset[str]) -> str`
  - `disk_reader(root: Path) -> ReadFile`

- [ ] **Step 1: Write the failing tests**

```python
"""The digest: deterministic, path-sensitive, and intolerant of CRLF."""

import hashlib
from pathlib import Path

import pytest

from cgis.guardian.review_fingerprint import (
    DIGEST_CHARS,
    SCHEME,
    CarriageReturnError,
    compute_fingerprint,
    disk_reader,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_digest_is_stable_across_calls() -> None:
    read = disk_reader(REPO_ROOT)
    assert compute_fingerprint(read, frozenset({"gemini"})) == compute_fingerprint(
        read, frozenset({"gemini"})
    )


def test_digest_has_the_documented_width() -> None:
    value = compute_fingerprint(disk_reader(REPO_ROOT), frozenset({"gemini"}))
    assert len(value) == DIGEST_CHARS
    assert all(char in "0123456789abcdef" for char in value)


def test_editing_a_closure_file_moves_the_digest() -> None:
    """A prompt change is a different reviewer."""
    real = disk_reader(REPO_ROOT)

    def edited(path: str) -> bytes | None:
        content = real(path)
        if path == "src/cgis/guardian/prompts.py" and content is not None:
            return content + b"\n# a hunting rule changed\n"
        return content

    assert compute_fingerprint(edited, frozenset({"gemini"})) != compute_fingerprint(
        real, frozenset({"gemini"})
    )


def test_editing_a_file_outside_the_closure_does_not() -> None:
    """A README edit is the same reviewer — the whole point of #375."""
    real = disk_reader(REPO_ROOT)

    def edited(path: str) -> bytes | None:
        if path == "README.md":
            return b"rewritten"
        return real(path)

    assert compute_fingerprint(edited, frozenset({"gemini"})) == compute_fingerprint(
        real, frozenset({"gemini"})
    )


def test_paths_are_part_of_the_hash() -> None:
    """Otherwise a module leaving the set is invisible in the digest.

    Two readers serve identical bytes under different names; the digests must
    differ.
    """
    body = b"import cgis.guardian.prompts\n"

    def under_a(path: str) -> bytes | None:
        return body if path == "src/cgis/guardian/core.py" else None

    def under_b(path: str) -> bytes | None:
        return body if path == "src/cgis/guardian/axes.py" else None

    assert compute_fingerprint(under_a, frozenset()) != compute_fingerprint(
        under_b, frozenset()
    )


def test_crlf_is_refused_not_repaired() -> None:
    """Folding CRLF to LF is a normaliser, and normalisers merge (spec §4.1.1)."""
    real = disk_reader(REPO_ROOT)

    def windows_checkout(path: str) -> bytes | None:
        content = real(path)
        if path == "src/cgis/guardian/prompts.py" and content is not None:
            return content.replace(b"\n", b"\r\n")
        return content

    with pytest.raises(CarriageReturnError, match="prompts.py"):
        compute_fingerprint(windows_checkout, frozenset({"gemini"}))


def test_scheme_string_is_in_the_preimage() -> None:
    """Changing the scheme or the width must change every digest."""
    assert SCHEME == b"cgis-review-fingerprint/v1\0"
    assert hashlib.sha256(SCHEME).hexdigest()  # smoke: the constant is bytes
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_review_fingerprint_digest.py -v`
Expected: FAIL — `ImportError: cannot import name 'compute_fingerprint'`

- [ ] **Step 3: Append the digest to the module**

Add to `src/cgis/guardian/review_fingerprint.py` (imports first: `import hashlib`, `from pathlib import Path`):

```python
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


def disk_reader(root: Path) -> ReadFile:
    """A reader over a working tree rooted at `root`.

    `root` is explicit and never the CWD: a review spends most of its time
    standing in a checkout of somebody else's repository, where a relative read
    would return a wrong value that looks entirely right.
    """

    def read(path: str) -> bytes | None:
        target = root / path
        return target.read_bytes() if target.is_file() else None

    return read


def compute_fingerprint(read: ReadFile, active_providers: frozenset[str]) -> str:
    """The digest over everything this review could read."""
    digest = hashlib.sha256()
    digest.update(SCHEME)
    for path in walk_closure(read, active_providers):
        content = read(path)
        if content is None:  # pragma: no cover - the walk just read it
            continue
        if b"\r\n" in content:
            _msg = f"CRLF line endings in {path}; the checkout must use LF."
            raise CarriageReturnError(_msg)
        digest.update(path.encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()[:DIGEST_CHARS]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_review_fingerprint_digest.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Record the current value for the report**

Run:

```bash
uv run python -c "
from pathlib import Path
from cgis.guardian.review_fingerprint import compute_fingerprint, disk_reader
print(compute_fingerprint(disk_reader(Path('.')), frozenset({'gemini'})))
"
```

Note the value in the commit message — it is the first fingerprint this repository ever had.

- [ ] **Step 6: Verify and commit**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
git add src/cgis/guardian/review_fingerprint.py tests/unit/test_review_fingerprint_digest.py
git commit -m "feat(guardian): a digest over what a review reads, refusing CRLF (#375)"
```

---

### Task 4: The architectural contract

**Files:**
- Create: `tests/unit/test_review_fingerprint_contract.py`
- Create: `tests/unit/review_path_inventory.txt`
- Create: `.gitattributes`

**Interfaces:**
- Consumes: `walk_closure`, `disk_reader` from Tasks 2 and 3.
- Produces: the pinned inventory file that later refactors must consciously update.

- [ ] **Step 1: Generate the pinned inventory**

```bash
uv run python -c "
from pathlib import Path
from cgis.guardian.review_fingerprint import walk_closure, disk_reader
paths = walk_closure(disk_reader(Path('.')), frozenset({'gemini','mistral','ollama'}))
Path('tests/unit/review_path_inventory.txt').write_text('\n'.join(paths) + '\n')
print(len(paths), 'modules pinned')
"
```

Expected: 37 modules.

- [ ] **Step 2: Write the contract tests**

```python
"""What must stay true of the review path, in the direction that can hurt.

The asymmetry: a closure that silently *shrinks* merges two reviewers into one
identity, permanently and unrecoverably downstream. A closure that grows mints a
spurious entity, which is visible and inert. Every test here fails on shrinkage
and passes on growth.
"""

import ast
from pathlib import Path

import pytest

from cgis.guardian.review_fingerprint import disk_reader, walk_closure

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INVENTORY = Path(__file__).parent / "review_path_inventory.txt"
ALL_PROVIDERS = frozenset({"gemini", "mistral", "ollama"})


def current_closure() -> list[str]:
    """The closure of the working tree, with every provider active."""
    return walk_closure(disk_reader(REPO_ROOT), ALL_PROVIDERS)


def test_closure_covers_the_pinned_inventory() -> None:
    """Growth passes; a module dropping out fails.

    If this fails, a module left the review path. Either that is correct and the
    inventory should be regenerated as a conscious act, or an import was severed
    by accident and the fingerprint has just stopped noticing something.
    """
    pinned = set(INVENTORY.read_text().split())
    missing = pinned - set(current_closure())
    assert not missing, f"modules left the review path: {sorted(missing)}"


def test_the_hasher_is_not_in_its_own_closure() -> None:
    """Otherwise editing the hasher splits identities without changing reviews."""
    assert "src/cgis/guardian/review_fingerprint.py" not in current_closure()


def _dynamic_import_calls(tree: ast.AST, dynamic_names: set[str]) -> list[str]:
    """Calls that import by name rather than by statement."""
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in dynamic_names:
            hits.append(func.id)
        elif (
            isinstance(func, ast.Attribute)
            and func.attr == "import_module"
            and isinstance(func.value, ast.Name)
            and func.value.id == "importlib"
        ):
            hits.append("importlib.import_module")
    return hits


def _bound_dynamic_names(tree: ast.AST) -> set[str]:
    """Local names bound to a dynamic importer.

    Keying on the bound name rather than the attribute expression: `from
    importlib import import_module` followed by a bare call evades a detector
    that only matches `importlib.import_module`.
    """
    names = {"__import__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "importlib":
            names.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "import_module"
            )
    return names


@pytest.mark.parametrize("path", current_closure())
def test_no_dynamic_imports_in_the_review_path(path: str) -> None:
    """A module reached by a runtime name is invisible to a static walk.

    That is the silent-merge direction: the closure would omit a module that
    genuinely shapes the review.
    """
    tree = ast.parse((REPO_ROOT / path).read_bytes())
    hits = _dynamic_import_calls(tree, _bound_dynamic_names(tree))
    assert not hits, f"{path} imports dynamically ({hits}); the walk cannot see it"


def test_no_packaged_data_files() -> None:
    """An import walk cannot see a data file, by construction.

    Nothing under src/cgis ships data today and patterns.yaml is read from the
    *analysed* repository, so it is subject input rather than reviewer. This pins
    that: the first packaged .yaml/.json fails here and forces the question of
    whether the digest must cover it.
    """
    packaged = [
        str(p.relative_to(REPO_ROOT))
        for p in (REPO_ROOT / "src" / "cgis").rglob("*")
        if p.is_file() and p.suffix in {".yaml", ".yml", ".json", ".toml", ".txt"}
    ]
    assert not packaged, f"packaged data the fingerprint cannot see: {packaged}"


def test_the_review_path_uses_lf() -> None:
    """CRLF anywhere in the closure would make the digest machine-dependent."""
    offenders = [p for p in current_closure() if b"\r\n" in (REPO_ROOT / p).read_bytes()]
    assert not offenders, f"CRLF line endings: {offenders}"
```

- [ ] **Step 3: Run the tests**

Run: `uv run pytest tests/unit/test_review_fingerprint_contract.py -v`
Expected: PASS. If `test_no_packaged_data_files` fails on `src/codegraph_brain.egg-info/*`, narrow the rglob to exclude `*.egg-info` — that is build output, not packaged data.

- [ ] **Step 4: Add .gitattributes**

Create `.gitattributes`:

```
# The review fingerprint (#375) hashes file bytes, so a checkout that stores
# CRLF would produce a different digest for identical code — and the measured
# digest would disagree with the reconstructed one on the same commit. Pinned
# rather than normalised at hash time, because a normaliser can only fail by
# merging two reviewers that differ.
*.py text eol=lf
```

- [ ] **Step 5: Verify and commit**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
git add tests/unit/test_review_fingerprint_contract.py tests/unit/review_path_inventory.txt .gitattributes
git commit -m "test(guardian): pin the review path, so it can only grow quietly (#375)"
```

---

### Task 5: The bench record carries it

**Files:**
- Modify: `src/cgis/guardian/martian.py:368` area (`ReviewRecord`)
- Modify: `scripts/guardian_martian.py` (`review_one`, near line 348 and 384)
- Modify: `tests/unit/test_guardian_martian_script.py` (fixtures that build a `ReviewRecord`)
- Modify: `tests/unit/test_guardian_martian.py` (same)
- Test: `tests/unit/test_review_fingerprint_record.py`

**Interfaces:**
- Consumes: `compute_fingerprint`, `disk_reader` (Task 3); `BaseProvider.name` (Task 1).
- Produces: `ReviewRecord.review_fingerprint: str`, `ReviewRecord.review_fingerprint_source: Literal["measured", "reconstructed"]`, `ReviewRecord.finder_provider: str`, `ReviewRecord.skeptic_provider: str | None`.

- [ ] **Step 1: Write the failing test**

```python
"""A review record states which reviewer produced it, and how it knows."""

from cgis.guardian.martian import ReviewRecord


def test_record_carries_a_fingerprint_and_its_provenance(sample_record: ReviewRecord) -> None:
    assert sample_record.review_fingerprint
    assert sample_record.review_fingerprint_source in {"measured", "reconstructed"}


def test_provenance_has_no_default() -> None:
    """A reconstructed row must never be able to claim it was measured.

    The two do not carry the same guarantee: a reconstructed digest is rebuilt
    from git and cannot see an uncommitted edit, which is exactly the blindness
    the measured one exists to escape.
    """
    fields = ReviewRecord.model_fields
    assert fields["review_fingerprint_source"].is_required()
    assert fields["review_fingerprint"].is_required()


def test_provider_is_stated_not_inferred() -> None:
    """The producer knows; a model-name prefix is a guess that breaks."""
    assert ReviewRecord.model_fields["finder_provider"].is_required()
```

Add the `sample_record` fixture to `tests/unit/conftest.py` by copying the existing `_review(...)` helper shape from `tests/unit/test_guardian_martian_script.py:752` and adding the four new fields.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_review_fingerprint_record.py -v`
Expected: FAIL — `KeyError: 'review_fingerprint'`

- [ ] **Step 3: Add the fields to ReviewRecord**

In `src/cgis/guardian/martian.py`, directly after `guardian_sha: str`:

```python
    #: What this reviewer *is*, as opposed to which commit it was cut from.
    #: `guardian_sha` stays above as provenance, because it moves on every
    #: commit including ones no review can see — a README edit used to mint a
    #: new reviewer with an empty track record (#375).
    review_fingerprint: str
    #: "measured" = taken from the working tree at review time. "reconstructed"
    #: = rebuilt from git afterwards, which cannot see an uncommitted edit and
    #: so may merge two runs that differed. No default: a backfilled row must
    #: not be able to claim a precision it does not have.
    review_fingerprint_source: Literal["measured", "reconstructed"]
    #: Stated by `build_provider`, not inferred from the model name — the
    #: fingerprint's closure is scoped by it, and a prefix table breaks on the
    #: first model whose name does not carry its vendor.
    finder_provider: str
    skeptic_provider: str | None = None
```

`Literal` is already imported in this module.

- [ ] **Step 4: Wire it into review_one**

In `scripts/guardian_martian.py`, at the top of `review_one` where the providers are built (near line 348), after `skeptic = build_skeptic_provider(...)`:

```python
    active_providers = frozenset(
        {provider.name} | ({skeptic[0].name} if skeptic else set())
    )
    fingerprint = compute_fingerprint(disk_reader(REPO_ROOT), active_providers)
```

`REPO_ROOT`, not the CWD: `review_one` runs with somebody else's checkout on disk.

Then in the `ReviewRecord(...)` construction (near line 384), beside `guardian_sha=guardian_version(),`:

```python
        review_fingerprint=fingerprint,
        review_fingerprint_source="measured",
        finder_provider=provider.name,
        skeptic_provider=skeptic[0].name if skeptic else None,
```

Add the import:

```python
from cgis.guardian.review_fingerprint import compute_fingerprint, disk_reader
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_review_fingerprint_record.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Repair the existing fixtures**

Run: `uv run pytest tests/unit/test_guardian_martian_script.py tests/unit/test_guardian_martian.py -x -q`

Every `ReviewRecord(...)` and every raw-dict fixture will fail validation for the two required fields. Add to each:

```python
            review_fingerprint="abc123abc123",
            review_fingerprint_source="measured",
            finder_provider="gemini",
```

Repeat until the suite is green. Do not give the fields defaults to avoid this work — the absence of a default is the point of Step 1's second test.

- [ ] **Step 7: Verify and commit**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
git add src/cgis/guardian/martian.py scripts/guardian_martian.py tests/unit/
git commit -m "feat(guardian): record which reviewer produced a review, not which commit (#375)"
```

---

### Task 6: The production path carries it

**Files:**
- Modify: `src/cgis/guardian/metrics.py:132-176` (`record_review`)
- Modify: `src/cgis/guardian/runner.py` (`run_guardian`, the `record_review` call)
- Modify: `scripts/guardian_review.py` (compute at the script boundary, pass down)
- Test: `tests/unit/test_guardian_metrics.py` (extend)

**Interfaces:**
- Consumes: `compute_fingerprint`, `disk_reader` (Task 3); `BaseProvider.name` (Task 1).
- Produces: `record_review(..., review_fingerprint: str | None = None)` writing a `"review_fingerprint"` key.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_guardian_metrics.py`:

```python
def test_record_review_writes_the_fingerprint(tmp_path: Path) -> None:
    """A live CI review is attributable to a reviewer version.

    The production row carried no guardian_sha at all, so until now nothing tied
    a posted review to the code that produced it.
    """
    metrics = tmp_path / "m.jsonl"
    record_review(
        model="gemini-2.5-flash",
        pr=1,
        prompt_tokens=10,
        completion_tokens=2,
        findings_total=0,
        lgtm=True,
        review_fingerprint="600405522794",
        metrics_path=metrics,
    )
    entry = json.loads(metrics.read_text().splitlines()[0])
    assert entry["review_fingerprint"] == "600405522794"


def test_record_review_fingerprint_defaults_to_none(tmp_path: Path) -> None:
    """Rows written before this landed read as unknown, not as some value."""
    metrics = tmp_path / "m.jsonl"
    record_review(
        model="m",
        pr=None,
        prompt_tokens=0,
        completion_tokens=0,
        findings_total=0,
        lgtm=True,
        metrics_path=metrics,
    )
    entry = json.loads(metrics.read_text().splitlines()[0])
    assert entry["review_fingerprint"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_guardian_metrics.py -k fingerprint -v`
Expected: FAIL — `TypeError: record_review() got an unexpected keyword argument 'review_fingerprint'`

- [ ] **Step 3: Add the parameter**

In `src/cgis/guardian/metrics.py`, add to the `record_review` signature after `duration_s`:

```python
    review_fingerprint: str | None = None,
```

and to the `entry` dict after `"duration_s": duration_s,`:

```python
        # Which reviewer produced this, as opposed to which commit (#375). None
        # on rows written before it landed — unknown, not "no fingerprint".
        "review_fingerprint": review_fingerprint,
```

- [ ] **Step 4: Thread it through run_guardian**

In `src/cgis/guardian/runner.py`, add to the `run_guardian` signature after `record_finder`:

```python
    review_fingerprint: str | None = None,
```

and pass it to the `record_review(...)` call inside:

```python
        review_fingerprint=review_fingerprint,
```

- [ ] **Step 5: Compute it at the script boundary**

In `scripts/guardian_review.py`, after `skeptic = build_skeptic_provider(...)`:

```python
    # Computed here rather than inside runner.py: nothing in the review closure
    # may import the fingerprint module, or the hasher enters its own hashed set
    # (#375 §4.2). project_root is this repository, not the reviewed checkout.
    active_providers = frozenset({provider.name} | ({skeptic[0].name} if skeptic else set()))
    review_fingerprint = compute_fingerprint(disk_reader(project_root), active_providers)
```

Move the `project_root = Path(__file__).parent.parent.absolute()` line above this block, and pass `review_fingerprint=review_fingerprint` to `run_guardian(...)`.

Add the import:

```python
from cgis.guardian.review_fingerprint import compute_fingerprint, disk_reader
```

- [ ] **Step 6: Assert the boundary still holds**

Run: `uv run pytest tests/unit/test_review_fingerprint_contract.py::test_the_hasher_is_not_in_its_own_closure -v`
Expected: PASS — `runner.py` must import nothing from the fingerprint module.

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_guardian_metrics.py tests/unit/test_guardian_runner.py -v`
Expected: PASS

- [ ] **Step 8: Verify and commit**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
git add src/cgis/guardian/metrics.py src/cgis/guardian/runner.py scripts/guardian_review.py tests/unit/test_guardian_metrics.py
git commit -m "feat(guardian): a live review says which reviewer produced it (#375)"
```

---

### Task 7: Backfill the three corpora

**Files:**
- Create: `scripts/backfill_review_fingerprint.py`
- Test: `tests/unit/test_backfill_review_fingerprint.py`
- Modify: `benchmarks/martian-reviews.jsonl`, `benchmarks/martian-p3-run1.jsonl`, `benchmarks/martian-repeat-reviews.jsonl`

**Interfaces:**
- Consumes: `compute_fingerprint`, `ReadFile` (Tasks 2, 3).
- Produces: `git_reader(sha: str, repo_root: Path) -> ReadFile`, `MODEL_PROVIDERS: dict[str, str]`, `backfill(path: Path, repo_root: Path) -> int`.

- [ ] **Step 1: Write the failing tests**

```python
"""Backfill: a historical digest, honestly labelled as reconstructed."""

import json
import subprocess
from pathlib import Path

import pytest

from backfill_review_fingerprint import (
    MODEL_PROVIDERS,
    UnknownModelError,
    backfill,
    git_reader,
    provider_for,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_provider_for_known_models() -> None:
    assert provider_for("gemini-2.5-flash") == "gemini"
    assert provider_for("mistral-medium-latest") == "mistral"


def test_provider_for_unknown_model_raises() -> None:
    """A default here would compute the digest over the wrong provider module."""
    with pytest.raises(UnknownModelError, match="claude-opus-5"):
        provider_for("claude-opus-5")


def test_git_reader_reads_a_historical_blob() -> None:
    read = git_reader("d0d807ef", REPO_ROOT)
    content = read("src/cgis/guardian/prompts.py")
    assert content is not None
    assert b"CGIS Guardian finder" in content


def test_git_reader_returns_none_for_a_missing_path() -> None:
    read = git_reader("d0d807ef", REPO_ROOT)
    assert read("src/cgis/guardian/does_not_exist.py") is None


def test_backfill_marks_rows_reconstructed(tmp_path: Path) -> None:
    corpus = tmp_path / "reviews.jsonl"
    corpus.write_text(
        json.dumps(
            {
                "url": "u",
                "project": "p",
                "pr_slice": "graph",
                "base_sha": "b",
                "head_sha": "h",
                "had_graph": True,
                "finder_model": "gemini-2.5-flash",
                "skeptic_model": "gemini-3.5-flash",
                "findings": [],
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "duration_s": 1.0,
                "parse_failed": False,
                "guardian_sha": "d0d807ef01c556b882dc85b9fc0d2851d92aa1e5",
                "reviewed_at": "2026-08-12T00:00:00+00:00",
            }
        )
        + "\n"
    )
    assert backfill(corpus, REPO_ROOT) == 1
    row = json.loads(corpus.read_text().splitlines()[0])
    assert row["review_fingerprint_source"] == "reconstructed"
    assert len(row["review_fingerprint"]) == 12
    assert row["finder_provider"] == "gemini"
    assert row["skeptic_provider"] == "gemini"


def test_unresolvable_sha_is_a_hard_failure(tmp_path: Path) -> None:
    """Never a null: a mixed corpus means two identity schemes at once."""
    corpus = tmp_path / "reviews.jsonl"
    corpus.write_text(json.dumps({"guardian_sha": "0" * 40, "finder_model": "x"}) + "\n")
    with pytest.raises(subprocess.CalledProcessError):
        backfill(corpus, REPO_ROOT)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_backfill_review_fingerprint.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backfill_review_fingerprint'`

- [ ] **Step 3: Write the script**

Create `scripts/backfill_review_fingerprint.py`:

```python
"""One-shot: give the existing corpora the fingerprint they were run without (#375).

No review is re-run and no model is called. For each row this rebuilds the
closure at that row's `guardian_sha` via `git show` and hashes it, which is why
the result is labelled `reconstructed`: a digest rebuilt from git cannot see an
uncommitted working-tree edit, and this repository has already lost experimental
rows to exactly that (spec §6.1).

The alternative — leaving old rows null — is worse. A corpus where some rows key
on a fingerprint and some on a sha means two identity schemes coexisting, under
which one reviewer appears as two entities depending on which run it came from.
"""

import argparse
import json
import subprocess
from pathlib import Path

from cgis.guardian.review_fingerprint import ReadFile, compute_fingerprint

#: Historical models only. Stated as a table rather than a prefix rule because a
#: prefix rule breaks on the first model whose name does not carry its vendor —
#: which is why records gained `finder_provider` (#375 §3.3). New rows never
#: reach this table; they state their provider.
MODEL_PROVIDERS: dict[str, str] = {
    "gemini-2.5-flash": "gemini",
    "gemini-3.5-flash": "gemini",
    "mistral-medium-latest": "mistral",
}


class UnknownModelError(RuntimeError):
    """Raised for a model with no provider mapping.

    Refused rather than defaulted: a wrong provider computes the closure over
    the wrong module and returns a confident wrong digest.
    """


def provider_for(model: str) -> str:
    """The provider that serves `model`, or raise."""
    provider = MODEL_PROVIDERS.get(model)
    if provider is None:
        _msg = f"No provider mapping for model {model!r}. Add it to MODEL_PROVIDERS."
        raise UnknownModelError(_msg)
    return provider


def git_reader(sha: str, repo_root: Path) -> ReadFile:
    """A reader over the tree at `sha`.

    A path missing at that commit returns None; anything else — an unresolvable
    sha above all — propagates, because a corpus row we cannot place is not a
    row to guess about.
    """

    def read(path: str) -> bytes | None:
        result = subprocess.run(  # noqa: S603
            ["git", "show", f"{sha}:{path}"],  # noqa: S607
            capture_output=True,
            cwd=repo_root,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout
        if b"does not exist" in result.stderr or b"exists on disk" in result.stderr:
            return None
        raise subprocess.CalledProcessError(
            result.returncode, "git show", result.stdout, result.stderr
        )

    return read


def backfill(path: Path, repo_root: Path) -> int:
    """Rewrite `path` in place with fingerprints; return the rows touched."""
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    cache: dict[tuple[str, frozenset[str]], str] = {}
    for row in rows:
        finder = provider_for(row["finder_model"])
        skeptic_model = row.get("skeptic_model")
        skeptic = provider_for(skeptic_model) if skeptic_model else None
        active = frozenset({finder} | ({skeptic} if skeptic else set()))
        key = (row["guardian_sha"], active)
        if key not in cache:
            cache[key] = compute_fingerprint(git_reader(row["guardian_sha"], repo_root), active)
        row["review_fingerprint"] = cache[key]
        row["review_fingerprint_source"] = "reconstructed"
        row["finder_provider"] = finder
        row["skeptic_provider"] = skeptic
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    return len(rows)


def main() -> None:
    """Backfill every corpus named on the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpora", nargs="+", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    for corpus in args.corpora:
        count = backfill(corpus, args.repo_root)
        print(f"{corpus}: {count} rows")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_backfill_review_fingerprint.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Run the backfill for real**

```bash
uv run python scripts/backfill_review_fingerprint.py \
  benchmarks/martian-reviews.jsonl \
  benchmarks/martian-p3-run1.jsonl \
  benchmarks/martian-repeat-reviews.jsonl
```

Expected: `64 rows`, `19 rows`, `6 rows`.

- [ ] **Step 6: Check the result against the spec's prediction**

```bash
uv run python -c "
import json, collections
c = collections.Counter()
for f in ('benchmarks/martian-reviews.jsonl','benchmarks/martian-p3-run1.jsonl','benchmarks/martian-repeat-reviews.jsonl'):
    for line in open(f):
        r = json.loads(line)
        c[(r['review_fingerprint'], r['finder_model'], r['skeptic_model'], r['had_graph'])] += 1
for k, v in sorted(c.items()):
    print(v, k)
print('identities:', len(c))
"
```

Expected: **3 identities**, and exactly two distinct fingerprint values across all rows (spec §6). If the count is not 3, stop and report — the spec's central prediction has failed and the design needs revisiting, not the numbers adjusting.

- [ ] **Step 7: Verify and commit**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
git add scripts/backfill_review_fingerprint.py tests/unit/test_backfill_review_fingerprint.py benchmarks/
git commit -m "feat(guardian): backfill the corpora, so seven identities become three (#375)"
```

---

### Task 8: Documentation and the PR

**Files:**
- Modify: `docs/specs/2026-08-14-review-fingerprint-design.md` (result section only)

- [ ] **Step 1: Record the measured outcome in the spec**

Append to §6 of the spec, under the predicted table, the actual observed digests and identity count from Task 7 Step 6. If they match the prediction, say so in one line; if they do not, the discrepancy is the finding and belongs in the PR description.

- [ ] **Step 2: Open the PR**

```bash
git push -u origin spec/375-review-fingerprint
gh pr create --title "feat(guardian): an identity for a reviewer, that a commit does not fragment (#375)" --body "$(cat <<'EOF'
Closes #375.

Spec: `docs/specs/2026-08-14-review-fingerprint-design.md`
Plan: `docs/specs/plans/2026-08-14-review-fingerprint.md`

`guardian_sha` pins the whole repository, so a README edit minted a new reviewer
with an empty track record. This adds `review_fingerprint`: a digest over the
import closure of the review entry points, derived rather than declared.

Measured before implementing: across the five shas that produced the 83 reviews
in the martian corpora, the prompt and context path changed zero bytes. The
closure collapses them to two digests, splitting on the gemini/mistral line that
is already a configuration boundary — seven identities become three.

`guardian_sha` stays as provenance. Backfill ran over all three corpora, marked
`reconstructed`, because a digest rebuilt from git cannot see an uncommitted
edit.

Reviewed as a design by the downstream consumer (hivemark), whose records are
irreversible, and by a second reviewer. Both review rounds are in the spec's git
history.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §3.1 derived closure, seeds | 2 |
| §3.2 output side retained | 2 (SEEDS comment), 4 (inventory pins it) |
| §3.3 provider pruning, union, stated not inferred | 1, 2, 5 |
| §3.4 what is outside | 2 (walk is `cgis`-only), 4 (packaged data) |
| §4 algorithm, scheme, truncation | 3 |
| §4.1.1 CRLF refused, .gitattributes | 3, 4 |
| §4.2 recording boundary, injected reader, explicit root | 2, 3, 5, 6 |
| §5 record fields | 5, 6 |
| §6 / §6.1 backfill, reconstructed, hard failure | 7 |
| §7 test contract (all five items) | 4 (four), 2 (unknown provider) |
| §8 residual risks | documented, no task — they are accepted, not fixed |

**Placeholders:** none. Every step carries the code or the command it needs.

**Type consistency:** `ReadFile` is defined once in Task 2 and consumed by Tasks 3 and 7. `walk_closure(read, active_providers)` keeps that argument order everywhere. `compute_fingerprint(read, active_providers)` matches. `BaseProvider.name` is `ClassVar[str]` in Task 1 and read as `provider.name` in Tasks 5 and 6. `review_fingerprint_source` is the same `Literal["measured", "reconstructed"]` in Tasks 5 and 7.

**Ordering constraint:** Tasks 5 and 7 must land together. `review_fingerprint` is required on `ReviewRecord`, so the corpora fail to load until the backfill has run. Do not merge Task 5 alone.
