"""The guardian review job treats the PR as data, never as code (#730).

`/guardian review` runs with provider keys in its environment, and the PR it
reviews may come from anyone. The job used to check the PR out at the root and
run its lockfile, its scripts and its checkers there. These tests pin the shape
that closes that: trusted tooling at the root, the PR in its own directory, and
no step that holds a secret running anything from inside it.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOW = Path(__file__).parent.parent.parent / ".github" / "workflows" / "guardian.yml"
PR_DIR = "pr"


@pytest.fixture(scope="module")
def steps() -> list[dict[str, Any]]:
    """The steps of the `review` job."""
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    result: list[dict[str, Any]] = jobs["review"]["steps"]
    return result


def _checkouts(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [s for s in steps if str(s.get("uses", "")).startswith("actions/checkout@")]


def _holds_secret(step: dict[str, Any]) -> bool:
    return any("secrets." in str(v) for v in (step.get("env") or {}).values())


def test_the_pr_is_checked_out_into_its_own_directory(steps: list[dict[str, Any]]) -> None:
    """A PR checkout at the root would put its scripts and lockfile where the job runs them."""
    pr = [s for s in _checkouts(steps) if "refs/pull/" in str(s["with"].get("ref", ""))]
    assert len(pr) == 1
    assert pr[0]["with"].get("path") == PR_DIR
    assert pr[0]["with"].get("persist-credentials") is False


def test_the_root_checkout_is_the_default_branch(steps: list[dict[str, Any]]) -> None:
    """The tooling that runs must come from reviewed, merged code."""
    root = [s for s in _checkouts(steps) if not s.get("with", {}).get("path")]
    assert len(root) == 1
    assert root[0]["with"]["ref"] == "${{ github.event.repository.default_branch }}"


def test_no_step_runs_inside_the_pr_checkout_except_git(steps: list[dict[str, Any]]) -> None:
    """Only git may run in pr/: uv, python or a checker there executes the PR's own config."""
    for step in steps:
        if step.get("working-directory") == PR_DIR:
            assert str(step["run"]).strip().startswith("git fetch "), step["name"]
            assert not _holds_secret(step), step["name"]


def test_the_review_reads_the_pr_checkout_and_runs_without_evidence(
    steps: list[dict[str, Any]],
) -> None:
    """The step with the keys reviews pr/ via --repo-root and never enables the evidence pass."""
    review = [s for s in steps if _holds_secret(s)]
    assert [s["name"] for s in review] == ["Run Guardian Review"]
    assert f"--repo-root {PR_DIR}" in review[0]["run"]
    assert "GUARDIAN_EVIDENCE" not in review[0]["env"]
    assert "working-directory" not in review[0]


def test_the_graph_is_built_from_the_pr_sources(steps: list[dict[str, Any]]) -> None:
    """The graph must describe the code under review, which now lives in pr/."""
    ingest = [s for s in steps if "cgis ingest" in str(s.get("run", ""))]
    assert len(ingest) == 1
    assert f" {PR_DIR}/src " in ingest[0]["run"]
