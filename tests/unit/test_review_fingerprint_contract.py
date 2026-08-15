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
#: Regenerate by running the code, never by hand:
#:
#:   uv run python -c "
#:   from pathlib import Path
#:   from cgis.guardian.review_fingerprint import walk_closure, disk_reader
#:   closure = walk_closure(disk_reader(Path('.')), frozenset({'gemini', 'mistral', 'ollama'}))
#:   Path('tests/unit/review_path_inventory.txt').write_text('\n'.join(closure) + '\n')
#:   "
#:
#: Then diff the result: an addition is a conscious ratchet-forward (a module
#: newly reachable from the review path); a removal means something left the
#: closure and demands the same scrutiny `test_closure_covers_the_pinned_inventory`
#: enforces below.
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


def _dynamic_import_calls(
    tree: ast.AST, dynamic_names: set[str], module_names: set[str]
) -> list[str]:
    """Calls that import by name rather than by statement.

    Both name sets are derived from the same tree rather than assumed: a
    dynamic importer can be reached through the callable it was bound to
    (`dynamic_names`) or through whatever the `importlib` module itself was
    bound to (`module_names`). Hardcoding either spelling leaves a hole, and
    the hole is in the silent-narrowing direction — a module reached by a call
    this misses is invisible to the closure walk.
    """
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
            and func.value.id in module_names
        ):
            hits.append(f"{func.value.id}.import_module")
    return hits


def _importlib_aliases(tree: ast.AST) -> set[str]:
    """Local names bound to the `importlib` module itself.

    `import importlib as il` then `il.import_module(...)` is the same hole as
    the bare-call form `_bound_dynamic_names` covers, one spelling over. Empty
    when the module is never imported, so an unrelated `os.import_module` call
    matches nothing (#385).
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(
                alias.asname or alias.name for alias in node.names if alias.name == "importlib"
            )
    return names


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
                alias.asname or alias.name for alias in node.names if alias.name == "import_module"
            )
    return names


def test_detector_catches_an_aliased_importlib() -> None:
    """`import importlib as il; il.import_module(...)` is the same hole.

    The attribute branch hardcoded the name `importlib`, so aliasing the module
    walked straight past it. A module reached that way is invisible to the
    closure walk — the silent-narrowing direction (#385).
    """
    tree = ast.parse("import importlib as il\nil.import_module('cgis.guardian.core')\n")
    assert _dynamic_import_calls(tree, _bound_dynamic_names(tree), _importlib_aliases(tree))


def test_detector_still_catches_the_unaliased_forms() -> None:
    """Widening the detector must not lose what it already caught."""
    for source in (
        "import importlib\nimportlib.import_module('x')\n",
        "from importlib import import_module\nimport_module('x')\n",
        "from importlib import import_module as im\nim('x')\n",
        "__import__('x')\n",
    ):
        tree = ast.parse(source)
        hits = _dynamic_import_calls(tree, _bound_dynamic_names(tree), _importlib_aliases(tree))
        assert hits, source


def test_detector_ignores_an_unrelated_import_module_attribute() -> None:
    """Matching on any `.import_module` would flag code that imports nothing."""
    tree = ast.parse("import os\nos.import_module('x')\n")
    assert not _dynamic_import_calls(tree, _bound_dynamic_names(tree), _importlib_aliases(tree))


@pytest.mark.parametrize("path", current_closure())
def test_no_dynamic_imports_in_the_review_path(path: str) -> None:
    """A module reached by a runtime name is invisible to a static walk.

    That is the silent-merge direction: the closure would omit a module that
    genuinely shapes the review.
    """
    tree = ast.parse((REPO_ROOT / path).read_bytes())
    hits = _dynamic_import_calls(tree, _bound_dynamic_names(tree), _importlib_aliases(tree))
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
