"""Guardian review quality metrics: append-only JSONL log with precision tracking."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

_DEFAULT_METRICS_FILE = Path("guardian_metrics.jsonl")

_METRICS_SUFFIXES = frozenset({".jsonl"})


def reject_metrics_path(metrics_path: Path) -> str | None:
    """Return a refusal for a metrics path we must not write to, or None if it is fine.

    `record_review` and `rate_review` are library functions handed a path that
    starts life as a CLI argument (`guardian_review.py --metrics`, threaded
    through `runner.py`). Until now they promised nothing about it: they
    appended to — and `rate_review` overwrote — whatever `Path` arrived, and the
    only reason that was safe is that the one production caller happens to pass
    a default (#347).

    Modelled on `mcp_server._reject_db_path`, which the project already wrote
    for the same shape after a real exploit (#315, fixture `pr-313`): a path
    supplied by an agent reaching a sink that creates files. Deliberately not
    "confine to the repo root" — that would break `tmp_path` in tests and
    ad-hoc runs while doing nothing about the actual harm, which is writing into
    a file that is not ours.

    Judged on the **resolved** path, because a dangling symlink is the bypass:
    `metrics.jsonl` pointing at a target that does not exist yet makes
    `is_file()` False, so every other check passes and the append creates the
    target. That is precisely the primitive demonstrated in `pr-313`.

    The last check is the one that stops the interesting attack: appending JSON
    to an existing `~/.ssh/authorized_keys` is refused because its first line is
    not a JSON object. A new file still has to be named `.jsonl` and land in a
    directory that already exists.

    Filesystem probes are wrapped, because they would otherwise surface as a
    crash rather than a refusal naming the path. `RuntimeError` alongside
    `OSError` is not defensive padding: on a symlink loop CPython's `resolve()`
    catches the ELOOP `OSError` and re-raises it as
    `RuntimeError("Symlink loop from ...")` (pathlib.py:1237), so `except
    OSError` alone does not cover the case this comment claims it does. Found
    by writing the test for it.
    """
    try:
        path = metrics_path.resolve()
    except (OSError, RuntimeError) as exc:
        return f"path is inaccessible ({exc})"
    if path.suffix.lower() not in _METRICS_SUFFIXES:
        return f"name must end in {', '.join(sorted(_METRICS_SUFFIXES))}"
    try:
        if path.is_dir():
            return "it is a directory"
        if not path.parent.is_dir():
            return "parent directory does not exist; cgis will not create one"
        if path.is_file() and path.stat().st_size > 0 and not _looks_like_our_log(path):
            return "existing file is not a Guardian metrics log"
    except OSError as exc:
        return f"path is inaccessible ({exc})"
    return None


#: Characters read to find the first line. A record is a JSON object well under
#: 1 KB, so this is generous by design.
_HEAD_BUDGET = 4096


def _looks_like_our_log(path: Path) -> bool:
    """Whether the file's first line is one of our records.

    Reads a bounded head rather than iterating lines. `for line in fh` looks
    frugal but is unbounded when the file contains no newline at all: a binary
    blob or a disk image is read entirely as one "line". The files this check
    exists to refuse are exactly the ones most likely to be shaped like that,
    so the old form failed in the case its own comment claimed to handle
    (gemini on #350).

    A head that is entirely blank is ambiguous — the file is either ours and
    nearly empty (an interrupted first run leaves a stray newline) or it is
    padded with whitespace past the budget, which ours never is. Size tells
    them apart.
    """
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        head = fh.read(_HEAD_BUDGET + 1)
    for line in head.splitlines():
        if line.strip():
            return _looks_like_a_record(line)
    return len(head) <= _HEAD_BUDGET


def _looks_like_a_record(line: str) -> bool:
    """Whether a line is a JSON object, i.e. plausibly one of our records."""
    try:
        return isinstance(json.loads(line), dict)
    except ValueError:
        return False


def _require_writable(metrics_path: Path) -> None:
    """Raise unless the metrics path is one we are willing to write to.

    Raises rather than degrading to a no-op: a review whose metrics silently
    went nowhere is indistinguishable from one that was never run, and the
    benchmark reads this log.
    """
    refusal = reject_metrics_path(metrics_path)
    if refusal is not None:
        _msg = f"Refusing metrics path '{metrics_path}': {refusal}."
        raise ValueError(_msg)


class SkepticRecord(BaseModel, frozen=True):
    """What the skeptic pass did, for one review's metrics row (#246).

    Grouped rather than passed as four loose keywords: they are meaningless
    apart — `judged`/`total` only interpret `status`, and `impact_threshold`
    only interprets what the report then hid.
    """

    model: str | None = None
    status: str = "off"
    judged: int = 0
    total: int = 0
    impact_threshold: int = 0


def record_review(
    *,
    model: str,
    pr: int | None,
    prompt_tokens: int,
    completion_tokens: int,
    findings_total: int,
    lgtm: bool,
    parse_failed: bool = False,
    skeptic: SkepticRecord | None = None,
    chunk_count: int | None = None,
    duration_s: float | None = None,
    metrics_path: Path = _DEFAULT_METRICS_FILE,
) -> Path:
    """Append one review entry to the metrics JSONL file and return the path.

    Counts come from the structured ReviewResult — no text parsing.
    Note: lgtm is False on parse_failed runs even though findings_total == 0.
    """
    skeptic = skeptic or SkepticRecord()
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
        "skeptic_model": skeptic.model,
        "skeptic_status": skeptic.status,
        "skeptic_judged": skeptic.judged,
        "skeptic_total": skeptic.total,
        "impact_threshold": skeptic.impact_threshold,
        "chunk_count": chunk_count,
        # Wall-clock of the whole LLM phase (finder + skeptic). None on entries
        # written before #275. Completed runs only — a failing review writes no
        # record at all, so this data is survivorship-biased by construction.
        "duration_s": duration_s,
    }
    _require_writable(metrics_path)
    with metrics_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    return metrics_path


def rate_review(pr: int, applied: int, metrics_path: Path = _DEFAULT_METRICS_FILE) -> bool:
    """Set findings_applied for the most recent entry matching the given PR.

    Returns True if an entry was updated, False if none found.

    Rewrites the whole file, so it validates the path like `record_review`
    does — this is the more destructive of the two (#347).
    """
    _require_writable(metrics_path)
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
