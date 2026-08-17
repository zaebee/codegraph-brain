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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cgis.guardian.bench import (
    GroundTruth,
    killed_ground_truth,
    load_ground_truth,
    match_findings,
    score,
)
from cgis.guardian.evidence import Evidence, collect_evidence
from cgis.guardian.providers.base import BaseProvider
from cgis.guardian.recording import load_finder_recording
from cgis.guardian.runner import build_skeptic_provider
from cgis.guardian.skeptic import apply_judgements, judge_all, visible_findings

sys.path.insert(0, str(Path(__file__).resolve().parent))

from guardian_replay_skeptic import changed_files, worktree_at
from recordings_from_corpus import build, is_frozen_pass, row_key

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCH_DIR = REPO_ROOT / "benchmarks" / "guardian"

#: The two arms. Named rather than derived, because "the opposite of the primary"
#: is exactly the rule under test — deriving the arm from the finder would build
#: the hypothesis into the instrument.
ARMS = ("gemini", "mistral")


class NoArmError(RuntimeError):
    """Raised when an arm's provider cannot be built.

    Refused before any call is spent. Half an experiment costs the same as none
    and reads like a result, which is worse.
    """


def arm_provider(name: str, env: Mapping[str, str]) -> tuple[BaseProvider, str]:
    """The skeptic for one arm, or a refusal naming the missing key.

    `GUARDIAN_SKEPTIC_MODEL` is dropped rather than passed through.
    `build_skeptic_provider` applies it to whichever provider it builds, so an
    environment holding the production value (`gemini-2.5-flash`) would hand
    that model name to the mistral arm — one arm running a model that does not
    exist, while the other ran the intended one. Each arm takes its provider's
    own default, and the models used are recorded on every row.
    """
    per_arm = {k: v for k, v in env.items() if k != "GUARDIAN_SKEPTIC_MODEL"}
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
) -> dict[str, Any]:
    """One recording under one arm, scored the way `guardian_bench` scores a run."""
    recording = load_finder_recording(recording_path)
    findings = recording.result.findings
    judgements = await judge_all(provider, findings, recording.diff, evidence=evidence)
    judged = apply_judgements(findings, judgements)
    visible = visible_findings(judged)
    matches = match_findings(visible, truth)
    bench_score = score(matches, truth)
    return {
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


async def collect_rows(repo_root: Path, limit: int | None) -> list[dict[str, Any]]:
    """Both arms over every frozen pass; one row per (pass, arm).

    Returns the rows rather than writing them. The write is the caller's, and
    synchronous: a blocking file write inside the event loop is the same defect
    the worktree collection above avoids with `to_thread`, and here there is
    nothing to gain by being in the loop at all.
    """
    arms = {name: arm_provider(name, os.environ) for name in ARMS}
    models = finder_models(BENCH_DIR / "results.jsonl")

    with tempfile.TemporaryDirectory(prefix="arms-") as tmp:
        written = build(BENCH_DIR / "results.jsonl", Path(tmp), BENCH_DIR, repo_root)
        # Passes with no findings cost nothing to judge and contribute nothing to
        # a comparison of judgements, so they are dropped before any worktree is
        # built rather than filtered out of the numbers afterwards.
        recordings = [p for p in written if load_finder_recording(p).result.findings]
        recordings.sort(key=lambda p: p.stem)
        if limit is not None:
            recordings = recordings[:limit]

        by_pr: dict[int, list[Path]] = defaultdict(list)
        for path in recordings:
            by_pr[int(path.stem.split("@")[0])].append(path)

        rows: list[dict[str, Any]] = []
        for pr in sorted(by_pr):
            truth = load_ground_truth(BENCH_DIR / f"pr-{pr}.yaml")
            sample = load_finder_recording(by_pr[pr][0])
            evidence = await asyncio.to_thread(evidence_for_pr, truth, sample.diff, repo_root)
            print(
                f"pr-{pr}: {len(by_pr[pr])} passes, "
                f"evidence {'collected' if evidence else 'unavailable'}",
                file=sys.stderr,
            )
            for path in by_pr[pr]:
                for name, (provider, model) in arms.items():
                    scored = await judge_one(provider, path, truth, evidence)
                    rows.append(
                        {
                            "timestamp": datetime.now(UTC).isoformat(),
                            "pr": pr,
                            "pass": path.stem,
                            "arm": name,
                            "skeptic_model": model,
                            "finder_model": models.get(path.stem, "unknown"),
                            "evidence": evidence is not None,
                            **scored,
                        }
                    )
                    print(
                        f"  {path.stem} [{name}] {scored['refuted']}/{scored['findings']} refuted",
                        file=sys.stderr,
                    )

    return rows


def _vendor(model: str) -> str:
    return "gemini" if "gemini" in model else "mistral" if "mistral" in model else "other"


def report(rows: list[dict[str, Any]]) -> None:
    """The comparison the issue asks for, split by which vendor found the findings."""
    print(f"\n{len(rows)} (pass, arm) results\n")
    print(
        f"{'finder':9} {'skeptic':9} {'kind':7} {'passes':>6} {'refuted':>8} "
        f"{'of':>5} {'killed GT':>10}"
    )
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(_vendor(str(row["finder_model"])), str(row["arm"]))].append(row)
    for (finder, arm), group in sorted(groups.items()):
        kind = "same" if finder == arm else "cross"
        refuted = sum(int(r["refuted"]) for r in group)
        total = sum(int(r["findings"]) for r in group)
        killed = sum(len(r["killed_gt"]) for r in group)
        print(f"{finder:9} {arm:9} {kind:7} {len(group):6} {refuted:8} {total:5} {killed:10}")
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
        "--limit", type=int, default=None, help="judge only the first N passes (a smoke run)"
    )
    args = parser.parse_args()
    rows = asyncio.run(collect_rows(args.repo_root, args.limit))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    report(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
