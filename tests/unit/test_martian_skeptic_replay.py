"""Replaying a skeptic over frozen Martian reviews, scored on their judge grids (#246 G)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from guardian_stubs import StubProvider

from cgis.guardian.findings import Finding

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

import martian_skeptic_replay as msr


def test_a_hidden_match_is_killed_and_hidden_noise_is_cut() -> None:
    # goldens g0, g1; candidates c0 (matches g0), c1 (noise), c2 (matches g1)
    grid = [1, 0, 0, 0, 0, 1]
    score = msr.score_against_grid(grid, n_goldens=2, n_candidates=3, visible=[1, 2])
    assert score == msr.GridScore(
        matched_before=2, matched_after=1, killed=1, noise_before=1, noise_after=1
    )


def test_hiding_only_noise_kills_nothing() -> None:
    grid = [1, 0, 0, 0, 0, 1]
    score = msr.score_against_grid(grid, n_goldens=2, n_candidates=3, visible=[0, 2])
    assert (score.killed, score.noise_after) == (0, 0)


def test_a_surviving_duplicate_keeps_the_golden() -> None:
    grid = [1, 1]  # one golden, two candidates that both match it
    score = msr.score_against_grid(grid, n_goldens=1, n_candidates=2, visible=[1])
    assert (score.matched_after, score.killed, score.noise_after) == (1, 0, 0)


def test_a_grid_that_does_not_fit_is_refused() -> None:
    with pytest.raises(ValueError, match="grid"):
        msr.score_against_grid([1, 0], n_goldens=2, n_candidates=2, visible=[0])


def test_the_clone_follows_the_pr_url() -> None:
    url = "https://github.com/getsentry/sentry/pull/80168"
    assert msr.clone_dir(url, Path("ws")) == Path("ws/getsentry__sentry")


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


@pytest.mark.asyncio
async def test_replay_one_judges_the_candidates_and_scores_them() -> None:
    findings = [_finding("a.py"), _finding("b.py")]
    grid = [1, 0]  # one golden, matched by candidate 0
    provider = StubProvider(
        [
            '{"verdict": "refuted", "impact_score": 0, "rationale": "r"}',
            '{"verdict": "refuted", "impact_score": 0, "rationale": "r"}',
        ]
    )
    row = await msr.replay_one(provider, findings, "", grid, 1, misdirected=False)
    assert row["killed"] == 1
    assert row["noise_after"] == 0
    assert [v["verdict"] for v in row["verdicts"]] == ["refuted", "refuted"]
    assert row["unruled"] == 0


@pytest.mark.asyncio
async def test_an_unruled_finding_stays_visible_and_is_counted() -> None:
    findings = [_finding("a.py")]
    row = await msr.replay_one(StubProvider(["not json"]), findings, "", [1], 1, misdirected=False)
    assert row["unruled"] == 1
    assert row["killed"] == 0


@pytest.mark.asyncio
async def test_the_misdirected_flag_reaches_the_skeptic() -> None:
    provider = StubProvider(['{"verdict": "misdirected", "impact_score": 4, "rationale": "r"}'])
    row = await msr.replay_one(provider, [_finding("a.py")], "", [1], 1, misdirected=True)
    assert "misdirected" in provider.prompts[0]
    assert row["misdirected"] == 1
    assert row["killed"] == 0  # kept visible


def _jrow(
    url: str, decisions: list[int | None], arm: str = "graph", **kw: object
) -> dict[str, object]:
    n_c = kw.pop("n_candidates", len(decisions))
    return {
        "url": url,
        "arm": arm,
        "project": "p",
        "judge_model": kw.pop("judge_model", "j"),
        "decisions": decisions,
        "n_goldens": 1,
        "n_candidates": n_c,
    }


def _write(path: Path, rows: list[dict[str, object]]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def test_grids_are_keyed_by_url_and_arm(tmp_path: Path) -> None:
    """Review of #726: a graph and an ablated row for one PR must not overwrite each other."""
    grids = msr.load_grids(
        _write(tmp_path / "j.jsonl", [_jrow("u", [1, 0]), _jrow("u", [0, 1], arm="ablated")])
    )
    assert grids[("u", "graph")].decisions == [1, 0]
    assert grids[("u", "ablated")].decisions == [0, 1]


def test_a_later_failed_row_drops_only_its_own_arm(tmp_path: Path) -> None:
    rows = [_jrow("u", [1, 0]), _jrow("u", [1, 0], arm="ablated"), _jrow("u", [1, None])]
    grids = msr.load_grids(_write(tmp_path / "j.jsonl", rows))
    assert set(grids) == {("u", "ablated")}


def test_a_later_clean_row_replaces_a_failed_one(tmp_path: Path) -> None:
    grids = msr.load_grids(
        _write(tmp_path / "j.jsonl", [_jrow("u", [1, None]), _jrow("u", [0, 1])])
    )
    assert grids[("u", "graph")].decisions == [0, 1]


def test_a_misfit_later_row_does_not_leave_a_stale_grid(tmp_path: Path) -> None:
    rows = [_jrow("u", [1, 0]), _jrow("u", [1, 0, 1], n_candidates=2)]
    assert msr.load_grids(_write(tmp_path / "j.jsonl", rows)) == {}


def test_one_judge_per_file(tmp_path: Path) -> None:
    rows = [_jrow("u1", [1]), _jrow("u2", [1], judge_model="other")]
    path = _write(tmp_path / "j.jsonl", rows)
    with pytest.raises(ValueError, match="judge"):
        msr.load_grids(path)


def _review(url: str, findings: list[Finding]) -> dict[str, object]:
    return {
        "url": url,
        "project": "p",
        "pr_slice": "graph",
        "base_sha": "b",
        "head_sha": "h",
        "had_graph": True,
        "finder_model": "gemini-2.5-flash",
        "skeptic_model": None,
        "findings": [f.model_dump() for f in findings],
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "duration_s": 0.0,
        "parse_failed": False,
        "guardian_sha": "x",
        "finder_provider": "gemini",
        "review_fingerprint": "f",
        "review_fingerprint_source": "measured",
        "reviewed_at": "t",
    }


def _main_args(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: list[str]) -> Path:
    reviews = tmp_path / "gemini-run1.jsonl"
    judged = tmp_path / "judged.jsonl"
    rows = [_review(f"https://github.com/o/r/pull/{n}", [_finding("a.py")]) for n in (1, 2)]
    reviews.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    grid_rows = [_jrow(str(r["url"]), [1]) for r in rows]
    judged.write_text("".join(json.dumps(r) + "\n" for r in grid_rows), encoding="utf-8")
    out = tmp_path / "out.jsonl"
    monkeypatch.setattr(msr, "pr_diff", lambda _r, _w: "")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "x",
            "--pair",
            f"{reviews}:{judged}",
            "--model",
            "claude-haiku-5-5",
            "--out",
            str(out),
            *extra,
        ],
    )
    return out


def test_main_writes_rows_and_resumes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = _main_args(tmp_path, monkeypatch, [])
    stub = StubProvider(['{"verdict": "confirmed", "impact_score": 5, "rationale": "r"}'] * 2)
    monkeypatch.setattr(msr, "arm_provider", lambda *_a: (stub, "claude-haiku-5-5"))
    assert msr.main() == 0
    assert len(out.read_text().splitlines()) == 2
    assert msr.main() == 0  # resumed: nothing left, no call made
    assert len(stub.prompts) == 2


def test_main_exits_3_when_the_budget_stops_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cgis.guardian.providers.base import ProviderUsage  # noqa: PLC0415

    class _Metered(StubProvider):
        async def generate_structured(self, s: str, u: str, schema: type) -> str:  # type: ignore[override]
            self._record_usage(ProviderUsage(prompt_tokens=1_000_000, completion_tokens=0))
            return await super().generate_structured(s, u, schema)

    out = _main_args(
        tmp_path, monkeypatch, ["--budget-usd", "0.05", "--price", "claude-haiku-5-5=0.10:0.50"]
    )
    stub = _Metered(['{"verdict": "confirmed", "impact_score": 5, "rationale": "r"}'] * 2)
    monkeypatch.setattr(msr, "arm_provider", lambda *_a: (stub, "claude-haiku-5-5"))
    assert msr.main() == 3
    assert len(out.read_text().splitlines()) == 1  # $0.10 on the first review, past $0.05


def test_columns_follow_candidate_findings_not_all_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of #726: a refuted finding is not a judge column; the next finding takes its place."""
    refuted = _finding("a.py").model_copy(update={"verdict": "refuted"})
    real = _finding("b.py").model_copy(update={"title": "the real one"})
    reviews = _write(
        tmp_path / "r.jsonl", [_review("https://github.com/o/r/pull/1", [refuted, real])]
    )
    judged = _write(tmp_path / "j.jsonl", [_jrow("https://github.com/o/r/pull/1", [1])])
    out = tmp_path / "out.jsonl"
    stub = StubProvider(['{"verdict": "refuted", "impact_score": 0, "rationale": "r"}'])
    monkeypatch.setattr(msr, "pr_diff", lambda _r, _w: "")
    monkeypatch.setattr(msr, "arm_provider", lambda *_a: (stub, "claude-haiku-5-5"))
    monkeypatch.setattr(
        sys,
        "argv",
        ["x", "--pair", f"{reviews}:{judged}", "--model", "claude-haiku-5-5", "--out", str(out)],
    )
    assert msr.main() == 0
    [row] = [json.loads(line) for line in out.read_text().splitlines()]
    assert row["n_candidates"] == 1
    assert "the real one" in stub.prompts[0]
    assert row["killed"] == 1


def test_a_grid_for_another_candidate_count_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviews = _write(
        tmp_path / "r.jsonl", [_review("https://github.com/o/r/pull/1", [_finding("a.py")])]
    )
    judged = _write(tmp_path / "j.jsonl", [_jrow("https://github.com/o/r/pull/1", [1, 0])])
    out = tmp_path / "out.jsonl"
    monkeypatch.setattr(msr, "pr_diff", lambda _r, _w: "")
    monkeypatch.setattr(msr, "arm_provider", lambda *_a: (StubProvider([]), "claude-haiku-5-5"))
    monkeypatch.setattr(
        sys,
        "argv",
        ["x", "--pair", f"{reviews}:{judged}", "--model", "claude-haiku-5-5", "--out", str(out)],
    )
    assert msr.main() == 0
    assert not out.exists() or out.read_text() == ""


def test_a_diff_that_cannot_be_made_skips_the_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _main_args(tmp_path, monkeypatch, [])

    def _no_diff(_r: object, _w: object) -> str:
        raise subprocess.CalledProcessError(128, "git")

    monkeypatch.setattr(msr, "pr_diff", _no_diff)
    stub = StubProvider([])
    monkeypatch.setattr(msr, "arm_provider", lambda *_a: (stub, "claude-haiku-5-5"))
    assert msr.main() == 0
    assert stub.prompts == []


def test_an_unreadable_judged_line_is_skipped_with_a_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "j.jsonl"
    path.write_text(json.dumps(_jrow("u", [1])) + "\n{not json\n", encoding="utf-8")
    assert set(msr.load_grids(path)) == {("u", "graph")}
    assert "unreadable" in capsys.readouterr().err


def test_a_torn_last_output_line_does_not_stop_a_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _main_args(tmp_path, monkeypatch, [])
    stub = StubProvider(['{"verdict": "confirmed", "impact_score": 5, "rationale": "r"}'] * 2)
    monkeypatch.setattr(msr, "arm_provider", lambda *_a: (stub, "claude-haiku-5-5"))
    assert msr.main() == 0
    out.write_text(out.read_text().splitlines()[0] + '\n{"url": "torn', encoding="utf-8")
    stub2 = StubProvider(['{"verdict": "confirmed", "impact_score": 5, "rationale": "r"}'])
    monkeypatch.setattr(msr, "arm_provider", lambda *_a: (stub2, "claude-haiku-5-5"))
    assert msr.main() == 0
    assert len(stub2.prompts) == 1  # only the review whose row was torn is redone


def test_a_bad_pair_is_a_usage_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub = StubProvider([])
    monkeypatch.setattr(msr, "arm_provider", lambda *_a: (stub, "claude-haiku-5-5"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "x",
            "--pair",
            str(tmp_path / "missing.jsonl"),
            "--model",
            "claude-haiku-5-5",
            "--out",
            str(tmp_path / "o.jsonl"),
        ],
    )
    with pytest.raises(SystemExit) as stop:
        msr.main()
    assert stop.value.code == 2  # argparse's usage error, not a crash


def test_a_missing_git_skips_the_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _main_args(tmp_path, monkeypatch, [])

    def _no_git(_r: object, _w: object) -> str:
        _msg = "git"
        raise FileNotFoundError(_msg)

    monkeypatch.setattr(msr, "pr_diff", _no_git)
    stub = StubProvider([])
    monkeypatch.setattr(msr, "arm_provider", lambda *_a: (stub, "claude-haiku-5-5"))
    assert msr.main() == 0
    assert stub.prompts == []
