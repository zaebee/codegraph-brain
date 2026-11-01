"""Unit tests for the GitHub inline-review poster (mocked HTTP, spec §6.4)."""

import io
import json
import os
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

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

_REVIEW_URL = "https://api.github.com/repos/zaebee/codegraph-brain/pulls/153/reviews"


def _post(result: ReviewResult, *, pr: int = 153, footer: str = "") -> urllib.request.Request:
    """Post through a stand-in for urlopen and return the request it received."""
    with (
        patch.dict(os.environ, {"GH_TOKEN": "tok"}),
        patch("cgis.guardian.github_poster.urllib.request.urlopen") as mock_open,
    ):
        mock_open.return_value.__enter__.return_value = MagicMock()
        post_inline_review(
            repo="zaebee/codegraph-brain",
            pr=pr,
            result=result,
            diff_index={"src/x.py": {11}},
            skeptic_model=None,
            footer=footer,
        )
    request: urllib.request.Request = mock_open.call_args.args[0]
    return request


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
    """One COMMENT review, sent as JSON to the reviews endpoint (#742)."""
    request = _post(ReviewResult(findings=[_IN_DIFF], summary="s"))
    assert request.full_url == _REVIEW_URL
    assert request.get_method() == "POST"
    payload = json.loads(request.data)
    assert payload["event"] == "COMMENT"
    assert payload["comments"][0]["line"] == 11


def test_post_inline_review_authenticates_with_the_workflow_token() -> None:
    """The bearer token and API headers the workflow exports; no gh config is read."""
    request = _post(ReviewResult(findings=[_IN_DIFF], summary="s"))
    headers = {key.lower(): value for key, value in request.header_items()}
    assert headers["authorization"] == "Bearer tok"
    assert headers["accept"] == "application/vnd.github+json"
    assert headers["content-type"] == "application/json"
    assert headers["x-github-api-version"] == "2022-11-28"
    assert headers["user-agent"]


def test_post_inline_review_falls_back_to_the_actions_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GITHUB_TOKEN is accepted where GH_TOKEN is unset, as gh itself accepts both."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "actions")
    with patch("cgis.guardian.github_poster.urllib.request.urlopen") as mock_open:
        mock_open.return_value.__enter__.return_value = MagicMock()
        post_inline_review(
            repo="zaebee/codegraph-brain",
            pr=153,
            result=ReviewResult(findings=[_IN_DIFF], summary="s"),
            diff_index={"src/x.py": {11}},
            skeptic_model=None,
        )
    request = mock_open.call_args.args[0]
    assert {key.lower(): value for key, value in request.header_items()}["authorization"] == (
        "Bearer actions"
    )


def test_post_without_a_token_fails_before_sending(monkeypatch: pytest.MonkeyPatch) -> None:
    """No token is a configuration error, reported without a network round-trip."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    result = ReviewResult(findings=[_IN_DIFF], summary="s")
    with (
        patch("cgis.guardian.github_poster.urllib.request.urlopen") as mock_open,
        pytest.raises(RuntimeError, match="GH_TOKEN"),
    ):
        post_inline_review(
            repo="zaebee/codegraph-brain",
            pr=153,
            result=result,
            diff_index={"src/x.py": {11}},
            skeptic_model=None,
        )
    mock_open.assert_not_called()


def test_api_rejection_reports_the_response_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-2xx carries GitHub's message into the exception the runner logs."""
    monkeypatch.setenv("GH_TOKEN", "tok")
    rejected = urllib.error.HTTPError(
        _REVIEW_URL,
        422,
        "Unprocessable Entity",
        {},
        io.BytesIO(b'{"message":"line not in diff"}'),
    )
    result = ReviewResult(findings=[_IN_DIFF], summary="s")
    with (
        patch("cgis.guardian.github_poster.urllib.request.urlopen", side_effect=rejected),
        pytest.raises(RuntimeError, match="line not in diff"),
    ):
        post_inline_review(
            repo="zaebee/codegraph-brain",
            pr=153,
            result=result,
            diff_index={"src/x.py": {11}},
            skeptic_model=None,
        )


def test_footer_rides_in_review_body() -> None:
    """The footer reaches the inline review body — a successful inline post
    skips the fallback comment, so this is the only way it gets to the PR."""
    result = ReviewResult(findings=[_IN_DIFF], summary="s")
    footer = "\n\n---\n> 🤖 **gemini-2.5-flash** · 1,000 prompt"
    body, _ = build_review(result, diff_index={"src/x.py": {11}}, skeptic_model=None, footer=footer)
    assert body.endswith(footer)
    request = _post(result, pr=157, footer=footer)
    payload = json.loads(request.data)
    assert payload["body"].endswith(footer)


def test_footer_default_empty_keeps_body_unchanged() -> None:
    """Omitting the footer must not alter the rendered body (back-compat)."""
    result = ReviewResult(findings=[_IN_DIFF], summary="s")
    with_default, _ = build_review(result, diff_index={}, skeptic_model=None)
    with_empty, _ = build_review(result, diff_index={}, skeptic_model=None, footer="")
    assert with_default == with_empty


_CONTENT = {"src/x.py": {10: "    context1", 11: "    added1", 12: "    added2", 13: "    ctx2"}}


def _finding(**over: object) -> Finding:
    base: dict[str, object] = {
        "file": "src/x.py",
        "line": 11,
        "severity": "major",
        "category": "logic",
        "title": "t",
        "evidence": "e",
        "problem": "p",
        "fix": "f",
        "confidence": 90,
    }
    base.update(over)
    return Finding(**base)  # type: ignore[arg-type]


def test_anchor_corrects_a_wrong_model_line() -> None:
    """A verbatim anchor overrides a hallucinated line — comment lands on the real line (#181)."""
    f = _finding(line=999, anchor="added2")  # model guessed 999; real line is 12
    result = ReviewResult(findings=[f], summary="s")
    _, comments = build_review(
        result,
        diff_index={"src/x.py": {10, 11, 12, 13}},
        skeptic_model=None,
        diff_content=_CONTENT,
    )
    assert len(comments) == 1
    assert comments[0]["line"] == 12


def test_hallucinated_anchor_demotes_to_body() -> None:
    """A quote that appears nowhere in the diff becomes a body note, not a wrong inline anchor."""
    f = _finding(line=11, anchor="this text is not in the diff at all")
    result = ReviewResult(findings=[f], summary="s")
    body, comments = build_review(
        result,
        diff_index={"src/x.py": {10, 11, 12, 13}},
        skeptic_model=None,
        diff_content=_CONTENT,
    )
    assert comments == []  # not posted at the (wrong) model line 11
    assert "t" in body  # finding survives in the body — nothing lost


def test_no_diff_content_keeps_legacy_behaviour() -> None:
    """Without diff_content, the model line is used as before (backward compatible)."""
    f = _finding(line=11, anchor="added2")
    result = ReviewResult(findings=[f], summary="s")
    _, comments = build_review(result, diff_index={"src/x.py": {10, 11, 12}}, skeptic_model=None)
    assert comments[0]["line"] == 11


def test_exact_match_beats_a_spurious_substring() -> None:
    """A trivial line that is a substring of the anchor must not outrank the exact
    match, even when the model's hallucinated line is nearer to it (#181 review)."""
    content = {
        "src/x.py": {
            4: "from cgis.resolver.indices import IndexBuilder, SymbolIndex",
            20: ")",  # substring of the anchor — must NOT win
        }
    }
    f = _finding(line=18, anchor="from cgis.resolver.indices import IndexBuilder, SymbolIndex")
    result = ReviewResult(findings=[f], summary="s")
    _, comments = build_review(
        result, diff_index={"src/x.py": {4, 20}}, skeptic_model=None, diff_content=content
    )
    assert comments[0]["line"] == 4  # exact match wins over the nearer ")" at 20


def test_trivial_anchor_does_not_substring_match() -> None:
    """A 1-char anchor like ')' can't substring-match 'qux()' — it demotes to body."""
    content = {"src/x.py": {12: "    qux()"}}
    f = _finding(line=12, anchor=")")
    result = ReviewResult(findings=[f], summary="s")
    _, comments = build_review(
        result, diff_index={"src/x.py": {12}}, skeptic_model=None, diff_content=content
    )
    assert comments == []  # no exact ')' line, too short to substring → demoted


def test_file_level_finding_stays_file_level() -> None:
    """A file-level finding (line=None, no anchor) is NOT promoted to inline just
    because its evidence text appears somewhere in the diff (#243 review)."""
    content = {"src/x.py": {10: "    added1", 11: "    added2"}}
    # evidence quotes a real diff line, but the model said this is file-level
    f = _finding(line=None, anchor=None, evidence="added2")
    result = ReviewResult(findings=[f], summary="s")
    _, comments = build_review(
        result, diff_index={"src/x.py": {10, 11}}, skeptic_model=None, diff_content=content
    )
    assert comments == []  # stays in the body, not anchored to line 11


def test_explicit_anchor_still_places_a_lineless_finding() -> None:
    """line=None but an explicit anchor → the model opted into positioning, so anchor wins."""
    content = {"src/x.py": {10: "    added1", 11: "    added2"}}
    f = _finding(line=None, anchor="added2")
    result = ReviewResult(findings=[f], summary="s")
    _, comments = build_review(
        result, diff_index={"src/x.py": {10, 11}}, skeptic_model=None, diff_content=content
    )
    assert comments[0]["line"] == 11


def test_short_keyword_anchor_does_not_substring_match() -> None:
    """A 5-char keyword like 'else:' must not substring-match 'something_else:' —
    only exact line equality may anchor below _MIN_SUBSTRING_ANCHOR (#243 review)."""
    content = {"src/x.py": {5: "    something_else: int = 1"}}
    f = _finding(line=99, anchor="else:")  # 5 chars; substring of the line but generic
    result = ReviewResult(findings=[f], summary="s")
    _, comments = build_review(
        result, diff_index={"src/x.py": {5}}, skeptic_model=None, diff_content=content
    )
    assert comments == []  # demoted: too short to substring, no exact match
