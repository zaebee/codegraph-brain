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

_NO_KEYS = ("GEMINI_API_KEY", "MISTRAL_API_KEY", "GUARDIAN_PROVIDER", "GUARDIAN_EVIDENCE")


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


@pytest.mark.asyncio
async def test_a_repo_root_that_is_not_a_directory_is_refused_before_building_a_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--repo-root` names the reviewed checkout (#730); a typo must not review nothing."""
    _argv(monkeypatch, "--repo-root", str(tmp_path / "missing"))
    with pytest.raises(ValueError, match="--repo-root"):
        await gr.main()


@pytest.mark.asyncio
async def test_evidence_on_a_foreign_checkout_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The evidence pass runs `uv run mypy/ruff` inside the reviewed checkout.

    With `--repo-root` that checkout is the PR — code its author controls — and
    the job holds provider keys (#730). Refused loudly, not skipped quietly: a
    silent skip would leave the repository variable claiming evidence is on.
    """
    _argv(monkeypatch, "--repo-root", str(tmp_path))
    monkeypatch.setenv("GUARDIAN_EVIDENCE", "1")
    with pytest.raises(ValueError, match="GUARDIAN_EVIDENCE"):
        await gr.main()


@pytest.mark.asyncio
async def test_a_foreign_checkout_without_evidence_gets_past_the_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The normal #730 case: a separate PR checkout, evidence off, reaches the provider."""
    _argv(monkeypatch, "--repo-root", str(tmp_path))
    with pytest.raises(RuntimeError, match="API_KEY"):
        await gr.main()


class _CollectedError(Exception):
    """Raised by the stand-in collector to stop the review once its root is known."""


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path]:
    """(the reviewed checkout as resolved, this repository) — resolved outside the event loop."""
    return tmp_path.resolve(), Path(gr.__file__).parent.parent.absolute()


@pytest.mark.asyncio
async def test_the_review_reads_the_repo_root_and_fingerprints_this_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, roots: tuple[Path, Path]
) -> None:
    """The flag must reach the collector.

    Validating it and then reviewing `.` would pass every test above.
    """
    seen: dict[str, Path] = {}

    class _Provider:
        name = "stub"

    def _collector(**kwargs: object) -> None:
        seen["root"] = Path(str(kwargs["project_root"]))
        raise _CollectedError

    def _fingerprint(reader: object, _active: object) -> str:
        seen["fingerprint_reader"] = Path(str(reader))
        return "fp"

    _argv(monkeypatch, "--repo-root", str(tmp_path))
    monkeypatch.setenv("GUARDIAN_SKEPTIC", "off")
    monkeypatch.setattr(gr, "build_provider", lambda _env: (_Provider(), "stub-model"))
    monkeypatch.setattr(gr, "build_skeptic_provider", lambda _env, **_kw: None)
    monkeypatch.setattr(gr, "disk_reader", lambda root: root)
    monkeypatch.setattr(gr, "compute_fingerprint", _fingerprint)
    monkeypatch.setattr(gr, "ContextCollector", _collector)
    expected_root, this_repository = roots
    with pytest.raises(_CollectedError):
        await gr.main()
    assert seen["root"] == expected_root
    assert seen["fingerprint_reader"] == this_repository
