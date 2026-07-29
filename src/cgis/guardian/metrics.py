"""Guardian review quality metrics: append-only JSONL log with precision tracking."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_DEFAULT_METRICS_FILE = Path("guardian_metrics.jsonl")


def record_review(
    *,
    model: str,
    pr: int | None,
    prompt_tokens: int,
    completion_tokens: int,
    findings_total: int,
    lgtm: bool,
    parse_failed: bool = False,
    skeptic_model: str | None = None,
    skeptic_status: str = "off",
    skeptic_judged: int = 0,
    skeptic_total: int = 0,
    impact_threshold: int = 0,
    chunk_count: int | None = None,
    metrics_path: Path = _DEFAULT_METRICS_FILE,
) -> Path:
    """Append one review entry to the metrics JSONL file and return the path.

    Counts come from the structured ReviewResult — no text parsing.
    Note: lgtm is False on parse_failed runs even though findings_total == 0.
    """
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
        "parse_failed": parse_failed,
        "skeptic_model": skeptic_model,
        "skeptic_status": skeptic_status,
        "skeptic_judged": skeptic_judged,
        "skeptic_total": skeptic_total,
        "impact_threshold": impact_threshold,
        "chunk_count": chunk_count,
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
