"""What counts as a test source (spec D5).

The pipeline's only test-awareness was `_TEST_FILE_PATTERN`, which matches
`foo.test.py` — a JavaScript convention. Python's actual layouts (`tests/`,
`test_*.py`, `conftest.py`) were ingested as ordinary code, so a test's
construction of a class was an ordinary CALLS edge and #415's orphan query read
all six of its own example rows as live.

These cases are the definition, not a sample of it: the orphan query's whole
value rests on "tests are not users", so a layout this table does not cover is a
class of false negative, and the fix belongs here rather than in a caller.
"""

import pytest

from cgis.core.paths import is_test_path

_PATHS: list[tuple[str, bool]] = [
    # --- tests ------------------------------------------------------------
    ("tests/test_resolver.py", True),
    ("tests/conftest.py", True),
    ("tests/unit/test_python_extractor.py", True),
    ("app/tests/_shared/reachability.py", True),
    ("src/pkg/test_helpers.py", True),
    ("src/pkg/helpers_test.py", True),
    ("conftest.py", True),
    ("test_thing.py", True),
    ("thing_test.py", True),
    ("a/b/tests/c/d/deep.py", True),
    ("test/test_one.py", True),
    ("src/pkg/tests.py", True),
    # A Windows-style path reaches the graph from a Windows checkout.
    ("tests\\unit\\test_x.py", True),
    # --- not tests --------------------------------------------------------
    ("src/cgis/pipeline.py", False),
    ("src/cgis/resolver/engine.py", False),
    # `contest`/`latest` contain "test" but are not tests, and the query would
    # silently stop counting a real package if substring matching crept in.
    ("src/contest/rules.py", False),
    ("src/latest/api.py", False),
    ("src/pkg/attestation.py", False),
    ("src/pkg/protest.py", False),
    # A directory whose name merely starts with "test".
    ("src/testing_utils/helper.py", False),
    ("src/pkg/testdata.py", False),
    # The fixture directory of a test suite is still test code, but a
    # production module named `fixtures` is not.
    ("src/pkg/fixtures.py", False),
]


@pytest.mark.parametrize(("path", "expected"), _PATHS, ids=[p[0] for p in _PATHS])
def test_is_test_path(path: str, expected: bool) -> None:
    """One row of the definition table."""
    assert is_test_path(path) is expected, (
        f"{path}: expected {'a test source' if expected else 'production code'}. "
        "A layout this table does not cover is a false negative in the orphan query."
    )
