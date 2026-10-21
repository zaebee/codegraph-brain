"""Semantic ground-truth matching for the frozen skeptic corpus (#246, experiment E).

`bench.match_findings` credits an entry with no `lines` to any finding in its
file, greedily by the finder's confidence. On A's corpus one such entry,
`file-container-regression` on PR 122, collected 11 findings across its passes,
most of them about something else ("Missing return type annotation", "Unsafe
type assertions") — so a skeptic correctly refuting them was scored as killing
ground truth. It was 14 of the 26 kills recorded over five arms.

Here a pair counts only when judges labelled it a match. The labels are made
once for the frozen corpus (`label_gt_matches.py`) and reused by every arm, so
arms differ only in what they hid. Assignment is a maximum matching, not greedy:
greedy let an unrelated high-confidence finding take an entry's slot and push
the real match into noise.

Candidates stay same-file: the judge sees summaries only (Martian's format), and
the file restriction is what keeps it from matching a claim to an entry about a
different file that happens to read alike.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cgis.guardian.bench import GroundTruth
from cgis.guardian.calibrate import assign_from_grid
from cgis.guardian.findings import Finding

Rule = Literal["all", "any"]
#: (pass stem, finding index in the recording, ground-truth id)
PairKey = tuple[str, int, str]


@dataclass(frozen=True)
class Labels:
    """Every judge's verdict on every same-file (finding, entry) pair of the corpus.

    A verdict is None where that judge's call failed. `all` accepts a pair only
    when every judge said match — a failed call is not a yes — and `any` when
    one did; the pre-registered rule is `all`, `any` is the sensitivity check.
    """

    judges: tuple[str, ...]
    verdicts: dict[PairKey, dict[str, bool | None]]

    def accepts(self, pass_stem: str, rule: Rule) -> dict[int, set[str]]:
        """Finding index → the entries it matches, for one pass, under `rule`."""
        if rule not in ("all", "any"):
            _msg = f"rule must be 'all' or 'any', not {rule!r}"
            raise ValueError(_msg)
        out: dict[int, set[str]] = {}
        for (stem, index, gt_id), by_judge in self.verdicts.items():
            if stem != pass_stem:
                continue
            votes = [by_judge.get(j) for j in self.judges]
            ok = all(v is True for v in votes) if rule == "all" else any(v is True for v in votes)
            out.setdefault(index, set())
            if ok:
                out[index].add(gt_id)
        return out

    def unlabelled(self) -> int:
        """Pairs with at least one failed judge call."""
        return sum(
            1
            for by_judge in self.verdicts.values()
            if any(by_judge.get(j) is None for j in self.judges)
        )


def dump_labels(labels: Labels, path: Path) -> None:
    """Write labels as JSON, one record per pair, in a stable order."""
    pairs = [
        {
            "pass": stem,
            "finding": index,
            "gt": gt_id,
            "verdicts": labels.verdicts[(stem, index, gt_id)],
        }
        for stem, index, gt_id in sorted(labels.verdicts)
    ]
    payload = {"judges": list(labels.judges), "pairs": pairs}
    path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")


def load_labels(path: Path) -> Labels:
    """Read what `dump_labels` wrote."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    verdicts = {(p["pass"], p["finding"], p["gt"]): dict(p["verdicts"]) for p in payload["pairs"]}
    return Labels(judges=tuple(payload["judges"]), verdicts=verdicts)


def candidate_pairs(findings: Sequence[Finding], truth: GroundTruth) -> list[tuple[int, str]]:
    """The (finding index, entry id) pairs a judge is asked about: same file only."""
    return [
        (index, entry.id)
        for index, finding in enumerate(findings)
        for entry in truth.findings
        if entry.file == finding.file
    ]


def _matched(
    indices: Sequence[int], accepts: Mapping[int, set[str]], truth: GroundTruth
) -> dict[str, int]:
    """Entry id → finding index, a maximum matching over the accepted pairs among `indices`."""
    goldens = [entry.id for entry in truth.findings]
    grid = [int(gold in accepts.get(i, set())) for gold in goldens for i in indices]
    assignment = assign_from_grid(grid, len(goldens), len(indices))
    return {goldens[g]: indices[c] for g, c in assignment.items()}


def semantic_counts(
    findings: Sequence[Finding],
    visible: Sequence[int],
    accepts: Mapping[int, set[str]],
    truth: GroundTruth,
) -> tuple[int, list[str], list[str]]:
    """Noise among the visible findings, the entries they match, and the entries the skeptic killed.

    "Killed" is matched by the finder's whole output but not by what stayed
    visible — the same definition as `bench.killed_ground_truth`, on semantic
    matches. A visible finding is noise when it matches no entry and its file is
    not marked ambiguous, as in `bench.match_findings`.
    """
    before = _matched(list(range(len(findings))), accepts, truth)
    after = _matched(list(visible), accepts, truth)
    used = set(after.values())
    ambiguous = {a.file for a in truth.ambiguous}
    noise = sum(1 for i in visible if i not in used and findings[i].file not in ambiguous)
    return noise, sorted(after), sorted(set(before) - set(after))
