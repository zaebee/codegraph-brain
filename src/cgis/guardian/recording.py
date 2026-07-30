"""Persisted finder passes: the recording format shared by bench and production.

Extracted from bench.py (#279) once run_guardian became a second consumer —
production code must not import the benchmark module. Same reason diff_index.py
was split out of chunker.py when the skeptic pass needed the per-file split.
"""

from pathlib import Path

from pydantic import BaseModel

from cgis.guardian.findings import ReviewResult


class FinderRecording(BaseModel, frozen=True):
    """A frozen finder pass: its findings plus the diff they were found in (#246 §4.1).

    The diff travels with the findings so a replay needs no worktree, ingest or
    git at all — without it, isolating the skeptic would still pay the whole
    setup cost the replay exists to avoid.
    """

    result: ReviewResult
    diff: str


def _validated_recording_path(path: Path, *, must_exist: bool) -> Path:
    """Resolve and check a CLI-supplied recording path before touching the filesystem.

    Both entry points take their path from a benchmark CLI flag, so the value is
    untrusted input. Validation mirrors the precedent set for the MCP drift
    surface (#167): resolve, require the expected suffix, and require an
    existing file when reading — deliberately WITHOUT confining the path under
    the CWD, which would break the legitimate cross-repo/tmpdir workflows this
    tool is used in.
    """
    resolved = path.expanduser().resolve()
    if resolved.suffix != ".json":
        _msg = f"Recording path must be a .json file: {path}"
        raise ValueError(_msg)
    if must_exist and not resolved.is_file():
        _msg = f"Recording path is not a file: {path}"
        raise ValueError(_msg)
    return resolved


def save_finder_recording(path: Path, result: ReviewResult, diff: str) -> None:
    """Write a finder pass to disk for later replay.

    A production recording is taken AFTER the skeptic ran, which is safe because
    load_finder_recording strips verdicts on read. One caveat travels with it:
    apply_judgements rewrites confidence to round(confidence * 0.9) for
    'uncertain' verdicts and the load path does not restore it, so a recorded
    confidence on that subset is not the finder's own number (#279).
    """
    target = _validated_recording_path(path, must_exist=False)
    recording = FinderRecording(result=result, diff=diff)
    target.write_text(recording.model_dump_json(), encoding="utf-8")


def load_finder_recording(path: Path) -> FinderRecording:
    """Load a recorded finder pass, stripping any skeptic verdicts it carries.

    A recording captured from a run that HAD a skeptic would otherwise smuggle
    those verdicts into the next variant's scoring — every replay must start
    from unjudged findings, or the arms are not comparable.
    """
    source = _validated_recording_path(path, must_exist=True)
    recording = FinderRecording.model_validate_json(source.read_text(encoding="utf-8"))
    findings = [
        f.model_copy(update={"verdict": None, "skeptic_note": None, "impact_score": None})
        for f in recording.result.findings
    ]
    clean = recording.result.model_copy(
        update={
            "findings": findings,
            "skeptic_status": "off",
            "skeptic_judged": 0,
            "skeptic_total": 0,
        }
    )
    return recording.model_copy(update={"result": clean})
