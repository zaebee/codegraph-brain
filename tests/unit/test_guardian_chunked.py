"""Unit tests for chunked review orchestration (spec: 2026-06-11-guardian-chunked-review)."""

from cgis.guardian.chunked import MAX_CHUNKS, RoutedReview, _cap_chunks, _dedup
from cgis.guardian.chunker import Chunk
from cgis.guardian.findings import Finding, ReviewResult


def _finding(
    file: str = "a.py",
    line: int | None = 1,
    category: str = "logic",
    confidence: int = 90,
    title: str = "t",
) -> Finding:
    """Minimal finding for merge/dedup tests."""
    return Finding(
        file=file,
        line=line,
        severity="major",
        category=category,
        title=title,
        evidence="e",
        problem="p",
        fix="f",
        confidence=confidence,
    )


def test_routed_review_chunk_count_defaults_none() -> None:
    """RoutedReview carries result + chunk accounting; None = single-pass."""
    rr = RoutedReview(result=ReviewResult(findings=[], summary="s"))
    assert rr.chunk_count is None


def test_cap_chunks_noop_at_or_under_max() -> None:
    """<= MAX_CHUNKS chunks come back unchanged, same order."""
    chunks = [Chunk(files=(f"{i}.py",), diff=f"d{i}\n") for i in range(MAX_CHUNKS)]
    assert _cap_chunks(chunks) == chunks


def test_cap_chunks_merges_smallest_into_overflow() -> None:
    """11 chunks -> 7 largest kept (sorted by first file) + 1 overflow, last."""
    big = [Chunk(files=(f"big{i}.py",), diff="x" * (100 + i) + "\n") for i in range(7)]
    small = [Chunk(files=(f"small{i}.py",), diff=f"s{i}\n") for i in range(4)]
    capped = _cap_chunks(big + small)
    assert len(capped) == MAX_CHUNKS
    overflow = capped[-1]
    assert overflow.files == tuple(sorted(f"small{i}.py" for i in range(4)))
    assert all(f"s{i}\n" in overflow.diff for i in range(4))
    assert [c.files[0] for c in capped[:-1]] == sorted(f"big{i}.py" for i in range(7))


def test_dedup_keeps_higher_confidence() -> None:
    """Same (file, line, category) -> one survivor, the more confident one."""
    low = _finding(confidence=81, title="low")
    high = _finding(confidence=95, title="high")
    other = _finding(file="b.py", title="other")
    result = _dedup([low, other, high])
    assert [f.title for f in result] == ["high", "other"]


def test_dedup_distinct_lines_kept() -> None:
    """Different lines are different findings."""
    assert len(_dedup([_finding(line=1), _finding(line=2), _finding(line=None)])) == 3
