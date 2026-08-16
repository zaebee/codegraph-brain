"""The S5778 ratchet, and the proof that it can fail.

Lives in the test suite rather than only in `.pre-commit-config.yaml` because
this repository's CI does not run pre-commit — a hook alone is skippable with
`--no-verify` and absent on any machine that never installed it, which is
exactly how the rule got reintroduced five times.
"""

import sys
import textwrap
from pathlib import Path

# This repository sets no pytest `pythonpath`, so every test that imports a
# script does this explicitly (test_backfill_review_fingerprint.py:16 and six
# others). Required, not optional.
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

from check_pytest_raises import GRANDFATHERED, check, offenders, problems_for

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_ONE_CALL = """
    def test_clean():
        with pytest.raises(ValueError):
            do_the_thing(argument)
"""

_TWO_CALLS = """
    def test_dirty():
        with pytest.raises(ValueError):
            do_the_thing(build_argument())
"""


def _tree(tmp_path: Path, source: str) -> Path:
    (tmp_path / "test_sample.py").write_text(textwrap.dedent(source), encoding="utf-8")
    return tmp_path


def test_a_single_call_is_clean(tmp_path: Path) -> None:
    assert offenders(_tree(tmp_path, _ONE_CALL)) == set()


def test_a_nested_call_is_caught(tmp_path: Path) -> None:
    """The exact shape SonarCloud flagged on #391: a fixture call as an argument."""
    found = offenders(_tree(tmp_path, _TWO_CALLS))
    assert len(found) == 1
    assert next(iter(found)).endswith("::test_dirty")


def test_a_call_outside_the_block_does_not_count(tmp_path: Path) -> None:
    """Hoisting is the prescribed fix, so it must actually satisfy the check."""
    assert (
        offenders(
            _tree(
                tmp_path,
                """
                def test_hoisted():
                    argument = build_argument()
                    with pytest.raises(ValueError):
                        do_the_thing(argument)
                """,
            )
        )
        == set()
    )


def test_an_async_test_is_scanned_too(tmp_path: Path) -> None:
    """Half this repository's script tests are async; missing them would be a hole."""
    found = offenders(
        _tree(
            tmp_path,
            """
            async def test_async_dirty():
                with pytest.raises(ValueError):
                    await do_the_thing(build_argument())
            """,
        )
    )
    assert len(found) == 1


def test_a_new_offender_is_reported() -> None:
    """One block no baseline entry covers, and nothing else.

    Against an explicit set rather than a temp tree: every real baseline entry
    is trivially stale against a tree that does not contain it, so routing this
    through `check` asserted one new offender amid twenty-two irrelevant stale
    reports and could not tell the two directions apart.
    """
    problems = problems_for(set(GRANDFATHERED) | {"unit/test_new.py::test_dirty"})
    assert len(problems) == 1
    assert problems[0].startswith("new: unit/test_new.py::test_dirty")
    assert "python:S5778" in problems[0]


def test_a_stale_baseline_entry_is_reported() -> None:
    """The other direction: a block that was fixed must leave the list.

    Without it `GRANDFATHERED` only ever grows stale and stops describing the
    repository — the list becomes scenery, which is how a pinned inventory
    quietly stops being a constraint.
    """
    fixed = sorted(GRANDFATHERED)[0]
    problems = problems_for(set(GRANDFATHERED) - {fixed})
    assert len(problems) == 1
    assert problems[0].startswith(f"stale: {fixed}")


def test_a_clean_set_reports_nothing() -> None:
    """The baseline exactly as it stands is neither new nor stale."""
    assert problems_for(set(GRANDFATHERED)) == []


def test_the_repository_itself_is_clean() -> None:
    """The gate. Fails on a new offender and on a stale baseline entry alike."""
    problems = check(REPO_ROOT / "tests")
    assert not problems, "\n".join(problems)
