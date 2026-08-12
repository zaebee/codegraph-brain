"""Tests for the Martian corpus layer (#342 Phase 2).

The population a gate is computed on is the thing Phase 1 got wrong — a silent
filter decided both verdicts (retraction R1). So the tests that matter here are
the ones that pin *which PRs are in* and *why one is out*, and one of them
asserts against the real vendored corpus rather than a fixture, because a gate
registered on 19 vs 26 is worth nothing if the loader disagrees.
"""

from pathlib import Path

import pytest

from cgis.guardian.martian import (
    DEFAULT_PROFILE,
    PROFILE_CATEGORIES,
    BenchPr,
    GoldenComment,
    SliceCounts,
    evaluated,
    golden_texts,
    load_corpus,
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

    def test_load_is_deterministic(self) -> None:
        """Two separate loads, bound first — a determinism check, not a tautology.

        Written the other way round at first (`load_corpus(...) == load_corpus(...)`),
        which is #309's item 1 verbatim: a reader cannot tell it from a
        copy-paste mistake, and neither can the analyser.
        """
        first = load_corpus(CORPUS)
        second = load_corpus(CORPUS)
        assert [p.url for p in first] == [p.url for p in second]
