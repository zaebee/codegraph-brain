"""Tests for the calibration runner (scripts/guardian_calibrate.py, #342).

The scoring primitives live in `cgis.guardian.calibrate` and are tested next
door. What is under test here is everything the script adds around them: which
recorded reviews it selects, how it resumes, what it writes, and — the part
that actually decided a published verdict — how it turns records back into the
three pre-registered gates.

`report` and `rescore` are exercised against constructed records rather than
`benchmarks/guardian/calibration.jsonl`, so a future re-run of the benchmark
cannot turn these into failures about data.
"""

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from guardian_stubs import FlakyProvider

from cgis.guardian.bench import GroundTruth, match_findings, score
from cgis.guardian.findings import Finding

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

import guardian_calibrate as gc

VERDICT_MATCH = '{"reasoning": "same bug", "match": true, "confidence": 0.9}'
VERDICT_MISS = '{"reasoning": "different", "match": false, "confidence": 0.8}'

FIXTURE_YAML = """
pr: 900
base: aaaa
head: bbbb
findings:
  - id: only-golden
    file: src/cgis/thing.py
    lines: [10, 20]
    severity: major
    category: types
    summary: the one defect this fixture curates
    source: human
ambiguous: []
"""


def _finding(**overrides: object) -> dict[str, Any]:
    base: dict[str, Any] = {
        "file": "src/cgis/thing.py",
        "line": 12,
        "severity": "major",
        "category": "types",
        "title": "A defect",
        "evidence": "x = 1",
        "problem": "It breaks.",
        "fix": "Do not.",
        "confidence": 80,
    }
    return base | overrides


def _row(**overrides: object) -> dict[str, Any]:
    base: dict[str, Any] = {
        "timestamp": "2026-08-01T00:00:00+00:00",
        "pr": 900,
        "run": 0,
        "model": "gemini-2.5-flash",
        "guardian_sha": "deadbeef",
        "features": "",
        "recall": 1.0,
        "precision": 1.0,
        "noise": 0,
        "matched": {"only-golden": 0},
        "ambiguous_hits": [],
        "findings": [_finding()],
    }
    return base | overrides


def _fixed_provider(env: Mapping[str, str]) -> tuple[FlakyProvider, str]:  # noqa: ARG001
    """Stand in for build_provider: a judge that always says "match"."""
    return FlakyProvider([], VERDICT_MATCH), "judge-x"


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def _bench_dir(tmp_path: Path, rows: list[dict[str, Any]] | None = None) -> Path:
    bench = tmp_path / "bench"
    bench.mkdir()
    (bench / "pr-900.yaml").write_text(FIXTURE_YAML, encoding="utf-8")
    _write_jsonl(bench / "results.jsonl", rows if rows is not None else [_row()])
    return bench


def _run_args(bench: Path, **overrides: object) -> argparse.Namespace:
    base: dict[str, Any] = {
        "results": bench / "results.jsonl",
        "fixtures": bench,
        "out": bench / "calibration.jsonl",
        "pr": None,
        "provider": None,
        "model": None,
        "concurrency": 2,
        "dry_run": False,
    }
    return argparse.Namespace(**(base | overrides))


def _record(**overrides: object) -> dict[str, Any]:
    """One calibration record: 1 golden x 2 candidates, the first a match."""
    base: dict[str, Any] = {
        "row_key": "900@2026-08-01T00:00:00+00:00",
        "pr": 900,
        "run": 0,
        "finder_model": "gemini-2.5-flash",
        "guardian_sha": "deadbeef",
        "features": "",
        "bench_arm": None,
        "n_goldens": 1,
        "n_candidates": 2,
        "our_precision": 0.5,
        "our_precision_strict": 0.5,
        "our_recall": 1.0,
        "judge_precision": 0.5,
        "judge_recall": 1.0,
        "tp": 1,
        "fp": 1,
        "fn": 0,
        "judge_failures": 0,
        "decisions": [1, 0],
        "assignment": {"0": 0},
        "judge_model": "judge-a",
        "judged_at": "2026-08-02T00:00:00+00:00",
    }
    return base | overrides


class TestVisible:
    """The scorer only ever saw the findings the skeptic left standing."""

    def test_drops_refuted_findings(self) -> None:
        findings = [_finding(verdict="refuted"), _finding(verdict="confirmed"), _finding()]
        assert len(gc.visible(findings)) == 2

    def test_a_missing_verdict_counts_as_visible(self) -> None:
        """Single-pass runs record no verdict at all; those findings were shown."""
        assert gc.visible([_finding()]) == [_finding()]


class TestStrictPrecision:
    """G2's number: our precision with the per-file ambiguous exemption removed."""

    def test_ambiguous_hits_return_to_the_denominator(self) -> None:
        row = _row(matched={"a": 0}, noise=1, ambiguous_hits=["x", "y"])
        assert gc.strict_precision(row) == pytest.approx(0.25)

    def test_matches_scored_precision_when_nothing_was_ambiguous(self) -> None:
        row = _row(matched={"a": 0, "b": 1}, noise=2, ambiguous_hits=[])
        assert gc.strict_precision(row) == pytest.approx(0.5)

    def test_no_candidates_is_vacuously_one(self) -> None:
        """Same degenerate convention as bench.score, so the two stay comparable."""
        assert gc.strict_precision(_row(matched={}, noise=0, ambiguous_hits=[])) == 1.0

    def test_strict_precision_agrees_with_bench_score(self) -> None:
        """The two definitions converged in #345, and must not drift apart again.

        This also pins the recorded row's shape, which is asymmetric and easy to
        get wrong: `guardian_bench.py` writes `noise` as `BenchScore`'s *count*
        but `ambiguous_hits` as `MatchResult`'s *list of indices*. Both models
        carry both names, so swapping either side is a one-word edit that would
        silently change the schema — and `strict_precision` reads the row as
        `noise + len(ambiguous_hits)`.
        """
        truth = GroundTruth.model_validate(
            {
                "pr": 900,
                "base": "a",
                "head": "b",
                "findings": [
                    {
                        "id": "g1",
                        "file": "src/a.py",
                        "lines": [1, 10],
                        "severity": "major",
                        "category": "types",
                        "summary": "s",
                        "source": "human",
                    }
                ],
                "ambiguous": [{"file": "src/b.py", "summary": "debatable"}],
            }
        )
        predictions = [
            Finding.model_validate(_finding(file="src/a.py", line=5)),  # matches
            Finding.model_validate(_finding(file="src/b.py", line=1)),  # ambiguous
            Finding.model_validate(_finding(file="src/c.py", line=1)),  # noise
        ]
        matches = match_findings(predictions, truth)
        scored = score(matches, truth)
        # Exactly the fields scripts/guardian_bench.py writes, and their shapes.
        row = _row(
            matched=matches.matched,
            noise=scored.noise,
            ambiguous_hits=matches.ambiguous_hits,
        )
        assert gc.strict_precision(row) == pytest.approx(scored.precision)
        assert scored.precision == pytest.approx(1 / 3)


class TestLoadRows:
    """The population bug that decided both Phase 1 gates lived here."""

    def test_includes_empty_and_skips_unscored(self, tmp_path: Path) -> None:
        """The regression this class exists to prevent: 51 of 118 rows silently gone.

        An earlier filter required a non-empty `findings` list. Both Phase 1
        gate verdicts flip between the two populations, so dropping rows here
        chose the answer before any judge was called. A row with no score at
        all is a different case and is still excluded — there is nothing in it
        to compare against.
        """
        path = _write_jsonl(
            tmp_path / "results.jsonl",
            [
                _row(),
                _row(timestamp="2026-08-01T00:00:01+00:00", findings=[]),
                {"pr": 900, "timestamp": "2026-08-01T00:00:02+00:00", "error": "crashed"},
            ],
        )
        rows = gc.load_rows(path, None)
        assert len(rows) == 2
        assert [len(r["findings"]) for r in rows] == [1, 0]

    def test_filters_to_one_pr(self, tmp_path: Path) -> None:
        path = _write_jsonl(
            tmp_path / "results.jsonl",
            [_row(), _row(pr=901, timestamp="2026-08-01T00:00:01+00:00")],
        )
        assert [r["pr"] for r in gc.load_rows(path, 901)] == [901]


class TestLoadTruths:
    """Fixtures are the ground truth; an ambiguous corpus must not load quietly."""

    def test_keys_by_pr_number(self, tmp_path: Path) -> None:
        bench = _bench_dir(tmp_path)
        truths = gc.load_truths(bench)
        assert set(truths) == {900}
        assert len(truths[900].findings) == 1

    def test_duplicate_pr_numbers_are_rejected(self, tmp_path: Path) -> None:
        """Silently keeping one would score a whole PR against ground truth nobody chose."""
        bench = _bench_dir(tmp_path)
        (bench / "pr-900-copy.yaml").write_text(FIXTURE_YAML, encoding="utf-8")
        with pytest.raises(ValueError, match="Duplicate pr: 900"):
            gc.load_truths(bench)


class TestLoadRecords:
    """The output is an append-only log, so re-judged rows appear twice."""

    def test_last_write_per_row_and_judge_wins(self, tmp_path: Path) -> None:
        path = _write_jsonl(
            tmp_path / "calibration.jsonl",
            [_record(tp=0, judge_failures=3), _record(tp=1, judge_failures=0)],
        )
        records = gc.load_records(path)
        assert len(records) == 1
        assert records[0]["tp"] == 1

    def test_the_same_row_judged_by_two_models_is_kept_twice(self, tmp_path: Path) -> None:
        path = _write_jsonl(
            tmp_path / "calibration.jsonl",
            [_record(judge_model="judge-a"), _record(judge_model="judge-b")],
        )
        assert len(gc.load_records(path)) == 2


class TestDoneKeys:
    """Resume must not skip the rows the report tells the operator to re-run."""

    def test_no_file_means_nothing_is_done(self, tmp_path: Path) -> None:
        assert gc.done_keys(tmp_path / "missing.jsonl", "judge-a") == set()

    def test_a_clean_row_is_done(self, tmp_path: Path) -> None:
        path = _write_jsonl(tmp_path / "c.jsonl", [_record()])
        assert gc.done_keys(path, "judge-a") == {_record()["row_key"]}

    def test_a_row_with_unruled_pairs_is_not_done(self, tmp_path: Path) -> None:
        """Otherwise `run` skips exactly the rows whose tp is known to be biased low."""
        path = _write_jsonl(tmp_path / "c.jsonl", [_record(judge_failures=2)])
        assert gc.done_keys(path, "judge-a") == set()

    def test_another_judge_does_not_count(self, tmp_path: Path) -> None:
        path = _write_jsonl(tmp_path / "c.jsonl", [_record(judge_model="judge-b")])
        assert gc.done_keys(path, "judge-a") == set()


class TestDenseDecisions:
    """Two judges' grids have to line up element-wise for G3 to mean anything."""

    def test_row_major_placement(self) -> None:
        pairs = [
            gc.JudgePair(
                golden_index=1,
                candidate_index=2,
                verdict=gc.JudgeVerdict(reasoning="r", match=True, confidence=0.9),
            )
        ]
        assert gc.dense_decisions(pairs, 2, 3) == [None, None, None, None, None, 1]

    def test_unruled_pairs_stay_none_rather_than_becoming_non_matches(self) -> None:
        """A failed judge call is missing data, not evidence of a non-match."""
        assert gc.dense_decisions([], 1, 2) == [None, None]

    def test_an_empty_review_has_an_empty_grid(self) -> None:
        assert gc.dense_decisions([], 3, 0) == []


class TestRescore:
    """Re-deriving published scores from the raw rulings already on disk."""

    def test_pairs_are_rebuilt_in_row_major_order(self) -> None:
        record = _record(n_goldens=2, n_candidates=2, decisions=[0, 1, None, 1])
        pairs = gc.pairs_from_decisions(record)
        assert [(p.golden_index, p.candidate_index, p.verdict.match) for p in pairs] == [
            (0, 0, False),
            (0, 1, True),
            (1, 1, True),
        ]

    def test_corrects_a_greedy_under_count(self) -> None:
        """The audited bug: golden 1 can take either candidate, golden 0 only the first.

        A greedy pass that spends candidate 0 first strands golden 0 at tp=1.
        The grid supports two matches, and that is what the record must say.
        """
        record = _record(n_goldens=2, n_candidates=2, decisions=[1, 0, 1, 1], tp=1, fp=1, fn=1)
        fixed = gc.rescored(record)
        assert (fixed["tp"], fixed["fp"], fixed["fn"]) == (2, 0, 0)
        assert fixed["judge_precision"] == pytest.approx(1.0)
        assert fixed["judge_recall"] == pytest.approx(1.0)

    def test_is_a_no_op_on_an_already_correct_record(self) -> None:
        """Which is what makes `rescore` a reproducibility check, not just a repair."""
        assert gc.rescored(_record()) == _record()

    def test_dry_run_reports_without_writing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = _write_jsonl(
            tmp_path / "c.jsonl",
            [_record(n_goldens=2, n_candidates=2, decisions=[1, 0, 1, 1], tp=1)],
        )
        before = path.read_text(encoding="utf-8")
        assert gc.rescore(argparse.Namespace(out=path, apply=False)) == 0
        assert "1 with a different tp" in capsys.readouterr().out
        assert path.read_text(encoding="utf-8") == before

    def test_apply_writes_the_corrected_records(self, tmp_path: Path) -> None:
        path = _write_jsonl(
            tmp_path / "c.jsonl",
            [_record(n_goldens=2, n_candidates=2, decisions=[1, 0, 1, 1], tp=1)],
        )
        assert gc.rescore(argparse.Namespace(out=path, apply=True)) == 0
        assert gc.load_records(path)[0]["tp"] == 2
        # Idempotent: a second pass has nothing left to correct.
        assert gc.rescored(gc.load_records(path)[0]) == gc.load_records(path)[0]

    def test_missing_file_is_an_error_not_a_traceback(self, tmp_path: Path) -> None:
        assert gc.rescore(argparse.Namespace(out=tmp_path / "nope.jsonl", apply=False)) == 1


class TestCalibrateRow:
    """One recorded review in, one calibration record out — no finder calls."""

    @pytest.mark.asyncio
    async def test_scores_a_review_against_its_fixture(self, tmp_path: Path) -> None:
        bench = _bench_dir(tmp_path)
        truth = gc.load_truths(bench)[900]
        record = await gc.calibrate_row(
            FlakyProvider([], VERDICT_MATCH), _row(), truth, concurrency=2
        )
        assert record["n_goldens"] == 1
        assert record["n_candidates"] == 1
        assert record["tp"] == 1
        assert record["judge_failures"] == 0
        assert record["decisions"] == [1]
        assert record["row_key"] == "900@2026-08-01T00:00:00+00:00"

    @pytest.mark.asyncio
    async def test_refuted_findings_are_never_shown_to_the_judge(self, tmp_path: Path) -> None:
        bench = _bench_dir(tmp_path)
        truth = gc.load_truths(bench)[900]
        row = _row(findings=[_finding(verdict="refuted"), _finding()])
        record = await gc.calibrate_row(FlakyProvider([], VERDICT_MISS), row, truth, concurrency=2)
        assert record["n_candidates"] == 1
        assert record["tp"] == 0

    @pytest.mark.asyncio
    async def test_identity_is_copied_from_the_judged_row_not_recomputed(
        self, tmp_path: Path
    ) -> None:
        """The record must carry the reviewer that ran, not the one running now (#390).

        The sentinel is deliberately not a real digest. A fingerprint recomputed
        here would be a perfectly valid 12-hex string over *this* checkout, and
        would pass any assertion that only checked shape or non-emptiness — while
        being the wrong reviewer, since the row being judged ran under an older
        guardian. Only an impossible value distinguishes "copied" from
        "recomputed and plausible".
        """
        bench = _bench_dir(tmp_path)
        truth = gc.load_truths(bench)[900]
        row = _row(
            review_fingerprint="not-a-real-digest",
            review_fingerprint_source="reconstructed",
            finder_provider="gemini",
            skeptic_provider="mistral",
            skeptic_model="mistral-medium-latest",
        )
        record = await gc.calibrate_row(FlakyProvider([], VERDICT_MATCH), row, truth, concurrency=2)
        assert record["review_fingerprint"] == "not-a-real-digest"
        assert record["review_fingerprint_source"] == "reconstructed"
        assert record["finder_provider"] == "gemini"
        assert record["skeptic_provider"] == "mistral"
        assert record["skeptic_model"] == "mistral-medium-latest"

    @pytest.mark.asyncio
    async def test_a_row_without_an_identity_yields_a_null_not_an_invention(
        self, tmp_path: Path
    ) -> None:
        """A pre-#390 results row degrades to null rather than acquiring a digest.

        Null is inert: downstream simply cannot attribute the record, exactly as
        it could not before this change. A digest invented here would be
        permanent and wrong. `run` reports the count separately so the
        degradation is announced rather than silent.
        """
        bench = _bench_dir(tmp_path)
        truth = gc.load_truths(bench)[900]
        record = await gc.calibrate_row(
            FlakyProvider([], VERDICT_MATCH), _row(), truth, concurrency=2
        )
        assert record["review_fingerprint"] is None
        assert record["review_fingerprint_source"] is None


class TestRun:
    """The runner's selection, resume and reporting behaviour."""

    @pytest.mark.asyncio
    async def test_missing_results_file_is_an_error_not_a_traceback(self, tmp_path: Path) -> None:
        args = _run_args(_bench_dir(tmp_path), results=tmp_path / "nope.jsonl")
        assert await gc.run(args) == 1

    @pytest.mark.asyncio
    async def test_dry_run_projects_calls_without_a_provider(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """No provider is built, so this works with no API key present."""
        assert await gc.run(_run_args(_bench_dir(tmp_path), dry_run=True)) == 0
        out = capsys.readouterr().out
        assert "1 judge calls projected" in out
        assert "pr-900: 1 calls" in out

    @pytest.mark.asyncio
    async def test_rows_without_a_fixture_are_skipped_with_a_warning(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bench = _bench_dir(tmp_path, [_row(), _row(pr=999, timestamp="2026-08-01T00:00:01+00:00")])
        assert await gc.run(_run_args(bench, dry_run=True)) == 0
        captured = capsys.readouterr()
        assert "No fixture for PRs [999]" in captured.err
        assert "1 recorded reviews" in captured.out

    @pytest.mark.asyncio
    async def test_writes_one_record_per_review_then_resumes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bench = _bench_dir(tmp_path)
        monkeypatch.setattr(gc, "build_provider", _fixed_provider)
        args = _run_args(bench)
        assert await gc.run(args) == 0
        records = gc.load_records(args.out)
        assert len(records) == 1
        assert records[0]["judge_model"] == "judge-x"
        assert records[0]["tp"] == 1

        capsys.readouterr()
        assert await gc.run(_run_args(bench)) == 0
        assert "1 already done, 0 to go" in capsys.readouterr().out
        assert len(gc.load_records(args.out)) == 1

    @pytest.mark.asyncio
    async def test_cli_overrides_reach_the_provider_without_mutating_the_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, str] = {}

        def _capture(env: Mapping[str, str]) -> tuple[FlakyProvider, str]:
            seen.update(env)
            return FlakyProvider([], VERDICT_MATCH), "judge-x"

        monkeypatch.setattr(gc, "build_provider", _capture)
        monkeypatch.delenv("GUARDIAN_PROVIDER", raising=False)
        args = _run_args(_bench_dir(tmp_path), provider="mistral", model="m-large")
        assert await gc.run(args) == 0
        assert seen["GUARDIAN_PROVIDER"] == "mistral"
        assert seen["GUARDIAN_MODEL"] == "m-large"
        assert "GUARDIAN_PROVIDER" not in __import__("os").environ


class TestReport:
    """The gates, rendered from records — the part that produced a published verdict."""

    def test_missing_file_is_an_error_not_a_traceback(self, tmp_path: Path) -> None:
        assert gc.report(argparse.Namespace(out=tmp_path / "nope.jsonl")) == 1

    def test_rho_line_marks_the_g1_threshold(self) -> None:
        strong = gc.rho_line("x", [1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 3.0, 4.0])
        weak = gc.rho_line("x", [1.0, 2.0, 3.0, 4.0], [4.0, 1.0, 3.0, 2.0])
        assert "PASS" in strong
        assert "FAIL" in weak

    def test_rho_line_says_undefined_rather_than_zero(self) -> None:
        """A constant column is unmeasurable; 0.0 would read as measured disagreement."""
        assert "undefined" in gc.rho_line("x", [1.0] * 4, [1.0, 2.0, 3.0, 4.0])

    def test_col_extracts_one_numeric_column(self) -> None:
        assert gc.numeric_column([_record(tp=1), _record(tp=3)], "tp") == [1.0, 3.0]

    def _spread(self, judge: str) -> list[dict[str, Any]]:
        """Four rows whose two precisions rank together, so G1 has something to measure."""
        return [
            _record(
                row_key=f"900@{i}",
                judge_model=judge,
                our_precision=p,
                our_precision_strict=p,
                judge_precision=p,
                our_recall=p,
                judge_recall=p,
                n_candidates=0 if i == 0 else 2,
            )
            for i, p in enumerate((0.1, 0.4, 0.7, 1.0))
        ]

    def test_reports_both_populations_and_the_gates(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = _write_jsonl(tmp_path / "c.jsonl", self._spread("judge-a"))
        assert gc.report(argparse.Namespace(out=path)) == 0
        out = capsys.readouterr().out
        assert "judge: judge-a" in out
        assert "population: ALL (n=4)" in out
        assert "population: NON-EMPTY (n=3)" in out
        assert "G1" in out
        assert "G2" in out
        assert "G3" in out

    def test_warns_when_rows_carry_unruled_pairs(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rows = self._spread("judge-a")
        rows[1]["judge_failures"] = 2
        path = _write_jsonl(tmp_path / "c.jsonl", rows)
        assert gc.report(argparse.Namespace(out=path)) == 0
        out = capsys.readouterr().out
        assert "1 rows carry unruled pairs" in out
        assert "2 failed pair calls" in out

    def test_a_single_judge_cannot_answer_g3(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = _write_jsonl(tmp_path / "c.jsonl", self._spread("judge-a"))
        assert gc.report(argparse.Namespace(out=path)) == 0
        assert "only one judge present" in capsys.readouterr().out

    def test_two_judges_get_kappa_beside_raw_agreement(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Raw agreement is printed as registered, then explicitly discounted."""
        rows = self._spread("judge-a") + self._spread("judge-b")
        path = _write_jsonl(tmp_path / "c.jsonl", rows)
        assert gc.report(argparse.Namespace(out=path)) == 0
        out = capsys.readouterr().out
        assert "raw agreement" in out
        assert "chance agreement" in out
        assert "Cohen's kappa" in out
        assert "positive overlap" in out

    def test_a_reshaped_grid_is_skipped_rather_than_crashing_the_report(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Editing a fixture between judge runs must not abort the whole report."""
        rows = [
            _record(judge_model="judge-a", n_goldens=1, n_candidates=2, decisions=[1, 0]),
            _record(judge_model="judge-b", n_goldens=2, n_candidates=2, decisions=[1, 0, 0, 0]),
        ]
        path = _write_jsonl(tmp_path / "c.jsonl", rows)
        assert gc.report(argparse.Namespace(out=path)) == 0
        out = capsys.readouterr().out
        assert "grids differ in shape" in out
        assert "no comparable decisions" in out


class TestMain:
    """Argument dispatch, so a typo in the parser cannot pass the suite."""

    def test_report_subcommand(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = _write_jsonl(tmp_path / "c.jsonl", [_record()])
        monkeypatch.setattr(sys, "argv", ["guardian_calibrate.py", "report", "--out", str(path)])
        assert gc.main() == 0

    def test_rescore_subcommand_defaults_to_a_dry_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = _write_jsonl(
            tmp_path / "c.jsonl",
            [_record(n_goldens=2, n_candidates=2, decisions=[1, 0, 1, 1], tp=1)],
        )
        monkeypatch.setattr(sys, "argv", ["guardian_calibrate.py", "rescore", "--out", str(path)])
        assert gc.main() == 0
        assert gc.load_records(path)[0]["tp"] == 1, "a bare rescore must not write"

    def test_run_subcommand_dispatches_to_the_async_runner(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bench = _bench_dir(tmp_path)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "guardian_calibrate.py",
                "run",
                "--dry-run",
                "--results",
                str(bench / "results.jsonl"),
                "--fixtures",
                str(bench),
            ],
        )
        assert gc.main() == 0
        assert "judge calls projected" in capsys.readouterr().out
