"""The guardian review job treats the PR as data, never as code (#730).

`/guardian review` runs with provider keys in its environment, and the PR it
reviews may come from anyone. The job used to check the PR out at the root and
run its lockfile, its scripts and its checkers there. These tests pin the shape
that closes that: trusted tooling at the root, the PR in its own directory, and
nothing running from inside it but one fixed `git fetch`.
"""

import os
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
import yaml

WORKFLOWS = Path(__file__).parent.parent.parent / ".github" / "workflows"
#: The review job itself, reusable by any repository (#739).
WORKFLOW = WORKFLOWS / "guardian-reusable.yml"
#: This repository's caller of it.
CALLER = WORKFLOWS / "guardian.yml"
PR_DIR = "pr"
#: The only command allowed to run inside the PR checkout.
#: The only commands allowed to run inside the PR checkout: build and mask the
#: credential, then one fixed fetch that reads it from git's environment config.
PR_FETCH = [
    "cred=$(printf 'x-access-token:%s' \"$FETCH_TOKEN\" | base64 -w0)",
    'echo "::add-mask::$cred"',
    "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=http.https://github.com/.extraheader \\",
    'GIT_CONFIG_VALUE_0="AUTHORIZATION: basic $cred" git fetch origin "$BASE_BRANCH"',
]
#: The only two ways a command may name the PR checkout: parse it, review it.
ALLOWED_MENTIONS = (
    re.compile(rf'cgis ingest "{PR_DIR}/\$SOURCE_ROOT"(?=\s)'),
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


def test_the_root_checkout_is_codegraph_brain_at_the_tooling_ref(
    steps: list[dict[str, Any]],
) -> None:
    """The tooling that runs must come from reviewed, merged code."""
    root = [s for s in _checkouts(steps) if not _with(s).get("path")]
    assert len(root) == 1
    assert _with(root[0]).get("repository") == "zaebee/codegraph-brain"
    assert _with(root[0]).get("ref") == "${{ inputs.tooling_ref }}"


def test_only_the_base_fetch_runs_inside_the_pr_checkout(steps: list[dict[str, Any]]) -> None:
    """Exactly one fixed git command runs in pr/; `git fetch … && uv sync` must not pass."""
    inside = [s for s in steps if _in_pr(s.get("working-directory"))]
    assert [
        [line.strip() for line in str(s.get("run", "")).strip().splitlines()] for s in inside
    ] == [PR_FETCH]
    # The one credential there is the caller's GITHUB_TOKEN, for that fetch; no provider key.
    assert [s.get("env") for s in inside] == [{"FETCH_TOKEN": "${{ github.token }}"}]


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
    commands = [line for line in str(ingest[0]["run"]).splitlines() if "cgis ingest" in line]
    expected = f'uv run --frozen cgis ingest "{PR_DIR}/$SOURCE_ROOT" '
    assert [c.startswith(expected) for c in commands] == [True]
    assert "working-directory" not in ingest[0]


@pytest.mark.parametrize(
    ("features", "data_keys"),
    [
        ("", False),
        ("full_files", False),
        ("diff_check", True),
        ("full_files, diff_check", True),
        ("full_files,\tdiff_check\n", True),
        ("diff_checks", False),
    ],
)
def test_data_keys_follow_the_diff_check_feature(
    steps: list[dict[str, Any]], tmp_path: Path, features: str, data_keys: bool
) -> None:
    """The ingest step's own shell decides --data-keys; run it with a stand-in `uv`."""
    ingest = next(s for s in steps if "cgis ingest" in str(s.get("run", "")))
    fake = tmp_path / "uv"
    fake.write_text('#!/bin/sh\necho "$@"\n')
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}"}
    env["GUARDIAN_FEATURES"] = features
    env["SOURCE_ROOT"] = "src"
    done = subprocess.run(
        ["bash", "-c", str(ingest["run"])], env=env, capture_output=True, text=True, check=True
    )
    assert ("--data-keys" in done.stdout.split()) is data_keys


def test_the_evidence_flag_reaches_no_level_of_the_review_job(
    workflow: dict[Any, Any], steps: list[dict[str, Any]]
) -> None:
    """Evidence runs the PR's own checkers; not at step, job or workflow level."""
    levels = [workflow.get("env") or {}, workflow["jobs"]["review"].get("env") or {}]
    levels += [s.get("env") or {} for s in steps]
    assert not any("GUARDIAN_EVIDENCE" in env for env in levels)


# Reviews post as the codegraph-brain GitHub App when it is configured (#734).
# Its private key is stronger than GITHUB_TOKEN — it reaches every repository
# the app is installed on — so the token minted from it is narrowed to this
# repository and to the one permission posting needs.
APP_TOKEN_ACTION = "actions/create-github-app-token@"
APP_TOKEN = "${{ steps.app-token.outputs.token || github.token }}"


def _app_token_step(steps: list[dict[str, Any]]) -> dict[str, Any]:
    minted = [s for s in steps if str(s.get("uses", "")).startswith(APP_TOKEN_ACTION)]
    assert len(minted) == 1
    return minted[0]


def test_the_app_token_is_minted_only_when_configured(steps: list[dict[str, Any]]) -> None:
    """Without the app's client id the step is skipped and posting falls back to GITHUB_TOKEN."""
    step = _app_token_step(steps)
    assert step.get("id") == "app-token"
    assert step.get("if") == "vars.GUARDIAN_APP_CLIENT_ID != ''"
    assert re.fullmatch(r"actions/create-github-app-token@[0-9a-f]{40}", step["uses"])
    assert _with(step)["client-id"] == "${{ vars.GUARDIAN_APP_CLIENT_ID }}"
    assert _with(step)["private-key"] == "${{ secrets.GUARDIAN_APP_PRIVATE_KEY }}"


def test_the_app_token_reaches_this_repository_and_pull_requests_only(
    steps: list[dict[str, Any]],
) -> None:
    """Scope and permissions are named, not inherited from whatever the app was granted."""
    granted = _with(_app_token_step(steps))
    assert granted["owner"] == "${{ github.repository_owner }}"
    assert granted["repositories"] == "${{ github.event.repository.name }}"
    permissions = {k: v for k, v in granted.items() if k.startswith("permission-")}
    assert permissions == {"permission-pull-requests": "write"}


def test_the_review_posts_with_the_app_token_or_falls_back(steps: list[dict[str, Any]]) -> None:
    """Both posting paths — inline review and fallback comment — use the same token."""
    review = next(s for s in steps if s.get("name") == "Run Guardian Review")
    assert review["env"]["GH_TOKEN"] == APP_TOKEN
    comment = next(s for s in steps if s.get("name") == "Post review as PR comment")
    assert _with(comment).get("token") == APP_TOKEN


def test_only_the_posting_steps_reference_the_app_token(steps: list[dict[str, Any]]) -> None:
    """Where the token is minted matters less than who can read it: the two posting steps only."""
    holders = [s.get("name") for s in steps if "steps.app-token" in yaml.safe_dump(s)]
    assert holders == ["Run Guardian Review", "Post review as PR comment"]


def test_the_app_token_is_minted_after_every_pr_step(steps: list[dict[str, Any]]) -> None:
    """Nothing that checks out or runs in the PR checkout comes after the token exists."""
    minted = steps.index(_app_token_step(steps))
    last_pr = max(
        i
        for i, s in enumerate(steps)
        if _in_pr(s.get("working-directory"))
        or "refs/pull/" in str(_with(s).get("ref", ""))
        or f"{PR_DIR}/" in str(s.get("run", ""))
    )
    assert minted > last_pr


def test_the_review_job_ends_before_the_app_token_expires(workflow: dict[Any, Any]) -> None:
    """An installation token lives 60 minutes; a longer job would post with an expired one."""
    assert workflow["jobs"]["review"]["timeout-minutes"] < 60


# The caller (#739): this repository's guardian.yml hands the review to the
# reusable workflow and keeps its own metrics jobs.


@pytest.fixture(scope="module")
def caller() -> dict[Any, Any]:
    """The parsed guardian.yml."""
    result: dict[Any, Any] = yaml.safe_load(CALLER.read_text(encoding="utf-8"))
    return result


def test_the_caller_hands_the_review_to_the_reusable_workflow(caller: dict[Any, Any]) -> None:
    """At the commit the event ran on, with this repository's sources and its metrics."""
    review = caller["jobs"]["review"]
    assert review["uses"] == "./.github/workflows/guardian-reusable.yml"
    assert review["with"]["tooling_ref"] == "${{ github.sha }}"
    assert review["with"]["source_root"] == "src"
    assert review["with"]["save_metrics"] is True
    assert review["secrets"] == "inherit"
    assert " ".join(review["if"].split()) == (
        "github.event.issue.pull_request != null && github.event.comment.body == '/guardian review'"
    )
    assert review["permissions"] == {
        "contents": "read",
        "pull-requests": "write",
        "issues": "write",
    }


def test_the_reusable_workflow_is_only_callable(workflow: dict[Any, Any]) -> None:
    """No trigger of its own: a comment reaches it only through a caller's `if`."""
    triggers = workflow.get("on") or workflow.get(True)
    assert set(triggers) == {"workflow_call"}
    assert workflow["permissions"] == {}


def test_metrics_are_restored_only_when_asked(steps: list[dict[str, Any]]) -> None:
    """The restore reads codegraph-brain's own branch; other callers never get it."""
    restore = next(s for s in steps if s.get("name") == "Restore metrics from data branch")
    assert restore.get("if") == "inputs.save_metrics"


def test_cross_org_callers_can_pass_the_secrets(workflow: dict[Any, Any]) -> None:
    """`secrets: inherit` stops at the organisation; the rest must be declared to be passable."""
    trigger = (workflow.get("on") or workflow.get(True))["workflow_call"]
    declared = trigger["secrets"]
    assert set(declared) == {
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "MISTRAL_API_KEY",
        "GUARDIAN_APP_PRIVATE_KEY",
    }
    assert all(v == {"required": False} for v in declared.values())


@pytest.mark.parametrize(
    ("event", "source_root", "ok"),
    [
        ("issue_comment", "src", True),
        ("issue_comment", "backend/app", True),
        ("issue_comment", "../elsewhere", False),
        ("issue_comment", "/abs", False),
        ("pull_request", "src", False),
    ],
)
def test_the_inputs_are_checked_by_the_steps_own_shell(
    steps: list[dict[str, Any]], tmp_path: Path, event: str, source_root: str, ok: bool
) -> None:
    """Run the validation step itself, with a stand-in `gh`, as the ingest test does."""
    validate = next(s for s in steps if s.get("name") == "Validate inputs and get base branch")
    fake = tmp_path / "gh"
    fake.write_text("#!/bin/sh\necho main\n")
    fake.chmod(0o755)
    out = tmp_path / "github_env"
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}"}
    env |= {"EVENT_NAME": event, "PR_NUMBER": "7", "SOURCE_ROOT": source_root}
    env |= {"GITHUB_ENV": str(out), "GITHUB_REPOSITORY": "o/r"}
    done = subprocess.run(
        ["bash", "-c", str(validate["run"])], env=env, capture_output=True, text=True, check=False
    )
    assert (done.returncode == 0) is ok
    if ok:
        assert "BASE_BRANCH=main" in out.read_text()
