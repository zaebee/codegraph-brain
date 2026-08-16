"""Fail on a `pytest.raises` block containing more than one call (python:S5778).

A block with two calls cannot say which one raised. The failure mode is not
theoretical and not symmetric: the call *under test* can stop raising entirely
while a helper in the same block starts to, and the test keeps passing. It is a
check that reports the reassuring answer without checking the thing it names.

This rule has been reintroduced five times in this repository, always noticed by
SonarCloud after the fact. Sonar only gates *new* code and only decorates a pull
request, so the loop was: write it, push it, get told, fix it, forget. This
script closes that loop locally (pre-commit) and in CI
(`tests/unit/test_check_pytest_raises.py`), which is the half that cannot be
skipped with `--no-verify`.

The fix at the call site is always the same — hoist the argument out:

    index = {"k": _scored_row()}          # was: nested inside the block
    with pytest.raises(UnjudgeableRowError):
        carry_to_calibration(corpus, index)

`GRANDFATHERED` holds the 22 blocks that predate the rule. It is a two-way
ratchet, like the pinned inventory in `test_review_fingerprint_contract.py`: a
new offender fails, and so does a stale entry that no longer offends. The second
direction is what keeps the list shrinking instead of becoming scenery.
"""

import argparse
import ast
import sys
from pathlib import Path

#: Blocks that predate this check. Only ever remove entries.
#:
#: Most are `str(tmp_path)` inside the block — which Sonar itself does not
#: flag — so they are listed rather than fixed: rewriting twenty-two unrelated
#: tests to satisfy a rule their own analyser does not apply to them would be a
#: large diff across files this rule was never the point of. New code gets the
#: strict reading.
GRANDFATHERED: frozenset[str] = frozenset(
    {
        "unit/test_drift.py::test_ideal_layer_not_a_mapping_raises_type_error",
        "unit/test_drift.py::test_ideal_must_sum_to_one",
        "unit/test_drift.py::test_ideal_unknown_triad_key_fails_loud",
        "unit/test_drift.py::test_layers_must_be_non_negative",
        "unit/test_drift.py::test_layers_must_sum_to_one",
        "unit/test_drift.py::test_layers_not_a_mapping_raises_type_error",
        "unit/test_drift.py::test_load_params_rejects_non_mapping_domain_params",
        "unit/test_drift.py::test_stray_baseline_key_rejected",
        "unit/test_drift.py::test_triad_weights_must_be_non_negative",
        "unit/test_drift.py::test_triad_weights_not_a_mapping_raises_type_error",
        "unit/test_drift.py::test_unknown_param_key_raises",
        "unit/test_drift.py::test_unknown_profile_raises",
        "unit/test_drift.py::test_unresolvable_placeholder_raises",
        "unit/test_drift_service.py::test_analyze_drift_bad_suffix_raises",
        "unit/test_drift_service.py::test_analyze_drift_missing_db_raises",
        "unit/test_drift_service.py::test_analyze_drift_missing_patterns_raises",
        "unit/test_guardian_martian_script.py::test_a_missing_concurrency_raises_instead_of_defaulting",
        "unit/test_guardian_recording.py::test_a_non_json_path_is_rejected",
        "unit/test_metrics.py::test_missing_database_raises_file_not_found",
        "unit/test_ontology_init.py::test_propose_missing_db_raises",
        "unit/test_pipeline.py::test_pipeline_raises_for_file_instead_of_dir",
        "unit/test_suggest_service.py::test_suggest_missing_db_raises",
    }
)


def _is_raises_block(node: ast.With) -> bool:
    """True for `with pytest.raises(...)` (or any `*.raises(...)`) context."""
    return any(
        isinstance(item.context_expr, ast.Call)
        and isinstance(item.context_expr.func, ast.Attribute)
        and item.context_expr.func.attr == "raises"
        for item in node.items
    )


def offenders(root: Path) -> set[str]:
    """Every `path::function` under `root` whose raises-block holds >1 call.

    Keyed by enclosing function rather than line number so the baseline
    survives edits above it, and by a path *relative to `root`* so the same
    baseline matches whatever absolute location the repository sits at. The
    first version keyed by the absolute path, which matched no baseline entry
    at all — so every block was reported as new AND as stale simultaneously,
    a contradiction the checker's own tests caught before it shipped.

    A block outside any function is ignored: there is no stable name to pin it
    by, and this repository has none.
    """
    found: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        functions = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        ]
        for function in functions:
            for node in ast.walk(function):
                if not isinstance(node, ast.With) or not _is_raises_block(node):
                    continue
                calls = [
                    inner
                    for statement in node.body
                    for inner in ast.walk(statement)
                    if isinstance(inner, ast.Call)
                ]
                if len(calls) > 1:
                    found.add(f"{path.relative_to(root)}::{function.name}")
    return found


def problems_for(found: set[str]) -> list[str]:
    """Both ratchet directions over an already-collected offender set.

    Split from `check` so each direction can be tested against an explicit set
    rather than against a temporary directory. Testing through a temp tree
    conflates them: every real baseline entry is trivially "stale" against a
    tree that does not contain it, so a test meaning to check *one new
    offender* silently asserted against twenty-three problems.
    """
    problems = [
        f"new: {name} — a pytest.raises block with more than one call cannot say "
        f"which one raised; hoist the arguments out of the block (python:S5778)"
        for name in sorted(found - GRANDFATHERED)
    ]
    problems += [
        f"stale: {name} — listed in GRANDFATHERED but no longer offends; remove it "
        f"so the list keeps shrinking"
        for name in sorted(GRANDFATHERED - found)
    ]
    return problems


def check(root: Path) -> list[str]:
    """Problems to report for a real tests tree; empty when it is clean."""
    return problems_for(offenders(root))


def main() -> int:
    """Print every problem and return a shell exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tests-root", type=Path, default=Path(__file__).parent.parent / "tests")
    args = parser.parse_args()
    problems = check(args.tests_root)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
