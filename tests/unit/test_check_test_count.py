"""The floor under the suite's own size (#405).

These tests cover the script; they are not the guard. The guard is the step in
`ci.yml`, because a check that ships inside the test tree is deleted by the push
it exists to catch — which is what happened on 2026-08-17, when 24 test files
and 5 modules went missing and Python Verification passed.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

from check_test_count import (
    BASELINE_PATH,
    MAX_DRIFT,
    CannotCountError,
    _as_count,
    baseline_here,
    baseline_on,
    parse_collected,
    problems,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _commit_repo(root: Path, baseline: str | None = None) -> None:
    """A one-commit git repository, with or without the baseline file in it."""
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "seed.txt").write_text("x", encoding="utf-8")
    if baseline is not None:
        (root / BASELINE_PATH.parent).mkdir(parents=True, exist_ok=True)
        (root / BASELINE_PATH).write_text(baseline, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, check=True)


class TestParsingPytestsSummary:
    """Three shapes, measured on this repository rather than assumed."""

    def test_the_ordinary_line(self) -> None:
        assert parse_collected("1932 tests collected in 1.83s") == 1932

    def test_a_filtered_run_reports_the_total_not_the_selection(self) -> None:
        """`137/1932` under `-k`: 137 matched a filter, 1932 exist.

        Reading the left number would fail every filtered run, and — worse —
        would pass a real deletion whenever the filter happened to be narrow
        enough that the shrunken suite still cleared the floor.
        """
        assert parse_collected("137/1932 tests collected (1785 deselected) in 1.95s") == 1932

    def test_a_single_test_still_parses(self) -> None:
        """pytest writes 'test' rather than 'tests' at one, so the plural is optional."""
        assert parse_collected("1 test collected in 0.01s") == 1

    def test_no_tests_collected_refuses_rather_than_returning_zero(self) -> None:
        """The line that carries no number at all.

        Zero would be a count, and a count of zero compared against a floor
        fails loudly — which sounds safe until the caller catches it. The real
        danger is the other plausible default, "skip the check", so this refuses
        with its own exception type instead of returning anything.
        """
        with pytest.raises(CannotCountError, match="not a count of zero"):
            parse_collected("no tests collected in 0.00s")

    def test_the_refusal_carries_the_output_it_could_not_parse(self) -> None:
        """A "could not run" with no output is a bug report nobody can action."""
        with pytest.raises(CannotCountError, match="ImportError: cannot import name"):
            parse_collected("ImportError: cannot import name 'x' from 'y'")


class TestReadingTheBaselines:
    """Where each number comes from, and what happens when it cannot be had."""

    def test_the_working_tree_copy_is_read(self, tmp_path: Path) -> None:
        (tmp_path / BASELINE_PATH.parent).mkdir(parents=True)
        (tmp_path / BASELINE_PATH).write_text("1932\n", encoding="utf-8")
        assert baseline_here(tmp_path) == 1932

    def test_a_missing_file_refuses(self, tmp_path: Path) -> None:
        with pytest.raises(CannotCountError, match="is missing"):
            baseline_here(tmp_path)

    @pytest.mark.parametrize("body", ["", "  ", "about 1932", "1932 tests", "-5"])
    def test_anything_but_a_plain_integer_refuses(self, body: str) -> None:
        """Including the near-misses, which are the ones that would parse wrong."""
        with pytest.raises(CannotCountError, match="does not hold a plain integer"):
            _as_count(body, "somewhere")

    def test_a_base_ref_that_does_not_resolve_refuses(self, tmp_path: Path) -> None:
        """A broken workflow, not a young repository.

        No falling back to the branch's own copy: a stale tree carries a stale
        baseline, so comparing a branch against itself passes the exact incident
        this check was written for.
        """
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        with pytest.raises(CannotCountError, match="does not resolve"):
            baseline_on("origin/main", tmp_path)

    def test_a_ref_that_predates_the_baseline_returns_none_rather_than_raising(
        self, tmp_path: Path
    ) -> None:
        """The bootstrap commit, and every branch cut before it.

        Told apart from the unresolvable ref above by resolving the ref first,
        not by matching git's error text — which would not distinguish "unknown
        ref" from "path not in this tree", the same defect #375 found in a
        `git show` reader.
        """
        _commit_repo(tmp_path)
        assert baseline_on("HEAD", tmp_path) is None

    def test_a_ref_that_carries_the_baseline_returns_it(self, tmp_path: Path) -> None:
        _commit_repo(tmp_path, baseline="1932\n")
        assert baseline_on("HEAD", tmp_path) == 1932


class TestTheFourRules:
    """Each rule closes a different route, so each is asserted on its own."""

    def test_a_suite_at_its_floor_is_clean(self) -> None:
        assert problems(actual=1932, floor=1932, here=1932) == []

    def test_growth_within_the_drift_allowance_is_clean(self) -> None:
        assert problems(actual=1932 + MAX_DRIFT, floor=1932, here=1932) == []

    def test_a_shrunken_suite_is_reported_with_the_delta(self) -> None:
        """The incident, in miniature: 1932 recorded, 1264 collected."""
        found = problems(actual=1264, floor=1932, here=1932)
        assert len(found) == 1
        assert "668 have gone missing" in found[0]

    def test_the_shrink_is_caught_even_when_the_branch_lowered_its_own_baseline(self) -> None:
        """The load-bearing case, and the reason the floor is read from the base branch.

        A stale tree carries a stale baseline. Checked against its own copy this
        is 1264 against 1264 and passes; against the base branch's 1932 it
        fails. Both rules fire here — the deletion and the lowering.
        """
        found = problems(actual=1264, floor=1932, here=1264)
        assert len(found) == 2
        assert any("gone missing" in p for p in found)
        assert any("may only rise" in p for p in found)

    def test_lowering_the_baseline_alone_is_refused(self) -> None:
        """Tests intact, floor quietly weakened for every branch cut afterwards."""
        found = problems(actual=1932, floor=1932, here=1900)
        assert len(found) == 1
        assert "may only rise" in found[0]

    def test_a_baseline_above_the_real_count_is_refused(self) -> None:
        """Otherwise the next branch starts red through no fault of its own."""
        found = problems(actual=1932, floor=1900, here=1950)
        assert len(found) == 1
        assert "would start red" in found[0]

    def test_a_baseline_left_far_behind_is_refused(self) -> None:
        """The anti-rot rule: a floor 1000 below reality bounds nothing."""
        found = problems(actual=1932 + MAX_DRIFT + 1, floor=1932, here=1932)
        assert len(found) == 1
        assert "keeps bounding something" in found[0]

    def test_no_prior_disables_the_first_two_rules_and_nothing_else(self) -> None:
        """`floor=None` is "undefined", not "zero" and not "fine".

        A shrink cannot be detected without a prior count, so rules 1 and 2 go
        quiet — but the file must still be honest about the suite in front of
        it, or the bootstrap commit could record any number at all and the floor
        would start life wrong.
        """
        assert problems(actual=1932, floor=None, here=1932) == []
        assert problems(actual=10, floor=None, here=99) != []
        assert problems(actual=1932 + MAX_DRIFT + 1, floor=None, here=1932) != []

    def test_drift_is_not_reported_when_the_baseline_is_already_too_high(self) -> None:
        """The two are mutually exclusive by construction, and both name a fix.

        Reported together they would tell the operator to raise and lower the
        same number in one message.
        """
        found = problems(actual=100, floor=50, here=400)
        assert len(found) == 1


def test_the_repository_currently_satisfies_its_own_floor() -> None:
    """The recorded baseline is not above what this checkout actually collects.

    Deliberately not a call to `collect_count`: that shells out to a second full
    collection, and the number is already known to the run collecting this test.
    What can be checked cheaply is the invariant that breaks first — a baseline
    committed ahead of reality, which would redden the next branch.
    """
    recorded = baseline_here(REPO_ROOT)
    assert recorded > 0
    assert recorded >= 1932, (
        f"{BASELINE_PATH} records {recorded}; it may only rise, and 1932 was the count when "
        f"the floor was introduced."
    )
