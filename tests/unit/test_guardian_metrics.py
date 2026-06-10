"""Unit tests for Guardian review metrics tracking."""

import json
from pathlib import Path

from cgis.guardian.metrics import load_reviews, rate_review, record_review


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
