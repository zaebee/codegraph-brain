"""Chunked review: per-chunk finder passes behind GUARDIAN_FEATURES=chunked.

Slice 2 of #154 (spec: 2026-06-11-guardian-chunked-review-design.md). The
finder LGTMs large PRs (attention dilution); each chunk gets a small,
complete world instead — its own diff, full files, and impact graph.
"""

import structlog
from pydantic import BaseModel

from cgis.guardian.chunker import Chunk, build_chunks
from cgis.guardian.collector import ContextCollector
from cgis.guardian.core import GuardianReviewer, finder_pass
from cgis.guardian.findings import Finding, ReviewResult, extract_json
from cgis.guardian.providers.base import BaseProvider
from cgis.guardian.skeptic import (
    SKEPTIC_SYSTEM_PROMPT,
    SkepticResult,
    apply_verdicts,
    build_skeptic_prompt,
)
from cgis.storage.sqlite_store import SQLiteStore

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


async def run_chunked_review(
    *,
    provider: BaseProvider,
    collector: ContextCollector,
    skeptic_provider: BaseProvider | None,
) -> RoutedReview:
    """Per-chunk finder passes -> filter -> merge -> dedup -> one skeptic pass.

    Degradations (spec §5): a chunk whose finder call raises or never parses
    contributes zero findings and a ⚠ bullet; parse_failed on the merged
    result only when EVERY chunk failed; skeptic failure returns the merged
    findings unverified.
    """
    diff = collector.get_git_diff()
    if collector.db_path is None:  # routed guard (§4.1) — belt and braces
        _msg = "run_chunked_review requires a graph DB"
        raise RuntimeError(_msg)
    with SQLiteStore(str(collector.db_path)) as store:
        chunks = build_chunks(diff, store, source_root=collector.source_root)
    if not chunks:
        return RoutedReview(
            result=ReviewResult(findings=[], summary="Empty diff — nothing to review."),
            chunk_count=0,
        )
    chunks = _cap_chunks(chunks)

    bullets: list[str] = []
    kept: list[Finding] = []
    finding_contexts: list[dict[str, str]] = []
    failed = 0
    for chunk in chunks:
        label = ", ".join(chunk.files)
        context = collector.collect_for_chunk(chunk)
        try:
            result = await finder_pass(provider, context)
        except Exception:
            log.warning(
                "Chunk finder call failed; chunk skipped.",
                files=chunk.files,
                exc_info=True,
            )
            failed += 1
            bullets.append(f"- [{label}]: ⚠ finder call failed")
            continue
        if result.parse_failed:
            failed += 1
            bullets.append(f"- [{label}]: ⚠ finder output unparsable")
            continue
        allowed = set(chunk.files)
        survivors = [f for f in result.findings if f.file in allowed]
        for dropped in (f for f in result.findings if f.file not in allowed):
            log.warning("Out-of-chunk finding dropped.", file=dropped.file, title=dropped.title)
        if survivors:
            finding_contexts.append(context)
        kept.extend(survivors)
        bullets.append(f"- [{label}]: {result.summary}")

    merged = ReviewResult(
        findings=_dedup(kept),
        summary="\n".join(bullets),
        parse_failed=failed == len(chunks),
    )
    if skeptic_provider is None or not merged.findings or merged.parse_failed:
        return RoutedReview(result=merged, chunk_count=len(chunks))

    # ONE skeptic pass over chunks that produced findings — not the full PR
    # diff: attention dilution hits the skeptic too (spec §4.5).
    skeptic_context = {
        "diff": "\n".join(c["diff"] for c in finding_contexts),
        "full_files": "\n\n".join(c["full_files"] for c in finding_contexts if "full_files" in c),
    }
    try:
        raw = await skeptic_provider.generate_structured(
            SKEPTIC_SYSTEM_PROMPT,
            build_skeptic_prompt(skeptic_context, merged.findings),
            SkepticResult,
        )
        verdicts = SkepticResult.model_validate_json(extract_json(raw))
    except Exception:
        log.warning("Skeptic pass failed; returning unverified findings.", exc_info=True)
        return RoutedReview(
            result=merged.model_copy(update={"skeptic_status": "failed"}),
            chunk_count=len(chunks),
        )
    verified = apply_verdicts(merged.findings, verdicts)
    return RoutedReview(
        result=merged.model_copy(update={"findings": verified, "skeptic_status": "ok"}),
        chunk_count=len(chunks),
    )


async def run_review_routed(
    *,
    provider: BaseProvider,
    collector: ContextCollector,
    skeptic_provider: BaseProvider | None,
) -> RoutedReview:
    """Single entry point for runner and bench: chunked vs single-pass (spec §4.1).

    chunked without a graph DB falls back to single pass: build_chunks would
    degrade to all-isolated chunks = one API call per file with zero
    connectivity benefit — strictly worse than the status quo.
    """
    chunked = "chunked" in collector.features
    if chunked and (collector.db_path is None or not collector.db_path.exists()):
        log.warning("chunked requested but no graph DB; falling back to single pass.")
        chunked = False
    if not chunked:
        reviewer = GuardianReviewer(
            provider=provider,
            context_collector=collector,
            skeptic_provider=skeptic_provider,
        )
        return RoutedReview(result=await reviewer.run_review(), chunk_count=None)
    return await run_chunked_review(
        provider=provider, collector=collector, skeptic_provider=skeptic_provider
    )
