"""Unit tests for Guardian review metrics tracking."""

from pathlib import Path

from cgis.guardian.metrics import _count_findings, load_reviews, rate_review, record_review


def test_count_findings_empty() -> None:
    """Empty review text produces zero findings and no LGTM."""
    count, lgtm = _count_findings("")
    assert count == 0
    assert not lgtm


def test_count_findings_lgtm() -> None:
    """LGTM with no findings sets lgtm=True."""
    text = "LGTM — no defects found in this diff."
    count, lgtm = _count_findings(text)
    assert count == 0
    assert lgtm


def test_count_findings_with_findings() -> None:
    """Correctly counts findings by category header."""
    text = (
        "**[Logic Bug] — something wrong**\n"
        "Confidence: 90%\n"
        "Lines: `foo = bar`\n"
        "\n"
        "**[Test Coverage] — missing test**\n"
        "Confidence: 85%\n"
        "Lines: `def fn():`\n"
    )
    count, lgtm = _count_findings(text)
    assert count == 2
    assert not lgtm


def test_count_findings_lgtm_false_when_findings_present() -> None:
    """lgtm is False when there are findings even if 'lgtm' appears in text."""
    text = "**[Logic Bug] — bad\n\nLGTM at the bottom"
    count, lgtm = _count_findings(text)
    assert count == 1
    assert not lgtm


def test_count_findings_non_standard_category() -> None:
    """Model-invented category names (e.g. Missing Tests) are still counted."""
    text = (
        "**[Missing Tests] — run() has no coverage**\n"
        "Confidence: 90%\n"
        "\n"
        "**[Architecture] — something structural**\n"
        "Confidence: 85%\n"
    )
    count, lgtm = _count_findings(text)
    assert count == 2
    assert not lgtm


def test_count_findings_dash_variants() -> None:
    """En-dash and hyphen separators are accepted alongside em-dash."""
    text = (
        "**[Logic Bug] — em-dash finding**\n"
        "**[Test Coverage] \u2013 en-dash finding**\n"
        "**[Type Safety] - hyphen finding**\n"
    )
    count, lgtm = _count_findings(text)
    assert count == 3
    assert not lgtm


def test_count_findings_ignores_generic_markdown() -> None:
    """Generic **[Note]** or **[See also]** blocks without '—' separator are not findings."""
    text = (
        "**[Note]** This is an informational aside.\n"
        "**[See also]** Some reference.\n"
        "**[Missing Tests] — actual finding**\n"
    )
    count, lgtm = _count_findings(text)
    assert count == 1
    assert not lgtm


def test_record_review_creates_file(tmp_path: Path) -> None:
    """record_review creates the file and appends a valid JSON entry."""
    p = tmp_path / "metrics.jsonl"
    record_review(
        model="gemini-test",
        pr=42,
        prompt_tokens=1000,
        completion_tokens=200,
        review_text="LGTM — no defects found.",
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
            model="m", pr=i, prompt_tokens=100, completion_tokens=50, review_text="", metrics_path=p
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
        review_text="**[Logic Bug] — x\n",
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
        model="m", pr=99, prompt_tokens=100, completion_tokens=50, review_text="", metrics_path=p
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
        review_text="**[Logic Bug] — x\n",
        metrics_path=p,
    )
    rate_review(pr=5, applied=1, metrics_path=p)
    updated = rate_review(pr=5, applied=0, metrics_path=p)
    assert not updated
    assert load_reviews(p)[-1]["findings_applied"] == 1


def test_load_reviews_empty_file(tmp_path: Path) -> None:
    """load_reviews returns empty list for missing file."""
    assert load_reviews(tmp_path / "missing.jsonl") == []
