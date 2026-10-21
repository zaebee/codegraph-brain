"""Judge the same frozen finder passes with two skeptics, and score both (#246).

#246 asks whether a **cross-model** skeptic cuts noise where a same-model one
cannot: a mistral skeptic over a mistral finder was measured as binary — it
refuted everything or nothing — and the designed answer was a different vendor.

That answer has since shipped: production runs `GUARDIAN_PROVIDER=mistral` with
`GUARDIAN_SKEPTIC=gemini`. So running the issue as written would measure the
configuration already in use and could not say *why* it still produces 19
findings with 1 real. What was never measured is the comparison itself — the
same finder output judged by both.

The corpus makes a stronger question answerable than the one asked. Of the 37
frozen passes carrying findings, 30 came from a gemini finder and 7 from a
mistral finder, so **both orientations are present**. Judging every one with
each skeptic separates two hypotheses the issue conflates:

* cross-vendor refutes more in *both* orientations → the mechanism is the vendor
  difference, as #246 supposes;
* gemini refutes more whichever finder produced the findings → the mechanism is
  gemini's disposition, and "cross-model" was never the operative word.

The original single-orientation design cannot tell those apart.

**Evidence is collected, not skipped.** `guardian_bench`'s replay path hardcodes
`evidence=None`, which would measure a configuration production does not run
(`GUARDIAN_EVIDENCE=1`). Here it is collected once per PR in a worktree at the
fixture head — the same mechanism `guardian_replay_skeptic` uses. Four of the six
fixtures touch Python and yield evidence, covering 96 of 135 findings; the other
two touch none, so their findings are judged with `evidence=None` because that is
also what production would do for them.

No finder call is made. The cost is one skeptic call per finding per arm.
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cgis
from cgis.guardian.bench import (
    GroundTruth,
    killed_ground_truth,
    load_ground_truth,
    match_findings,
    score,
)
from cgis.guardian.evidence import Evidence, collect_evidence
from cgis.guardian.providers.base import BaseProvider, ProviderUsage
from cgis.guardian.recording import load_finder_recording
from cgis.guardian.runner import build_skeptic_provider
from cgis.guardian.skeptic import apply_judgements, judge_all, visible_findings

sys.path.insert(0, str(Path(__file__).resolve().parent))

from guardian_replay_skeptic import changed_files, worktree_at
from recordings_from_corpus import build, is_frozen_pass, row_key
from run_budget import Budget, make_budget
from semantic_match import Labels, load_labels, semantic_counts

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCH_DIR = REPO_ROOT / "benchmarks" / "guardian"

#: The default pair. Named rather than derived, because "the opposite of the
#: primary" is exactly the rule under test — deriving an arm from the finder
#: would build the hypothesis into the instrument.
#:
#: `--arms` overrides it, and each arm may name its own model
#: (`openrouter=nvidia/nemotron-3-super-120b-a12b:free`). Per arm rather than
#: from the environment: `GUARDIAN_SKEPTIC_MODEL` applies to whichever provider
#: is built, so a single value would hand one arm the other's model — one arm
#: running something that does not exist, reported as a difference between
#: vendors.
DEFAULT_ARMS = "gemini,mistral"


class NoArmError(RuntimeError):
    """Raised when an arm's provider cannot be built.

    Refused before any call is spent. Half an experiment costs the same as none
    and reads like a result, which is worse.
    """


def parse_arms(spec: str) -> dict[str, str | None]:
    """`gemini,openrouter=vendor/model:free` → {arm: model or None}.

    A model per arm, never one shared. The environment's
    `GUARDIAN_SKEPTIC_MODEL` is dropped in `arm_provider` for the same reason.
    """
    arms: dict[str, str | None] = {}
    for item in spec.split(","):
        name, _, model = item.strip().partition("=")
        if name:
            arms[name] = model or None
    return arms


def arm_provider(
    name: str, env: Mapping[str, str], model: str | None = None
) -> tuple[BaseProvider, str]:
    """The skeptic for one arm, or a refusal naming the missing key.

    The environment's `GUARDIAN_SKEPTIC_MODEL` is dropped and `model` used
    instead. `build_skeptic_provider` applies that variable to whichever
    provider it builds, so an environment holding the production value
    (`gemini-2.5-flash`) would hand that name to a mistral or openrouter arm —
    one arm running a model that does not exist while the other ran the intended
    one, and the difference reported as a comparison between vendors. The models
    actually used are recorded on every row.
    """
    per_arm = {k: v for k, v in env.items() if k != "GUARDIAN_SKEPTIC_MODEL"}
    if model:
        per_arm["GUARDIAN_SKEPTIC_MODEL"] = model
    built = build_skeptic_provider({**per_arm, "GUARDIAN_SKEPTIC": name}, primary="none")
    if built is None:
        _msg = (
            f"Arm {name!r} could not be built — its API key is missing, or the model was "
            f"rejected. Both arms must exist before either is run: a single-arm result "
            f"answers nothing this experiment asks."
        )
        raise NoArmError(_msg)
    return built


def finder_models(results: Path) -> dict[str, str]:
    """`row_key` → the model that produced that pass.

    The recording carries findings and a diff and nothing about who wrote them,
    so the vendor split — the whole reason both orientations are separable — has
    to come back from the corpus row.
    """
    models: dict[str, str] = {}
    for line in results.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if is_frozen_pass(row):
            models[row_key(row).replace(":", "-")] = str(row.get("model") or "unknown")
    return models


def evidence_for_pr(truth: GroundTruth, diff: str, repo_root: Path) -> Evidence | None:
    """Checker output at the reviewed commit, or None when there is nothing to check.

    Once per PR rather than once per pass: every pass of a PR was shown the same
    diff, so the checkers would report the same thing, and a worktree costs a
    `uv sync`. None here is not a degradation — a fixture whose diff touches no
    Python has nothing for the checkers to say, and production would collect
    nothing for it either.
    """
    with worktree_at(truth.head, repo_root) as tree:
        return collect_evidence(tree, changed_files(diff))


async def judge_one(
    provider: BaseProvider,
    recording_path: Path,
    truth: GroundTruth,
    evidence: Evidence | None,
    labels: Labels | None = None,
) -> dict[str, Any]:
    """One recording under one arm, scored the way `guardian_bench` scores a run.

    With `labels` (#246 E) the row also carries the semantic score: noise and
    killed ground truth from judge-labelled matches instead of file-level
    crediting. Every finding's verdict is kept either way, so a row can be
    re-scored later without paying the skeptic again.
    """
    recording = load_finder_recording(recording_path)
    findings = recording.result.findings
    judgements = await judge_all(provider, findings, recording.diff, evidence=evidence)
    judged = apply_judgements(findings, judgements)
    visible = visible_findings(judged)
    matches = match_findings(visible, truth)
    bench_score = score(matches, truth)
    shown = {id(f) for f in visible}
    semantic: dict[str, Any] = {}
    if labels is not None:
        noise, matched, killed = semantic_counts(
            judged,
            [i for i, f in enumerate(judged) if id(f) in shown],
            labels.accepts(recording_path.stem, "all"),
            truth,
        )
        semantic = {
            "noise_semantic": noise,
            "matched_semantic": matched,
            "killed_gt_semantic": killed,
        }
    return {
        "verdicts": [
            None
            if j is None
            else {"verdict": j.verdict, "impact_score": j.impact_score, "rationale": j.rationale}
            for j in judgements
        ],
        **semantic,
        "findings": len(findings),
        "refuted": sum(1 for f in judged if f.verdict == "refuted"),
        "uncertain": sum(1 for f in judged if f.verdict == "uncertain"),
        "confirmed": sum(1 for f in judged if f.verdict == "confirmed"),
        "unruled": sum(1 for j in judgements if j is None),
        "recall": bench_score.recall,
        "precision": bench_score.precision,
        "noise": bench_score.noise,
        # The recall guard, and the reason `missed` is not enough: it cannot tell
        # "the finder never found it" from "the finder found it and the skeptic
        # killed it" (#270), and only the second is this experiment's doing.
        "killed_gt": killed_ground_truth(judged, truth),
    }


def _passes_by_pr(repo_root: Path, tmp: Path, limit: int | None) -> dict[int, list[Path]]:
    """The frozen passes that carry findings, sorted, trimmed to `limit`, grouped by PR.

    Passes with no findings cost nothing to judge and contribute nothing to a
    comparison of judgements, so they are dropped before any worktree is built
    rather than filtered out of the numbers afterwards.
    """
    written = build(BENCH_DIR / "results.jsonl", tmp, BENCH_DIR, repo_root)
    recordings = [p for p in written if load_finder_recording(p).result.findings]
    recordings.sort(key=lambda p: p.stem)
    if limit is not None:
        recordings = recordings[:limit]
    by_pr: dict[int, list[Path]] = defaultdict(list)
    for path in recordings:
        by_pr[int(path.stem.split("@")[0])].append(path)
    return by_pr


async def _judged_row(
    arm: tuple[str, BaseProvider, str],
    path: Path,
    truth: GroundTruth,
    evidence: Evidence | None,
    finder_model: str,
    labels: Labels | None = None,
) -> tuple[dict[str, Any], list[ProviderUsage]]:
    """One pass under one arm: its row, and the calls the judging made."""
    name, provider, model = arm
    first_call = len(provider.call_usages)
    scored = await judge_one(provider, path, truth, evidence, labels)
    # This row's own calls: the provider's record is cumulative.
    calls = provider.call_usages[first_call:]
    row = {
        "timestamp": datetime.now(UTC).isoformat(),
        "pr": _pr_of(path),
        "pass": path.stem,
        "arm": name,
        "skeptic_model": model,
        "skeptic_effort": provider.effort,
        "skeptic_prompt_tokens": sum(c.prompt_tokens for c in calls),
        "skeptic_completion_tokens": sum(c.completion_tokens for c in calls),
        # The size that decides a long-prompt price tier.
        "skeptic_max_call_prompt_tokens": max((c.prompt_tokens for c in calls), default=0),
        "finder_model": finder_model,
        "evidence": evidence is not None,
        **scored,
    }
    print(
        f"  {path.stem} [{name}] {scored['refuted']}/{scored['findings']} refuted",
        file=sys.stderr,
    )
    return row, calls


@dataclass(frozen=True)
class _PrContext:
    """What every pass of one PR is judged against."""

    truth: GroundTruth
    evidence: Evidence | None
    finder_models: dict[str, str]
    labels: Labels | None = None


async def _judge_passes(
    paths: list[Path],
    arms: dict[str, tuple[BaseProvider, str]],
    context: _PrContext,
    rows: list[dict[str, Any]],
    budget: Budget | None,
) -> bool:
    """Judge one PR's passes under every arm, appending rows; False once the budget stops it.

    The budget is checked before each row, so a run reaching the cap on its
    last row is complete rather than stopped.
    """
    for path in paths:
        for name, (provider, model) in arms.items():
            if budget is not None and budget.exhausted:
                budget.stopped = True
                return False
            finder = context.finder_models.get(path.stem, "unknown")
            row, calls = await _judged_row(
                (name, provider, model),
                path,
                context.truth,
                context.evidence,
                finder,
                context.labels,
            )
            rows.append(row)
            if budget is not None:
                budget.charge(name, calls)
    return True


def _pr_of(path: Path) -> int:
    """The PR number a recording's stem names (`143@a` -> 143)."""
    return int(path.stem.split("@")[0])


async def collect_rows(
    repo_root: Path,
    limit: int | None,
    arm_spec: dict[str, str | None],
    budget: Budget | None = None,
    labels: Labels | None = None,
) -> list[dict[str, Any]]:
    """Both arms over every frozen pass; one row per (pass, arm).

    `labels` adds the semantic score to every row (#246 E).

    Returns the rows rather than writing them. The write is the caller's, and
    synchronous: a blocking file write inside the event loop is the same defect
    the worktree collection above avoids with `to_thread`, and here there is
    nothing to gain by being in the loop at all.
    """
    arms = {name: arm_provider(name, os.environ, model) for name, model in arm_spec.items()}
    models = finder_models(BENCH_DIR / "results.jsonl")

    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="arms-") as tmp:
        by_pr = _passes_by_pr(repo_root, Path(tmp), limit)
        for pr in sorted(by_pr):
            truth = load_ground_truth(BENCH_DIR / f"pr-{pr}.yaml")
            sample = load_finder_recording(by_pr[pr][0])
            evidence = await asyncio.to_thread(evidence_for_pr, truth, sample.diff, repo_root)
            print(
                f"pr-{pr}: {len(by_pr[pr])} passes, "
                f"evidence {'collected' if evidence else 'unavailable'}",
                file=sys.stderr,
            )
            context = _PrContext(truth, evidence, models, labels)
            if not await _judge_passes(by_pr[pr], arms, context, rows, budget):
                break
    return rows


#: Substrings that identify a model's vendor, lower-cased.
#:
#: Explicit rather than "does the name contain the vendor's name", because
#: Mistral ships families that do not: `codestral-latest` is a Mistral model and
#: was the arm proposed for #246's first draft. Raised in review of #411.
_VENDOR_MARKERS: dict[str, tuple[str, ...]] = {
    "gemini": ("gemini",),
    "mistral": ("mistral", "codestral", "ministral", "magistral"),
    "anthropic": ("claude",),
}

#: A model this table does not recognise. **Not** a third vendor — a refusal to
#: guess, and it has to travel as far as the report.
#:
#: The failure it replaces was silent and pointed one way. An unrecognised model
#: used to become "other", "other" never equals an arm name, so every one of its
#: rows read `cross` — in the exact variable this experiment measures. A new
#: vendor tomorrow would not have produced an error; it would have produced a
#: wrong answer that looked like the expected one.
UNKNOWN_VENDOR = "unknown"


#: The share of findings an arm may leave unruled before its numbers are refused.
#:
#: Not a tolerance — a validity gate, and it is the only thing standing between
#: this experiment and a wrong conclusion. `judge_finding` contains provider
#: errors per finding and returns None, so a spent quota, an upstream 429 and a
#: model that cannot produce JSON all arrive as "unruled". An arm that answered
#: nothing and an arm that refuted nothing are the same row shape, and "refutes
#: nothing" is precisely what #246 predicts for a same-vendor skeptic. Reported
#: without this check, a broken arm would confirm the hypothesis.
MAX_UNRULED_RATE = 0.05


class ArmTooQuietError(RuntimeError):
    """Raised when an arm left too many findings unruled to be scored.

    Refused rather than footnoted. A caveat under a table does not stop the
    table being read, and this table's whole content is how much each skeptic
    refuted.
    """


def validity_problems(rows: list[dict[str, Any]]) -> list[str]:
    """Arms whose unruled share is too high to interpret, with the numbers."""
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for row in rows:
        seen = totals[str(row["arm"])]
        seen[0] += int(row.get("unruled") or 0)
        seen[1] += int(row["findings"])
    return [
        f"arm {arm!r} left {unruled} of {findings} findings unruled "
        f"({unruled / findings:.0%} > {MAX_UNRULED_RATE:.0%}); its numbers cannot be read as "
        f"leniency because they may be silence."
        for arm, (unruled, findings) in sorted(totals.items())
        if findings and unruled / findings > MAX_UNRULED_RATE
    ]


def _vendor(model: str) -> str:
    """The vendor behind a model name, or `UNKNOWN_VENDOR`.

    Lower-cased first: the corpus holds names as the provider reported them, and
    a capitalised variant would fall through to unknown for no reason anyone
    chose.
    """
    lowered = model.lower()
    for vendor, markers in _VENDOR_MARKERS.items():
        if any(marker in lowered for marker in markers):
            return vendor
    return UNKNOWN_VENDOR


def pairing_kind(finder: str, arm: str) -> str:
    """How a (finder, skeptic) pair relates: `same`, `cross`, or `unknown`.

    Its own function rather than a conditional inside the report loop, because
    it is the experiment's independent variable and deserves to be read and
    tested on its own (raised by SonarCloud on #411 as a nested conditional).

    `unknown` is never folded into `cross`. For a model nobody has attributed,
    same-vs-cross is not merely unmeasured but unknowable, and answering `cross`
    would be inventing the very thing under test — which is exactly what the
    previous version did, silently, to every unrecognised model.
    """
    if finder == UNKNOWN_VENDOR:
        return "unknown"
    return "same" if finder == arm else "cross"


def report(rows: list[dict[str, Any]]) -> None:
    """The comparison the issue asks for, split by which vendor found the findings."""
    print(f"\n{len(rows)} (pass, arm) results\n")
    print(
        f"{'finder':10} {'skeptic':11} {'kind':8} {'passes':>6} {'refuted':>8} "
        f"{'of':>5} {'killed GT':>10}"
    )
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(_vendor(str(row["finder_model"])), str(row["arm"]))].append(row)
    for (finder, arm), group in sorted(groups.items()):
        kind = pairing_kind(finder, arm)
        refuted = sum(int(r["refuted"]) for r in group)
        total = sum(int(r["findings"]) for r in group)
        killed = sum(len(r["killed_gt"]) for r in group)
        print(f"{finder:10} {arm:11} {kind:8} {len(group):6} {refuted:8} {total:5} {killed:10}")
    unattributed = sorted(
        {str(r["finder_model"]) for r in rows if _vendor(str(r["finder_model"])) == UNKNOWN_VENDOR}
    )
    if unattributed:
        print(
            f"\n{len(unattributed)} model name(s) match no vendor and are excluded from the "
            f"same/cross split: {', '.join(unattributed)}. Add a marker to _VENDOR_MARKERS."
        )
    print(
        "\n'killed GT' is ground truth the finder matched and the skeptic then hid — the "
        "recall cost, which `missed` cannot show."
    )


def main() -> int:
    """Run both arms and write the rows."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / ".guardian-arms.jsonl")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--arms",
        default=DEFAULT_ARMS,
        help="comma-separated arms, each optionally name=model (see DEFAULT_ARMS)",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="judge only the first N passes (a smoke run)"
    )
    parser.add_argument(
        "--budget-usd", type=float, default=None, help="stop once this much is spent"
    )
    parser.add_argument(
        "--price",
        action="append",
        default=[],
        metavar="ARM=IN:OUT",
        help=(
            "an arm's USD per million input:output tokens, one per arm with --budget-usd; "
            "add ;ABOVE=IN:OUT for a call whose prompt exceeds ABOVE tokens "
            "(claude-haiku-5-5: anthropic=0.10:0.50;100000=0.50:2.50)"
        ),
    )
    parser.add_argument(
        "--labels",
        type=Path,
        default=None,
        help="judge-labelled matches (label_gt_matches.py): also score each row semantically",
    )
    args = parser.parse_args()
    arm_spec = parse_arms(args.arms)
    labels = load_labels(args.labels) if args.labels else None
    try:
        budget = make_budget(args.budget_usd, args.price, arm_spec)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        rows = asyncio.run(collect_rows(args.repo_root, args.limit, arm_spec, budget, labels))
    finally:
        # Also on a crash: the money was spent either way and must be reconcilable.
        if budget is not None:
            print(f"Spent ${budget.spent_usd:.4f} of ${budget.cap_usd:.2f}.", file=sys.stderr)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    # Written before the gate: the rows are evidence either way, and a refused
    # run whose data was discarded cannot be diagnosed.
    args.out.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    if budget is not None and budget.stopped:
        print(
            f"Stopped at the budget after {len(rows)} rows; rows kept at {args.out}, "
            f"no comparison printed.",
            file=sys.stderr,
        )
        return 3
    problems = validity_problems(rows)
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        print(f"Rows kept at {args.out}; no comparison printed.", file=sys.stderr)
        return 2
    report(rows)
    return 0


if __name__ == "__main__":
    cgis.log_to_stderr()  # stdout is the script's output (#619)
    raise SystemExit(main())
