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


def test_pruned_provider_import_is_never_followed() -> None:
    """A synthetic fixture where an unselected provider reaches a module no seed does.

    In this repository's real providers, `gemini.py`, `mistral.py` and
    `ollama.py` import nothing but `providers.base` plus third-party/stdlib —
    so no file is reachable *only* through an unselected provider, and
    `test_unselected_providers_are_pruned` alone cannot tell a during-walk
    prune from a full-traversal-then-filter-by-provider-filename
    implementation: both would produce the same output against the real repo.

    This fixture breaks that tie. `mistral.py` imports a module
    (`mistral_only.py`) that is not itself a provider file and that no other
    seed reaches. A filter that strips only known provider *filenames* from a
    completed traversal would still contain `mistral_only.py`, because the
    traversal (unpruned) would have read `mistral.py`'s imports before any
    filter ran, and the filter never looks at what `mistral.py` pulled in —
    only at whether a path's own name matches an unselected provider. Only a
    prune applied *during* the walk — skipping `mistral.py` before its
    imports are ever read — keeps `mistral_only.py` out.
    """
    fake_files = {
        "src/cgis/guardian/core.py": (
            b"import cgis.guardian.providers.gemini\nimport cgis.guardian.providers.mistral\n"
        ),
        "src/cgis/guardian/providers/base.py": b"",
        "src/cgis/guardian/providers/gemini.py": b"import cgis.guardian.providers.base\n",
        "src/cgis/guardian/providers/mistral.py": b"import cgis.guardian.mistral_only\n",
        "src/cgis/guardian/providers/ollama.py": b"",
        "src/cgis/guardian/mistral_only.py": b"",
    }

    def fake_reader(path: str) -> bytes | None:
        return fake_files.get(path)

    closure = walk_closure(fake_reader, frozenset({"gemini"}))
    assert "src/cgis/guardian/providers/gemini.py" in closure
    assert "src/cgis/guardian/providers/base.py" in closure
    assert "src/cgis/guardian/providers/mistral.py" not in closure
    assert "src/cgis/guardian/mistral_only.py" not in closure


def test_relative_imports_are_resolved_into_the_closure() -> None:
    """`from .x import y` and `from ..a.b import y` must resolve, not vanish.

    A relative import's `ast.ImportFrom.module` never starts with `"cgis"`
    (`from .base import X` parses to `module="base"`), so a walk that only
    checks that prefix silently drops it and everything reachable only
    through it — no error, just a smaller closure. This fixture chains a
    level-1 relative import (`core.py` -> `rel_level1_target.py`, both in
    `cgis.guardian`) into a level-2 one (`rel_level1_target.py` ->
    `cgis.extractors.rel_level2_target`, crossing up to the parent package)
    and asserts both resolved targets are present.
    """
    fake_files = {
        "src/cgis/guardian/core.py": b"from .rel_level1_target import X\n",
        "src/cgis/guardian/rel_level1_target.py": (
            b"from ..extractors.rel_level2_target import Y\n"
        ),
        "src/cgis/extractors/rel_level2_target.py": b"",
    }

    def fake_reader(path: str) -> bytes | None:
        return fake_files.get(path)

    closure = walk_closure(fake_reader, frozenset())
    assert "src/cgis/guardian/rel_level1_target.py" in closure
    assert "src/cgis/extractors/rel_level2_target.py" in closure
