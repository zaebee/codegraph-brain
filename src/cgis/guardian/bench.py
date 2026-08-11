"""Benchmark ground truth, matching, and scoring (spec §3.1-3.3).

Matching is deterministic and pure so the scorer can be unit-tested without
any LLM: a prediction matches a ground-truth entry iff same file AND (line
within the entry's range, or the entry has no range). Greedy by descending
prediction confidence; each entry matches at most once.
"""

import statistics
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from cgis.guardian.findings import Category, Finding, Severity

# Re-exported: the recording format moved to its own module when production
# became a second consumer (#279); bench remains the historical import site.
from cgis.guardian.recording import FinderRecording as FinderRecording  # noqa: PLC0414
from cgis.guardian.recording import load_finder_recording as load_finder_recording  # noqa: PLC0414
from cgis.guardian.recording import save_finder_recording as save_finder_recording  # noqa: PLC0414
from cgis.guardian.skeptic import visible_findings


class GroundTruthEntry(BaseModel, frozen=True):
    """One curated real finding for a benchmark PR."""

    id: str
    file: str
    lines: tuple[int, int] | None = None
    severity: Severity
    category: Category
    summary: str
    source: Literal["gemini", "sonar", "fix-commit", "human"]

    @model_validator(mode="after")
    def _lines_ordered(self) -> "GroundTruthEntry":
        """Reject inverted ranges — a transposed YAML range would silently never match."""
        if self.lines is not None and self.lines[0] > self.lines[1]:
            _msg = f"lines range is inverted: {self.lines}"
            raise ValueError(_msg)
        return self


class AmbiguousEntry(BaseModel, frozen=True):
    """A debatable suggestion — neither a miss nor noise (spec §3.1)."""

    file: str
    summary: str


class GroundTruth(BaseModel, frozen=True):
    """Curated findings for one merged PR."""

    pr: int
    base: str
    head: str
    findings: list[GroundTruthEntry]
    ambiguous: list[AmbiguousEntry] = Field(default_factory=list)


class MatchResult(BaseModel, frozen=True):
    """Outcome of matching predictions against one PR's ground truth."""

    matched: dict[str, int]  # ground-truth id -> prediction index
    missed: list[str]  # ground-truth ids with no match
    noise: list[int]  # prediction indices matching nothing
    ambiguous_hits: list[int]  # prediction indices on ambiguous files
    total_predictions: int  # len(predictions) passed to match_findings


class BenchScore(BaseModel, frozen=True):
    """Per-PR metrics derived from a MatchResult (spec §3.3)."""

    recall: float
    precision: float
    noise: int
    #: Predictions on a file carrying an `ambiguous` entry. Counted in the
    #: precision denominator like any other false positive (#345) and reported
    #: apart from `noise` only so a curator can see how much of a precision
    #: failure landed on ground the corpus already marks as debatable.
    ambiguous_hits: int
    missed: list[str]


def load_ground_truth(path: Path) -> GroundTruth:
    """Load and validate one benchmarks/guardian/pr-N.yaml file."""
    return GroundTruth.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def _entry_accepts(entry: GroundTruthEntry, prediction: Finding) -> bool:
    """File must match; a lines range additionally requires the line within it."""
    if entry.file != prediction.file:
        return False
    if entry.lines is None:
        return True
    return prediction.line is not None and entry.lines[0] <= prediction.line <= entry.lines[1]


def match_findings(predictions: Sequence[Finding], truth: GroundTruth) -> MatchResult:
    """Match predictions to ground truth: greedy by descending confidence.

    Ties broken by original sequence position (stable sort).
    """
    matched: dict[str, int] = {}
    noise: list[int] = []
    ambiguous_hits: list[int] = []
    ambiguous_files = {a.file for a in truth.ambiguous}
    order = sorted(range(len(predictions)), key=lambda i: -predictions[i].confidence)
    for i in order:
        prediction = predictions[i]
        hit = next(
            (e.id for e in truth.findings if e.id not in matched and _entry_accepts(e, prediction)),
            None,
        )
        if hit is not None:
            matched[hit] = i
        elif prediction.file in ambiguous_files:
            ambiguous_hits.append(i)
        else:
            noise.append(i)
    missed = [e.id for e in truth.findings if e.id not in matched]
    return MatchResult(
        matched=matched,
        missed=missed,
        noise=sorted(noise),
        ambiguous_hits=sorted(ambiguous_hits),
        total_predictions=len(predictions),
    )


def score(match: MatchResult, truth: GroundTruth) -> BenchScore:
    """Compute recall / precision / noise for one PR.

    Precision is TP / (TP + FP), and **an ambiguous hit is an FP** (#345).

    It used to be exempt from the denominator, on the reasoning that a
    debatable suggestion is "neither correct nor noise" (spec §3.1). Two things
    retired that. First, it made the reported number stop describing the
    review: the exemption is per *file*, so on pr-142 every candidate landed on
    a file carrying one ambiguous entry, the denominator emptied, and this
    function returned the vacuous 1.0 it reserves for "nothing wrong was said"
    — for a review two independent judges scored at 0.14 and 0.19. Ten of 118
    recorded reviews reported that vacuous 1.0.

    Second, the exemption was never actually argued for. CURATION.md routes
    style nits here to keep them from depressing **recall** — but omitting them
    from `findings` already does that, since recall divides by `len(findings)`.
    Exempting them from precision as well was a side effect of the same
    mechanism, and it points the wrong way: guardian's own precision rules
    forbid style nits, so emitting one *is* a precision failure, and the
    exemption hid exactly the failure those rules exist to catch.

    Measured against two independent LLM judges over the same 118 reviews
    (#342 Phase 1), this definition is also the one that tracks them:
    Spearman +0.92 against +0.72, mean absolute difference 0.10 against 0.25.

    `MatchResult.ambiguous_hits` survives as curation diagnostics — see
    `BenchScore.ambiguous_hits`.
    """
    gt_total = len(truth.findings)
    recall = len(match.matched) / gt_total if gt_total else 1.0
    relevant = len(match.matched) + len(match.noise) + len(match.ambiguous_hits)
    precision = len(match.matched) / relevant if relevant else 1.0
    return BenchScore(
        recall=recall,
        precision=precision,
        noise=len(match.noise),
        ambiguous_hits=len(match.ambiguous_hits),
        missed=match.missed,
    )


def score_separation(gt_scores: Sequence[int], noise_scores: Sequence[int]) -> float | None:
    """Median impact_score of GT-matching findings minus that of the rest (#246 §4.3).

    This is the gate metric for the per-finding skeptic: it answers whether the
    skeptic RANKS, which noise counts alone cannot. A flat distribution scores 0
    even when noise happens to fall.

    None when either population is empty — with nothing to compare, the run
    cannot answer the question. Callers pool across benchmark PRs before
    calling: individual PRs carry 1-6 GT matches, too few for a median to mean
    anything.
    """
    if not gt_scores or not noise_scores:
        return None
    return statistics.median(gt_scores) - statistics.median(noise_scores)


def killed_ground_truth(
    findings: Sequence[Finding], truth: GroundTruth, threshold: int = 0
) -> list[str]:
    """Ground-truth ids the finder matched but the skeptic then made invisible (#270).

    "Before" is every finding the finder produced, judged or not; "after" is what
    a human would actually read — refuted findings dropped, and anything scored
    below ``threshold`` hidden. A finding hidden by the threshold is exactly as
    invisible as a refuted one, so both count.

    This is the failure mode the benchmark exists to catch, and it cannot be
    derived from ``matched_gt``: that flag is computed on the post-skeptic
    visible list, so a refuted finding can never carry it and the kill is
    invisible by construction.

    Ids, not a count: which ground truth was lost is the actionable part.
    """
    before = set(match_findings(list(findings), truth).matched)
    after = set(match_findings(visible_findings(findings, threshold), truth).matched)
    return sorted(before - after)


def annotate_matches(
    findings: Sequence[Finding], visible: Sequence[Finding], match: MatchResult
) -> list[dict[str, object]]:
    """Dump each finding with a ``matched_gt`` flag for the benchmark record (#246 §4.2).

    ``match`` indexes into ``visible`` (what the matcher scored), while the
    record keeps EVERY finding including the ones the skeptic hid — otherwise
    "noise fell" would be indistinguishable from "we went blind". Identity, not
    equality, maps a finding back to its matcher position: two findings can be
    field-identical, and only one of them may have matched.
    """
    position = {id(f): i for i, f in enumerate(visible)}
    matched_positions = set(match.matched.values())
    return [
        {**f.model_dump(), "matched_gt": position.get(id(f)) in matched_positions} for f in findings
    ]
