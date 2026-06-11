"""Unit tests for the GitHub inline-review poster (mocked subprocess, spec §6.4)."""

import json
from unittest.mock import patch

from cgis.guardian.findings import Finding, ReviewResult
from cgis.guardian.github_poster import build_review, post_inline_review

_IN_DIFF = Finding(
    file="src/x.py",
    line=11,
    severity="major",
    category="logic",
    title="in-diff",
    evidence="e",
    problem="p",
    fix="f",
    confidence=90,
)
_OUTSIDE = Finding(
    file="src/x.py",
    line=999,
    severity="minor",
    category="tests",
    title="outside",
    evidence="e",
    problem="p",
    fix="f",
    confidence=85,
)
_REFUTED = _IN_DIFF.model_copy(update={"verdict": "refuted", "title": "dead"})


def test_build_review_splits_inline_and_outside() -> None:
    """In-index findings become comments; others go to the body; refuted vanish."""
    result = ReviewResult(findings=[_IN_DIFF, _OUTSIDE, _REFUTED], summary="s")
    body, comments = build_review(result, diff_index={"src/x.py": {10, 11, 12}}, skeptic_model=None)
    assert len(comments) == 1
    assert comments[0]["path"] == "src/x.py"
    assert comments[0]["line"] == 11
    assert comments[0]["side"] == "RIGHT"
    assert "in-diff" in str(comments[0]["body"])
    assert "outside" in body
    assert "dead" not in body


def test_build_review_line_none_goes_to_body() -> None:
    """A file-level finding (line=None) can never be inline."""
    file_level = _IN_DIFF.model_copy(update={"line": None})
    _, comments = build_review(
        ReviewResult(findings=[file_level], summary="s"),
        diff_index={"src/x.py": {11}},
        skeptic_model=None,
    )
    assert comments == []


def test_post_inline_review_payload() -> None:
    """The gh api call posts one COMMENT review with the built payload on stdin."""
    result = ReviewResult(findings=[_IN_DIFF], summary="s")
    with patch("cgis.guardian.github_poster.subprocess.run") as mock_run:
        post_inline_review(
            repo="zaebee/codegraph-brain",
            pr=153,
            result=result,
            diff_index={"src/x.py": {11}},
            skeptic_model=None,
        )
    args, kwargs = mock_run.call_args
    assert args[0][:3] == ["gh", "api", "repos/zaebee/codegraph-brain/pulls/153/reviews"]
    payload = json.loads(kwargs["input"])
    assert payload["event"] == "COMMENT"
    assert payload["comments"][0]["line"] == 11
    assert kwargs["check"] is True


def test_footer_rides_in_review_body() -> None:
    """The footer reaches the inline review body — a successful inline post
    skips the fallback comment, so this is the only way it gets to the PR."""
    result = ReviewResult(findings=[_IN_DIFF], summary="s")
    footer = "\n\n---\n> 🤖 **gemini-2.5-flash** · 1,000 prompt"
    body, _ = build_review(result, diff_index={"src/x.py": {11}}, skeptic_model=None, footer=footer)
    assert body.endswith(footer)
    with patch("cgis.guardian.github_poster.subprocess.run") as mock_run:
        post_inline_review(
            repo="zaebee/codegraph-brain",
            pr=157,
            result=result,
            diff_index={"src/x.py": {11}},
            skeptic_model=None,
            footer=footer,
        )
    payload = json.loads(mock_run.call_args.kwargs["input"])
    assert payload["body"].endswith(footer)


def test_footer_default_empty_keeps_body_unchanged() -> None:
    """Omitting the footer must not alter the rendered body (back-compat)."""
    result = ReviewResult(findings=[_IN_DIFF], summary="s")
    with_default, _ = build_review(result, diff_index={}, skeptic_model=None)
    with_empty, _ = build_review(result, diff_index={}, skeptic_model=None, footer="")
    assert with_default == with_empty
