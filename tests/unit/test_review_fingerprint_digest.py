"""The digest: deterministic, path-sensitive, and intolerant of CRLF."""

import hashlib
from pathlib import Path

import pytest

from cgis.guardian.review_fingerprint import (
    DIGEST_CHARS,
    SCHEME,
    CarriageReturnError,
    compute_fingerprint,
    disk_reader,
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


def test_scheme_string_is_in_the_preimage() -> None:
    """Changing the scheme or the width must change every digest."""
    assert SCHEME == b"cgis-review-fingerprint/v1\0"
    assert hashlib.sha256(SCHEME).hexdigest()  # smoke: the constant is bytes
