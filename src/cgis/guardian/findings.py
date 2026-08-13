"""Structured findings contract for the Guardian reviewer (spec §2.1)."""

import json
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

Severity = Literal["critical", "major", "minor"]
# "security" exists so curated ground truth can name an exploitable defect (#315).
# The finder prompt does not yet offer it as a choice — teaching the finder to
# emit it is #258, and needs bench validation. Scoring is unaffected either way:
# bench matching compares file and line range, never category.
Category = Literal["logic", "contract", "tests", "types", "ontology", "security"]
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


def dedup_findings(findings: list[Finding]) -> list[Finding]:
    """Drop duplicate (file, line, category) findings, keeping the higher confidence.

    Shared by every fan-out reviewer: chunked review partitions files so
    cross-chunk duplicates cannot occur and this is insurance, but per-axis
    review (#331) asks several axes about the same lines, where collisions are
    expected rather than exceptional. First-occurrence order is preserved.
    """
    best: dict[tuple[str, int | None, str], Finding] = {}
    order: list[tuple[str, int | None, str]] = []
    for finding in findings:
        key = (finding.file, finding.line, finding.category)
        if key not in best:
            best[key] = finding
            order.append(key)
        elif finding.confidence > best[key].confidence:
            best[key] = finding
    return [best[k] for k in order]


#: Characters that may sit between two elements of a JSON array.
_ARRAY_GAP = ", \t\r\n"


def salvage_findings(text: str) -> list[Finding]:
    """Findings recoverable from a finder response that failed strict validation.

    The recall-lean finder emits many findings per review, and a longer JSON body
    is likelier to be severed mid-object by a provider. Discarding the whole
    response then throws away every finding that did parse — in the Phase 3 run
    that was fifteen of eighteen, lost because the sixteenth lacked a field and
    the seventeenth was a bare string fragment.

    Decodes the `findings` array one element at a time rather than parsing the
    document, because a truncated document has no valid parse at all. Each
    element is validated on its own: one that decodes as JSON but is not a
    `Finding` is skipped and the scan continues, and the first element that
    cannot be decoded ends it — that is the truncation point.

    Never raises. This runs on the failure path, where an exception would replace
    a recoverable problem with an unrecoverable one.

    Salvaging is a rescue, not a measurement. Callers keep `parse_failed` set, so
    the benchmark still excludes such a review; production shows the findings
    rather than nothing.
    """
    payload = extract_json(text)
    opening = payload.find("[", payload.find('"findings"')) if '"findings"' in payload else -1
    if opening == -1:
        return []

    decoder = json.JSONDecoder()
    position = opening + 1
    recovered: list[Finding] = []
    while position < len(payload):
        while position < len(payload) and payload[position] in _ARRAY_GAP:
            position += 1
        if position >= len(payload) or payload[position] == "]":
            break
        try:
            element, position = decoder.raw_decode(payload, position)
        except ValueError:
            break  # the truncation point
        try:
            recovered.append(Finding.model_validate(element))
        except ValidationError:
            continue  # decoded but not a finding; the scan has already advanced
    return recovered
