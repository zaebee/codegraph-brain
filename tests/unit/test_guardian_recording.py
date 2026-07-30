"""Unit tests for the finder-recording module extracted from bench (#279)."""

from pathlib import Path

import pytest

from cgis.guardian.findings import Finding, ReviewResult
from cgis.guardian.recording import (
    FinderRecording,
    load_finder_recording,
    save_finder_recording,
)

_FINDING = Finding(
    file="a.py",
    line=1,
    severity="major",
    category="logic",
    title="t",
    evidence="e",
    problem="p",
    fix="f",
    confidence=90,
)


def test_recording_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "rec.json"
    result = ReviewResult(findings=[_FINDING], summary="s")

    save_finder_recording(path, result, "the-diff")
    loaded = load_finder_recording(path)

    assert loaded.diff == "the-diff"
    assert [f.title for f in loaded.result.findings] == ["t"]


def test_load_strips_skeptic_verdicts(tmp_path: Path) -> None:
    """Every replay must start from unjudged findings, or arms are not comparable."""
    path = tmp_path / "rec.json"
    judged = _FINDING.model_copy(
        update={"verdict": "refuted", "impact_score": 0, "skeptic_note": "why"}
    )
    save_finder_recording(path, ReviewResult(findings=[judged], summary="s"), "d")

    loaded = load_finder_recording(path)

    assert loaded.result.findings[0].verdict is None
    assert loaded.result.findings[0].skeptic_note is None
    assert loaded.result.findings[0].impact_score is None
    assert loaded.result.skeptic_status == "off"
    assert loaded.result.skeptic_judged == 0
    assert loaded.result.skeptic_total == 0


def test_a_non_json_path_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"must be a \.json file"):
        save_finder_recording(tmp_path / "rec.txt", ReviewResult(findings=[], summary=""), "d")


def test_loading_a_missing_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a file"):
        load_finder_recording(tmp_path / "nope.json")


def test_bench_still_re_exports_the_moved_names() -> None:
    """bench.py is the historical import site; existing callers must keep working."""
    from cgis.guardian import bench  # noqa: PLC0415

    assert bench.FinderRecording is FinderRecording
    assert bench.save_finder_recording is save_finder_recording
