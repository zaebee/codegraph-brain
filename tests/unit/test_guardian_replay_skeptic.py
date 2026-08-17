"""The replay arm: same findings, same diff, one variable (#401).

Running the guardian twice cannot measure what evidence does, because the finder
is sampled and the two runs disagree about what was claimed before anything is
judged. A recorded pass removes that: its findings carry the verdicts of the run
that produced them, so re-judging the same list *with* evidence is one arm,
paired per finding.

Everything here is exercised except the model call itself.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from guardian_stubs import StubProvider

from cgis.guardian.evidence import Evidence
from cgis.guardian.recording import load_finder_recording
from cgis.guardian.skeptic import FindingJudgement

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

from guardian_replay_skeptic import (
    NoBaselineError,
    NoEvidenceError,
    NoSkepticError,
    baseline_verdicts,
    changed_files,
    cites_a_checker,
    flips,
    main,
    replay,
    worktree_at,
    write_rows,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_DIFF = (
    "diff --git a/src/a.py b/src/a.py\n"
    "--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-x\n+y\n"
    "diff --git a/src/b.py b/src/b.py\n"
    "--- a/src/b.py\n+++ b/src/b.py\n@@ -1 +1 @@\n-p\n+q\n"
)


def _finding(verdict: str | None) -> dict[str, Any]:
    base: dict[str, Any] = {
        "file": "src/a.py",
        "line": 1,
        "severity": "major",
        "category": "logic",
        "title": "t",
        "evidence": "e",
        "problem": "p",
        "fix": "f",
        "confidence": 80,
    }
    if verdict is not None:
        base["verdict"] = verdict
    return base


def _recording(tmp_path: Path, verdicts: list[str | None]) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "guardian_finder.json"
    path.write_text(
        json.dumps(
            {
                "result": {"findings": [_finding(v) for v in verdicts], "summary": ""},
                "diff": _DIFF,
            }
        ),
        encoding="utf-8",
    )
    return path


def _judgement(verdict: str, rationale: str = "because") -> FindingJudgement:
    return FindingJudgement(verdict=verdict, impact_score=0, rationale=rationale)  # type: ignore[arg-type]


class TestTheBaselineMustExist:
    """A replay without a control arm produces a column and calls it a result."""

    def test_recorded_verdicts_are_read_in_order(self, tmp_path: Path) -> None:
        path = _recording(tmp_path, ["confirmed", "refuted", "confirmed"])
        assert baseline_verdicts(path) == ["confirmed", "refuted", "confirmed"]

    def test_a_recording_with_no_verdicts_is_refused(self, tmp_path: Path) -> None:
        """A finder pass captured before any skeptic ran has nothing to compare to."""
        path = _recording(tmp_path, [None, None])
        with pytest.raises(NoBaselineError, match="no usable baseline"):
            baseline_verdicts(path)

    def test_one_unruled_finding_refuses_the_whole_recording(self, tmp_path: Path) -> None:
        """Partly-judged is not a baseline, and silently dropping the gap would bias it.

        The unruled findings are exactly the ones whose skeptic call failed —
        which correlates with size and difficulty, not with nothing.
        """
        path = _recording(tmp_path, ["confirmed", None, "refuted"])
        with pytest.raises(NoBaselineError, match="1 of 3"):
            baseline_verdicts(path)

    def test_the_loader_strips_verdicts_so_the_replay_starts_blind(self, tmp_path: Path) -> None:
        """The reason the baseline is read from raw JSON rather than the model.

        `load_finder_recording` removes verdicts on purpose. If the replay saw
        them it would be judging findings that already carry an answer, and the
        arm would measure agreement with itself.
        """
        path = _recording(tmp_path, ["confirmed", "refuted"])
        loaded = load_finder_recording(path)
        assert [f.verdict for f in loaded.result.findings] == [None, None]
        assert baseline_verdicts(path) == ["confirmed", "refuted"]


class TestPairing:
    """Positional, because `judge_all` and `apply_judgements` both are."""

    def test_a_transition_matrix_counts_pairs(self) -> None:
        matrix = flips(
            ["confirmed", "confirmed", "refuted"],
            [_judgement("refuted"), _judgement("confirmed"), _judgement("refuted")],
        )
        assert matrix["confirmed -> refuted"] == 1
        assert matrix["confirmed -> confirmed"] == 1
        assert matrix["refuted -> refuted"] == 1

    def test_a_failed_call_is_its_own_outcome(self) -> None:
        """Counting it as a verdict would let an API outage look like a change of mind."""
        matrix = flips(["confirmed"], [None])
        assert matrix["confirmed -> call failed"] == 1

    def test_mismatched_lengths_refuse_rather_than_zip_short(self) -> None:
        """`zip` without `strict` would silently drop the tail and still report a matrix."""
        replayed = [_judgement("refuted")]
        with pytest.raises(ValueError, match="not paired"):
            flips(["confirmed", "refuted"], replayed)


class TestAttribution:
    """Which flips the evidence can actually claim."""

    @pytest.mark.parametrize(
        "rationale",
        [
            "the provided Ruff checker output explicitly shows 'All checks passed!'",
            "mypy reports no error for this file",
            "the type check is clean",
            "the linter does not flag it",
        ],
    )
    def test_a_rationale_resting_on_the_checkers_counts(self, rationale: str) -> None:
        assert cites_a_checker(_judgement("refuted", rationale))

    def test_a_rationale_resting_on_the_diff_does_not(self) -> None:
        """The skeptic reading code is not the feature under test.

        Counting these would let the arm claim every ordinary refutation, which
        is most of them — on the live run that produced this design, 8 of 11.
        """
        assert not cites_a_checker(
            _judgement("refuted", "the claim misreads what Path.resolve does")
        )

    def test_a_failed_call_cites_nothing(self) -> None:
        assert not cites_a_checker(None)


def test_changed_files_come_from_the_recorded_diff() -> None:
    """From the recording, not from git: the claims are about those files.

    A path list re-derived today would follow the branch, and the checkers would
    then report on code the finder never saw.
    """
    assert changed_files(_DIFF) == ("src/a.py", "src/b.py")


def _enter_and_raise(ref: str, seen: list[Path]) -> None:
    """Open a worktree, record it, and fail inside — the case the finally must cover."""
    with worktree_at(ref, REPO_ROOT) as tree:
        seen.append(tree)
        assert (tree / "pyproject.toml").is_file()
        _msg = "boom"
        raise RuntimeError(_msg)


def test_the_worktree_is_removed_even_when_the_body_raises() -> None:
    """A leaked worktree makes the next run fail on a path that already exists.

    The body is a helper so the `pytest.raises` block holds one call
    (python:S5778) — otherwise an assertion inside it could raise and the test
    would pass on the wrong exception.
    """
    ref = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    seen: list[Path] = []
    with pytest.raises(RuntimeError, match="boom"):
        _enter_and_raise(ref, seen)
    assert seen
    assert not seen[0].exists()
    listed = subprocess.run(
        ["git", "worktree", "list"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout
    assert str(seen[0]) not in listed


_VERDICT_JSON = '{"verdict": "refuted", "impact_score": 0, "rationale": "ruff reports no issue"}'


class TestReplayOrchestration:
    """The whole arm, with the model and the worktree stubbed out."""

    def _wire(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        evidence: Evidence | None,
        responses: list[str] | None = None,
    ) -> None:
        module = sys.modules["guardian_replay_skeptic"]
        monkeypatch.setattr(module, "_evidence_at", lambda *_a: evidence)
        monkeypatch.setattr(
            module, "skeptic_from", lambda _env: StubProvider(responses or [_VERDICT_JSON] * 8)
        )

    @pytest.mark.asyncio
    async def test_a_paired_matrix_and_an_attribution_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The output the experiment rests on: what moved, and what can be credited."""
        path = _recording(tmp_path, ["confirmed", "confirmed"])
        self._wire(monkeypatch, evidence=Evidence(commands=("ruff check",), output="clean"))
        matrix, cited = await replay(path, "HEAD", REPO_ROOT, {})
        assert matrix["confirmed -> refuted"] == 2
        assert cited == 2

    @pytest.mark.asyncio
    async def test_missing_evidence_refuses_instead_of_repeating_the_control(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The refusal that keeps the experiment from concluding by accident.

        Without it the arm judges with no evidence, which is exactly the run
        already recorded — and it would report "evidence changed nothing" while
        never having applied any.
        """
        path = _recording(tmp_path, ["confirmed"])
        self._wire(monkeypatch, evidence=None)
        with pytest.raises(NoEvidenceError, match="conclude that evidence changes nothing"):
            await replay(path, "HEAD", REPO_ROOT, {})

    @pytest.mark.asyncio
    async def test_a_recording_whose_findings_and_verdicts_disagree_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Length equality is checked before anything is paired, not during.

        The two lists come from the same file by different routes — raw JSON for
        the verdicts, the model loader for the findings — so a loader that ever
        filters would desynchronise them silently, and positional pairing would
        then compare each finding against somebody else's verdict.
        """
        path = _recording(tmp_path, ["confirmed", "confirmed"])
        shorter = load_finder_recording(_recording(tmp_path / "one", ["confirmed"]))
        module = sys.modules["guardian_replay_skeptic"]
        monkeypatch.setattr(module, "load_finder_recording", lambda _p: shorter)
        self._wire(monkeypatch, evidence=Evidence(commands=("x",), output="y"))
        with pytest.raises(NoBaselineError, match="baseline verdicts"):
            await replay(path, "HEAD", REPO_ROOT, {})


class TestTheControlArm:
    """The no-evidence replay, which is what makes a treatment number readable.

    The skeptic is sampled — `gemini.py` sends no temperature and `guardian.yml`
    sets none — so the recorded verdicts are one draw rather than a fixed point.
    Without this arm every flip in the evidence arm is the effect and the
    resampling noise added together, with nothing to subtract.
    """

    def _skeptic(self, monkeypatch: pytest.MonkeyPatch, responses: list[str]) -> None:
        module = sys.modules["guardian_replay_skeptic"]
        monkeypatch.setattr(module, "skeptic_from", lambda _env: StubProvider(responses))

    @pytest.mark.asyncio
    async def test_no_worktree_is_built_and_no_checker_is_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The control must not touch the evidence path at all.

        Asserted by making that path fatal rather than by reading a flag back: a
        `with_evidence` that were quietly ignored would still return a full
        matrix, and the two arms would then differ by nothing while the report
        said they differed by the feature.
        """
        module = sys.modules["guardian_replay_skeptic"]

        def _never(*_args: object) -> Evidence:
            _msg = "the control arm collected evidence"
            raise AssertionError(_msg)

        monkeypatch.setattr(module, "_evidence_at", _never)
        self._skeptic(monkeypatch, [_VERDICT_JSON] * 2)
        path = _recording(tmp_path, ["confirmed", "confirmed"])
        matrix, _ = await replay(path, None, REPO_ROOT, {}, with_evidence=False)
        assert matrix["confirmed -> refuted"] == 2

    @pytest.mark.asyncio
    async def test_the_skeptic_is_asked_without_evidence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`evidence=None` reaches the judge, not merely the call site.

        `_evidence_section` renders nothing for None, so this keyword *is* the
        whole difference between the arms. A refactor that gave it a default
        would leave the control running the treatment's prompt, and the
        experiment would report a difference of zero for the wrong reason.
        """
        module = sys.modules["guardian_replay_skeptic"]
        seen: list[object] = []

        async def _capture(
            _provider: object, findings: list[Any], _diff: str, *, evidence: object
        ) -> list[FindingJudgement]:
            seen.append(evidence)
            return [_judgement("refuted") for _ in findings]

        monkeypatch.setattr(module, "judge_all", _capture)
        self._skeptic(monkeypatch, [])
        path = _recording(tmp_path, ["confirmed"])
        await replay(path, None, REPO_ROOT, {}, with_evidence=False)
        assert seen == [None]

    @pytest.mark.asyncio
    async def test_the_evidence_arm_without_a_commit_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A missing commit must not fall through into a mislabelled control.

        The reviewed commit is not optional decoration: checkers run against
        today's tree describe a different subject, and a flip caused by that
        would be credited to the evidence.
        """
        self._skeptic(monkeypatch, [])
        path = _recording(tmp_path, ["confirmed"])
        with pytest.raises(NoEvidenceError, match="needs the commit"):
            await replay(path, None, REPO_ROOT, {})


class TestThePerFindingDump:
    """What the matrix cannot say, and the reason the rows exist.

    "Eight findings were refuted" is an improvement if all eight were false and a
    regression if one of them was the only real one. Recall is a property of
    *which* finding moved, so no aggregate can bear on it — that is a limit of
    the shape of the number, not of its precision.
    """

    def _findings(self, tmp_path: Path, count: int) -> list[Any]:
        path = _recording(tmp_path, ["confirmed"] * count)
        return list(load_finder_recording(path).result.findings)

    def test_each_row_carries_both_verdicts_and_the_reason(self, tmp_path: Path) -> None:
        findings = self._findings(tmp_path, 2)
        out = tmp_path / "rows" / "dump.jsonl"
        write_rows(
            out,
            findings,
            ["confirmed", "refuted"],
            [_judgement("refuted", "mypy reports no error"), _judgement("refuted", "the diff")],
        )
        rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
        assert [(r["was"], r["now"]) for r in rows] == [
            ("confirmed", "refuted"),
            ("refuted", "refuted"),
        ]
        assert [r["cites_a_checker"] for r in rows] == [True, False]
        assert rows[0]["rationale"] == "mypy reports no error"

    def test_a_failed_call_keeps_its_row(self, tmp_path: Path) -> None:
        """Dropped instead of nulled, it would renumber every finding after it.

        The rows are read back beside the recording to answer "did the real
        finding survive", and that question is asked by position.
        """
        findings = self._findings(tmp_path, 3)
        out = tmp_path / "dump.jsonl"
        write_rows(
            out,
            findings,
            ["confirmed"] * 3,
            [_judgement("refuted"), None, _judgement("confirmed")],
        )
        rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
        assert [r["index"] for r in rows] == [0, 1, 2]
        assert rows[1]["now"] is None
        assert rows[1]["rationale"] is None

    def test_non_ascii_in_a_rationale_stays_readable(self, tmp_path: Path) -> None:
        r"""These rows exist to be read, and `—` in place of an em dash defeats that.

        Raised in review of #406. Today's corpus has no non-ASCII in any
        rationale, so this asserts on input the current data does not contain —
        which is the point: the first model that writes a dash would otherwise
        degrade the file silently, and nothing would fail.
        """
        findings = self._findings(tmp_path, 1)
        out = tmp_path / "dump.jsonl"
        write_rows(out, findings, ["confirmed"], [_judgement("refuted", "ruff — no issue")])
        raw = out.read_text(encoding="utf-8")
        assert "—" in raw
        assert "\\u2014" not in raw

    def test_a_length_mismatch_refuses_rather_than_truncating(self, tmp_path: Path) -> None:
        """`strict=True` on the zip: a short column would silently drop the tail."""
        findings = self._findings(tmp_path, 2)
        out = tmp_path / "dump.jsonl"
        short = [_judgement("refuted")]
        with pytest.raises(ValueError, match=r"argument 2 is shorter|argument 3 is shorter"):
            write_rows(out, findings, ["confirmed"], short)


class TestExactlyOneArmPerRun:
    """Neither flag and both flags are one failure: a run reporting an arm it did not perform.

    `--control --at <sha>` is the dangerous half, and it is the half that
    survived the first draft: it ran the control while naming a commit, so the
    output reads back as a treatment result. Raised in review of #406 — in the
    pull request about mislabelled arms.
    """

    @pytest.mark.parametrize(
        "argv",
        [
            pytest.param(["--recording", "r.json"], id="neither-arm"),
            pytest.param(["--recording", "r.json", "--control", "--at", "HEAD"], id="both-arms"),
        ],
    )
    def test_the_cli_refuses_rather_than_picking_one(
        self, argv: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["guardian_replay_skeptic.py", *argv])
        with pytest.raises(SystemExit) as exit_info:
            main()
        assert exit_info.value.code == 2


class TestSkepticSelection:
    """No skeptic means nothing to replay, and that is a refusal."""

    def test_an_unconfigured_skeptic_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        module = sys.modules["guardian_replay_skeptic"]
        monkeypatch.setattr(module, "build_provider", lambda _e: (StubProvider([]), "m"))
        monkeypatch.setattr(module, "build_skeptic_provider", lambda _e, **_kw: None)
        with pytest.raises(NoSkepticError, match="No skeptic is configured"):
            module.skeptic_from({})

    def test_the_configured_skeptic_is_returned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        module = sys.modules["guardian_replay_skeptic"]
        stub = StubProvider([])
        monkeypatch.setattr(module, "build_provider", lambda _e: (StubProvider([]), "m"))
        monkeypatch.setattr(module, "build_skeptic_provider", lambda _e, **_kw: (stub, "s"))
        assert module.skeptic_from({}) is stub


def test_main_prints_the_matrix_and_the_attribution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI, end to end, with the model stubbed.

    Asserts the attribution line is present: a run that printed only the matrix
    would invite reading every flip as the feature's doing, which is the reading
    this arm exists to prevent.
    """
    module = sys.modules["guardian_replay_skeptic"]
    path = _recording(tmp_path, ["confirmed", "refuted"])
    monkeypatch.setattr(
        module, "_evidence_at", lambda *_a: Evidence(commands=("ruff check",), output="clean")
    )
    monkeypatch.setattr(module, "skeptic_from", lambda _env: StubProvider([_VERDICT_JSON] * 8))
    monkeypatch.setattr(
        sys, "argv", ["x", "--recording", str(path), "--at", "HEAD", "--repo-root", str(REPO_ROOT)]
    )
    assert module.main() == 0
    out = capsys.readouterr().out
    assert "2 findings re-judged" in out
    assert "confirmed -> refuted" in out
    assert "rationales citing a checker: 2/2" in out


class TestBadInputFailsAsPromised:
    """A module that promises a named refusal must not crash with a type error."""

    def test_an_explicitly_null_result_is_refused_not_crashed(self, tmp_path: Path) -> None:
        """`{"result": null}` — a key present and holding null.

        `.get("result", {})` returns the null, not the default, because the key
        exists. The old code then called `.get("findings")` on None and raised
        AttributeError, so one shape of unusable recording escaped the promise
        this module makes about how it refuses.
        """
        path = tmp_path / "guardian_finder.json"
        path.write_text(json.dumps({"result": None, "diff": _DIFF}), encoding="utf-8")
        with pytest.raises(NoBaselineError, match="no usable baseline"):
            baseline_verdicts(path)

    def test_a_missing_result_key_is_refused_too(self, tmp_path: Path) -> None:
        path = tmp_path / "guardian_finder.json"
        path.write_text(json.dumps({"diff": _DIFF}), encoding="utf-8")
        with pytest.raises(NoBaselineError, match="no usable baseline"):
            baseline_verdicts(path)

    def test_an_unknown_ref_says_what_git_said(self) -> None:
        """The message is the whole value here.

        `CalledProcessError` stringifies without stderr, so the failure would
        report an exit status and not whether the ref is unknown, the tree is
        locked, or the path already exists — three problems with three different
        fixes, indistinguishable from the traceback.
        """
        with pytest.raises(RuntimeError, match="Cannot create a worktree"):
            _enter_worktree("refs/heads/definitely-not-a-branch-9d3f")


def _enter_worktree(ref: str) -> None:
    """Open and immediately close a worktree — one call for the raises block."""
    with worktree_at(ref, REPO_ROOT):
        pass
