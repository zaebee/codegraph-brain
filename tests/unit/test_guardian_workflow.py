"""The guardian review job treats the PR as data, never as code (#730).

`/guardian review` runs with provider keys in its environment, and the PR it
reviews may come from anyone. The job used to check the PR out at the root and
run its lockfile, its scripts and its checkers there. These tests pin the shape
that closes that: trusted tooling at the root, the PR in its own directory, and
nothing running from inside it but one fixed `git fetch`.
"""

import re
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
import yaml

WORKFLOW = Path(__file__).parent.parent.parent / ".github" / "workflows" / "guardian.yml"
PR_DIR = "pr"
#: The only command allowed to run inside the PR checkout.
PR_FETCH = 'git fetch origin "$BASE_BRANCH"'
#: The only two ways a command may name the PR checkout: parse it, review it.
ALLOWED_MENTIONS = (
    re.compile(rf"cgis ingest {PR_DIR}/src(?=\s)"),
    re.compile(rf"--repo-root[ =]{PR_DIR}(?=\s)"),
)
#: Ways a command could reach into the PR checkout, after the allowed mentions are removed.
#: Out of scope, as contrived rather than natural respellings: indirection through a
#: shell variable, `git -C`, UV_PROJECT in env, and writes to $GITHUB_ENV.
FORBIDDEN_MENTIONS = (
    re.compile(rf"(?<![\w$-]){PR_DIR}/"),
    re.compile(rf"--(?:directory|project|repo-root)[ =]+(?:\./)?{PR_DIR}\b"),
    re.compile(rf"\b(?:cd|pushd)\s+(?:\./)?{PR_DIR}\b"),
)


def _in_pr(directory: object) -> bool:
    """Whether a working directory is the PR checkout or below it, however it is spelt."""
    parts = PurePosixPath(str(directory or ".")).parts
    return parts[:1] == (PR_DIR,)


@pytest.fixture(scope="module")
def workflow() -> dict[Any, Any]:
    """The parsed workflow file."""
    result: dict[Any, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return result


@pytest.fixture(scope="module")
def steps(workflow: dict[Any, Any]) -> list[dict[str, Any]]:
    """The steps of the `review` job."""
    result: list[dict[str, Any]] = workflow["jobs"]["review"]["steps"]
    return result


def _with(step: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = step.get("with") or {}
    return result


def _checkouts(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [s for s in steps if str(s.get("uses", "")).startswith("actions/checkout@")]


def _holds_secret(step: dict[str, Any]) -> bool:
    return any("secrets." in str(v) for v in (step.get("env") or {}).values())


def test_the_pr_is_checked_out_into_its_own_directory(steps: list[dict[str, Any]]) -> None:
    """A PR checkout at the root would put its scripts and lockfile where the job runs them."""
    pr = [s for s in _checkouts(steps) if "refs/pull/" in str(_with(s).get("ref", ""))]
    assert len(pr) == 1
    assert _with(pr[0]).get("path") == PR_DIR
    assert _with(pr[0]).get("persist-credentials") is False


def test_the_root_checkout_is_the_default_branch(steps: list[dict[str, Any]]) -> None:
    """The tooling that runs must come from reviewed, merged code."""
    root = [s for s in _checkouts(steps) if not _with(s).get("path")]
    assert len(root) == 1
    assert _with(root[0]).get("ref") == "${{ github.event.repository.default_branch }}"


def test_only_the_base_fetch_runs_inside_the_pr_checkout(steps: list[dict[str, Any]]) -> None:
    """Exactly one fixed git command runs in pr/; `git fetch … && uv sync` must not pass."""
    inside = [s for s in steps if _in_pr(s.get("working-directory"))]
    assert [str(s.get("run", "")).strip() for s in inside] == [PR_FETCH]
    assert not any(_holds_secret(s) for s in inside)


def test_no_action_runs_from_or_inside_the_pr_checkout(steps: list[dict[str, Any]]) -> None:
    """A local action (`uses: ./…`) is checkout code; an action aimed at pr/ reads its config."""
    for step in steps:
        assert not str(step.get("uses", "")).startswith("./"), step.get("name")
        if "uses" in step:
            assert not _in_pr(_with(step).get("working-directory")), step.get("name")


def test_no_command_reaches_into_the_pr_checkout(steps: list[dict[str, Any]]) -> None:
    """`uv sync --directory pr` or `python pr/scripts/…` at the root: the same leak, respelt."""
    for step in steps:
        run = str(step.get("run", "")) + " "
        for allowed in ALLOWED_MENTIONS:
            run = allowed.sub(" ", run)
        for pattern in FORBIDDEN_MENTIONS:
            assert not pattern.search(run), (step.get("name"), pattern.pattern)


def test_the_review_runs_this_repositorys_script_on_the_pr(steps: list[dict[str, Any]]) -> None:
    """The step with the keys runs scripts/ from the root and reviews pr/ through --repo-root."""
    review = [s for s in steps if _holds_secret(s)]
    assert [s["name"] for s in review] == ["Run Guardian Review"]
    run = str(review[0]["run"]) + " "
    assert "uv run --frozen python scripts/guardian_review.py" in run
    assert ALLOWED_MENTIONS[1].search(run)
    assert "working-directory" not in review[0]


def test_the_graph_is_built_from_the_pr_sources(steps: list[dict[str, Any]]) -> None:
    """The graph must describe the code under review, parsed by the root's cgis."""
    ingest = [s for s in steps if "cgis ingest" in str(s.get("run", ""))]
    assert len(ingest) == 1
    assert str(ingest[0]["run"]).startswith(f"uv run --frozen cgis ingest {PR_DIR}/src ")
    assert "working-directory" not in ingest[0]


def test_the_evidence_flag_reaches_no_level_of_the_review_job(
    workflow: dict[Any, Any], steps: list[dict[str, Any]]
) -> None:
    """Evidence runs the PR's own checkers; not at step, job or workflow level."""
    levels = [workflow.get("env") or {}, workflow["jobs"]["review"].get("env") or {}]
    levels += [s.get("env") or {} for s in steps]
    assert not any("GUARDIAN_EVIDENCE" in env for env in levels)
