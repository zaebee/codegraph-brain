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
from run_budget import make_budget
from semantic_match import Labels, load_labels

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


class _Metered(StubProvider):
    """A stub judge that bills every call: 1,000 in, 100 out."""

    async def generate_structured(self, system_prompt: str, user_prompt: str, schema: type) -> str:  # type: ignore[override]
        """Record one call's usage, then answer as the stub does."""
        from cgis.guardian.providers.base import ProviderUsage  # noqa: PLC0415

        self._record_usage(ProviderUsage(prompt_tokens=1_000, completion_tokens=100))
        return await super().generate_structured(system_prompt, user_prompt, schema)


@pytest.mark.asyncio
async def test_the_budget_charges_each_judge_its_own_calls_and_stops() -> None:
    judges = {"j1": _Metered([_YES, _YES]), "j2": _Metered([_NO, _NO])}
    budget = make_budget(0.001, ["j1=1:10", "j2=1:10"], judges)
    assert budget is not None
    labels = Labels(judges=("j1", "j2"), verdicts={})
    passes = [("1@a", [_finding("a.py"), _finding("a.py")], _truth())]

    finished = await label_gt_matches.label(passes, judges, labels, budget, concurrency=1)

    # Unit j1 costs 2 * (1000 * 1 + 100 * 10) / 1e6 = $0.004, past the $0.001 cap,
    # so j2 is never asked and the run reports a stop.
    assert finished is False
    assert budget.stopped is True
    assert budget.spent_usd == pytest.approx(0.004)
    assert judges["j2"].prompts == []


def test_main_writes_what_it_has_when_labelling_crashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "l.json"

    async def _boom(_passes: object, _judges: object, labels: Labels, *_a: object) -> bool:
        labels.verdicts[("1@a", 0, "g1")] = {"claude-m1": True}
        _msg = "network"
        raise RuntimeError(_msg)

    monkeypatch.setattr(
        label_gt_matches, "arm_provider", lambda *_a: (StubProvider([]), "claude-m1")
    )
    monkeypatch.setattr(label_gt_matches, "corpus_passes", lambda _t: [])
    monkeypatch.setattr(label_gt_matches, "label", _boom)
    monkeypatch.setattr(sys, "argv", ["x", "--judges", "claude-m1", "--out", str(out)])
    with pytest.raises(RuntimeError):
        label_gt_matches.main()
    assert load_labels(out).verdicts == {("1@a", 0, "g1"): {"claude-m1": True}}


def test_main_reports_a_missing_key_as_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _no_key(*_a: object) -> object:
        _msg = "no key"
        raise label_gt_matches.NoArmError(_msg)

    monkeypatch.setattr(label_gt_matches, "arm_provider", _no_key)
    monkeypatch.setattr(sys, "argv", ["x", "--judges", "m1", "--out", str(tmp_path / "l.json")])
    with pytest.raises(SystemExit):
        label_gt_matches.main()


def test_each_judge_is_built_for_its_own_vendor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gemini review of #723: the provider follows the model, not a hardcoded vendor."""
    built: list[tuple[str, str]] = []

    def _arm(vendor: str, _env: object, model: str) -> tuple[StubProvider, str]:
        built.append((vendor, model))
        return StubProvider([]), model

    monkeypatch.setattr(label_gt_matches, "arm_provider", _arm)
    monkeypatch.setattr(label_gt_matches, "corpus_passes", lambda _t: [])
    out = tmp_path / "l.json"
    monkeypatch.setattr(
        sys, "argv", ["x", "--judges", "claude-haiku-5-5,gemini-2.5-flash", "--out", str(out)]
    )
    assert label_gt_matches.main() == 0
    assert built == [("anthropic", "claude-haiku-5-5"), ("gemini", "gemini-2.5-flash")]


def test_resume_accepts_the_same_judges_in_another_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "l.json"
    label_gt_matches.write(Labels(judges=("claude-a", "claude-b"), verdicts={}), out)
    monkeypatch.setattr(label_gt_matches, "arm_provider", lambda *_a: (StubProvider([]), "m"))
    monkeypatch.setattr(label_gt_matches, "corpus_passes", lambda _t: [])
    monkeypatch.setattr(sys, "argv", ["x", "--judges", "claude-b,claude-a", "--out", str(out)])
    assert label_gt_matches.main() == 0
    assert load_labels(out).judges == ("claude-a", "claude-b")
    assert "0 of 0 pairs labelled by every judge" in capsys.readouterr().err


def test_main_refuses_an_empty_judge_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["x", "--judges", " , ", "--out", str(tmp_path / "l.json")])
    with pytest.raises(SystemExit):
        label_gt_matches.main()
