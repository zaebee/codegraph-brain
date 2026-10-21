"""Labelling the frozen corpus's (finding, ground truth) pairs, with judges stubbed (#246 E)."""

import json
import sys
from pathlib import Path

import pytest
from guardian_stubs import StubProvider

from cgis.guardian.bench import GroundTruth
from cgis.guardian.findings import Finding

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

import label_gt_matches
from semantic_match import Labels

_YES = '{"reasoning": "r", "match": true, "confidence": 0.9}'
_NO = '{"reasoning": "r", "match": false, "confidence": 0.9}'


def _finding(file: str) -> Finding:
    return Finding(
        file=file,
        line=1,
        severity="major",
        category="logic",
        title="t",
        evidence="e",
        problem="p",
        fix="f",
        confidence=50,
    )


def _truth() -> GroundTruth:
    entry = {"severity": "major", "category": "logic", "source": "human"}
    return GroundTruth.model_validate(
        {
            "pr": 1,
            "base": "b",
            "head": "h",
            "findings": [
                {**entry, "id": "g1", "file": "a.py", "summary": "s1"},
                {**entry, "id": "g2", "file": "b.py", "summary": "s2"},
            ],
        }
    )


@pytest.mark.asyncio
async def test_each_judge_rules_each_same_file_pair_once() -> None:
    judges = {"j1": StubProvider([_YES, _NO]), "j2": StubProvider([_NO, _NO])}
    labels = Labels(judges=("j1", "j2"), verdicts={})
    passes = [("1@a", [_finding("a.py"), _finding("a.py"), _finding("c.py")], _truth())]

    await label_gt_matches.label(passes, judges, labels, budget=None, concurrency=1)

    assert sorted(labels.verdicts) == [("1@a", 0, "g1"), ("1@a", 1, "g1")]
    assert [len(p.prompts) for p in judges.values()] == [2, 2]
    assert sum(v["j1"] is True for v in labels.verdicts.values()) == 1


@pytest.mark.asyncio
async def test_a_resumed_run_asks_only_what_is_missing() -> None:
    labels = Labels(
        judges=("j1",),
        verdicts={("1@a", 0, "g1"): {"j1": True}, ("1@a", 1, "g1"): {"j1": None}},
    )
    judge = StubProvider([_NO])
    passes = [("1@a", [_finding("a.py"), _finding("a.py")], _truth())]

    await label_gt_matches.label(passes, {"j1": judge}, labels, budget=None, concurrency=1)

    assert len(judge.prompts) == 1  # only the failed one is retried
    assert labels.verdicts[("1@a", 1, "g1")] == {"j1": False}
    assert labels.verdicts[("1@a", 0, "g1")] == {"j1": True}


@pytest.mark.asyncio
async def test_the_judge_sees_summaries_not_locations() -> None:
    """Martian's format: the golden is its summary, the candidate carries no file or line."""
    judge = StubProvider([_YES])
    labels = Labels(judges=("j1",), verdicts={})
    passes = [("1@a", [_finding("a.py")], _truth())]
    await label_gt_matches.label(passes, {"j1": judge}, labels, budget=None, concurrency=1)
    assert "s1" in judge.prompts[0]
    assert "a.py" not in judge.prompts[0]


def test_main_refuses_a_cap_without_prices(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys, "argv", ["x", "--judges", "m1", "--out", str(tmp_path / "l.json"), "--budget-usd", "1"]
    )
    with pytest.raises(SystemExit):
        label_gt_matches.main()
    assert not (tmp_path / "l.json").exists()


def test_written_labels_name_their_judges(tmp_path: Path) -> None:
    labels = Labels(judges=("j1",), verdicts={("1@a", 0, "g1"): {"j1": True}})
    out = tmp_path / "l.json"
    label_gt_matches.write(labels, out)
    assert json.loads(out.read_text())["judges"] == ["j1"]
