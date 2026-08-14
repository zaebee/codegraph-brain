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
    read = disk_reader(REPO_ROOT)
    assert compute_fingerprint(read, frozenset({"gemini"})) == compute_fingerprint(
        read, frozenset({"gemini"})
    )


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

    with pytest.raises(CarriageReturnError, match=r"prompts\.py"):
        compute_fingerprint(windows_checkout, frozenset({"gemini"}))


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

    with pytest.raises(BrokenReaderError, match=r"prompts\.py"):
        compute_fingerprint(flaky, frozenset({"gemini"}))


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
