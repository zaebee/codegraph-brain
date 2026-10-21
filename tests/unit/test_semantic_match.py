"""Semantic ground-truth matching for the skeptic experiments (#246, experiment E).

The file-and-line matcher credits a ground-truth entry without `lines` to any
finding in its file. On A's corpus that put 11 findings on one entry, most of
them about something else, so a skeptic correctly refuting them was scored as
losing recall. These tests pin the replacement: a pair matches only when the
judges labelled it so, and the assignment is a maximum matching, not greedy.
"""

import json
import sys
from pathlib import Path

import pytest

from cgis.guardian.bench import GroundTruth
from cgis.guardian.findings import Finding

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

from semantic_match import (
    Labels,
    candidate_pairs,
    dump_labels,
    load_labels,
    semantic_counts,
)


def _finding(file: str, title: str, confidence: int = 50) -> Finding:
    return Finding(
        file=file,
        line=1,
        severity="major",
        category="logic",
        title=title,
        evidence="e",
        problem="p",
        fix="f",
        confidence=confidence,
    )


def _truth(*entries: tuple[str, str], ambiguous: tuple[str, ...] = ()) -> GroundTruth:
    return GroundTruth.model_validate(
        {
            "pr": 1,
            "base": "b",
            "head": "h",
            "findings": [
                {
                    "id": i,
                    "file": f,
                    "severity": "major",
                    "category": "logic",
                    "summary": i,
                    "source": "human",
                }
                for i, f in entries
            ],
            "ambiguous": [{"file": f, "summary": "s"} for f in ambiguous],
        }
    )


def test_pairs_are_same_file_only() -> None:
    findings = [_finding("a.py", "x"), _finding("b.py", "y")]
    truth = _truth(("g1", "a.py"), ("g2", "c.py"))
    assert candidate_pairs(findings, truth) == [(0, "g1")]


def test_an_unrelated_finding_in_the_file_is_noise_not_a_match() -> None:
    """The defect: file-level crediting. Unlabelled, a finding in the file matches nothing."""
    findings = [_finding("a.py", "unrelated", confidence=90), _finding("a.py", "the real one")]
    truth = _truth(("g1", "a.py"))
    labels = {0: set(), 1: {"g1"}}
    noise, matched, _ = semantic_counts(findings, [0, 1], labels, truth)
    assert matched == ["g1"]
    assert noise == 1


def test_refuting_the_unrelated_finding_kills_nothing() -> None:
    findings = [_finding("a.py", "unrelated", confidence=90), _finding("a.py", "the real one")]
    truth = _truth(("g1", "a.py"))
    labels = {0: set(), 1: {"g1"}}
    _, _, killed = semantic_counts(findings, [1], labels, truth)  # finding 0 refuted
    assert killed == []


def test_refuting_the_real_match_kills_it() -> None:
    findings = [_finding("a.py", "unrelated"), _finding("a.py", "the real one")]
    truth = _truth(("g1", "a.py"))
    labels = {0: set(), 1: {"g1"}}
    _, matched, killed = semantic_counts(findings, [0], labels, truth)
    assert matched == []
    assert killed == ["g1"]


def test_a_duplicate_keeps_the_match_when_one_copy_survives() -> None:
    findings = [_finding("a.py", "real"), _finding("a.py", "real again")]
    truth = _truth(("g1", "a.py"))
    labels = {0: {"g1"}, 1: {"g1"}}
    noise, matched, killed = semantic_counts(findings, [1], labels, truth)
    assert (matched, killed) == (["g1"], [])
    assert noise == 0  # the surviving duplicate is the match


def test_assignment_is_maximum_not_greedy() -> None:
    """Greedy by confidence would give g1 to finding 0 and leave g2 unmatched."""
    findings = [_finding("a.py", "both", confidence=90), _finding("a.py", "only g1", confidence=10)]
    truth = _truth(("g1", "a.py"), ("g2", "a.py"))
    labels = {0: {"g1", "g2"}, 1: {"g1"}}
    _, matched, _ = semantic_counts(findings, [0, 1], labels, truth)
    assert matched == ["g1", "g2"]


def test_an_ambiguous_file_is_not_noise() -> None:
    findings = [_finding("amb.py", "anything")]
    truth = _truth(("g1", "a.py"), ambiguous=("amb.py",))
    noise, _, _ = semantic_counts(findings, [0], {0: set()}, truth)
    assert noise == 0


def test_labels_round_trip_and_the_rule(tmp_path: Path) -> None:
    labels = Labels(
        judges=("j1", "j2"),
        verdicts={
            ("p@1", 0, "g1"): {"j1": True, "j2": True},
            ("p@1", 1, "g1"): {"j1": True, "j2": False},
            ("p@1", 2, "g1"): {"j1": None, "j2": True},
        },
    )
    path = tmp_path / "labels.json"
    dump_labels(labels, path)
    back = load_labels(path)
    assert back == labels
    assert json.loads(path.read_text())["judges"] == ["j1", "j2"]
    assert back.accepts("p@1", "all") == {0: {"g1"}, 1: set(), 2: set()}
    assert back.accepts("p@1", "any") == {0: {"g1"}, 1: {"g1"}, 2: {"g1"}}
    assert back.unlabelled() == 1


def test_an_unknown_rule_is_refused() -> None:
    labels = Labels(judges=("j",), verdicts={})
    with pytest.raises(ValueError, match="rule"):
        labels.accepts("p", "most")  # type: ignore[arg-type]


def test_labels_need_a_judge() -> None:
    """With no judges, "all" of nothing is true and every pair would be accepted."""
    with pytest.raises(ValueError, match="judge"):
        Labels(judges=(), verdicts={})


def test_missing_counts_absent_and_failed_pairs() -> None:
    labels = Labels(judges=("j1", "j2"), verdicts={("p", 0, "g1"): {"j1": True, "j2": None}})
    assert labels.missing("p", [(0, "g1"), (1, "g1")]) == 2
