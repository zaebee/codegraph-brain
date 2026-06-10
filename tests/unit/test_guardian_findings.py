"""Tests for the structured findings contract (spec §2.1)."""

import json

import pytest
from pydantic import ValidationError

from cgis.guardian.findings import Finding, ReviewResult, extract_json


def _finding(**overrides: object) -> Finding:
    base: dict[str, object] = {
        "file": "src/cgis/cli.py",
        "line": 42,
        "severity": "major",
        "category": "logic",
        "title": "off-by-one in pagination",
        "evidence": "for i in range(n + 1):",
        "problem": "iterates one element past the end.",
        "fix": "use range(n).",
        "confidence": 85,
    }
    base.update(overrides)
    return Finding.model_validate(base)


def test_finding_skeptic_fields_default_to_none() -> None:
    """A fully-populated finding validates; skeptic fields default to None."""
    f = _finding()
    assert f.verdict is None
    assert f.skeptic_note is None


def test_finding_is_frozen() -> None:
    """Finding is immutable — updates go through model_copy."""
    f = _finding()
    with pytest.raises(ValidationError):
        f.confidence = 90  # type: ignore[misc]
    assert f.model_copy(update={"verdict": "confirmed"}).verdict == "confirmed"


def test_finding_line_must_be_positive() -> None:
    """line=0 violates ge=1 (gemini Schema rejects exclusiveMinimum, so ge not gt)."""
    with pytest.raises(ValidationError):
        _finding(line=0)


def test_finding_line_none_means_file_level() -> None:
    """line=None is valid (file-level finding)."""
    assert _finding(line=None).line is None


def test_finding_confidence_bounds() -> None:
    """confidence outside [0, 100] is rejected."""
    with pytest.raises(ValidationError):
        _finding(confidence=101)
    with pytest.raises(ValidationError):
        _finding(confidence=-1)


def test_finding_rejects_unknown_severity_and_category() -> None:
    """Literal fields reject values outside the closed sets."""
    with pytest.raises(ValidationError):
        _finding(severity="blocker")
    with pytest.raises(ValidationError):
        _finding(category="style")


def test_review_result_empty_findings_is_lgtm() -> None:
    """Empty findings list with a summary is the LGTM shape."""
    r = ReviewResult(findings=[], summary="checked X and Y")
    assert r.findings == []
    assert r.parse_failed is False


def test_review_result_round_trips_json() -> None:
    """model_validate_json(model_dump_json()) is the identity."""
    r = ReviewResult(findings=[_finding()], summary="s")
    assert ReviewResult.model_validate_json(r.model_dump_json()) == r


def test_extract_json_plain() -> None:
    """Plain JSON passes through untouched."""
    assert extract_json('{"a": 1}') == '{"a": 1}'


def test_extract_json_strips_fences() -> None:
    """```json fences (LLM habit) are stripped."""
    fenced = '```json\n{"a": 1}\n```'
    assert extract_json(fenced) == '{"a": 1}'


def test_extract_json_strips_bare_fences() -> None:
    """``` fences without a language tag are stripped too."""
    fenced = '```\n{"a": 1}\n```'
    assert extract_json(fenced) == '{"a": 1}'


def test_extract_json_ignores_trailing_prose_after_fence() -> None:
    """LLM commentary after the closing fence is discarded."""
    text = '```json\n{"a": 1}\n```\n\nExtra commentary from the model.'
    assert extract_json(text) == '{"a": 1}'


def test_extract_json_unclosed_fence_returns_body() -> None:
    """An opening fence with no closing fence yields the body as-is."""
    assert extract_json('```json\n{"a": 1}') == '{"a": 1}'


def test_extract_json_lone_fence_returns_empty() -> None:
    """A bare ``` with no payload yields an empty string."""
    assert extract_json("```") == ""


def test_finding_schema_has_no_exclusive_minimum() -> None:
    """gemini's response Schema rejects exclusiveMinimum — line must use ge, not gt."""
    schema = json.dumps(ReviewResult.model_json_schema())
    assert "exclusiveMinimum" not in schema


def test_extract_json_inline_closing_fence() -> None:
    """Closing fence on the same line as the JSON must still be stripped."""
    text = '```json\n{"findings": [], "summary": "ok"}```'
    assert json.loads(extract_json(text)) == {"findings": [], "summary": "ok"}
