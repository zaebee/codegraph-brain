"""Unit tests for Guardian review metrics tracking."""

import json
import os
from pathlib import Path

import pytest

from cgis.guardian.metrics import (
    load_reviews,
    rate_review,
    record_review,
    reject_metrics_path,
)


def test_record_review_creates_file(tmp_path: Path) -> None:
    """record_review creates the file and appends a valid JSON entry."""
    p = tmp_path / "metrics.jsonl"
    record_review(
        model="gemini-test",
        pr=42,
        prompt_tokens=1000,
        completion_tokens=200,
        findings_total=0,
        lgtm=True,
        parse_failed=False,
        metrics_path=p,
    )
    reviews = load_reviews(p)
    assert len(reviews) == 1
    r = reviews[0]
    assert r["pr"] == 42
    assert r["model"] == "gemini-test"
    assert r["total_tokens"] == 1200
    assert r["lgtm"] is True
    assert r["findings_total"] == 0
    assert r["findings_applied"] is None


def test_record_review_appends(tmp_path: Path) -> None:
    """Multiple calls append multiple entries."""
    p = tmp_path / "metrics.jsonl"
    for i in range(3):
        record_review(
            model="m",
            pr=i,
            prompt_tokens=100,
            completion_tokens=50,
            findings_total=0,
            lgtm=False,
            parse_failed=False,
            metrics_path=p,
        )
    assert len(load_reviews(p)) == 3


def test_rate_review_updates_latest(tmp_path: Path) -> None:
    """rate_review updates findings_applied on the most recent matching entry."""
    p = tmp_path / "metrics.jsonl"
    record_review(
        model="m",
        pr=10,
        prompt_tokens=100,
        completion_tokens=50,
        findings_total=1,
        lgtm=False,
        parse_failed=False,
        metrics_path=p,
    )
    updated = rate_review(pr=10, applied=1, metrics_path=p)
    assert updated
    reviews = load_reviews(p)
    assert reviews[-1]["findings_applied"] == 1


def test_rate_review_no_match(tmp_path: Path) -> None:
    """rate_review returns False when no unrated entry exists for the PR."""
    p = tmp_path / "metrics.jsonl"
    record_review(
        model="m",
        pr=99,
        prompt_tokens=100,
        completion_tokens=50,
        findings_total=0,
        lgtm=False,
        parse_failed=False,
        metrics_path=p,
    )
    assert not rate_review(pr=1, applied=0, metrics_path=p)


def test_rate_review_missing_file(tmp_path: Path) -> None:
    """rate_review returns False gracefully when the file does not exist."""
    assert not rate_review(pr=1, applied=0, metrics_path=tmp_path / "missing.jsonl")


def test_rate_review_skips_already_rated(tmp_path: Path) -> None:
    """rate_review does not overwrite an already-rated entry."""
    p = tmp_path / "metrics.jsonl"
    record_review(
        model="m",
        pr=5,
        prompt_tokens=100,
        completion_tokens=50,
        findings_total=1,
        lgtm=False,
        parse_failed=False,
        metrics_path=p,
    )
    rate_review(pr=5, applied=1, metrics_path=p)
    updated = rate_review(pr=5, applied=0, metrics_path=p)
    assert not updated
    assert load_reviews(p)[-1]["findings_applied"] == 1


def test_load_reviews_empty_file(tmp_path: Path) -> None:
    """load_reviews returns empty list for missing file."""
    assert load_reviews(tmp_path / "missing.jsonl") == []


def test_record_review_structured_fields(tmp_path: Path) -> None:
    """record_review takes structured counts and writes parse_failed."""
    path = tmp_path / "m.jsonl"
    record_review(
        model="test-model",
        pr=152,
        prompt_tokens=10,
        completion_tokens=5,
        findings_total=2,
        lgtm=False,
        parse_failed=False,
        metrics_path=path,
    )
    entry = json.loads(path.read_text().splitlines()[0])
    assert entry["findings_total"] == 2
    assert entry["lgtm"] is False
    assert entry["parse_failed"] is False


def test_record_review_parse_failed_flag(tmp_path: Path) -> None:
    """parse_failed=True is recorded so the benchmark can see degraded runs."""
    path = tmp_path / "m.jsonl"
    record_review(
        model="m",
        pr=None,
        prompt_tokens=0,
        completion_tokens=0,
        findings_total=0,
        lgtm=False,
        parse_failed=True,
        metrics_path=path,
    )
    assert json.loads(path.read_text())["parse_failed"] is True


def test_record_review_chunk_count(tmp_path: Path) -> None:
    """chunk_count rides in the entry; defaults to None for single-pass."""
    path = tmp_path / "m.jsonl"
    record_review(
        model="m",
        pr=1,
        prompt_tokens=1,
        completion_tokens=1,
        findings_total=0,
        lgtm=True,
        chunk_count=3,
        metrics_path=path,
    )
    record_review(
        model="m",
        pr=2,
        prompt_tokens=1,
        completion_tokens=1,
        findings_total=0,
        lgtm=True,
        metrics_path=path,
    )
    entries = [json.loads(line) for line in path.read_text().splitlines()]
    assert entries[0]["chunk_count"] == 3
    assert entries[1]["chunk_count"] is None


def test_record_review_writes_the_duration(tmp_path: Path) -> None:
    """The timing field lands in the entry (#275)."""
    metrics_path = tmp_path / "metrics.jsonl"

    record_review(
        model="m",
        pr=1,
        prompt_tokens=10,
        completion_tokens=2,
        findings_total=0,
        lgtm=True,
        duration_s=12.5,
        metrics_path=metrics_path,
    )

    entry = json.loads(metrics_path.read_text(encoding="utf-8").strip())
    assert entry["duration_s"] == 12.5


def test_record_review_duration_defaults_to_none(tmp_path: Path) -> None:
    """Historical entries have no timing; the field must be optional, not required."""
    metrics_path = tmp_path / "metrics.jsonl"

    record_review(
        model="m",
        pr=1,
        prompt_tokens=10,
        completion_tokens=2,
        findings_total=0,
        lgtm=True,
        metrics_path=metrics_path,
    )

    entry = json.loads(metrics_path.read_text(encoding="utf-8").strip())
    assert entry["duration_s"] is None


# ---------------------------------------------------------------------------
# Path guard (#347) — the sink is library code, the path starts as a CLI arg
# ---------------------------------------------------------------------------


def _record(path: Path) -> Path:
    """record_review with the boring arguments filled in."""
    return record_review(
        model="m",
        pr=1,
        prompt_tokens=1,
        completion_tokens=1,
        findings_total=0,
        lgtm=True,
        metrics_path=path,
    )


def test_a_fresh_jsonl_path_is_accepted(tmp_path: Path) -> None:
    """The guard must not get in the way of the normal case."""
    assert reject_metrics_path(tmp_path / "guardian_metrics.jsonl") is None


def test_an_existing_metrics_log_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "m.jsonl"
    _record(path)
    assert reject_metrics_path(path) is None


def test_refuses_to_append_to_a_file_that_is_not_ours(tmp_path: Path) -> None:
    """The attack this guard exists for: JSON appended to someone else's file.

    Named `.jsonl` so only the content check can catch it.
    """
    victim = tmp_path / "authorized_keys.jsonl"
    victim.write_text("ssh-ed25519 AAAAC3Nz real-key\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not a Guardian metrics log"):
        _record(victim)
    assert victim.read_text(encoding="utf-8") == "ssh-ed25519 AAAAC3Nz real-key\n"


def test_refuses_a_path_that_is_not_a_jsonl(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"must end in \.jsonl"):
        _record(tmp_path / "authorized_keys")


def test_refuses_a_dangling_symlink_pointing_outside(tmp_path: Path) -> None:
    """The pr-313 primitive, in this sink.

    A symlink whose target does not exist passes `is_file()`, so without
    resolving first every check would pass and the append would CREATE the
    target — a write primitive at an arbitrary path.
    """
    target = tmp_path / "secrets" / "authorized_keys"
    target.parent.mkdir()
    link = tmp_path / "metrics.jsonl"
    link.symlink_to(target)
    assert not link.exists(), "fixture must be a dangling link for this to test anything"
    with pytest.raises(ValueError, match=r"must end in \.jsonl"):
        _record(link)
    assert not target.exists(), "the guard must not have created the target"


def test_a_symlink_to_a_real_metrics_log_is_still_fine(tmp_path: Path) -> None:
    """Resolving must not turn every symlink into a refusal."""
    real = tmp_path / "real.jsonl"
    _record(real)
    link = tmp_path / "link.jsonl"
    link.symlink_to(real)
    assert reject_metrics_path(link) is None


def test_refuses_a_directory(tmp_path: Path) -> None:
    directory = tmp_path / "logs.jsonl"
    directory.mkdir()
    with pytest.raises(ValueError, match="is a directory"):
        _record(directory)


def test_refuses_a_missing_parent_rather_than_creating_a_tree(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="parent directory does not exist"):
        _record(tmp_path / "nope" / "deep" / "m.jsonl")


def test_an_empty_file_is_accepted(tmp_path: Path) -> None:
    """A zero-byte file is what the first run leaves behind if it is interrupted."""
    path = tmp_path / "m.jsonl"
    path.write_text("", encoding="utf-8")
    assert reject_metrics_path(path) is None


def test_a_json_array_is_not_a_record(tmp_path: Path) -> None:
    """Our records are objects, one per line; a JSON array is some other file."""
    path = tmp_path / "m.jsonl"
    path.write_text("[1, 2, 3]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not a Guardian metrics log"):
        _record(path)


def test_binary_content_is_refused_rather_than_raising(tmp_path: Path) -> None:
    """errors="replace" on read: a refusal, not a UnicodeDecodeError."""
    path = tmp_path / "m.jsonl"
    path.write_bytes(b"\x00\x01\x02binary\xff\n")
    with pytest.raises(ValueError, match="not a Guardian metrics log"):
        _record(path)


def test_rate_review_guards_the_same_path(tmp_path: Path) -> None:
    """rate_review rewrites the whole file, so it is the more destructive one."""
    victim = tmp_path / "notes.jsonl"
    victim.write_text("not json\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not a Guardian metrics log"):
        rate_review(pr=1, applied=1, metrics_path=victim)
    assert victim.read_text(encoding="utf-8") == "not json\n"


def test_refuses_a_symlink_loop_instead_of_crashing(tmp_path: Path) -> None:
    """A loop makes resolve() raise, and it is NOT the exception you expect.

    CPython catches the ELOOP `OSError` inside `Path.resolve()` and re-raises it
    as `RuntimeError("Symlink loop from ...")` (pathlib.py:1237), so a guard
    catching only OSError lets it through as a crash. Mine did, until this test
    was written; so does nothing else in the repo now.
    """
    first = tmp_path / "a.jsonl"
    second = tmp_path / "b.jsonl"
    first.symlink_to(second)
    second.symlink_to(first)
    assert reject_metrics_path(first) is not None
    with pytest.raises(ValueError, match="inaccessible"):
        _record(first)


def test_refuses_an_unreadable_file_instead_of_crashing(tmp_path: Path) -> None:
    """`is_file()` swallows OSError and returns True; the read that follows does not."""
    path = tmp_path / "m.jsonl"
    path.write_text('{"pr": 1}\n', encoding="utf-8")
    path.chmod(0o000)
    try:
        if os.access(path, os.R_OK):  # running as root — the mode is not enforced
            pytest.skip("cannot make a file unreadable as this user")
        with pytest.raises(ValueError, match="inaccessible"):
            _record(path)
    finally:
        path.chmod(0o600)


def test_a_whitespace_only_file_is_accepted(tmp_path: Path) -> None:
    """No first line to judge means nothing to refuse on."""
    path = tmp_path / "m.jsonl"
    path.write_text("\n  \n\n", encoding="utf-8")
    assert reject_metrics_path(path) is None
