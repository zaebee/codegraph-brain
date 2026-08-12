"""Tests for the Martian corpus layer (#342 Phase 2).

The population a gate is computed on is the thing Phase 1 got wrong — a silent
filter decided both verdicts (retraction R1). So the tests that matter here are
the ones that pin *which PRs are in* and *why one is out*, and one of them
asserts against the real vendored corpus rather than a fixture, because a gate
registered on 19 vs 26 is worth nothing if the loader disagrees.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from cgis.guardian.findings import Finding
from cgis.guardian.martian import (
    DEFAULT_PROFILE,
    PROFILE_CATEGORIES,
    UNKNOWN_SLICE,
    BenchPr,
    GoldenComment,
    PrPlan,
    ReviewRecord,
    SliceCounts,
    build_plan,
    candidate_findings,
    evaluated,
    golden_texts,
    load_corpus,
    plan_population,
    slice_counts,
    slice_of,
)

CORPUS = Path(__file__).parent.parent.parent / "benchmarks" / "martian"


def _pr(**overrides: object) -> BenchPr:
    base: dict[str, object] = {
        "project": "sentry",
        "pr_title": "A title",
        "url": "https://github.com/getsentry/sentry/pull/80168",
        "comments": [
            GoldenComment(comment="a real bug", severity="High", category="bug"),
            GoldenComment(comment="a doc defect", severity="Medium", category="doc_defect"),
            GoldenComment(comment="a style nit", severity="Low", category="style"),
        ],
    }
    return BenchPr.model_validate(base | overrides)


class TestReproducibility:
    """The corpus tells us which entries it cannot back; believe it."""

    def test_a_plain_entry_is_reproducible(self) -> None:
        assert _pr().is_reproducible

    @pytest.mark.parametrize(
        "note",
        ["reviewed commit is not in the repo", "there is no such PR, it is a mix of many PRs"],
    )
    def test_an_az_comment_marks_it_unreproducible(self, note: str) -> None:
        """`gh pr diff` still returns *a* diff for these, which is the trap.

        It is a different state from the one the golden comments were written
        against, so scoring it charges Guardian for defects absent from the code
        it saw and credits it for ones nobody reviewed.
        """
        assert not _pr(az_comment=note).is_reproducible

    def test_evaluated_keeps_order_and_drops_only_the_flagged(self) -> None:
        prs = [_pr(url="https://github.com/o/r/pull/1"), _pr(az_comment="x"), _pr()]
        assert [p.url for p in evaluated(prs)] == [prs[0].url, prs[2].url]


class TestUrlParsing:
    """15 of the 50 are not upstream, so repo cannot be assumed from project."""

    def test_upstream(self) -> None:
        pr = _pr()
        assert pr.repo == "getsentry/sentry"
        assert pr.number == 80168

    def test_the_benchmark_fork_org(self) -> None:
        pr = _pr(url="https://github.com/ai-code-review-evaluation/sentry-greptile/pull/2")
        assert pr.repo == "ai-code-review-evaluation/sentry-greptile"
        assert pr.number == 2

    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/o/r/pull/7",
            "https://github.com/o/r/pull/7/",
            "https://github.com/o/r/pull/7/files",
            "https://github.com/o/r/pull/7/files?w=1",
            "https://github.com/o/r/pull/7#discussion_r1",
        ],
    )
    def test_decorated_urls_do_not_shift_the_fields(self, url: str) -> None:
        """Index-from-the-end parsing broke on every form but the first two."""
        pr = _pr(url=url)
        assert (pr.repo, pr.number) == ("o/r", 7)

    def test_a_url_that_is_not_a_pr_fails_by_naming_itself(self) -> None:
        with pytest.raises(ValueError, match="Not a GitHub PR URL"):
            _ = _pr(url="https://example.com/nope").repo


class TestProfiles:
    """Profiles filter by CATEGORY, not severity — the spec says otherwise and is wrong."""

    def test_core_is_the_default_and_keeps_doc_defect(self) -> None:
        assert DEFAULT_PROFILE == "core"
        assert golden_texts(_pr()) == ["a real bug", "a doc defect"]

    def test_strict_keeps_only_the_defect_categories(self) -> None:
        assert golden_texts(_pr(), "strict") == ["a real bug"]

    def test_all_keeps_everything(self) -> None:
        assert len(golden_texts(_pr(), "all")) == 3

    def test_severity_does_not_select(self) -> None:
        """A Low-severity bug survives `strict`; a Critical style nit does not.

        Backwards under the spec's "severity drives the profiles", which is why
        this is pinned rather than left implied.
        """
        pr = _pr(
            comments=[
                GoldenComment(comment="low bug", severity="Low", category="bug"),
                GoldenComment(comment="critical nit", severity="Critical", category="style"),
            ]
        )
        assert golden_texts(pr, "strict") == ["low bug"]

    def test_profiles_are_nested(self) -> None:
        """strict ⊂ core ⊂ all — otherwise "same profile" would not mean much."""
        assert PROFILE_CATEGORIES["strict"] < PROFILE_CATEGORIES["core"] < PROFILE_CATEGORIES["all"]


class TestSliceOf:
    """The axis the spec originally got wrong: files touched, not repo language."""

    @pytest.mark.parametrize(
        "files",
        [("src/a.py",), ("app/x.ts",), ("app/X.tsx",), ("go/main.go", "web/x.ts")],
    )
    def test_any_supported_file_makes_it_graph_enabled(self, files: tuple[str, ...]) -> None:
        """The mixed case is grafana: a Go project whose PR touches one .ts file."""
        assert slice_of(_pr(changed_files=files)) == "graph"

    @pytest.mark.parametrize(
        "files",
        [("a.rb", "b.erb"), ("A.java",), ("main.go",), ("x.es6", "y.js"), ()],
    )
    def test_everything_else_is_diff_only(self, files: tuple[str, ...]) -> None:
        """`.es6`/`.js` included: discourse uses them and no extractor parses them."""
        assert slice_of(_pr(changed_files=files)) == "diff-only"


class TestSliceCounts:
    def test_counts_prs_and_comments_per_slice(self) -> None:
        prs = [
            _pr(changed_files=("a.py",)),
            _pr(changed_files=("a.ts",)),
            _pr(changed_files=("a.rb",)),
        ]
        counts = slice_counts(prs)
        assert (counts["graph"].prs, counts["graph"].comments) == (2, 4)
        assert (counts["diff-only"].prs, counts["diff-only"].comments) == (1, 2)

    def test_an_empty_slice_is_reported_as_zero_not_omitted(self) -> None:
        """An absent row and a zero row mean different things when a gate compares two."""
        counts = slice_counts([_pr(changed_files=("a.py",))])
        assert set(counts) == {"graph", "diff-only"}
        assert counts["diff-only"] == SliceCounts(prs=0, comments=0)


class TestTheRealCorpus:
    """Asserted against the vendored files, because G5 is registered on these numbers."""

    def test_fifty_prs_and_one_hundred_seventy_three_comments(self) -> None:
        prs = load_corpus(CORPUS)
        assert len(prs) == 50
        assert sum(len(p.comments) for p in prs) == 173

    def test_five_are_flagged_unreproducible(self) -> None:
        prs = load_corpus(CORPUS)
        assert len(prs) - len(evaluated(prs)) == 5

    def test_profile_totals_match_upstreams_published_ones(self) -> None:
        """139 / 158 / 173 are upstream's own numbers for strict / core / all."""
        prs = load_corpus(CORPUS)
        totals = {p: sum(len(golden_texts(pr, p)) for pr in prs) for p in PROFILE_CATEGORIES}
        assert totals == {"strict": 139, "core": 158, "all": 173}

    def test_the_corpus_directory_holds_only_vendored_files(self) -> None:
        """Nothing generated may live in there, and this is not tidiness.

        `load_corpus` globs `*.json`, so a generated file in that directory is
        parsed as corpus and crashes the loader — the first version of the plan
        script wrote `plan.json` there and broke itself. The directory is also
        documented as a verbatim copy of upstream.
        """
        assert {p.name for p in CORPUS.glob("*.json")} == {
            "cal_dot_com.json",
            "discourse.json",
            "grafana.json",
            "keycloak.json",
            "sentry.json",
        }

    def test_load_is_deterministic(self) -> None:
        """Two separate loads, bound first — a determinism check, not a tautology.

        Written the other way round at first (`load_corpus(...) == load_corpus(...)`),
        which is #309's item 1 verbatim: a reader cannot tell it from a
        copy-paste mistake, and neither can the analyser.
        """
        first = load_corpus(CORPUS)
        second = load_corpus(CORPUS)
        assert [p.url for p in first] == [p.url for p in second]


class TestBuildPlan:
    """Resolving each PR's slice before anything is spent."""

    @staticmethod
    def _fetch(mapping: dict[str, tuple[str, ...]]) -> Callable[[BenchPr], tuple[str, ...]]:
        def fetch(pr: BenchPr) -> tuple[str, ...]:
            if pr.url not in mapping:
                _msg = f"no diff for {pr.url}"
                raise RuntimeError(_msg)
            return mapping[pr.url]

        return fetch

    def test_resolves_slice_and_carries_the_corpus_facts(self) -> None:
        pr = _pr()
        [plan] = build_plan([pr], self._fetch({pr.url: ("src/a.py",)}))
        assert plan.pr_slice == "graph"
        assert plan.changed_files == ("src/a.py",)
        assert (plan.repo, plan.number) == ("getsentry/sentry", 80168)
        assert plan.reproducible
        assert plan.golden_comments == 2  # core profile drops the style nit
        assert plan.fetch_error is None

    def test_a_fetch_failure_is_unknown_not_diff_only(self) -> None:
        """The distinction the whole model exists for.

        A failure classified as diff-only would quietly inflate that slice and
        move a gate that compares the two — and it would look like data.
        """
        [plan] = build_plan([_pr()], self._fetch({}))
        assert plan.pr_slice == UNKNOWN_SLICE
        assert plan.fetch_error is not None
        assert "no diff for" in plan.fetch_error

    def test_one_unreachable_pr_does_not_lose_the_others(self) -> None:
        """How the reconnaissance for this work first mis-counted the corpus at 49."""
        good, bad = (
            _pr(url="https://github.com/o/r/pull/1"),
            _pr(url="https://github.com/o/r/pull/2"),
        )
        plans = build_plan([good, bad], self._fetch({good.url: ("a.py",)}))
        assert [p.pr_slice for p in plans] == ["graph", UNKNOWN_SLICE]

    def test_unreproducible_rows_are_planned_but_flagged(self) -> None:
        """Planned so the report can name them; excluded only when populations are counted."""
        [plan] = build_plan([_pr(az_comment="mix of many PRs")], self._fetch({_pr().url: ()}))
        assert not plan.reproducible


class TestPlanPopulation:
    """The numbers G5 is registered on, derived by tested code rather than by hand."""

    def _plans(self) -> list[PrPlan]:
        rows = [
            ("https://github.com/o/r/pull/1", ("a.py",), None),
            ("https://github.com/o/r/pull/2", ("a.ts",), None),
            ("https://github.com/o/r/pull/3", ("a.rb",), None),
            ("https://github.com/o/r/pull/4", ("a.py",), "flagged"),
        ]
        prs = [_pr(url=u, az_comment=az) for u, _, az in rows]
        return build_plan(prs, self._fetch_map({u: f for u, f, _ in rows}))

    @staticmethod
    def _fetch_map(mapping: dict[str, tuple[str, ...]]) -> Callable[[BenchPr], tuple[str, ...]]:
        return lambda pr: mapping[pr.url]

    def test_excludes_the_unreproducible(self) -> None:
        """pull/4 is graph-enabled and flagged, so it is planned but not counted."""
        pop = plan_population(self._plans())
        assert pop["graph"].prs == 2
        assert pop["diff-only"].prs == 1
        assert sum(s.prs for s in pop.values()) == 3

    def test_unknown_gets_its_own_row_rather_than_joining_a_slice(self) -> None:
        prs = [_pr(url="https://github.com/o/r/pull/9")]

        def boom(pr: BenchPr) -> tuple[str, ...]:  # noqa: ARG001
            _msg = "gh exploded"
            raise RuntimeError(_msg)

        pop = plan_population(build_plan(prs, boom))
        assert pop[UNKNOWN_SLICE].prs == 1
        assert pop["graph"].prs == 0
        assert pop["diff-only"].prs == 0


class TestReviewRecord:
    """The trajectory record: enough to re-judge without paying the finder again."""

    @staticmethod
    def _finding(**overrides: object) -> Finding:
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

    def _record(self, **overrides: object) -> ReviewRecord:
        base: dict[str, object] = {
            "url": "https://github.com/o/r/pull/1",
            "project": "p",
            "pr_slice": "graph",
            "base_sha": "b",
            "head_sha": "h",
            "had_graph": True,
            "finder_model": "gemini-2.5-flash",
            "skeptic_model": None,
            "findings": [],
            "prompt_tokens": 1,
            "completion_tokens": 2,
            "duration_s": 3.0,
            "parse_failed": False,
            "guardian_sha": "deadbeef",
            "reviewed_at": "2026-08-12T00:00:00+00:00",
        }
        return ReviewRecord.model_validate(base | overrides)

    def test_refuted_findings_are_not_shown_to_the_judge(self) -> None:
        """They were shown to nobody, so charging the reviewer measures the finder."""
        record = self._record(
            findings=[
                self._finding(title="kept"),
                self._finding(title="killed", verdict="refuted"),
                self._finding(title="confirmed", verdict="confirmed"),
            ]
        )
        assert [f.title for f in candidate_findings(record)] == ["kept", "confirmed"]

    def test_had_graph_is_recorded_not_inferred_from_the_slice(self) -> None:
        """Ingest can fail on a PR the plan called graph-enabled.

        Such a row must not count as evidence for graph context, which is what
        inferring the flag from `pr_slice` would do.
        """
        record = self._record(pr_slice="graph", had_graph=False)
        assert record.pr_slice == "graph"
        assert not record.had_graph

    def test_the_record_carries_no_score(self) -> None:
        """Judging is a separate pass, so re-judging never means re-running."""
        assert not {"precision", "recall", "tp", "matched"} & set(self._record().model_dump())

    def test_skeptic_usage_is_recorded_separately(self) -> None:
        """The skeptic has its own provider, so it is absent from the finder's counters.

        Without this the run's bill cannot be reconciled — which is exactly the
        hole found while estimating Phase 2's cost: no row in the old bench
        carries both, so the skeptic's share was unknown.
        """
        record = self._record(skeptic_prompt_tokens=900, skeptic_completion_tokens=90)
        assert record.prompt_tokens == 1
        assert record.skeptic_prompt_tokens == 900
