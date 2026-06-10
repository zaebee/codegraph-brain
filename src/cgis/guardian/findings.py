"""Structured findings contract for the Guardian reviewer (spec §2.1)."""

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["critical", "major", "minor"]
Category = Literal["logic", "contract", "tests", "types", "ontology"]
Verdict = Literal["confirmed", "refuted", "uncertain"]


class Finding(BaseModel, frozen=True):
    """One reviewed defect, anchored to a file (and optionally a line)."""

    file: str
    # ge (not gt): gemini's response Schema rejects exclusiveMinimum
    line: int | None = Field(default=None, ge=1)
    severity: Severity
    category: Category
    title: str
    evidence: str
    problem: str
    fix: str
    confidence: int = Field(ge=0, le=100)
    verdict: Verdict | None = None
    skeptic_note: str | None = None


class ReviewResult(BaseModel, frozen=True):
    """The full review: findings (empty = LGTM) plus a checked-aspects summary."""

    findings: list[Finding]
    summary: str
    parse_failed: bool = False


def extract_json(text: str) -> str:
    """Return the JSON payload from an LLM response, stripping markdown fences.

    The closing fence is matched as a real newline followed by ``` — raw
    newlines cannot occur inside JSON strings, so embedded backticks in
    field values are safe. Text after the closing fence is discarded.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    newline = stripped.find("\n")
    if newline == -1:
        return ""
    body = stripped[newline + 1 :]
    closing = body.find("\n```")
    if closing != -1:
        return body[:closing].strip()
    # No newline before the closing fence (e.g. `{...}```` on one line):
    # valid JSON never ends with backticks, so stripping the suffix is safe.
    return body.strip().removesuffix("```").strip()
