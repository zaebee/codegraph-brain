"""Tests for the review entry point (scripts/guardian_review.py).

The script had no tests at all — a long-standing gap in the guardian subsystem
map. These cover the one thing #347 added to it: refusing an unusable
`--metrics` path *before* the review runs, rather than after every LLM call has
been paid for.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

import guardian_review as gr

_NO_KEYS = ("GEMINI_API_KEY", "MISTRAL_API_KEY", "GUARDIAN_PROVIDER")


def _argv(monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    """Set sys.argv and clear every provider credential from the environment."""
    monkeypatch.setattr(sys, "argv", ["guardian_review.py", *args])
    for name in _NO_KEYS:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.asyncio
async def test_refuses_a_bad_metrics_path_before_building_a_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ordering is the point, and the exception type is what proves it.

    With no API keys in the environment, `build_provider` raises
    `RuntimeError("Set MISTRAL_API_KEY or GEMINI_API_KEY…")`. So getting a
    `ValueError` about the metrics path means the check ran *first* — which is
    the whole reason it is duplicated here rather than left to `record_review`,
    which runs after the review is complete and paid for.
    """
    _argv(monkeypatch, "--metrics", str(tmp_path / "notes.txt"))
    with pytest.raises(ValueError, match="Refusing metrics path"):
        await gr.main()


@pytest.mark.asyncio
async def test_a_usable_metrics_path_gets_past_the_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The guard must not block the normal case.

    Reaching `build_provider`'s complaint about missing credentials is exactly
    what "got past the check" looks like without running a real review.
    """
    _argv(monkeypatch, "--metrics", str(tmp_path / "guardian_metrics.jsonl"))
    with pytest.raises(RuntimeError, match="API_KEY"):
        await gr.main()
