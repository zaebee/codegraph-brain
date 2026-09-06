"""One definition of what a test source is (spec D5).

cgis had no notion of a test source. `IngestionPipeline._TEST_FILE_PATTERN`
matches `foo.test.py` — a JavaScript convention — while `tests/`, `test_*.py`
and `conftest.py` were ingested as ordinary code. A test's construction of a
class therefore produced an ordinary CALLS edge, identical in every column but
source and location, and #415's orphan query read all six of its own example
rows as live: both real orphans had three test constructions each.

The decision lives in one function so that "what is a test" has one answer. The
`Node.is_test` column is its cached result, so a query can filter in SQL without
re-deriving it.

Patterns are hardcoded. They cover the two repositories that exist —
`codegraph-brain/tests/` and `owner-api/app/tests/_shared/` — and configuration
is deferred until a third layout gives it something concrete to express.
"""

import re

# A path *segment* named `test` or `tests`, not a substring: `contest/`,
# `latest/` and `testing_utils/` are production packages, and matching them
# would silently drop real code out of every "from a non-test source" filter.
_TEST_DIR = re.compile(r"(?:^|[/\\])tests?(?:[/\\]|$)")

# `test_x.py`, `x_test.py`, `conftest.py`, and a module simply named `tests.py`.
# `testdata.py` and `attestation.py` are not tests, so the name is anchored on
# both sides rather than searched.
_TEST_FILE = re.compile(r"(?:^|[/\\])(?:test_[^/\\]*|[^/\\]*_test|conftest|tests?)\.pyi?$")


def is_test_path(file_path: str) -> bool:
    """Is this file test code rather than production code?

    Used to answer "does anything in *production* still use this?" — the
    question #415 turns on. A class constructed only by its own test is exactly
    the shape being hunted, so counting the test as a user defeats the query.
    """
    return bool(_TEST_DIR.search(file_path) or _TEST_FILE.search(file_path))
