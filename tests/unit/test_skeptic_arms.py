"""Both arms over one frozen pass, with the model calls stubbed (#246).

The experiment itself needs two providers; these tests need none. What they
cover is everything that decides whether the numbers mean anything: which
skeptic each arm builds, that neither arm can inherit the other's model, and
that a same/cross classification is derived from the finder rather than assumed.
"""

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from guardian_stubs import StubProvider

from cgis.guardian.bench import load_ground_truth

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

import skeptic_arms
from skeptic_arms import ARMS, NoArmError, _vendor, arm_provider, finder_models, judge_one, report

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BENCH_DIR = REPO_ROOT / "benchmarks" / "guardian"


class TestBuildingAnArm:
    """Which provider each arm gets, and what it must not inherit."""

    def test_a_missing_provider_refuses_before_any_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Half an experiment costs the same as none and reads like a result.

        This refusal is not hypothetical: it is what stopped the first real run
        after 2 calls instead of 135, when the mistral key returned HTTP 402.
        """
        monkeypatch.setattr(skeptic_arms, "build_skeptic_provider", lambda _e, **_k: None)
        with pytest.raises(NoArmError, match="answers nothing this experiment asks"):
            arm_provider("mistral", {})

    def test_the_arm_name_is_what_selects_the_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str] = []

        def _capture(env: dict[str, str], **_kw: object) -> tuple[StubProvider, str]:
            seen.append(env["GUARDIAN_SKEPTIC"])
            return StubProvider([]), "m"

        monkeypatch.setattr(skeptic_arms, "build_skeptic_provider", _capture)
        for name in ARMS:
            arm_provider(name, {})
        assert seen == list(ARMS)

    def test_a_skeptic_model_in_the_environment_reaches_neither_arm(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The cross-arm contamination that would have run one arm on a fake model.

        `build_skeptic_provider` applies `GUARDIAN_SKEPTIC_MODEL` to whichever
        provider it builds. An environment holding the production value
        (`gemini-2.5-flash`) would hand that name to the mistral arm, so one arm
        would run a model that does not exist while the other ran the intended
        one — and the comparison would be between a working skeptic and a broken
        one, reported as a comparison between vendors.
        """
        envs: list[dict[str, str]] = []

        def _capture(env: dict[str, str], **_kw: object) -> tuple[StubProvider, str]:
            envs.append(dict(env))
            return StubProvider([]), "m"

        monkeypatch.setattr(skeptic_arms, "build_skeptic_provider", _capture)
        arm_provider("mistral", {"GUARDIAN_SKEPTIC_MODEL": "gemini-2.5-flash", "X": "keep"})
        assert "GUARDIAN_SKEPTIC_MODEL" not in envs[0]
        assert envs[0]["X"] == "keep"


class TestTheVendorSplit:
    """Both orientations are in the corpus, and that is what the split rests on."""

    @pytest.mark.parametrize(
        ("model", "vendor"),
        [
            ("gemini-2.5-flash", "gemini"),
            ("gemini-3.5-flash", "gemini"),
            ("mistral-medium-latest", "mistral"),
            # Mistral families whose names do not contain "mistral". codestral
            # was the arm proposed in #246's first draft, so this is not
            # hypothetical. Raised in review of #411.
            ("codestral-latest", "mistral"),
            ("ministral-8b-latest", "mistral"),
            ("Gemini-2.5-Flash", "gemini"),
            ("gpt-5", "unknown"),
            ("", "unknown"),
        ],
    )
    def test_a_model_name_maps_to_its_vendor(self, model: str, vendor: str) -> None:
        assert _vendor(model) == vendor

    def test_the_corpus_really_carries_both_orientations(self) -> None:
        """The premise of the whole design, asserted rather than assumed.

        If every frozen pass came from one vendor there would be one
        orientation, and the experiment could not tell "cross-vendor refutes
        more" from "gemini refutes more" — which is the distinction #246's
        original single-orientation design could not make.
        """
        vendors = {_vendor(m) for m in finder_models(BENCH_DIR / "results.jsonl").values()}
        assert {"gemini", "mistral"} <= vendors

    def test_only_frozen_passes_are_mapped(self, tmp_path: Path) -> None:
        """A judged row's findings carry rewritten confidences (#279), so it is not a pass."""
        rows = [
            {
                "pr": 1,
                "timestamp": "t1",
                "matched": [],
                "precision": 1.0,
                "model": "m",
                "findings": [],
            },
            {"pr": 2, "timestamp": "t2", "matched": [], "precision": 1.0, "skeptic_model": "s"},
        ]
        path = tmp_path / "results.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        assert finder_models(path) == {"1@t1": "m"}


@pytest.mark.asyncio
async def test_judge_one_scores_a_pass_and_reports_what_the_skeptic_killed(
    tmp_path: Path,
) -> None:
    """The row a single (pass, arm) produces, including the recall guard.

    `killed_gt` is carried because `missed` cannot tell "the finder never found
    it" from "the finder found it and the skeptic hid it" (#270), and only the
    second is something an arm did.
    """
    recording = json.loads(
        (BENCH_DIR / "experiments" / "401-evidence" / "pr-399-recording.json").read_text(
            encoding="utf-8"
        )
    )
    findings = recording["result"]["findings"][:2]
    for finding in findings:
        finding.pop("verdict", None)
        finding.pop("skeptic_note", None)
    path = tmp_path / "rec.json"
    path.write_text(
        json.dumps({"result": {"findings": findings, "summary": ""}, "diff": recording["diff"]}),
        encoding="utf-8",
    )
    truth = load_ground_truth(BENCH_DIR / "pr-143.yaml")
    provider = StubProvider(
        ['{"verdict": "refuted", "impact_score": 0, "rationale": "r"}'] * len(findings)
    )

    row = await judge_one(provider, path, truth, None)

    assert row["findings"] == len(findings)
    assert row["refuted"] == len(findings)
    assert row["confirmed"] == 0
    assert isinstance(row["killed_gt"], list)


class _Rec:
    """A recording stand-in: findings and the diff they were found in."""

    def __init__(self, findings: list[object]) -> None:
        self.result = type("R", (), {"findings": findings})()
        self.diff = "diff --git a/x.py b/x.py\n"


class TestTheRunItself:
    """Orchestration, with every call stubbed: what runs, how often, and on what."""

    def _wire(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, passes: list[str]
    ) -> list[str]:
        """Stub every outside edge; return the list evidence collection appends to."""
        paths = [tmp_path / f"{name}.json" for name in passes]
        for path in paths:
            path.write_text("{}", encoding="utf-8")
        collected: list[str] = []
        monkeypatch.setattr(
            skeptic_arms, "arm_provider", lambda name, _env: (StubProvider([]), f"{name}-model")
        )
        monkeypatch.setattr(skeptic_arms, "finder_models", lambda _p: {})
        monkeypatch.setattr(skeptic_arms, "build", lambda *_a: paths)
        monkeypatch.setattr(skeptic_arms, "load_finder_recording", lambda _p: _Rec(["f"]))
        monkeypatch.setattr(skeptic_arms, "load_ground_truth", lambda _p: object())

        def _evidence(_truth: object, _diff: str, _root: Path) -> object:
            collected.append("once")
            return object()

        monkeypatch.setattr(skeptic_arms, "evidence_for_pr", _evidence)

        async def _judge(*_a: object, **_k: object) -> dict[str, Any]:
            return {"findings": 1, "refuted": 0, "killed_gt": []}

        monkeypatch.setattr(skeptic_arms, "judge_one", _judge)
        return collected

    @pytest.mark.asyncio
    async def test_evidence_is_collected_once_per_pr_not_once_per_pass(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Three passes of one PR share a diff, so the checkers would say the same thing.

        Per pass it would be three worktrees and three `uv sync`s for one answer.
        Asserted by counting, because a per-pass version returns identical rows
        and differs only in how long it takes — invisible in the output.
        """
        collected = self._wire(monkeypatch, tmp_path, ["143@a", "143@b", "143@c"])
        rows = await skeptic_arms.collect_rows(REPO_ROOT, None)
        assert len(collected) == 1
        assert len(rows) == 3 * len(ARMS)

    @pytest.mark.asyncio
    async def test_each_pass_is_judged_by_every_arm(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Pairing is the whole design: the same findings, both skeptics."""
        self._wire(monkeypatch, tmp_path, ["143@a", "144@b"])
        rows = await skeptic_arms.collect_rows(REPO_ROOT, None)
        by_pass: dict[str, set[str]] = {}
        for row in rows:
            by_pass.setdefault(str(row["pass"]), set()).add(str(row["arm"]))
        assert by_pass == {"143@a": set(ARMS), "144@b": set(ARMS)}

    @pytest.mark.asyncio
    async def test_limit_trims_the_work_before_a_worktree_is_built(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """`--limit` is the smoke run; it must cost one PR, not all of them."""
        collected = self._wire(monkeypatch, tmp_path, ["143@a", "144@b"])
        rows = await skeptic_arms.collect_rows(REPO_ROOT, 1)
        assert len(collected) == 1
        assert len(rows) == len(ARMS)

    @pytest.mark.asyncio
    async def test_a_pass_with_no_findings_is_dropped_before_any_worktree(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Nothing to judge means nothing to compare, and a worktree costs a uv sync."""
        self._wire(monkeypatch, tmp_path, ["143@a"])
        monkeypatch.setattr(skeptic_arms, "load_finder_recording", lambda _p: _Rec([]))
        collected: list[str] = []
        monkeypatch.setattr(
            skeptic_arms, "evidence_for_pr", lambda *_a: collected.append("x") or object()
        )
        rows = await skeptic_arms.collect_rows(REPO_ROOT, None)
        assert rows == []
        assert collected == []


def test_main_writes_the_rows_it_collected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI wiring, and the reason the write is not inside the coroutine.

    `collect_rows` returns rows and `main` writes them, so the blocking file
    write never happens on the event loop — the same rule the worktree
    collection follows with `to_thread`.
    """
    row = {
        "finder_model": "gemini-2.5-flash",
        "arm": "mistral",
        "refuted": 1,
        "findings": 2,
        "killed_gt": [],
    }

    async def _rows(_root: Path, _limit: int | None) -> list[dict[str, Any]]:
        return [row]

    out = tmp_path / "nested" / "arms.jsonl"
    monkeypatch.setattr(skeptic_arms, "collect_rows", _rows)
    monkeypatch.setattr(sys, "argv", ["skeptic_arms.py", "--out", str(out)])
    assert skeptic_arms.main() == 0
    assert [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()] == [row]
    assert "cross" in capsys.readouterr().out


def test_report_labels_same_and_cross_from_the_finder(capsys: pytest.CaptureFixture[str]) -> None:
    """The label is derived, not stored — an arm is 'same' only against its own vendor."""
    rows: list[dict[str, Any]] = [
        {
            "finder_model": "mistral-medium-latest",
            "arm": "gemini",
            "refuted": 3,
            "findings": 5,
            "killed_gt": [],
        },
        {
            "finder_model": "mistral-medium-latest",
            "arm": "mistral",
            "refuted": 0,
            "findings": 5,
            "killed_gt": ["a"],
        },
    ]
    report(rows)
    out = capsys.readouterr().out
    assert "cross" in out
    assert "same" in out


@pytest.mark.parametrize(
    ("finder", "arm", "kind"),
    [
        ("gemini", "gemini", "same"),
        ("mistral", "mistral", "same"),
        ("gemini", "mistral", "cross"),
        ("mistral", "gemini", "cross"),
        # Both orderings, because `unknown` must not depend on which arm it met.
        ("unknown", "gemini", "unknown"),
        ("unknown", "mistral", "unknown"),
    ],
)
def test_the_independent_variable_is_classified_on_its_own(
    finder: str, arm: str, kind: str
) -> None:
    """same / cross / unknown, read and checked apart from the report that prints it."""
    assert skeptic_arms.pairing_kind(finder, arm) == kind


def test_an_unattributed_finder_is_never_reported_as_cross(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The silent failure behind the codestral case, and the general answer to it.

    An unrecognised model used to become "other"; "other" equals no arm name, so
    every one of its rows read `cross` — in the exact variable this experiment
    measures. A new vendor would not have raised anything; it would have
    produced a wrong answer shaped like the expected one. So `unknown` is its
    own kind and the model is named, because the fix is a one-line marker and
    nobody can apply it to a number they never saw.
    """
    rows: list[dict[str, Any]] = [
        {"finder_model": "gpt-5", "arm": "gemini", "refuted": 1, "findings": 4, "killed_gt": []}
    ]
    report(rows)
    out = capsys.readouterr().out
    # The table row, not the whole blob: the sentence naming the omission says
    # "same/cross split", so a substring check over the output would pass on the
    # explanation while the row said `cross`.
    row_line = next(line for line in out.splitlines() if line.startswith("unknown"))
    assert row_line.split()[:3] == ["unknown", "gemini", "unknown"]
    assert "gpt-5" in out
    assert "_VENDOR_MARKERS" in out
