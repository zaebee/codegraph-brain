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
