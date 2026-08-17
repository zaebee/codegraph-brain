"""A floor under the size of the test suite itself (#405).

On 2026-08-17 a bot pushed a stale worktree over a pull request. It committed a
whole tree rather than a diff, so a week of `main` read as deletion: 62 files,
5 modules under `src/cgis` and 24 test files. **Python Verification passed** —
because the tests that would have failed were deleted by the same commit. To
CI, "the module and its tests are gone" and "both still pass" are one
observation.

The corpus floors already in the repository (`>= 72` in
`test_recordings_from_corpus.py`, `>= 16` in
`test_backfill_calibration_fingerprint.py`) were written against exactly this
shape of silence and could not help: they live inside test files, and this
failure deletes the file. A guard shipped inside the thing it guards is removed
by the event it exists to catch. So this runs from `ci.yml`, not from pytest.

**The baseline is read twice, from two places, and that is the load-bearing
part.** The floor comes from the *base branch*; only the ceiling consults the
copy in the pull request. A stale tree carries a stale baseline — in the
incident, a tree from six days earlier held roughly the count it had then, so a
check against the branch's own file would have compared 1264 against ~1264 and
passed. The base branch held 1932.

The point generalises past this repository: a reference value stored inside the
subject cannot bound the subject.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: One line, one integer. Under `.github/` rather than `tests/` so that deleting
#: the test tree does not also delete the record of how large it was.
BASELINE_PATH = Path(".github/test-count-baseline")

#: How far the real count may run ahead of the recorded baseline before the
#: baseline must be raised. Without a ceiling the floor rots: a suite that grows
#: to 3000 against a baseline of 1932 would let a thousand tests be deleted
#: silently, which is the failure this file exists to prevent, arriving late.
MAX_DRIFT = 150

#: `pytest --collect-only -q` ends in one of three shapes, measured on this
#: repository rather than assumed:
#:
#:     1932 tests collected in 1.83s
#:     137/1932 tests collected (1785 deselected) in 1.95s
#:     no tests collected in 0.00s
#:
#: The third yields no number at all. It must raise rather than fall back to a
#: default, because every plausible default (0, or "skip the check") turns a
#: broken command into a passing one — and a floor that cannot fail is worse
#: than no floor, since it reads as protection.
_COLLECTED = re.compile(r"^(?:(\d+)/)?(\d+) tests? collected", re.M)


class CannotCountError(RuntimeError):
    """Raised when the number of tests cannot be established.

    Its own type so a caller cannot confuse "the suite shrank" with "we never
    found out how big it is". The first is a finding; the second is a broken
    check, and reporting one as the other is the whole defect class here.
    """


def parse_collected(output: str) -> int:
    """The total pytest collected, from the summary line.

    Returns the *total*, not the selected count: under `-k` the line reads
    `137/1932`, and 137 is how many matched a filter rather than how many exist.
    A floor fed the filtered number would fail on every filtered run and, worse,
    would pass a real deletion whenever the filter happened to be narrow.
    """
    match = _COLLECTED.search(output)
    if match is None:
        _msg = (
            "Could not find a collected-test count in pytest's output. The last line is "
            "normally '<N> tests collected'; 'no tests collected' means collection failed "
            "and is not a count of zero.\n"
            f"--- output ---\n{output.strip()[-2000:]}"
        )
        raise CannotCountError(_msg)
    return int(match.group(2))


def collect_count(repo_root: Path) -> int:
    """How many tests pytest can collect right now."""
    result = subprocess.run(
        ["uv", "run", "--frozen", "pytest", "--collect-only", "-q"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    # Not `check=True`: a non-zero exit with a usable summary line is possible,
    # and the exit code is not the question being asked. The parse decides, and
    # it refuses loudly when there is nothing to parse — so the output is passed
    # through either way rather than being thrown away with an exception whose
    # message would not contain it.
    return parse_collected(result.stdout + result.stderr)


def baseline_on(ref: str, repo_root: Path) -> int | None:
    """The baseline as recorded on `ref`, or None when `ref` predates the file.

    None is not a fallback and not a silent pass. It says there is no prior
    count to compare against, and a shrink is undetectable without a prior — the
    honest report of that is "this rule cannot fire", not a number. It happens
    for exactly two reasons, both self-limiting: the commit that introduces the
    baseline, and branches cut before it. Removing the file later cannot get
    here, because a branch that deletes it fails on `baseline_here` first.

    A ref that does not resolve at all is a different thing entirely — a broken
    workflow, not a young repository — and raises. The two are told apart by
    resolving the ref first rather than by matching git's error text, which
    would not distinguish "unknown ref" from "path not in this tree".
    """
    resolved = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    if resolved.returncode:
        _msg = (
            f"{ref} does not resolve. The floor has to come from the base branch, so a "
            f"missing base ref is a broken workflow rather than a young repository — "
            f"falling back to this branch's own copy is the evasion this check closes."
        )
        raise CannotCountError(_msg)
    result = subprocess.run(
        ["git", "show", f"{ref}:{BASELINE_PATH.as_posix()}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        return None
    return _as_count(result.stdout, f"{ref}:{BASELINE_PATH}")


def baseline_here(repo_root: Path) -> int:
    """The baseline as it stands in the working tree — the copy a branch may raise."""
    path = repo_root / BASELINE_PATH
    if not path.is_file():
        _msg = f"{path} is missing. It records how large the suite is expected to be."
        raise CannotCountError(_msg)
    return _as_count(path.read_text(encoding="utf-8"), str(path))


def _as_count(raw: str, source: str) -> int:
    """One integer, or a refusal naming where the unusable value came from."""
    text = raw.strip()
    if not text.isdigit():
        _msg = f"{source} does not hold a plain integer: {text[:80]!r}"
        raise CannotCountError(_msg)
    return int(text)


def problems(actual: int, floor: int | None, here: int) -> list[str]:
    """Every rule broken, in the order a reader wants them.

    Four rules, each closing a different route:

    1. `actual >= floor` — the suite may not shrink below what the base branch
       recorded. This is the anti-deletion rule, and it uses the base branch's
       number so that editing the file in the branch cannot defeat it.
    2. `here >= floor` — the recorded baseline may only go up. Otherwise a
       branch lowers it, merges, and every later branch inherits a weaker floor.
    3. `here <= actual` — the baseline may not claim more tests than exist, or
       the next branch starts red through no fault of its own.
    4. `actual - here <= MAX_DRIFT` — the baseline has to be kept current, or it
       stops bounding anything.

    Rules 3 and 4 are about maintaining the file and are suppressed while rule 1
    is broken. When tests really have gone missing, "the baseline claims more
    than exists" is a consequence of the deletion, not a second problem — and
    its remedy reads as *lower the baseline*, which is precisely the wrong move
    to put beside "668 tests have gone missing".

    A `floor` of None disables rules 1 and 2, and nothing else. There is no
    prior count on the base branch, so a shrink is not merely undetected but
    undefined — the caller says so on stderr rather than letting a green step
    imply a floor was applied.
    """
    found: list[str] = []
    if floor is not None:
        if actual < floor:
            found.append(
                f"The suite collects {actual} tests; {floor} were recorded on the base "
                f"branch. {floor - actual} have gone missing. Deleting a module together "
                f"with its tests leaves CI green, which is why this is checked separately "
                f"from whether the tests pass."
            )
        if here < floor:
            found.append(
                f"{BASELINE_PATH} was lowered from {floor} to {here}. It may only rise: a "
                f"lowered floor is inherited by every branch cut afterwards."
            )
        if actual < floor:
            return found
    if here > actual:
        found.append(
            f"{BASELINE_PATH} records {here} but only {actual} tests are collected, so the "
            f"next branch would start red. Lower it to {actual} or restore the tests."
        )
    elif actual - here > MAX_DRIFT:
        found.append(
            f"The suite has grown to {actual}, more than {MAX_DRIFT} past the recorded "
            f"{here}. Raise {BASELINE_PATH} to {actual} so the floor keeps bounding "
            f"something."
        )
    return found


def check(base_ref: str, repo_root: Path) -> tuple[list[str], bool]:
    """Problems to report, and whether the anti-deletion floor was actually in force.

    The flag travels with the result rather than being inferred by the caller.
    A clean run with no floor and a clean run with a floor are different
    outcomes, and only one of them means the suite did not shrink.
    """
    floor = baseline_on(base_ref, repo_root)
    found = problems(collect_count(repo_root), floor, baseline_here(repo_root))
    return found, floor is not None


def main() -> int:
    """Print every problem and return a shell exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-ref",
        default="origin/main",
        help="where the floor is read from; must not be the branch under test",
    )
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    args = parser.parse_args()
    try:
        found, floor_applied = check(args.base_ref, args.repo_root)
    except CannotCountError as exc:
        # Reported as a failure, never as a pass. "We could not tell" and "the
        # suite is intact" are different answers, and only one of them is this
        # check's job to give.
        print(f"Test-count floor could not run: {exc}", file=sys.stderr)
        return 2
    if not floor_applied:
        # Said out loud on every such run. A step that goes green while the
        # anti-deletion rule never ran looks identical in the log to one where
        # it ran and passed, and that is the confusion this whole file exists
        # to remove.
        print(
            f"NOTE: {args.base_ref} carries no {BASELINE_PATH}, so there is no prior count "
            f"and the anti-deletion floor did not apply. Expected only while the baseline "
            f"is being introduced, or on a branch cut before it.",
            file=sys.stderr,
        )
    for problem in found:
        print(problem, file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
