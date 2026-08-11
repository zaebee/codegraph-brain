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
import re
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


#: Rate limits, matched on the exception's text. Provider SDKs raise their own
#: types (`mistralai.SDKError`, google-genai's `ClientError`) and importing them
#: here would turn optional dependencies into required ones — so the check is on
#: the message. `\b429\b` rather than `429` so a token count of 1429 is not read
#: as a status code.
_RATE_LIMIT_PATTERN = re.compile(r"\b429\b|rate.?limit|resource.?exhausted|too many requests", re.I)

#: Attempts per judge pair, including the first. Backoff is BACKOFF ** attempt.
DEFAULT_JUDGE_ATTEMPTS = 5
_JUDGE_BACKOFF_BASE = 2.0


def is_rate_limited(exc: Exception) -> bool:
    """Whether an exception reports a rate limit rather than a real failure.

    `BaseProvider._retry` deliberately retries only transport errors, matching
    what the Mistral SDK itself retries. That is right for a review — a handful
    of large calls — and wrong for this workload: a calibration run issues
    thousands of tiny calls in seconds, where 429 is the *expected* response,
    not an anomaly. Measured on the first Mistral run: 76.5% of pairs lost.

    Kept local to calibration rather than pushed into the provider so that a
    measurement task does not silently change how production reviews retry.
    """
    return bool(_RATE_LIMIT_PATTERN.search(str(exc)))


async def judge_pair(
    provider: BaseProvider,
    golden: str,
    candidate: str,
    max_attempts: int = DEFAULT_JUDGE_ATTEMPTS,
) -> JudgeVerdict | None:
    """Ask the judge whether one candidate matches one golden comment.

    Retries rate limits with exponential backoff; everything else — auth
    failure, unparseable output — fails on the first attempt rather than
    burning the budget on something that cannot succeed.

    None once attempts are exhausted. A failed call must not be recorded as a
    non-match: that would silently convert judge downtime into a precision
    result.
    """
    prompt = JUDGE_PROMPT.format(golden_comment=golden, candidate=candidate)
    for attempt in range(1, max_attempts + 1):
        try:
            raw = await provider.generate_structured("", prompt, JudgeVerdict)
        except Exception as exc:
            if not is_rate_limited(exc) or attempt == max_attempts:
                log.warning(
                    "Judge call failed; pair left unruled.",
                    golden=golden[:60],
                    error=str(exc)[:120],
                )
                return None
            await provider._sleep(_JUDGE_BACKOFF_BASE**attempt)  # noqa: SLF001
            continue
        try:
            return JudgeVerdict.model_validate_json(extract_json(raw))
        except Exception:
            log.warning("Judge returned unparseable output.", golden=golden[:60])
            return None
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
    """Fraction of pair decisions two judges rule identically (gate G3as registered).

    Deliberately per-decision rather than per-score: two judges can reach the
    same precision by matching different pairs, and that agreement would be an
    artefact. None when there is nothing to compare.

    **This statistic is near-useless at this task's base rate**, and the number
    is kept only because G3 was registered in it. A judge matches ~5% of pairs,
    so two judges agree by chance about 91% of the time — above the 80%
    threshold before either has said anything. Read `cohen_kappa` and
    `positive_agreement` instead; see #342.
    """
    _require_same_length(a, b)
    if not a:
        return None
    return sum(x == y for x, y in zip(a, b, strict=True)) / len(a)


def cohen_kappa(a: Sequence[bool], b: Sequence[bool]) -> float | None:
    """Chance-corrected agreement between two judges' pair decisions.

    What G3 should have been written in: it subtracts the agreement two judges
    would reach by both saying "no" to almost everything, which is what
    `decision_agreement` cannot do.

    None when expected agreement is 1.0 — if neither judge ever varies, there is
    no disagreement to correct for and the denominator vanishes. Returning 1.0
    there would claim perfect agreement between two judges that never made a
    distinction.
    """
    _require_same_length(a, b)
    if not a:
        return None
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    p_a, p_b = sum(a) / n, sum(b) / n
    expected = p_a * p_b + (1 - p_a) * (1 - p_b)
    if expected >= 1.0:
        return None
    return (observed - expected) / (1 - expected)


def positive_agreement(a: Sequence[bool], b: Sequence[bool]) -> float | None:
    """Jaccard overlap on the pairs the judges called a match.

    The substantive question G3 was asking: not "do they agree it is mostly
    noise" but "do they credit the SAME findings". None when neither judge
    matched anything, where the question does not arise.
    """
    _require_same_length(a, b)
    both = sum(x and y for x, y in zip(a, b, strict=True))
    either = sum(x or y for x, y in zip(a, b, strict=True))
    return both / either if either else None


def _require_same_length(a: Sequence[object], b: Sequence[object]) -> None:
    """Reject misaligned decision vectors — a silent zip would invent agreement."""
    if len(a) != len(b):
        _msg = f"length mismatch: {len(a)} vs {len(b)}"
        raise ValueError(_msg)
