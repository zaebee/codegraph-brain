"""Tests for benchmark ground-truth matching and scoring (spec §3.2-3.3)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from cgis.guardian.bench import (
    FinderRecording,
    GroundTruth,
    GroundTruthEntry,
    MatchResult,
    annotate_matches,
    killed_ground_truth,
    load_finder_recording,
    load_ground_truth,
    match_findings,
    save_finder_recording,
    score,
    score_separation,
)
from cgis.guardian.findings import Finding, ReviewResult

_TRUTH = GroundTruth.model_validate(
    {
        "pr": 144,
        "base": "aaa",
        "head": "bbb",
        "findings": [
            {
                "id": "float-eq",
                "file": "tests/unit/test_quotient.py",
                "lines": [60, 75],
                "severity": "major",
                "category": "tests",
                "summary": "float ==",
                "source": "sonar",
            },
            {
                "id": "no-lines",
                "file": "src/cgis/query/drift.py",
                "severity": "minor",
                "category": "types",
                "summary": "anywhere in file",
                "source": "gemini",
            },
        ],
        "ambiguous": [{"file": "src/cgis/query/triads.py", "summary": "clip debate"}],
    }
)


def _pred(file: str, line: int | None, confidence: int = 90) -> Finding:
    return Finding(
        file=file,
        line=line,
        severity="major",
        category="logic",
        title="t",
        evidence="e",
        problem="p",
        fix="f",
        confidence=confidence,
    )


def test_match_in_line_range() -> None:
    """Same file + line inside [lo, hi] matches; category is NOT required."""
    m = match_findings([_pred("tests/unit/test_quotient.py", 68)], _TRUTH)
    assert m.matched == {"float-eq": 0}
    assert m.noise == []


def test_match_outside_line_range_is_noise() -> None:
    """Same file but line outside the range → noise."""
    m = match_findings([_pred("tests/unit/test_quotient.py", 10)], _TRUTH)
    assert m.matched == {}
    assert m.noise == [0]


def test_match_entry_without_lines_accepts_any_line() -> None:
    """A GT entry without lines matches any line (and None) in that file."""
    assert match_findings([_pred("src/cgis/query/drift.py", 999)], _TRUTH).matched
    assert match_findings([_pred("src/cgis/query/drift.py", None)], _TRUTH).matched


def test_ambiguous_is_neither_match_nor_noise() -> None:
    """Predictions on ambiguous files are tracked separately."""
    m = match_findings([_pred("src/cgis/query/triads.py", 5)], _TRUTH)
    assert m.matched == {}
    assert m.noise == []
    assert m.ambiguous_hits == [0]


def test_each_entry_matches_once_greedy_by_confidence() -> None:
    """Two predictions on one entry: the higher-confidence one wins, other is noise."""
    preds = [
        _pred("tests/unit/test_quotient.py", 61, confidence=80),
        _pred("tests/unit/test_quotient.py", 62, confidence=95),
    ]
    m = match_findings(preds, _TRUTH)
    assert m.matched == {"float-eq": 1}
    assert m.noise == [0]


def test_score_metrics() -> None:
    """recall = matched/GT, precision = TP/(TP+FP), noise = count."""
    preds = [
        _pred("tests/unit/test_quotient.py", 68),
        _pred("src/cgis/other.py", 1),
    ]
    m = match_findings(preds, _TRUTH)
    s = score(m, _TRUTH)
    assert s.recall == pytest.approx(0.5)
    assert s.precision == pytest.approx(0.5)
    assert s.noise == 1
    assert s.missed == ["no-lines"]


def test_score_empty_ground_truth_perfect_recall() -> None:
    """No GT entries → recall 1.0 (a clean PR replayed with zero findings)."""
    truth = GroundTruth(pr=1, base="a", head="b", findings=[], ambiguous=[])
    s = score(match_findings([], truth), truth)
    assert s.recall == pytest.approx(1.0)
    assert s.precision == pytest.approx(1.0)


def test_score_ambiguous_hits_do_not_depress_precision() -> None:
    """A prediction on an ambiguous file is neither TP nor FP for precision."""
    preds = [
        _pred("tests/unit/test_quotient.py", 68),  # matches GT
        _pred("src/cgis/query/triads.py", 5),  # ambiguous file
    ]
    m = match_findings(preds, _TRUTH)
    s = score(m, _TRUTH)
    assert m.ambiguous_hits == [1]
    assert s.precision == pytest.approx(1.0)
    assert s.noise == 0


def test_load_ground_truth_yaml(tmp_path: Path) -> None:
    """YAML file loads into the GroundTruth model."""
    p = tmp_path / "pr-1.yaml"
    p.write_text(
        "pr: 1\nbase: aaa\nhead: bbb\n"
        "findings:\n"
        "  - id: x\n    file: f.py\n    lines: [1, 2]\n    severity: major\n"
        "    category: logic\n    summary: s\n    source: human\n"
    )
    gt = load_ground_truth(p)
    assert gt.pr == 1
    assert gt.findings[0].lines == (1, 2)
    assert gt.ambiguous == []


def test_inverted_lines_range_rejected() -> None:
    """A transposed lines range fails validation instead of silently never matching."""
    with pytest.raises(ValidationError, match="inverted"):
        GroundTruthEntry(
            id="x",
            file="f.py",
            lines=(75, 60),
            severity="major",
            category="logic",
            summary="s",
            source="human",
        )


def test_match_result_records_total_predictions() -> None:
    """total_predictions is captured by the matcher, not supplied by the caller."""
    preds = [_pred("tests/unit/test_quotient.py", 68), _pred("src/cgis/other.py", 1)]
    assert match_findings(preds, _TRUTH).total_predictions == 2


# ---------------------------------------------------------------------------
# Impact-score separation: the #246 gate metric (spec §4.3)
# ---------------------------------------------------------------------------


def test_score_separation_is_the_gap_between_gt_and_noise_medians() -> None:
    """Does the skeptic rank real findings above noise? That is the whole question."""
    assert score_separation([8, 9, 7], [1, 2, 0, 3]) == 6.5  # median 8 - median 1.5


def test_score_separation_is_none_without_both_populations() -> None:
    """A run with no GT match (or no noise) cannot answer the question."""
    assert score_separation([], [1, 2]) is None
    assert score_separation([8], []) is None


def test_score_separation_detects_a_flat_distribution() -> None:
    """The PR #263 pathology: everything scored alike -> zero separation, gate fails."""
    assert score_separation([5, 5], [5, 5, 5]) == 0.0


def test_annotate_matches_flags_gt_matching_predictions() -> None:
    """Each recorded finding carries whether it matched ground truth (spec §4.2)."""
    hit = _pred(file="a.py", line=10, confidence=90)
    miss = _pred(file="zzz.py", line=99, confidence=50)
    visible = [hit, miss]
    matches = MatchResult(
        matched={"gt-1": 0}, missed=[], noise=[1], ambiguous_hits=[], total_predictions=2
    )

    rows = annotate_matches([hit, miss], visible, matches)

    assert [r["matched_gt"] for r in rows] == [True, False]
    assert rows[0]["file"] == "a.py"


def test_annotate_matches_marks_hidden_findings_as_unmatched() -> None:
    """A refuted finding never reached the matcher; it cannot count as a GT hit."""
    shown = _pred(file="a.py", line=10, confidence=90)
    refuted = _pred(file="b.py", line=1, confidence=90).model_copy(update={"verdict": "refuted"})
    matches = MatchResult(
        matched={"gt-1": 0}, missed=[], noise=[], ambiguous_hits=[], total_predictions=1
    )

    rows = annotate_matches([shown, refuted], [shown], matches)

    assert [r["matched_gt"] for r in rows] == [True, False]


# ---------------------------------------------------------------------------
# Frozen finder output: replay (spec §4.1)
# ---------------------------------------------------------------------------

_RECORDED = FinderRecording(
    result=ReviewResult(findings=[_pred(file="src/a.py", line=3)], summary="s"),
    diff="diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n+x\n",
)


def test_finder_recording_round_trips(tmp_path: Path) -> None:
    """A recorded finder pass reloads exactly, so every skeptic variant judges one set."""
    path = tmp_path / "finder.json"
    save_finder_recording(path, _RECORDED.result, _RECORDED.diff)

    loaded = load_finder_recording(path)

    assert len(loaded.result.findings) == 1
    assert loaded.result.findings[0].file == "src/a.py"
    assert "+x" in loaded.diff


def test_finder_recording_carries_the_diff_not_just_findings(tmp_path: Path) -> None:
    """The diff rides along so replay needs no worktree, ingest or git at all.

    Without it, isolating the skeptic would still pay the whole setup cost the
    replay exists to avoid.
    """
    path = tmp_path / "finder.json"
    save_finder_recording(path, _RECORDED.result, _RECORDED.diff)

    assert "diff --git" in load_finder_recording(path).diff


def test_recorded_skeptic_fields_are_not_trusted(tmp_path: Path) -> None:
    """A recording made WITH a skeptic must not smuggle old verdicts into a new run."""
    stale = ReviewResult(
        findings=[_pred(file="a.py", line=1).model_copy(update={"verdict": "refuted"})],
        summary="s",
        skeptic_status="ok",
        skeptic_judged=1,
        skeptic_total=1,
    )
    path = tmp_path / "finder.json"
    save_finder_recording(path, stale, "d")

    loaded = load_finder_recording(path)

    assert loaded.result.findings[0].verdict is None
    assert loaded.result.skeptic_status == "off"


def test_recording_path_must_be_json(tmp_path: Path) -> None:
    """A CLI-supplied path is validated before it reaches the filesystem (Sonar taint)."""
    with pytest.raises(ValueError, match=r"must be a \.json"):
        save_finder_recording(tmp_path / "finder.yaml", _RECORDED.result, "d")
    with pytest.raises(ValueError, match=r"must be a \.json"):
        load_finder_recording(tmp_path / "finder.yaml")


def test_loading_a_missing_recording_is_a_clear_error(tmp_path: Path) -> None:
    """A typo'd --replay-finder path fails loudly, not with a raw OSError."""
    with pytest.raises(ValueError, match="not a file"):
        load_finder_recording(tmp_path / "nope.json")


# ---------------------------------------------------------------------------
# What the skeptic KILLED (#270)
# ---------------------------------------------------------------------------


def test_refuting_a_gt_match_is_reported_as_killed() -> None:
    """The failure mode this benchmark exists to catch: a real finding refuted away."""
    kept = _pred(file="src/cgis/query/drift.py", line=5)
    doomed = _pred(file="tests/unit/test_quotient.py", line=65).model_copy(
        update={"verdict": "refuted"}
    )

    assert killed_ground_truth([kept, doomed], _TRUTH) == ["float-eq"]


def test_nothing_killed_when_every_match_survives() -> None:
    """Refuting pure noise costs no ground truth."""
    gt_hit = _pred(file="src/cgis/query/drift.py", line=5)
    noise = _pred(file="src/cgis/nowhere.py", line=1).model_copy(update={"verdict": "refuted"})

    assert killed_ground_truth([gt_hit, noise], _TRUTH) == []


def test_threshold_hidden_gt_counts_as_killed() -> None:
    """Hidden by the impact threshold is as invisible to a human as refuted."""
    hidden = _pred(file="tests/unit/test_quotient.py", line=65).model_copy(
        update={"verdict": "confirmed", "impact_score": 1}
    )

    assert killed_ground_truth([hidden], _TRUTH, threshold=5) == ["float-eq"]
    assert killed_ground_truth([hidden], _TRUTH) == []  # default threshold hides nothing


def test_uncertain_confidence_discount_does_not_fake_a_kill() -> None:
    """'uncertain' discounts confidence x0.9 and matching is greedy by confidence.

    Re-ordering may change WHICH prediction claims a ground-truth slot, but the
    slot stays claimed — so no kill may be reported.
    """
    first = _pred(file="src/cgis/query/drift.py", line=5, confidence=80)
    second = _pred(file="src/cgis/query/drift.py", line=9, confidence=79).model_copy(
        update={"verdict": "uncertain", "confidence": 71}
    )

    assert killed_ground_truth([first, second], _TRUTH) == []


def test_killed_ids_are_reported_not_just_counted() -> None:
    """Which ground truth was lost is the actionable part; a count is not."""
    both_dead = [
        _pred(file="tests/unit/test_quotient.py", line=65).model_copy(
            update={"verdict": "refuted"}
        ),
        _pred(file="src/cgis/query/drift.py", line=5).model_copy(update={"verdict": "refuted"}),
    ]

    assert sorted(killed_ground_truth(both_dead, _TRUTH)) == ["float-eq", "no-lines"]


def test_pr_278_entry_scores_every_prediction_as_noise() -> None:
    """The precision baseline: an entry with no ground truth measures noise only (#279)."""
    truth = load_ground_truth(Path("benchmarks/guardian/pr-278.yaml"))
    predictions = [
        _pred("src/cgis/guardian/providers/base.py", 102),
        _pred("src/cgis/guardian/providers/mistral.py", 26),
    ]

    result = score(match_findings(predictions, truth), truth)

    assert result.precision == 0.0
    assert result.noise == 2
    assert result.recall == 1.0  # vacuous: no ground truth to miss


def test_pr_278_entry_files_nothing_as_ambiguous() -> None:
    """Ambiguous exempts per FILE, so one entry would blind the whole baseline."""
    truth = load_ground_truth(Path("benchmarks/guardian/pr-278.yaml"))

    assert truth.findings == []
    assert truth.ambiguous == []
