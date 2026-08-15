"""The digest: deterministic, path-sensitive, and intolerant of CRLF."""

import hashlib
from pathlib import Path

import pytest

from cgis.guardian.review_fingerprint import (
    DIGEST_CHARS,
    SCHEME,
    BrokenReaderError,
    CarriageReturnError,
    compute_fingerprint,
    disk_reader,
    walk_closure,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_digest_is_stable_across_calls() -> None:
    """Two independently built calls over the same tree must agree.

    Not `x == x` on one shared reader and one shared frozenset (flagged by
    Sonar python:S5863 — identical expressions on both sides of `==` cannot
    fail meaningfully, since a bug in either side is a bug in both). Each
    side below gets its *own* `disk_reader(REPO_ROOT)` closure and its own
    `frozenset({"gemini"})` object, so this exercises what could actually
    vary between two computations rather than an object compared against
    itself. It would now fail if `walk_closure`'s path ordering, or any part
    of `compute_fingerprint`, depended on a fresh `frozenset`'s iteration
    order (sets carry no ordering guarantee across separately constructed
    instances) or on reader/object identity rather than on what the tree
    actually contains.
    """
    first = compute_fingerprint(disk_reader(REPO_ROOT), frozenset({"gemini"}))
    second = compute_fingerprint(disk_reader(REPO_ROOT), frozenset({"gemini"}))
    assert first == second


def test_digest_has_the_documented_width() -> None:
    value = compute_fingerprint(disk_reader(REPO_ROOT), frozenset({"gemini"}))
    assert len(value) == DIGEST_CHARS
    assert all(char in "0123456789abcdef" for char in value)


def test_editing_a_closure_file_moves_the_digest() -> None:
    """A prompt change is a different reviewer."""
    real = disk_reader(REPO_ROOT)

    def edited(path: str) -> bytes | None:
        content = real(path)
        if path == "src/cgis/guardian/prompts.py" and content is not None:
            return content + b"\n# a hunting rule changed\n"
        return content

    assert compute_fingerprint(edited, frozenset({"gemini"})) != compute_fingerprint(
        real, frozenset({"gemini"})
    )


def test_editing_a_file_outside_the_closure_does_not() -> None:
    """A README edit is the same reviewer — the whole point of #375."""
    real = disk_reader(REPO_ROOT)

    def edited(path: str) -> bytes | None:
        if path == "README.md":
            return b"rewritten"
        return real(path)

    assert compute_fingerprint(edited, frozenset({"gemini"})) == compute_fingerprint(
        real, frozenset({"gemini"})
    )


def test_paths_are_part_of_the_hash() -> None:
    """Otherwise a module leaving the set is invisible in the digest.

    Two readers serve identical bytes under different names; the digests must
    differ.
    """
    body = b"import cgis.guardian.prompts\n"

    def under_a(path: str) -> bytes | None:
        return body if path == "src/cgis/guardian/core.py" else None

    def under_b(path: str) -> bytes | None:
        return body if path == "src/cgis/guardian/axes.py" else None

    assert compute_fingerprint(under_a, frozenset()) != compute_fingerprint(under_b, frozenset())


def test_crlf_is_refused_not_repaired() -> None:
    """Folding CRLF to LF is a normaliser, and normalisers merge (spec §4.1.1)."""
    real = disk_reader(REPO_ROOT)

    def windows_checkout(path: str) -> bytes | None:
        content = real(path)
        if path == "src/cgis/guardian/prompts.py" and content is not None:
            return content.replace(b"\n", b"\r\n")
        return content

    active = frozenset({"gemini"})
    with pytest.raises(CarriageReturnError, match=r"prompts\.py"):
        compute_fingerprint(windows_checkout, active)


def test_broken_reader_raises_instead_of_silently_skipping() -> None:
    """A reader that goes missing after the walk must not merge two reviewers.

    `walk_closure` already reads this path successfully to discover it belongs
    to the closure. A `None` on the later re-read (a transient `git show`
    failure, for one) must raise rather than silently drop the file out of the
    preimage.
    """
    real = disk_reader(REPO_ROOT)
    target = "src/cgis/guardian/prompts.py"

    calls_during_walk = 0

    def counting(path: str) -> bytes | None:
        nonlocal calls_during_walk
        if path == target:
            calls_during_walk += 1
        return real(path)

    walk_closure(counting, frozenset({"gemini"}))
    assert calls_during_walk > 0  # sanity: the target really is in the closure

    seen = 0

    def flaky(path: str) -> bytes | None:
        nonlocal seen
        if path != target:
            return real(path)
        seen += 1
        return real(path) if seen <= calls_during_walk else None

    active = frozenset({"gemini"})
    with pytest.raises(BrokenReaderError, match=r"prompts\.py"):
        compute_fingerprint(flaky, active)


def test_scheme_string_is_in_the_preimage() -> None:
    """Changing the scheme, the path/content separator, or DIGEST_CHARS must move every digest.

    The expected value is reconstructed independently from the documented
    preimage rule, not by calling `walk_closure` — sharing that code path with
    the implementation would only prove the implementation equals itself.

    `DIGEST_CHARS` is pinned to its documented value (12) rather than merely
    read back: both the expected value below and the real call truncate with
    the *same* imported constant, so a width change moves both sides
    identically and the docstring's claim about `DIGEST_CHARS` would hold
    vacuously without this assertion — the test would keep passing through a
    change it claims to catch.
    """
    assert SCHEME == b"cgis-review-fingerprint/v1\0"
    assert DIGEST_CHARS == 12

    body = b"import cgis.guardian.prompts\n"
    path = "src/cgis/guardian/core.py"

    def reader(candidate: str) -> bytes | None:
        return body if candidate == path else None

    expected = hashlib.sha256()
    expected.update(SCHEME)
    expected.update(path.encode())
    expected.update(b"\0")
    expected.update(hashlib.sha256(body).digest())

    assert compute_fingerprint(reader, frozenset()) == expected.hexdigest()[:DIGEST_CHARS]


def test_disk_reader_refuses_an_absolute_path() -> None:
    """`root / path` silently discards `root` when `path` is absolute.

    An absolute path here is a caller error, not a missing file, and the two
    must not share an answer: `None` is this reader's word for "absent", so
    returning it would let a wrong root read a real file somewhere else and
    report the closure as merely incomplete (#385).
    """
    read = disk_reader(REPO_ROOT)
    with pytest.raises(ValueError, match="absolute"):
        read("/etc/hostname")


def test_disk_reader_still_answers_none_for_a_missing_relative_path() -> None:
    """The refusal above must not swallow the reader's real "absent" answer."""
    assert disk_reader(REPO_ROOT)("src/cgis/guardian/does_not_exist.py") is None


@pytest.mark.parametrize(
    "rooted",
    [
        "/etc/hostname",
        "\\etc\\hostname",
        "C:/etc/hostname",
        "C:\\etc\\hostname",
        "C:etc/hostname",
        "c:etc/hostname",
        "\\\\server\\share\\x",
    ],
)
def test_disk_reader_refuses_anything_that_could_escape_the_root(rooted: str) -> None:
    """Not "absolute" — *rooted or drive-bearing*, which is a wider class.

    `C:etc/hostname` is drive-*relative*: `PureWindowsPath("C:etc/hostname")`
    reports `is_absolute()` as False, yet
    `PureWindowsPath("D:/repo") / "C:etc/hostname"` is `C:etc/hostname` with the
    root discarded entirely. Checked by running it. So the guard tests for a
    drive or a leading separator rather than for absoluteness, and this test is
    named for what it actually covers.

    It must also not depend on which OS is running it.

    `Path(path).is_absolute()` is host-dependent: on Linux it reads `C:/x` as
    relative, so a drive-letter path would slip past on the very host this runs
    on, while `root / path` on Windows would discard the root. Judging the
    string as both a POSIX and a Windows path catches every spelling anywhere
    (#386 review).
    """
    read = disk_reader(REPO_ROOT)
    with pytest.raises(ValueError, match="absolute"):
        read(rooted)
