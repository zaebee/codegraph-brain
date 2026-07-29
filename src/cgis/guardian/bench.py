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

    Precision is TP / (TP + FP): ambiguous hits are excluded from the
    denominator — they are neither correct nor noise (spec §3.1), so they
    must not depress precision.
    """
    gt_total = len(truth.findings)
    recall = len(match.matched) / gt_total if gt_total else 1.0
    relevant = len(match.matched) + len(match.noise)
    precision = len(match.matched) / relevant if relevant else 1.0
    return BenchScore(
        recall=recall, precision=precision, noise=len(match.noise), missed=match.missed
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
