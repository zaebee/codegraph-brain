"""Tests for the Phase 2 planner (scripts/guardian_martian.py, #342).

`plan` decides which PRs each gate is computed over. It spends nothing, which
makes it easy to treat as harmless plumbing — it is the opposite: a planner that
quietly mis-slices two PRs moves G5 without anyone seeing a number change.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

from cgis.guardian.martian import SliceCounts

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

import guardian_martian as gm

CORPUS_FIXTURE = [
    {
        "pr_title": "graph one",
        "url": "https://github.com/o/r/pull/1",
        "comments": [{"comment": "c1", "severity": "High", "category": "bug"}],
    },
    {
        "pr_title": "diff one",
        "url": "https://github.com/o/r/pull/2",
        "comments": [{"comment": "c2", "severity": "High", "category": "bug"}],
    },
    {
        "pr_title": "flagged",
        "url": "https://github.com/o/r/pull/3",
        "az_comment": "reviewed commit is not in the repo",
        "comments": [{"comment": "c3", "severity": "High", "category": "bug"}],
    },
]

FILES = {
    "https://github.com/o/r/pull/1": ("src/a.py",),
    "https://github.com/o/r/pull/2": ("lib/a.rb",),
    "https://github.com/o/r/pull/3": ("src/b.py",),
}


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    directory = tmp_path / "corpus"
    directory.mkdir()
    (directory / "proj.json").write_text(json.dumps(CORPUS_FIXTURE), encoding="utf-8")
    return directory


def _args(corpus: Path, out: Path, **overrides: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "corpus": corpus,
        "out": out,
        "profile": "core",
        "refresh": False,
    }
    return argparse.Namespace(**(base | overrides))


def _stub_fetch(monkeypatch: pytest.MonkeyPatch, mapping: dict[str, tuple[str, ...]]) -> list[str]:
    """Replace the gh call; returns the list of URLs it was asked for."""
    asked: list[str] = []

    def fetch(pr: object) -> tuple[str, ...]:
        url = pr.url  # type: ignore[attr-defined]
        asked.append(url)
        if url not in mapping:
            _msg = f"no diff for {url}"
            raise RuntimeError(_msg)
        return mapping[url]

    monkeypatch.setattr(gm, "fetch_changed_files", fetch)
    return asked


class TestFetchChangedFiles:
    """The gh wrapper, including the flag that is easy to read as decoration."""

    def test_parses_names_and_drops_blank_lines(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[dict[str, object]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append({"cmd": cmd, **kwargs})
            return subprocess.CompletedProcess(cmd, 0, stdout="a.py\n\nb/c.ts\n", stderr="")

        monkeypatch.setattr(gm.subprocess, "run", fake_run)
        pr = gm.BenchPr(project="p", pr_title="t", url="https://github.com/o/r/pull/1", comments=[])
        assert gm.fetch_changed_files(pr) == ("a.py", "b/c.ts")

    def test_stdin_is_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Not decoration: gh reads stdin and eats a caller's loop input.

        That is how the reconnaissance for this work first processed 49 of 50
        PRs and lost one without any error.
        """
        seen: dict[str, object] = {}

        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            seen.update(kwargs)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(gm.subprocess, "run", fake_run)
        pr = gm.BenchPr(project="p", pr_title="t", url="https://github.com/o/r/pull/1", comments=[])
        gm.fetch_changed_files(pr)
        assert seen["stdin"] == subprocess.DEVNULL
        assert seen["check"] is True


class TestLoadCached:
    def test_missing_file_is_an_empty_cache(self, tmp_path: Path) -> None:
        assert gm.load_cached(tmp_path / "nope.json") == {}

    def test_rows_that_failed_are_not_cached(self, tmp_path: Path) -> None:
        """Caching a failure would make one bad network moment permanent."""
        path = tmp_path / "plan.json"
        path.write_text(
            json.dumps(
                [
                    {"url": "u1", "changed_files": ["a.py"], "fetch_error": None},
                    {"url": "u2", "changed_files": [], "fetch_error": "boom"},
                ]
            ),
            encoding="utf-8",
        )
        assert gm.load_cached(path) == {"u1": ("a.py",)}


class TestPlan:
    def test_writes_a_row_per_pr_and_classifies_them(
        self,
        corpus: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _stub_fetch(monkeypatch, FILES)
        out = tmp_path / "plan.json"
        monkeypatch.setattr(gm, "REGISTERED", {"graph": 1, "diff-only": 1})
        assert gm.plan(_args(corpus, out)) == 0
        rows = json.loads(out.read_text(encoding="utf-8"))
        assert [(r["number"], r["pr_slice"], r["reproducible"]) for r in rows] == [
            (1, "graph", True),
            (2, "diff-only", True),
            (3, "graph", False),
        ]
        assert "Populations match the registration" in capsys.readouterr().out

    def test_second_run_uses_the_cache(
        self, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = tmp_path / "plan.json"
        monkeypatch.setattr(gm, "REGISTERED", {"graph": 1, "diff-only": 1})
        _stub_fetch(monkeypatch, FILES)
        gm.plan(_args(corpus, out))
        asked = _stub_fetch(monkeypatch, FILES)
        gm.plan(_args(corpus, out))
        assert asked == [], "a cached plan must cost no network calls"

    def test_refresh_ignores_the_cache(
        self, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = tmp_path / "plan.json"
        monkeypatch.setattr(gm, "REGISTERED", {"graph": 1, "diff-only": 1})
        _stub_fetch(monkeypatch, FILES)
        gm.plan(_args(corpus, out))
        asked = _stub_fetch(monkeypatch, FILES)
        gm.plan(_args(corpus, out, refresh=True))
        assert len(asked) == 3

    def test_a_fetch_failure_is_reported_and_fails_the_check(
        self,
        corpus: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`unknown` must never be folded into a slice, and must not exit 0."""
        _stub_fetch(monkeypatch, {k: v for k, v in FILES.items() if not k.endswith("/1")})
        assert gm.plan(_args(corpus, tmp_path / "plan.json")) == 1
        captured = capsys.readouterr()
        assert "FAILED TO FETCH" in captured.out
        assert "could not be classified" in captured.err


class TestCheckRegistration:
    """A population that drifted from the registration is an error, not a note."""

    @staticmethod
    def _pop(graph: int, diff: int, unknown: int = 0) -> dict[str, SliceCounts]:
        return {
            "graph": SliceCounts(prs=graph, comments=0),
            "diff-only": SliceCounts(prs=diff, comments=0),
            gm.UNKNOWN_SLICE: SliceCounts(prs=unknown, comments=0),
        }

    def test_match_is_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert gm.check_registration(self._pop(19, 26)) == 0
        assert "match the registration" in capsys.readouterr().out

    def test_drift_names_both_numbers(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert gm.check_registration(self._pop(18, 26)) == 1
        assert "registered 19, measured 18" in capsys.readouterr().err

    def test_unknown_short_circuits_before_comparing(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Comparing an incomplete population against the registration is meaningless."""
        assert gm.check_registration(self._pop(19, 26, unknown=1)) == 1
        err = capsys.readouterr().err
        assert "could not be classified" in err
        assert "DRIFT" not in err


class TestMain:
    def test_plan_subcommand_dispatches(
        self, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_fetch(monkeypatch, FILES)
        monkeypatch.setattr(gm, "REGISTERED", {"graph": 1, "diff-only": 1})
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "guardian_martian.py",
                "plan",
                "--corpus",
                str(corpus),
                "--out",
                str(tmp_path / "p.json"),
            ],
        )
        assert gm.main() == 0
