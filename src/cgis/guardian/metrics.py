"""Guardian review quality metrics: append-only JSONL log with precision tracking."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_DEFAULT_METRICS_FILE = Path("guardian_metrics.jsonl")


def _count_findings(review_text: str) -> tuple[int, bool]:
    """Parse finding count and LGTM flag from review text.

    Returns (findings_total, lgtm).
    Counts headings that match the output format: lines starting with '**['.
    """
    findings = len(
        re.findall(
            r"^\*\*\[(?:Logic Bug|Test Coverage|Type Safety|Ontology)", review_text, re.MULTILINE
        )
    )
    lgtm = findings == 0 and "lgtm" in review_text.lower()
    return findings, lgtm


def record_review(
    *,
    model: str,
    pr: int | None,
    prompt_tokens: int,
    completion_tokens: int,
    review_text: str,
    metrics_path: Path = _DEFAULT_METRICS_FILE,
) -> Path:
    """Append one review entry to the metrics JSONL file and return the path.

    The file is created if it does not exist. Each line is a self-contained
    JSON object so the file can be streamed line-by-line without loading it all.
    """
    findings_total, lgtm = _count_findings(review_text)
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "pr": pr,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "findings_total": findings_total,
        "findings_applied": None,
        "lgtm": lgtm,
    }
    with metrics_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    return metrics_path


def rate_review(pr: int, applied: int, metrics_path: Path = _DEFAULT_METRICS_FILE) -> bool:
    """Set findings_applied for the most recent entry matching the given PR.

    Returns True if an entry was updated, False if none found.
    """
    if not metrics_path.exists():
        return False

    lines = metrics_path.read_text(encoding="utf-8").splitlines()
    updated = False
    result: list[str] = []
    for line in reversed(lines):
        if not updated:
            entry = json.loads(line)
            if entry.get("pr") == pr and entry.get("findings_applied") is None:
                entry["findings_applied"] = applied
                line = json.dumps(entry)  # noqa: PLW2901
                updated = True
        result.append(line)

    if updated:
        metrics_path.write_text("\n".join(reversed(result)) + "\n", encoding="utf-8")
    return updated


def load_reviews(metrics_path: Path = _DEFAULT_METRICS_FILE) -> list[dict[str, Any]]:
    """Return all review records from the metrics file, oldest first."""
    if not metrics_path.exists():
        return []
    return [
        json.loads(line)
        for line in metrics_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
