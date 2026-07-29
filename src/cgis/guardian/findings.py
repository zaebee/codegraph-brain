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
    # Verbatim single source line the finding sits on, used to derive the inline
    # anchor deterministically instead of trusting the model's ``line`` (#181).
    anchor: str | None = None
    verdict: Verdict | None = None
    skeptic_note: str | None = None
    # 0-10 importance from the skeptic (#246 spec §3.1). None = not judged.
    # Orthogonal to `verdict`: a finding can be true (confirmed) and worthless
    # (score 1), which one enum value cannot express.
    impact_score: int | None = Field(default=None, ge=0, le=10)


class ReviewResult(BaseModel, frozen=True):
    """The full review: findings (empty = LGTM) plus a checked-aspects summary."""

    findings: list[Finding]
    summary: str
    parse_failed: bool = False
    # "off" = skeptic not configured; "ok" = every finding judged; "partial" =
    # some judgement calls failed (see skeptic_judged/skeptic_total); "failed" =
    # no finding was judged, single-pass results returned (spec §5.5 / #246
    # §3.4 — never silent).
    skeptic_status: Literal["off", "ok", "partial", "failed"] = "off"
    skeptic_judged: int = 0
    skeptic_total: int = 0


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
