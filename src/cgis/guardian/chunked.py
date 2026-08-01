"""Chunked review: per-chunk finder passes behind GUARDIAN_FEATURES=chunked.

Slice 2 of #154 (spec: 2026-06-11-guardian-chunked-review-design.md). The
finder LGTMs large PRs (attention dilution); each chunk gets a small,
complete world instead — its own diff, full files, and impact graph.
"""

import structlog
from pydantic import BaseModel

from cgis.guardian.axes import run_axis_review
from cgis.guardian.chunker import Chunk, build_chunks, split_diff_by_file
from cgis.guardian.collector import ContextCollector
from cgis.guardian.core import GuardianReviewer, finder_pass
from cgis.guardian.findings import Finding, ReviewResult, dedup_findings
from cgis.guardian.providers.base import BaseProvider
from cgis.guardian.skeptic import (
    apply_judgements,
    judge_all,
    skeptic_status_for,
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


def _normalize_path(path: str) -> str:
    """Strip LLM path artifacts (leading ./, diff-header a/ b/ prefixes).

    Used only as a fallback after an exact match fails, so a real directory
    literally named `a/` or `b/` can never be corrupted by the stripping
    (gemini review, PR #159).
    """
    p = path.removeprefix("./")
    if p.startswith(("a/", "b/")):
        p = p[2:]
    return p


def _chunk_survivors(chunk: Chunk, findings: list[Finding]) -> list[Finding]:
    """Keep findings inside the chunk's files; drop out-of-chunk hallucinations.

    Exact match first; then a normalized fallback so an LLM path artifact
    ("./x.py", "a/x.py") doesn't drop a real finding. Fallback survivors are
    canonicalized so inline-comment anchoring still works downstream.
    """
    allowed = set(chunk.files)
    survivors: list[Finding] = []
    for finding in findings:
        if finding.file in allowed:
            survivors.append(finding)
            continue
        normalized = _normalize_path(finding.file)
        if normalized in allowed:
            survivors.append(finding.model_copy(update={"file": normalized}))
            continue
        log.warning("Out-of-chunk finding dropped.", file=finding.file, title=finding.title)
    return survivors


async def _single_pass(
    provider: BaseProvider,
    collector: ContextCollector,
    skeptic_provider: BaseProvider | None,
) -> RoutedReview:
    """Run the unchunked reviewer.

    Shared by the routing default and the no-reviewable-files fallback so the
    two cannot drift apart (#277). chunk_count is None, which is the recorded
    marker for "this review did not chunk".
    """
    reviewer = GuardianReviewer(
        provider=provider,
        context_collector=collector,
        skeptic_provider=skeptic_provider,
    )
    return RoutedReview(result=await reviewer.run_review(), chunk_count=None)


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
        if not split_diff_by_file(diff):
            return RoutedReview(
                result=ReviewResult(findings=[], summary="Empty diff — nothing to review."),
                chunk_count=0,
            )
        # Blocks exist but none are reviewable source: a docs-only PR. Single
        # pass reviews it today, so returning "nothing to review" here would be
        # a silent regression — exactly the invisible-skip failure #277 is about.
        log.info("No reviewable source in the diff; falling back to single pass.")
        return await _single_pass(provider, collector, skeptic_provider)
    chunks = _cap_chunks(chunks)

    bullets: list[str] = []
    kept: list[Finding] = []
    finding_contexts: list[dict[str, str]] = []
    failed = 0
    for chunk in chunks:
        label = ", ".join(chunk.files)
        try:
            # Context collection is inside the guard on purpose: it opens the
            # graph DB per chunk, and a flaky DB must cost one chunk, not the
            # whole review (opus quality review on slice 2).
            context = collector.collect_for_chunk(chunk)
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
        survivors = _chunk_survivors(chunk, result.findings)
        if survivors:
            finding_contexts.append(context)
        kept.extend(survivors)
        bullets.append(f"- [{label}]: {result.summary}")

    merged = ReviewResult(
        findings=dedup_findings(kept),
        summary="\n".join(bullets),
        parse_failed=failed == len(chunks),
    )
    if skeptic_provider is None or not merged.findings or merged.parse_failed:
        return RoutedReview(result=merged, chunk_count=len(chunks))

    # Judgement context is limited to chunks that produced findings — not the
    # full PR diff (spec §4.5); judge_all narrows further, per finding's file.
    skeptic_context = {
        "diff": "\n".join(c["diff"] for c in finding_contexts),
        "full_files": "\n\n".join(c["full_files"] for c in finding_contexts if "full_files" in c),
    }
    judgements = await judge_all(skeptic_provider, merged.findings, skeptic_context["diff"])
    judged = sum(1 for j in judgements if j is not None)
    if judged == 0:
        log.warning("Every skeptic judgement failed; returning unverified findings.")
    return RoutedReview(
        result=merged.model_copy(
            update={
                "findings": apply_judgements(merged.findings, judgements),
                "skeptic_status": skeptic_status_for(judged, len(judgements)),
                "skeptic_judged": judged,
                "skeptic_total": len(judgements),
            }
        ),
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
    if "axes" in collector.features:
        # Per-axis fan-out (#331) is checked first: it needs no graph DB, and
        # combining it with chunked would multiply calls by axes x chunks while
        # entangling two effects in one measurement — the mistake #330 recorded.
        return RoutedReview(
            result=await run_axis_review(
                provider=provider, collector=collector, skeptic_provider=skeptic_provider
            ),
            chunk_count=None,
        )

    chunked = "chunked" in collector.features
    if chunked and (collector.db_path is None or not collector.db_path.exists()):
        log.warning("chunked requested but no graph DB; falling back to single pass.")
        chunked = False
    if not chunked:
        return await _single_pass(provider, collector, skeptic_provider)
    return await run_chunked_review(
        provider=provider, collector=collector, skeptic_provider=skeptic_provider
    )
