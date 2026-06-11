"""Chunked review: per-chunk finder passes behind GUARDIAN_FEATURES=chunked.

Slice 2 of #154 (spec: 2026-06-11-guardian-chunked-review-design.md). The
finder LGTMs large PRs (attention dilution); each chunk gets a small,
complete world instead — its own diff, full files, and impact graph.
"""

import structlog
from pydantic import BaseModel

from cgis.guardian.chunker import Chunk
from cgis.guardian.findings import Finding, ReviewResult

log = structlog.getLogger(__name__)

MAX_CHUNKS = 8


class RoutedReview(BaseModel, frozen=True):
    """Review outcome plus chunk accounting (chunk_count=None = single-pass path)."""

    result: ReviewResult
    chunk_count: int | None = None


def _cap_chunks(chunks: list[Chunk]) -> list[Chunk]:
    """Bound API calls (spec §4.3): keep the MAX_CHUNKS-1 largest, merge the rest.

    The overflow chunk goes last; kept chunks stay in slice-1 order (sorted
    by first file). Ties in size break by first file name — deterministic.
    """
    if len(chunks) <= MAX_CHUNKS:
        return chunks
    ranked = sorted(chunks, key=lambda c: (-len(c.diff), c.files[0]))
    keep, rest = ranked[: MAX_CHUNKS - 1], ranked[MAX_CHUNKS - 1 :]
    rest_sorted = sorted(rest, key=lambda c: c.files[0])
    overflow = Chunk(
        files=tuple(sorted({f for c in rest_sorted for f in c.files})),
        diff="".join(c.diff for c in rest_sorted),
    )
    log.warning("Chunk cap hit; smallest chunks merged.", merged=len(rest), cap=MAX_CHUNKS)
    return [*sorted(keep, key=lambda c: c.files[0]), overflow]


def _dedup(findings: list[Finding]) -> list[Finding]:
    """Drop duplicate (file, line, category) findings, keeping the higher confidence.

    Cross-chunk duplicates are impossible after the per-chunk file filter
    (chunks partition files) — this is insurance against intra-pass
    duplicates. First-occurrence order is preserved.
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
