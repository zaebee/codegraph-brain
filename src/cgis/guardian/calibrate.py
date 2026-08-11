"""Phase 1 calibration: score our benchmark with Martian's judge (#342).

`bench.match_findings` decides a hit by file and line range. Martian's
Code Review Bench decides it by asking an LLM whether two review comments
describe the same underlying issue, with no anchor on either side. Both claim to
measure precision and recall; neither number means anything next to the other
until the gap between them is known. This module produces both scores from one
recorded review so the gap can be measured rather than assumed.

The judge prompt is reproduced from `withmartian/code-review-benchmark`
(`offline/code_review_benchmark/step3_judge_comments.py`, MIT) deliberately
verbatim — a paraphrase would make our number incomparable to theirs, which is
the one thing this exercise exists to avoid.

Nothing here mutates the existing scorer. `bench.score` remains the regression
gate; this is a second opinion on the same evidence.
"""

import asyncio
import statistics
from collections.abc import Sequence

import structlog
from pydantic import BaseModel, Field

from cgis.guardian.bench import GroundTruthEntry
from cgis.guardian.findings import Finding, extract_json
from cgis.guardian.providers.base import BaseProvider

log = structlog.getLogger(__name__)

#: Verbatim from Martian's step3_judge_comments.py (MIT). Placeholders keep
#: their upstream names so a diff against the source stays readable.
JUDGE_PROMPT = """You are evaluating AI code review tools.
Determine if the candidate issue matches the golden (expected) comment.

Golden Comment (the issue we're looking for):
{golden_comment}

Candidate Issue (from the tool's review):
{candidate}

Instructions:
- Determine if the candidate identifies the SAME underlying issue as the golden comment
- Accept semantic matches - different wording is fine if it's the same problem
- Focus on whether they point to the same bug, concern, or code issue

Respond with ONLY a JSON object:
{{"reasoning": "brief explanation", "match": true/false, "confidence": 0.0-1.0}}"""

#: Bounded for the same reason the skeptic is: provider rate limits, not CPU.
#: A full calibration run is ~3000 pair calls, so this is the throughput knob.
DEFAULT_JUDGE_CONCURRENCY = 4

#: Spearman needs at least three points before a rank correlation says anything.
_MIN_CORRELATION_POINTS = 3


class JudgeVerdict(BaseModel, frozen=True):
    """One judge ruling on one (golden, candidate) pair."""

    reasoning: str
    match: bool
    confidence: float = Field(ge=0.0, le=1.0)


class JudgePair(BaseModel, frozen=True):
    """A verdict together with the pair it ruled on."""

    golden_index: int = Field(ge=0)
    candidate_index: int = Field(ge=0)
    verdict: JudgeVerdict


class JudgeScore(BaseModel, frozen=True):
    """Precision and recall for one review, judged semantically."""

    tp: int
    fp: int
    fn: int
    precision: float
    recall: float


def golden_text(entry: GroundTruthEntry) -> str:
    """Render a ground-truth entry as a golden comment.

    Summary only. Martian's golden comments carry no file and no line — matching
    is purely semantic — so passing ours would hand the judge an anchor its own
    corpus never provides, and the resulting number would no longer be on their
    scale.
    """
    return entry.summary


def candidate_text(finding: Finding) -> str:
    """Render a finding as the review comment a human would have read.

    `evidence` is excluded along with file and line. It is a verbatim source
    line, which is positional information in prose form: including it while the
    golden side has none would bias the judge toward matching on location rather
    than on claim.
    """
    return f"{finding.title}\n\n{finding.problem}\n\nSuggested fix: {finding.fix}"


async def judge_pair(provider: BaseProvider, golden: str, candidate: str) -> JudgeVerdict | None:
    """Ask the judge whether one candidate matches one golden comment.

    None on any failure — transport, rate limit, unparseable output. A failed
    call must not be recorded as a non-match: that would silently convert judge
    downtime into a precision result.
    """
    prompt = JUDGE_PROMPT.format(golden_comment=golden, candidate=candidate)
    try:
        raw = await provider.generate_structured("", prompt, JudgeVerdict)
        return JudgeVerdict.model_validate_json(extract_json(raw))
    except Exception:
        log.warning("Judge call failed; pair left unruled.", golden=golden[:60])
        return None


async def judge_matrix(
    provider: BaseProvider,
    goldens: Sequence[str],
    candidates: Sequence[str],
    concurrency: int = DEFAULT_JUDGE_CONCURRENCY,
) -> tuple[list[JudgePair], int]:
    """Judge every (golden, candidate) pair; return the rulings and the failure count.

    The failure count is returned rather than logged away because it bounds how
    much of the resulting score is real: a run with many failed pairs
    under-reports true positives and must not be quoted as a precision number.
    """
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def _one(g_index: int, c_index: int) -> JudgePair | None:
        async with semaphore:
            verdict = await judge_pair(provider, goldens[g_index], candidates[c_index])
        if verdict is None:
            return None
        return JudgePair(golden_index=g_index, candidate_index=c_index, verdict=verdict)

    results = await asyncio.gather(
        *(_one(g, c) for g in range(len(goldens)) for c in range(len(candidates)))
    )
    pairs = [r for r in results if r is not None]
    return pairs, len(results) - len(pairs)


def assign_matches(pairs: Sequence[JudgePair]) -> dict[int, int]:
    """Greedy 1:1 assignment of goldens to candidates, best confidence first.

    Martian report a single `tp` per tool, which only holds if a golden and a
    candidate can each be spent once — otherwise precision and recall would
    disagree about how many true positives there were. Greedy by descending
    judge confidence mirrors `match_findings`, which is greedy by descending
    finding confidence; ties break on (golden, candidate) index so the result is
    deterministic across runs.
    """
    ordered = sorted(
        (p for p in pairs if p.verdict.match),
        key=lambda p: (-p.verdict.confidence, p.golden_index, p.candidate_index),
    )
    assignment: dict[int, int] = {}
    used_candidates: set[int] = set()
    for pair in ordered:
        if pair.golden_index in assignment or pair.candidate_index in used_candidates:
            continue
        assignment[pair.golden_index] = pair.candidate_index
        used_candidates.add(pair.candidate_index)
    return assignment


def judge_score(*, n_goldens: int, n_candidates: int, assignment: dict[int, int]) -> JudgeScore:
    """Derive precision and recall from a 1:1 assignment.

    Vacuous cases follow `bench.score` so the two scorers degrade identically
    and the calibration compares scoring, not edge-case policy: no goldens means
    recall 1.0 (nothing could be missed), no candidates means precision 1.0
    (nothing wrong was said).
    """
    tp = len(assignment)
    if tp > n_goldens or tp > n_candidates:
        _msg = (
            f"assignment of {tp} exceeds its sides (goldens={n_goldens}, candidates={n_candidates})"
        )
        raise ValueError(_msg)
    return JudgeScore(
        tp=tp,
        fp=n_candidates - tp,
        fn=n_goldens - tp,
        precision=tp / n_candidates if n_candidates else 1.0,
        recall=tp / n_goldens if n_goldens else 1.0,
    )


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Rank correlation between two scorers' outputs, or None when undefined.

    None — not 0.0 — when either column is constant or there are fewer than
    three points. 0.0 would read as "measured, and they disagree"; the honest
    statement is that no correlation exists to measure. pr-141 carries no ground
    truth and scores precision 0 on every run, so it produces exactly such a
    column.
    """
    if len(xs) != len(ys):
        _msg = f"length mismatch: {len(xs)} vs {len(ys)}"
        raise ValueError(_msg)
    if len(xs) < _MIN_CORRELATION_POINTS:
        return None
    try:
        return statistics.correlation(xs, ys, method="ranked")
    except statistics.StatisticsError:
        return None


def decision_agreement(a: Sequence[bool], b: Sequence[bool]) -> float | None:
    """Fraction of pair decisions two judges rule identically (gate G3).

    Deliberately per-decision rather than per-score: two judges can reach the
    same precision by matching different pairs, and that agreement would be an
    artefact. None when there is nothing to compare.
    """
    if len(a) != len(b):
        _msg = f"length mismatch: {len(a)} vs {len(b)}"
        raise ValueError(_msg)
    if not a:
        return None
    return sum(x == y for x, y in zip(a, b, strict=True)) / len(a)
