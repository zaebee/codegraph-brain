"""Tests for the Phase 2 planner (scripts/guardian_martian.py, #342).

`plan` decides which PRs each gate is computed over. It spends nothing, which
makes it easy to treat as harmless plumbing — it is the opposite: a planner that
quietly mis-slices two PRs moves G5 without anyone seeing a number change.
"""

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

from cgis.extractors.registry import build_extractors
from cgis.guardian.martian import SliceCounts
from cgis.pipeline import IngestionPipeline
from cgis.storage.sqlite_store import SQLiteStore

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

import guardian_martian as gm

from cgis.guardian import calibrate as gm_cal
from cgis.guardian.findings import Finding

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

    def test_a_failure_carries_ghs_stderr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The recorded message is the only diagnostic, so it has to say something.

        `check=True` would raise `CalledProcessError`, which stringifies without
        the captured stderr — an expired token and a rate limit would look
        identical on the plan row.
        """

        def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="gh: rate limited\n")

        monkeypatch.setattr(gm.subprocess, "run", fake_run)
        pr = gm.BenchPr(project="p", pr_title="t", url="https://github.com/o/r/pull/1", comments=[])
        with pytest.raises(RuntimeError, match="rate limited"):
            gm.fetch_changed_files(pr)


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

    def test_an_unreadable_cache_degrades_loudly(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Falling back to fetching is free and correct; doing so in silence is not."""
        path = tmp_path / "plan.json"
        path.write_text("{ truncated", encoding="utf-8")
        assert gm.load_cached(path) == {}
        assert "Ignoring unreadable cache" in capsys.readouterr().err

    def test_a_cache_whose_schema_moved_is_also_ignored(self, tmp_path: Path) -> None:
        path = tmp_path / "plan.json"
        path.write_text(json.dumps([{"url": "u1", "files": ["a.py"]}]), encoding="utf-8")
        assert gm.load_cached(path) == {}


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

    def test_columns_stay_aligned_for_a_long_project_name(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`cal_dot_com` is 11 characters and overflowed a hardcoded width of 10.

        The width is measured from the data now, so the header and every row
        agree regardless of what a project is called.
        """
        directory = tmp_path / "corpus"
        directory.mkdir()
        (directory / "a_very_long_project_name.json").write_text(
            json.dumps(CORPUS_FIXTURE[:1]), encoding="utf-8"
        )
        _stub_fetch(monkeypatch, FILES)
        monkeypatch.setattr(gm, "REGISTERED", {"graph": 1, "diff-only": 0})
        gm.plan(_args(directory, tmp_path / "p.json"))
        lines = capsys.readouterr().out.splitlines()
        header = next(ln for ln in lines if ln.startswith("project"))
        row = next(ln for ln in lines if ln.startswith("  a_very_long_project_name"))
        # Both columns are right-aligned, so the count must end where "PRs" does.
        end_of_column = header.index("PRs") + len("PRs")
        assert row[:end_of_column].rstrip().endswith("1")


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


class TestSelected:
    """Nothing expensive may be spent on a PR whose result could not be used."""

    @staticmethod
    def _plans() -> list[gm.PrPlan]:
        return [
            gm.PrPlan(
                project="p",
                url=f"https://github.com/o/r/pull/{n}",
                repo="o/r",
                number=n,
                reproducible=repro,
                pr_slice="graph",
                changed_files=("a.py",),
                golden_comments=1,
            )
            for n, repro in ((1, True), (2, False), (3, True))
        ]

    def test_drops_the_unreproducible(self) -> None:
        assert [p.number for p in gm.selected(self._plans(), None, None)] == [1, 3]

    def test_narrows_to_one_pr(self) -> None:
        assert [p.number for p in gm.selected(self._plans(), 3, None)] == [3]

    def test_a_flagged_pr_cannot_be_selected_by_number(self) -> None:
        """Asking for it by hand must not override the exclusion."""
        assert gm.selected(self._plans(), 2, None) == []

    def test_limit_applies_after_filtering(self) -> None:
        assert [p.number for p in gm.selected(self._plans(), None, 1)] == [1]

    def test_limit_zero_selects_nothing(self) -> None:
        """`--limit 0` used to mean "no limit", because 0 is falsy."""
        assert gm.selected(self._plans(), None, 0) == []
        assert len(gm.selected(self._plans(), None, None)) == 2


class TestGraphAlignment:
    """The check that turns an invisible failure into a loud one."""

    def _db(self, tmp_path: Path, files: dict[str, str]) -> Path:
        tree = tmp_path / "tree"
        for rel, src in files.items():
            (tree / rel).parent.mkdir(parents=True, exist_ok=True)
            (tree / rel).write_text(src, encoding="utf-8")
        db = tmp_path / "g.db"
        with SQLiteStore(str(db)) as store:
            IngestionPipeline(build_extractors([])).run(str(tree), store=store)
        return db

    def test_counts_the_changed_files_the_graph_knows(self, tmp_path: Path) -> None:
        db = self._db(tmp_path, {"src/pkg/mod.py": "def f() -> int:\n    return 1\n"})
        assert gm.graph_alignment(db, ["src/pkg/mod.py", "src/pkg/absent.py"]) == (1, 2)

    def test_unsupported_files_are_not_counted_against_the_graph(self, tmp_path: Path) -> None:
        db = self._db(tmp_path, {"src/pkg/mod.py": "def f() -> int:\n    return 1\n"})
        assert gm.graph_alignment(db, ["README.md", "a.rb"]) == (0, 0)

    def test_a_graph_that_knows_nothing_is_refused(self, tmp_path: Path) -> None:
        """The real bug this catches: whole-checkout ingest vs source_root="src".

        Node ids keep the full relative path, so a collector configured with
        source_root="src" derives `pkg.mod` for a node stored as `src.pkg.mod`.
        Every lookup misses, the review is produced with no graph context, and
        nothing says so.
        """
        db = self._db(tmp_path, {"src/pkg/mod.py": "def f() -> int:\n    return 1\n"})
        with pytest.raises(RuntimeError, match="silently have no graph context"):
            gm.require_alignment(db, ["src/pkg/other.py"])

    def test_alignment_holds_for_the_root_this_runner_uses(self, tmp_path: Path) -> None:
        """SOURCE_ROOT must be the empty string while `prepare` ingests whole checkouts."""
        assert gm.SOURCE_ROOT == ""
        db = self._db(tmp_path, {"src/pkg/mod.py": "def f() -> int:\n    return 1\n"})
        assert gm.require_alignment(db, ["src/pkg/mod.py"]).endswith("1/1 changed files")


def _fake_runner(
    monkeypatch: pytest.MonkeyPatch, outcomes: dict[str, tuple[int, str, str]]
) -> list[list[str]]:
    """Record every subprocess command; answer by first-matching key."""
    seen: list[list[str]] = []

    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        joined = " ".join(cmd)
        for key, (code, out, err) in outcomes.items():
            if key in joined:
                return subprocess.CompletedProcess(cmd, code, stdout=out, stderr=err)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(gm.subprocess, "run", fake_run)
    return seen


class TestPrRefs:
    def test_returns_base_and_head(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _fake_runner(monkeypatch, {"gh pr view": (0, '{"baseRefOid":"b1","headRefOid":"h1"}', "")})
        assert gm.pr_refs("https://github.com/o/r/pull/1") == ("b1", "h1")

    def test_a_failure_carries_stderr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _fake_runner(monkeypatch, {"gh pr view": (1, "", "could not resolve to a PullRequest")})
        with pytest.raises(RuntimeError, match="could not resolve"):
            gm.pr_refs("https://github.com/o/r/pull/1")


class TestEnsureCheckout:
    def test_clones_once_then_fetches_both_shas_and_checks_out(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both SHAs, not just the head.

        `ContextCollector` diffs `base...HEAD`; a missing base makes the diff
        silently empty — a review of nothing, scored as finding nothing.
        """
        seen = _fake_runner(monkeypatch, {})
        clone = gm.ensure_checkout("o/r", "baseaaa", "headbbb", tmp_path)
        verbs = [c[1] for c in seen]
        assert verbs == ["clone", "fetch", "fetch", "checkout"]
        assert [c[-1] for c in seen if c[1] == "fetch"] == ["baseaaa", "headbbb"]
        assert clone == tmp_path / "o__r"

    def test_an_existing_clone_is_reused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "o__r" / ".git").mkdir(parents=True)
        seen = _fake_runner(monkeypatch, {})
        gm.ensure_checkout("o/r", "b", "h", tmp_path)
        assert "clone" not in [c[1] for c in seen]

    def test_git_failures_name_the_stderr(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _fake_runner(monkeypatch, {"clone": (128, "", "repository not found")})
        with pytest.raises(RuntimeError, match="repository not found"):
            gm.ensure_checkout("o/r", "b", "h", tmp_path)


class TestEnsureGraph:
    def test_removes_the_previous_database_first(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A graph carried over from another commit answers with absent nodes."""
        db = tmp_path / "g.db"
        db.write_text("stale", encoding="utf-8")
        existed: list[bool] = []

        def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            existed.append(db.exists())
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(gm.subprocess, "run", fake_run)
        gm.ensure_graph(tmp_path / "tree", db, "headsha")
        assert existed == [False]

    def test_a_failed_ingest_raises_with_stderr(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _fake_runner(monkeypatch, {"ingest": (1, "", "tree-sitter exploded")})
        with pytest.raises(RuntimeError, match="tree-sitter exploded"):
            gm.ensure_graph(tmp_path / "tree", tmp_path / "g.db", "headsha")


class TestPrepare:
    @staticmethod
    def _plan_file(tmp_path: Path, *, pr_slice: str = "graph") -> Path:
        path = tmp_path / "plan.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "project": "p",
                        "url": "https://github.com/o/r/pull/1",
                        "repo": "o/r",
                        "number": 1,
                        "reproducible": True,
                        "pr_slice": pr_slice,
                        "changed_files": ["src/a.py"],
                        "golden_comments": 1,
                    }
                ]
            ),
            encoding="utf-8",
        )
        return path

    def _args(self, tmp_path: Path, **overrides: object) -> argparse.Namespace:
        base: dict[str, object] = {
            "plan": self._plan_file(tmp_path),
            "workspace": tmp_path / "ws",
            "pr": None,
            "limit": None,
            "no_graph": False,
        }
        return argparse.Namespace(**(base | overrides))

    def test_missing_plan_says_what_to_run(self, tmp_path: Path) -> None:
        args = self._args(tmp_path, plan=tmp_path / "absent.json")
        with pytest.raises(FileNotFoundError, match=r"Run `guardian_martian\.py plan` first"):
            gm.prepare(args)

    def test_checks_out_and_ingests_a_graph_pr(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(gm, "pr_refs", lambda url: ("b", "h" * 12))  # noqa: ARG005
        monkeypatch.setattr(gm, "ensure_checkout", lambda *a: tmp_path / "co")  # noqa: ARG005
        monkeypatch.setattr(gm, "ensure_graph", lambda *a: None)  # noqa: ARG005
        monkeypatch.setattr(gm, "require_alignment", lambda *a: "ingested, graph knows 1/1")  # noqa: ARG005
        assert gm.prepare(self._args(tmp_path)) == 0
        assert "graph knows 1/1" in capsys.readouterr().out

    def test_no_graph_skips_ingest(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gm, "pr_refs", lambda url: ("b", "h"))  # noqa: ARG005
        monkeypatch.setattr(gm, "ensure_checkout", lambda *a: tmp_path / "co")  # noqa: ARG005

        def boom(*_: object) -> None:
            _msg = "ingest must not run"
            raise AssertionError(_msg)

        monkeypatch.setattr(gm, "ensure_graph", boom)
        assert gm.prepare(self._args(tmp_path, no_graph=True)) == 0

    def test_a_failing_pr_is_recorded_and_exits_non_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """One unreachable repository must not abort the other forty-nine."""

        def boom(url: str) -> tuple[str, str]:  # noqa: ARG001
            _msg = "gh pr view exited 1: not found"
            raise RuntimeError(_msg)

        monkeypatch.setattr(gm, "pr_refs", boom)
        assert gm.prepare(self._args(tmp_path)) == 1
        assert "prepared 0/1" in capsys.readouterr().out

    def test_nothing_selected_is_an_error(self, tmp_path: Path) -> None:
        assert gm.prepare(self._args(tmp_path, pr=999)) == 1


class TestGraphProvenance:
    """One database per repository, several PRs per repository, each at its own commit."""

    def test_the_commit_is_recorded_beside_the_database(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = tmp_path / "g.db"

        def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            db.write_text("a graph", encoding="utf-8")  # what a real ingest leaves
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(gm.subprocess, "run", fake_run)
        gm.ensure_graph(tmp_path / "tree", db, "abc123")
        assert gm.graph_commit(db) == "abc123"

    def test_an_unmarked_database_reports_no_commit(self, tmp_path: Path) -> None:
        (tmp_path / "g.db").write_text("db", encoding="utf-8")
        assert gm.graph_commit(tmp_path / "g.db") is None

    def test_a_marker_without_its_database_is_not_provenance(self, tmp_path: Path) -> None:
        """It describes a graph that no longer exists.

        Trusting it would skip the rebuild and run a graph-slice PR with no
        graph at all — the same silent absence, one level down.
        """
        db = tmp_path / "g.db"
        db.with_name(db.name + gm.COMMIT_MARKER_SUFFIX).write_text("abc", encoding="utf-8")
        assert gm.graph_commit(db) is None

    def test_an_unmarked_database_is_rebuilt_rather_than_trusted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unknown provenance is the case the marker exists to catch.

        A database left by an older run carries no marker; skipping the rebuild
        for it would reuse a graph nobody can attribute to a commit.
        """
        (tmp_path / "ws" / "o__r" / ".git").mkdir(parents=True)
        (tmp_path / "ws" / "o__r.db").write_text("db", encoding="utf-8")
        rebuilt: list[str] = []
        _wire_review(monkeypatch, rebuilt)
        asyncio.run(gm.review_one(_review_row(), argparse.Namespace(workspace=tmp_path / "ws")))
        assert rebuilt == ["wanted-head"]

    def test_a_graph_built_from_another_commit_is_rebuilt_not_reused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The bug this guards: `prepare` over six PRs leaves one graph, of the last.

        Reviewing an earlier PR would then use a graph of a different tree, and
        `graph_alignment` would pass anyway because the file names mostly still
        match. Wrong, and plausible.
        """
        (tmp_path / "ws" / "o__r" / ".git").mkdir(parents=True)
        db = tmp_path / "ws" / "o__r.db"
        db.write_text("db", encoding="utf-8")
        db.with_name(db.name + ".commit").write_text("some-other-commit", encoding="utf-8")
        rebuilt: list[str] = []
        _wire_review(monkeypatch, rebuilt)
        asyncio.run(gm.review_one(_review_row(), argparse.Namespace(workspace=tmp_path / "ws")))
        assert rebuilt == ["wanted-head"]


class _StubProvider:
    cumulative_usage = SimpleNamespace(prompt_tokens=0, completion_tokens=0)


def _wire_review(monkeypatch: pytest.MonkeyPatch, rebuilt: list[str]) -> None:
    """Stub everything `review_one` touches; record which head was re-ingested."""
    monkeypatch.setattr(gm, "pr_refs", lambda _url: ("base", "wanted-head"))
    monkeypatch.setattr(gm, "_git", lambda *a, **k: "sha\n")  # noqa: ARG005
    monkeypatch.setattr(gm, "graph_alignment", lambda *a: (1, 1))  # noqa: ARG005
    monkeypatch.setattr(gm, "ensure_graph", lambda _c, _d, head: rebuilt.append(head))
    monkeypatch.setattr(gm, "build_provider", lambda _env: (_StubProvider(), "m"))
    monkeypatch.setattr(gm, "build_skeptic_provider", lambda _env, primary: None)  # noqa: ARG005
    monkeypatch.setattr(gm, "ContextCollector", lambda **_: object())

    async def fake_routed(**_: object) -> object:
        return SimpleNamespace(result=SimpleNamespace(findings=[], parse_failed=False))

    monkeypatch.setattr(gm, "run_review_routed", fake_routed)


class TestWorkspaceHygiene:
    """Two ways the workspace can defeat a run before any model is called."""

    def test_alignment_does_not_create_a_database_it_only_reads(self, tmp_path: Path) -> None:
        """SQLiteStore makes a 48 KB empty file when the path is absent."""
        db = tmp_path / "absent.db"
        assert gm.graph_alignment(db, ["src/a.py"]) == (0, 1)
        assert not db.exists()

    def test_an_interrupted_clone_is_cleared_before_retrying(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """git refuses a non-empty destination, so a partial clone poisons every rerun."""
        stale = tmp_path / "o__r"
        (stale / "half-downloaded").mkdir(parents=True)
        seen = _fake_runner(monkeypatch, {})
        gm.ensure_checkout("o/r", "b", "h", tmp_path)
        assert not stale.joinpath("half-downloaded").exists()
        assert next(c[1] for c in seen) == "clone"

    def test_git_needs_a_verb(self) -> None:
        """`_git()` with no arguments used to raise IndexError while building its message.

        Now the signature refuses it, so the failure is at the call site with a
        name attached rather than inside the error handler.
        """
        with pytest.raises(TypeError):
            gm._git()  # type: ignore[call-arg]  # noqa: SLF001

    def test_stale_wal_sidecars_are_removed_with_the_database(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A clean close checkpoints them away; an interrupted ingest does not."""
        db = tmp_path / "g.db"
        for name in ("g.db", "g.db-wal", "g.db-shm"):
            (tmp_path / name).write_text("stale", encoding="utf-8")
        _fake_runner(monkeypatch, {})
        gm.ensure_graph(tmp_path / "tree", db, "headsha")
        # The stubbed ingest creates no database, so only the marker it writes
        # on success survives — the point is that the stale -wal/-shm are gone.
        assert [p.name for p in sorted(tmp_path.glob("g.db*"))] == ["g.db.commit"]

    def test_a_file_where_the_clone_should_be_is_removed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """rmtree raises NotADirectoryError on a file, which would abort the run."""
        (tmp_path / "o__r").write_text("not a repo", encoding="utf-8")
        seen = _fake_runner(monkeypatch, {})
        gm.ensure_checkout("o/r", "b", "h", tmp_path)
        assert next(c[1] for c in seen) == "clone"


class TestPrRefsParsing:
    """The recorded message is the only diagnostic, so it must quote gh."""

    def test_unparseable_output_is_quoted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _fake_runner(monkeypatch, {"gh pr view": (0, "gh: not logged in\n", "")})
        with pytest.raises(RuntimeError, match="not logged in"):
            gm.pr_refs("https://github.com/o/r/pull/1")

    def test_a_missing_key_names_itself(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _fake_runner(monkeypatch, {"gh pr view": (0, '{"baseRefOid":"b"}', "")})
        with pytest.raises(RuntimeError, match="headRefOid"):
            gm.pr_refs("https://github.com/o/r/pull/1")


class TestReview:
    """The only step that spends money, so resume and isolation are not niceties."""

    @staticmethod
    def _plan_file(tmp_path: Path, count: int = 2) -> Path:
        path = tmp_path / "plan.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "project": "p",
                        "url": f"https://github.com/o/r/pull/{n}",
                        "repo": "o/r",
                        "number": n,
                        "reproducible": True,
                        "pr_slice": "graph",
                        "changed_files": ["src/a.py"],
                        "golden_comments": 1,
                    }
                    for n in range(1, count + 1)
                ]
            ),
            encoding="utf-8",
        )
        return path

    def _args(self, tmp_path: Path, **overrides: object) -> argparse.Namespace:
        base: dict[str, object] = {
            "plan": self._plan_file(tmp_path),
            "workspace": tmp_path / "ws",
            "out": tmp_path / "reviews.jsonl",
            "pr": None,
            "limit": None,
            "dry_run": False,
        }
        return argparse.Namespace(**(base | overrides))

    @staticmethod
    def _record(url: str) -> gm.ReviewRecord:
        return gm.ReviewRecord(
            url=url,
            project="p",
            pr_slice="graph",
            base_sha="b",
            head_sha="h",
            had_graph=True,
            finder_model="m",
            skeptic_model=None,
            findings=[],
            prompt_tokens=1,
            completion_tokens=1,
            duration_s=1.0,
            parse_failed=False,
            guardian_sha="sha",
            reviewed_at="2026-08-12T00:00:00+00:00",
        )

    def test_dry_run_spends_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        async def boom(_row: object, _args: object) -> gm.ReviewRecord:
            _msg = "review_one must not run under --dry-run"
            raise AssertionError(_msg)

        monkeypatch.setattr(gm, "review_one", boom)
        assert asyncio.run(gm.review(self._args(tmp_path, dry_run=True))) == 0
        assert "2 to review" in capsys.readouterr().out

    def test_already_reviewed_prs_are_not_paid_for_twice(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = tmp_path / "reviews.jsonl"
        out.write_text(
            self._record("https://github.com/o/r/pull/1").model_dump_json() + "\n",
            encoding="utf-8",
        )
        asked: list[int] = []

        async def once(row: gm.PrPlan, _args: object) -> gm.ReviewRecord:
            asked.append(row.number)
            return self._record(row.url)

        monkeypatch.setattr(gm, "review_one", once)
        assert asyncio.run(gm.review(self._args(tmp_path, out=out))) == 0
        assert asked == [2], "a resumed run must skip what it already paid for"

    def test_one_failure_does_not_lose_the_rest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        async def half(row: gm.PrPlan, _args: object) -> gm.ReviewRecord:
            if row.number == 1:
                _msg = "provider exploded"
                raise RuntimeError(_msg)
            return self._record(row.url)

        monkeypatch.setattr(gm, "review_one", half)
        args = self._args(tmp_path)
        assert asyncio.run(gm.review(args)) == 1
        written = [json.loads(ln) for ln in args.out.read_text(encoding="utf-8").splitlines()]
        assert [r["url"] for r in written] == ["https://github.com/o/r/pull/2"]
        assert "provider exploded" in capsys.readouterr().err

    def test_records_are_flushed_as_they_are_earned(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A crash on PR 40 must not throw away the 39 already paid for."""
        seen: list[int] = []

        async def peek(row: gm.PrPlan, args: object) -> gm.ReviewRecord:
            if row.number == 2:
                seen.append(len(args.out.read_text(encoding="utf-8").splitlines()))  # type: ignore[attr-defined]
            return self._record(row.url)

        monkeypatch.setattr(gm, "review_one", peek)
        asyncio.run(gm.review(self._args(tmp_path)))
        assert seen == [1]


class TestDoneUrls:
    def test_absent_file_is_empty(self, tmp_path: Path) -> None:
        assert gm.done_urls(tmp_path / "nope.jsonl") == set()


def _review_finding(**overrides: object) -> Finding:
    """A finding as the recorder stores it."""
    base: dict[str, object] = {
        "file": "a.py",
        "line": 1,
        "severity": "major",
        "category": "logic",
        "title": "t",
        "evidence": "e",
        "problem": "p",
        "fix": "f",
        "confidence": 90,
    }
    return Finding.model_validate(base | overrides)


def _review_row(pr_slice: str = "graph") -> gm.PrPlan:
    """One prepared, reproducible PR plan."""
    return gm.PrPlan(
        project="p",
        url="https://github.com/o/r/pull/1",
        repo="o/r",
        number=1,
        reproducible=True,
        pr_slice=pr_slice,
        changed_files=("src/a.py",),
        golden_comments=1,
    )


class TestReviewOne:
    """The paid step. What it hands the collector decides what G5 measures."""

    def _wire(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, aligned: int
    ) -> dict[str, object]:
        """Stub every provider and git call; capture the collector's arguments."""
        (tmp_path / "ws" / "o__r" / ".git").mkdir(parents=True)
        (tmp_path / "ws" / "o__r.db").write_text("db", encoding="utf-8")
        captured: dict[str, object] = {}

        class _Usage:
            prompt_tokens = 11
            completion_tokens = 22

        class _Provider:
            cumulative_usage = _Usage()

        class _Result:
            findings: ClassVar[list[object]] = []
            parse_failed = False

        class _Routed:
            result = _Result()

        def fake_collector(**kwargs: object) -> object:
            captured.update(kwargs)
            return object()

        async def fake_routed(**_: object) -> object:
            return _Routed()

        monkeypatch.setattr(gm, "pr_refs", lambda _url: ("basesha", "headsha"))
        monkeypatch.setattr(gm, "_git", lambda *a, **k: "guardiansha\n")  # noqa: ARG005
        monkeypatch.setattr(gm, "graph_alignment", lambda *a: (aligned, 1))  # noqa: ARG005
        # The graph is rebuilt unless its marker proves the commit; the fixture
        # writes no marker, so this call is expected rather than incidental.
        monkeypatch.setattr(gm, "ensure_graph", lambda *a: None)  # noqa: ARG005
        monkeypatch.setattr(gm, "build_provider", lambda _env: (_Provider(), "finder-model"))
        monkeypatch.setattr(gm, "build_skeptic_provider", lambda _env, primary: None)  # noqa: ARG005
        monkeypatch.setattr(gm, "ContextCollector", fake_collector)
        monkeypatch.setattr(gm, "run_review_routed", fake_routed)
        return captured

    def test_a_graph_pr_gets_the_database_and_the_empty_source_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = self._wire(tmp_path, monkeypatch, aligned=1)
        args = argparse.Namespace(workspace=tmp_path / "ws")
        record = asyncio.run(gm.review_one(_review_row(), args))
        assert captured["db_path"] == tmp_path / "ws" / "o__r.db"
        assert captured["source_root"] == ""
        assert captured["base_ref"] == "basesha"
        assert record.had_graph is True

    def test_a_graph_pr_whose_graph_knows_nothing_is_recorded_as_ungraphed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Otherwise a failed ingest counts as evidence for graph context.

        The slice still says "graph" — that is the plan's classification — but
        `had_graph` is what G5 must be computed on, and the collector is given
        no database rather than one that answers nothing.
        """
        captured = self._wire(tmp_path, monkeypatch, aligned=0)
        args = argparse.Namespace(workspace=tmp_path / "ws")
        record = asyncio.run(gm.review_one(_review_row(), args))
        assert captured["db_path"] is None
        assert record.pr_slice == "graph"
        assert record.had_graph is False

    def test_an_unprepared_checkout_says_which_command_to_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(gm, "pr_refs", lambda _url: ("b", "h"))
        args = argparse.Namespace(workspace=tmp_path / "empty")
        coroutine = gm.review_one(_review_row(), args)
        with pytest.raises(RuntimeError, match="prepare --pr 1"):
            asyncio.run(coroutine)


class TestResumeRobustness:
    """Resume is what protects work already paid for, so it must not be fragile."""

    def test_a_truncated_last_line_does_not_block_resume(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A crash mid-append leaves one. Refusing to parse it would re-buy everything."""
        path = tmp_path / "reviews.jsonl"
        path.write_text('{"url": "u1"}\n{"url": "u2", "find', encoding="utf-8")
        assert gm.done_urls(path) == {"u1"}
        assert "Skipping unreadable record" in capsys.readouterr().err

    def test_the_skeptic_is_the_opposite_of_whatever_the_finder_is(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Hardcoding "gemini" handed a mistral finder a mistral skeptic.

        `build_skeptic_provider` picks the *opposite* provider, so the argument
        has to describe the finder that was actually built, not the one the
        script expects.
        """
        (tmp_path / "ws" / "o__r" / ".git").mkdir(parents=True)
        seen: dict[str, str] = {}

        class _Mistral(gm.MistralProvider):
            cumulative_usage = SimpleNamespace(prompt_tokens=0, completion_tokens=0)

            def __init__(self) -> None:
                pass

        async def fake_routed(**_: object) -> object:
            return SimpleNamespace(result=SimpleNamespace(findings=[], parse_failed=False))

        def spy(_env: object, primary: str) -> None:
            seen["primary"] = primary
            return

        monkeypatch.setattr(gm, "pr_refs", lambda _url: ("b", "h"))
        monkeypatch.setattr(gm, "_git", lambda *a, **k: "sha\n")  # noqa: ARG005
        monkeypatch.setattr(gm, "graph_alignment", lambda *a: (0, 1))  # noqa: ARG005
        monkeypatch.setattr(gm, "build_provider", lambda _env: (_Mistral(), "mistral-medium"))
        monkeypatch.setattr(gm, "build_skeptic_provider", spy)
        monkeypatch.setattr(gm, "ContextCollector", lambda **_: object())
        monkeypatch.setattr(gm, "run_review_routed", fake_routed)

        asyncio.run(gm.review_one(_review_row(), argparse.Namespace(workspace=tmp_path / "ws")))
        assert seen["primary"] == "mistral"


class TestGuardianVersion:
    """`guardian_sha` says which reviewer produced a record."""

    def test_asks_this_repository_not_the_working_directory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`review_one` works with someone else's checkout on disk.

        Without an explicit cwd, a caller standing in that checkout would have
        recorded the *reviewed* project's SHA as the reviewer's — wrong, and
        indistinguishable from right.
        """
        seen: dict[str, object] = {}

        def fake_git(*args: str, **kwargs: object) -> str:
            seen["args"] = args
            seen["cwd"] = kwargs.get("cwd")
            return "abc123\n"

        monkeypatch.setattr(gm, "_git", fake_git)
        assert gm.guardian_version() == "abc123"
        assert seen["cwd"] == gm.REPO_ROOT

    def test_outside_a_checkout_it_degrades_rather_than_losing_the_review(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*_a: str, **_k: object) -> str:
            _msg = "not a git repository"
            raise RuntimeError(_msg)

        monkeypatch.setattr(gm, "_git", boom)
        monkeypatch.delenv("GUARDIAN_SHA", raising=False)
        assert gm.guardian_version() == "unknown"

    def test_an_explicit_sha_wins_when_git_is_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*_a: str, **_k: object) -> str:
            _msg = "not a git repository"
            raise RuntimeError(_msg)

        monkeypatch.setattr(gm, "_git", boom)
        monkeypatch.setenv("GUARDIAN_SHA", "deadbeef")
        assert gm.guardian_version() == "deadbeef"


class TestJudge:
    """Scoring the recorded reviews. Cheap per call, but it decides every gate."""

    @staticmethod
    def _review(url: str = "https://github.com/o/r/pull/1", **overrides: object) -> gm.ReviewRecord:
        base: dict[str, object] = {
            "url": url,
            "project": "p",
            "pr_slice": "graph",
            "base_sha": "b",
            "head_sha": "h",
            "had_graph": True,
            "finder_model": "m",
            "skeptic_model": None,
            "findings": [],
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "duration_s": 1.0,
            "parse_failed": False,
            "guardian_sha": "sha",
            "reviewed_at": "2026-08-12T00:00:00+00:00",
        }
        return gm.ReviewRecord.model_validate(base | overrides)

    def test_load_reviews_keeps_the_last_row_per_url(self, tmp_path: Path) -> None:
        """A re-review exists because the earlier one was incomplete."""
        path = tmp_path / "r.jsonl"
        path.write_text(
            self._review(guardian_sha="old").model_dump_json()
            + "\n"
            + self._review(guardian_sha="new").model_dump_json()
            + "\n",
            encoding="utf-8",
        )
        loaded = gm.load_reviews(path)
        assert len(loaded) == 1
        assert loaded[0].guardian_sha == "new"

    def test_load_reviews_says_which_command_to_run(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="review"):
            gm.load_reviews(tmp_path / "absent.jsonl")

    def test_rows_with_unruled_pairs_are_not_treated_as_done(self, tmp_path: Path) -> None:
        """Their tp is biased low, so resuming past them would freeze a bad score."""
        path = tmp_path / "j.jsonl"
        path.write_text(
            json.dumps({"url": "u1", "judge_model": "m", "judge_failures": 0})
            + "\n"
            + json.dumps({"url": "u2", "judge_model": "m", "judge_failures": 3})
            + "\n",
            encoding="utf-8",
        )
        assert gm.judged_keys(path) == {("u1", "m")}

    def test_a_corrupt_judged_row_does_not_block_resume(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "j.jsonl"
        path.write_text(
            '{"url":"u1","judge_model":"m","judge_failures":0}\n{"url": tr', encoding="utf-8"
        )
        assert gm.judged_keys(path) == {("u1", "m")}
        assert "Skipping unreadable judged row" in capsys.readouterr().err

    def test_dry_run_needs_no_credentials(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A mode that spends nothing must not demand an API key to say so."""
        reviews = tmp_path / "r.jsonl"
        reviews.write_text(self._review().model_dump_json() + "\n", encoding="utf-8")

        def boom(_env: object) -> tuple[object, str]:
            _msg = "Set MISTRAL_API_KEY or GEMINI_API_KEY to run Guardian."
            raise RuntimeError(_msg)

        monkeypatch.setattr(gm, "build_provider", boom)
        args = argparse.Namespace(
            corpus=Path("benchmarks/martian"),
            reviews=reviews,
            out=tmp_path / "j.jsonl",
            profile="core",
            pr=None,
            dry_run=True,
        )
        assert asyncio.run(gm.judge(args)) == 0
        assert "would be judged" in capsys.readouterr().out

    def test_judge_one_scores_and_keeps_the_reproducible_grid(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The grid is why a re-score never has to pay the judge again."""
        pr = gm.BenchPr(
            project="p",
            pr_title="t",
            url="https://github.com/o/r/pull/1",
            comments=[
                {"comment": "g1", "severity": "High", "category": "bug"},
                {"comment": "g2", "severity": "High", "category": "bug"},
            ],
        )
        finding = _review_finding()
        record = self._review(findings=[finding])

        async def fake_matrix(
            _p: object, goldens: list[str], candidates: list[str], *_a: object, **_k: object
        ) -> tuple[list[gm_cal.JudgePair], int]:
            pairs = [
                gm_cal.JudgePair(
                    golden_index=0,
                    candidate_index=0,
                    verdict=gm_cal.JudgeVerdict(reasoning="same", match=True, confidence=0.9),
                ),
                gm_cal.JudgePair(
                    golden_index=1,
                    candidate_index=0,
                    verdict=gm_cal.JudgeVerdict(reasoning="no", match=False, confidence=0.8),
                ),
            ]
            assert len(goldens) == 2
            assert len(candidates) == 1
            return pairs, 0

        monkeypatch.setattr(gm, "judge_matrix", fake_matrix)
        judged = asyncio.run(gm.judge_one(record, pr, object(), "judge-x", "core"))
        assert (judged.tp, judged.fp, judged.fn) == (1, 0, 1)
        assert judged.decisions == [1, 0]
        assert judged.precision == pytest.approx(1.0)
        assert judged.recall == pytest.approx(0.5)

    def test_refuted_findings_never_reach_the_judge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """They were shown to nobody, so scoring them would measure the finder."""
        pr = gm.BenchPr(
            project="p",
            pr_title="t",
            url="https://github.com/o/r/pull/1",
            comments=[{"comment": "g1", "severity": "High", "category": "bug"}],
        )
        record = self._review(
            findings=[_review_finding(), _review_finding(title="killed", verdict="refuted")]
        )
        seen: dict[str, int] = {}

        async def fake_matrix(
            _p: object, goldens: list[str], candidates: list[str], *_a: object, **_k: object
        ) -> tuple[list[gm_cal.JudgePair], int]:
            assert goldens
            seen["candidates"] = len(candidates)
            return [], 0

        monkeypatch.setattr(gm, "judge_matrix", fake_matrix)
        asyncio.run(gm.judge_one(record, pr, object(), "judge-x", "core"))
        assert seen["candidates"] == 1

    def test_pr_filter_is_exact_not_a_substring(self, tmp_path: Path) -> None:
        """`--pr 123` matched PR 1234, because the filter was `f"/{n}" in url`."""
        reviews = tmp_path / "r.jsonl"
        reviews.write_text(
            self._review(url="https://github.com/o/r/pull/123").model_dump_json()
            + "\n"
            + self._review(url="https://github.com/o/r/pull/1234").model_dump_json()
            + "\n",
            encoding="utf-8",
        )
        args = argparse.Namespace(
            corpus=Path("benchmarks/martian"),
            reviews=reviews,
            out=tmp_path / "j.jsonl",
            profile="core",
            pr=123,
            dry_run=True,
        )
        assert asyncio.run(gm.judge(args)) == 0

    def test_an_unreadable_review_row_does_not_strand_the_rest(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "r.jsonl"
        path.write_text(self._review().model_dump_json() + "\n{ truncated", encoding="utf-8")
        assert len(gm.load_reviews(path)) == 1
        assert "Skipping unreadable review" in capsys.readouterr().err

    def test_judged_rows_that_are_not_objects_are_skipped(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A bare list or a row missing its keys is as unusable as broken JSON."""
        path = tmp_path / "j.jsonl"
        path.write_text(
            '{"url":"u1","judge_model":"m","judge_failures":0}\n'
            "[]\n"
            '{"judge_failures":0}\n'
            '{"url": tr\n',
            encoding="utf-8",
        )
        assert gm.judged_keys(path) == {("u1", "m")}
        assert capsys.readouterr().err.count("Skipping unreadable judged row") == 3

    def test_the_loop_writes_a_row_per_review_and_survives_one_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        urls = [f"https://github.com/getsentry/sentry/pull/{n}" for n in (80168, 80528)]
        reviews = tmp_path / "r.jsonl"
        reviews.write_text(
            "".join(self._review(url=u).model_dump_json() + "\n" for u in urls), encoding="utf-8"
        )
        out = tmp_path / "j.jsonl"

        async def half(record: gm.ReviewRecord, *_a: object) -> gm.JudgedReview:
            if record.url.endswith("80528"):
                _msg = "judge exploded"
                raise RuntimeError(_msg)
            return gm.JudgedReview(
                url=record.url,
                project="sentry",
                pr_slice="graph",
                had_graph=True,
                profile="core",
                judge_model="j",
                n_goldens=3,
                n_candidates=1,
                tp=1,
                fp=0,
                fn=2,
                precision=1.0,
                recall=1 / 3,
                judge_failures=0,
                decisions=[1, 0, 0],
                judged_at="2026-08-12T00:00:00+00:00",
            )

        monkeypatch.setattr(gm, "build_provider", lambda _env: (object(), "judge-x"))
        monkeypatch.setattr(gm, "judge_one", half)
        args = argparse.Namespace(
            corpus=Path("benchmarks/martian"),
            reviews=reviews,
            out=out,
            profile="core",
            pr=None,
            dry_run=False,
        )
        assert asyncio.run(gm.judge(args)) == 1
        written = [json.loads(ln) for ln in out.read_text(encoding="utf-8").splitlines()]
        assert [r["url"] for r in written] == [urls[0]]
        captured = capsys.readouterr()
        assert "tp=1 fp=0 fn=2" in captured.out
        assert "judge exploded" in captured.err

    def test_a_review_whose_pr_left_the_corpus_is_reported_not_fatal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """It cannot be scored against anything, but it must not end the run."""
        reviews = tmp_path / "r.jsonl"
        reviews.write_text(
            self._review(url="https://github.com/o/gone/pull/9").model_dump_json() + "\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(gm, "build_provider", lambda _env: (object(), "judge-x"))
        args = argparse.Namespace(
            corpus=Path("benchmarks/martian"),
            reviews=reviews,
            out=tmp_path / "j.jsonl",
            profile="core",
            pr=None,
            dry_run=False,
        )
        assert asyncio.run(gm.judge(args)) == 1
        assert "not in the corpus" in capsys.readouterr().err


class TestReport:
    """The gates. Free to run, and the last place a scoring mistake is cheap."""

    @staticmethod
    def _row(**overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "url": "https://github.com/o/r/pull/1",
            "project": "sentry",
            "pr_slice": "graph",
            "had_graph": True,
            "profile": "core",
            "judge_model": "j",
            "n_goldens": 3,
            "n_candidates": 1,
            "tp": 1,
            "fp": 0,
            "fn": 2,
            "precision": 1.0,
            "recall": 1 / 3,
            "judge_failures": 0,
            "decisions": [1, 0, 0],
            "judged_at": "2026-08-12T00:00:00+00:00",
        }
        return base | overrides

    def _write(self, tmp_path: Path, rows: list[dict[str, object]]) -> argparse.Namespace:
        path = tmp_path / "j.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return argparse.Namespace(judged=path, beta=0.5)

    def test_g5_is_undefined_rather_than_failed_when_a_slice_is_empty(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The #345 shape again: an empty slice scores recall 1.0 vacuously.

        Comparing against it produced a confident "-66.7 pp FAIL" that measured
        nothing at all.
        """
        args = self._write(tmp_path, [self._row()])
        assert gm.report(args) == 1
        out = capsys.readouterr().out
        assert "UNDEFINED" in out
        assert "FAIL" not in out.split("G5")[1]

    def test_g5_compares_the_two_slices_when_both_exist(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rows = [
            self._row(had_graph=True, tp=8, fn=2),
            self._row(url="https://github.com/o/r/pull/2", had_graph=False, tp=1, fn=9),
        ]
        assert gm.report(self._write(tmp_path, rows)) == 0
        g5 = capsys.readouterr().out.split("G5")[1]
        assert "+70.0 pp" in g5
        assert "PASS" in g5

    def test_a_mixed_profile_report_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Two profiles in one table would be two different corpora silently added up."""
        rows = [self._row(), self._row(url="https://github.com/o/r/pull/2", profile="all")]
        assert gm.report(self._write(tmp_path, rows)) == 1
        err = capsys.readouterr().err
        assert "REFUSING to mix profiles" in err
        assert "--judge." not in err, "the message must not cite a flag that does not exist"

    def test_missing_judged_file_says_what_to_run(self, tmp_path: Path) -> None:
        args = argparse.Namespace(judged=tmp_path / "absent.jsonl", beta=0.5)
        with pytest.raises(FileNotFoundError, match="judge"):
            gm.report(args)

    def test_an_empty_judged_file_does_not_pass_by_evaluating_nothing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Exiting 0 would let a CI step succeed by measuring nothing at all."""
        assert gm.report(self._write(tmp_path, [])) == 1
        assert "nothing to report" in capsys.readouterr().err

    def test_every_line_carries_its_n(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """G6: a rate without its n is not reportable."""
        gm.report(self._write(tmp_path, [self._row()]))
        for line in capsys.readouterr().out.splitlines():
            if " P=" in line:
                assert " n=" in line
