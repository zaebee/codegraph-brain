"""The guardian review job treats the PR as data, never as code (#730).

`/guardian review` runs with provider keys in its environment, and the PR it
reviews may come from anyone. The job used to check the PR out at the root and
run its lockfile, its scripts and its checkers there. These tests pin the shape
that closes that: trusted tooling at the root, the PR in its own directory, and
nothing running from inside it but one fixed `git fetch`.
"""

import ast
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
    "cred=$(printf 'x-access-token:%s' \"$FETCH_TOKEN\" | base64 | tr -d '\\n')",
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


def _last_pr_step(steps: list[dict[str, Any]]) -> int:
    """Index of the last step that checks out, parses or runs inside the PR checkout."""
    return max(
        i
        for i, s in enumerate(steps)
        if _in_pr(s.get("working-directory"))
        or "refs/pull/" in str(_with(s).get("ref", ""))
        or f"{PR_DIR}/" in str(s.get("run", ""))
    )


#: A `jq` command word in a shell script: start, or after whitespace or a shell
#: separator, and followed by whitespace or end of line. The sign-in step must
#: not need it: a self-hosted runner may not have it (#742, #775).
_JQ_COMMAND = re.compile(r"(?:^|[\s;|&(`])jq(?=\s|$)", re.M)

#: A `python3` command word. The sign-in step must not assume it is on PATH on
#: a self-hosted runner; uv is installed and the environment synced (#775).
_PYTHON3_COMMAND = re.compile(r"(?:^|[\s;|&(`])python3(?=\s|$)", re.M)


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
    assert minted > _last_pr_step(steps)


# OIDC sign-in (#775): opencode-go needs no static key. The job exchanges a
# short-lived GitHub OIDC token for inference access immediately before the
# review, and the review step inherits it from GITHUB_ENV.


def _oidc_step(steps: list[dict[str, Any]]) -> dict[str, Any]:
    matches = [s for s in steps if s.get("name") == "Sign in to OpenCode (OIDC)"]
    assert len(matches) == 1
    return matches[0]


def test_the_review_job_can_request_an_oidc_token(workflow: dict[Any, Any]) -> None:
    """`id-token: write` is what makes ACTIONS_ID_TOKEN_REQUEST_* exist at all."""
    assert workflow["jobs"]["review"]["permissions"] == {
        "contents": "read",
        "pull-requests": "write",
        "issues": "write",
        "id-token": "write",
    }


def test_the_oidc_sign_in_runs_after_every_pr_step_and_before_the_review(
    steps: list[dict[str, Any]],
) -> None:
    """The token is short-lived, so it is minted last — after no PR code can see it."""
    signed_in = steps.index(_oidc_step(steps))
    review = steps.index(next(s for s in steps if s.get("name") == "Run Guardian Review"))
    assert signed_in > _last_pr_step(steps)
    assert signed_in < review


def test_the_oidc_sign_in_exchanges_a_github_token_for_inference(
    steps: list[dict[str, Any]],
) -> None:
    step = _oidc_step(steps)
    assert step.get("if") == "vars.OPENCODE_CLIENT_ID != ''"
    assert step["env"] == {
        "AUDIENCE": "https://opencode.ai/console",
        "CLIENT_ID": "${{ vars.OPENCODE_CLIENT_ID }}",
        "GUARDIAN_PROVIDER": "${{ vars.GUARDIAN_PROVIDER }}",
    }
    run = step["run"]
    for needle in (
        "ACTIONS_ID_TOKEN_REQUEST_URL",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
        "--oauth2-bearer",
        "audience=$AUDIENCE",
        "grant_type=urn:ietf:params:oauth:grant-type:token-exchange",
        "subject_token_type=urn:ietf:params:oauth:token-type:jwt",
        'client_id="$CLIENT_ID"',
        'subject_token="$oidc"',
        "https://opencode.ai/console/auth/oauth/token",
        "::add-mask::",
        'echo "OPENCODE_API_KEY=$token" >> "$GITHUB_ENV"',
    ):
        assert needle in run
    # The token is masked before it is exported, and neither jq nor a PATH
    # python3 is assumed: uv was installed and its environment synced above.
    assert run.index("::add-mask::") < run.index('>> "$GITHUB_ENV"')
    assert not _JQ_COMMAND.search(run)
    assert not _PYTHON3_COMMAND.search(run)
    assert run.count("uv run --frozen python -c") == 2


@pytest.mark.parametrize(
    ("provider", "signs_in"),
    [
        ("", True),
        ("opencode-go", True),
        ("OPEncode-Go", True),
        ("opencode", True),
        ("mistral", False),
        ("MISTRAL", False),
        ("anthropic", False),
    ],
)
def test_the_oidc_sign_in_only_runs_when_opencode_is_selected(
    steps: list[dict[str, Any]], tmp_path: Path, provider: str, signs_in: bool
) -> None:
    """Another explicit provider must not depend on OpenCode's availability.

    Empty still signs in on purpose: the minted key then makes autodetection
    choose opencode. The missing-token branch is reached only for a provider
    this step owns, so no network is involved here.
    """
    run = _oidc_step(steps)["run"]
    env = {
        **os.environ,
        "GUARDIAN_PROVIDER": provider,
        "GITHUB_ENV": str(tmp_path / "github_env"),
    }
    env.pop("ACTIONS_ID_TOKEN_REQUEST_URL", None)
    env.pop("ACTIONS_ID_TOKEN_REQUEST_TOKEN", None)
    done = subprocess.run(["bash", "-c", run], env=env, capture_output=True, text=True, check=False)
    output = done.stdout + done.stderr
    if signs_in:
        assert done.returncode != 0
        assert "GitHub OIDC is unavailable" in output
    else:
        assert done.returncode == 0
        assert f"not the selected provider ({provider.lower()})" in output
    assert not (tmp_path / "github_env").exists()


def test_the_oidc_sign_in_fails_loudly_instead_of_minting_an_empty_key(
    steps: list[dict[str, Any]],
) -> None:
    """Every failure path runs before the model call; none can leave an empty key."""
    run = _oidc_step(steps)["run"]
    for needle in (
        "GitHub OIDC is unavailable",
        "failed with HTTP",
        "carried no value field",
        "returned no access_token",
        "empty access_token",
    ):
        assert needle in run


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
        # A called workflow cannot request more than its caller grants (#775).
        "id-token": "write",
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


def test_the_review_step_does_not_receive_a_static_opencode_key(
    steps: list[dict[str, Any]],
) -> None:
    """The OIDC step writes GITHUB_ENV; a step-level value would override it (#775).

    `ANTHROPIC_API_KEY` stays in the environment because existing callers pass
    it by name; it is simply not required. `GUARDIAN_ANTHROPIC_EFFORT` is gone:
    an explicit anthropic pick runs at its provider default.
    """
    review = next(s for s in steps if s.get("name") == "Run Guardian Review")
    env = review["env"]
    assert "OPENCODE_API_KEY" not in env
    assert not any("OPENCODE" in str(v) and "secrets." in str(v) for v in env.values())
    assert env["GUARDIAN_OPENCODE_EFFORT"] == "${{ vars.GUARDIAN_OPENCODE_EFFORT }}"
    assert env["ANTHROPIC_API_KEY"] == "${{ secrets.ANTHROPIC_API_KEY }}"
    assert "GUARDIAN_ANTHROPIC_EFFORT" not in env


@pytest.mark.parametrize(
    ("event", "source_root", "ok"),
    [
        ("issue_comment", "src", True),
        ("issue_comment", "backend/app", True),
        ("issue_comment", "../elsewhere", False),
        ("issue_comment", "/abs", False),
        ("issue_comment", "..\\elsewhere", False),
        ("issue_comment", "my..pkg", True),
        ("issue_comment", "a/..", False),
        ("issue_comment", " src", False),
        ("issue_comment", "", True),
        ("issue_comment", "./src", True),
        ("pull_request", "src", False),
    ],
)
def test_the_inputs_are_checked_by_the_steps_own_shell(
    steps: list[dict[str, Any]], tmp_path: Path, event: str, source_root: str, ok: bool
) -> None:
    """Run the validation step itself, as the ingest test does."""
    validate = next(s for s in steps if s.get("name") == "Validate inputs")
    out = tmp_path / "github_env"
    env = {**os.environ, "EVENT_NAME": event, "PR_NUMBER": "7", "SOURCE_ROOT": source_root}
    env |= {"GITHUB_ENV": str(out), "GITHUB_REPOSITORY": "o/r"}
    done = subprocess.run(
        ["bash", "-c", str(validate["run"])], env=env, capture_output=True, text=True, check=False
    )
    assert (done.returncode == 0) is ok
    if ok:
        assert "PR_NUMBER=7" in out.read_text()


# The job must not assume the GitHub CLI (#742). A self-hosted runner without
# `gh` has to reach the same result as a GitHub-hosted one; two steps shelled
# out to it and one module posted through it. These tests keep both free of it
# and hold the action pins to the current majors.

#: A `gh` command word in a shell script: start, or after whitespace or a shell
#: separator, and followed by whitespace or end of line. `$GITHUB_…` has a `$`
#: before it, and `grep` does not start with `gh`.
_GH_COMMAND = re.compile(r"(?:^|[\s;|&(`])gh(?=\s|$)", re.M)

#: Guardian library code (what the review job runs) plus the workflow's entry
#: script. `scripts/guardian_martian.py` is a developer bench tool, not part of
#: this job, and keeps its own `gh` calls.
_ROOT = Path(__file__).parent.parent.parent
GUARDIAN_SOURCES = [
    *sorted((_ROOT / "src" / "cgis" / "guardian").glob("*.py")),
    _ROOT / "scripts" / "guardian_review.py",
]

#: Ways Python can spawn a command; the scanned first argument names it.
_SPAWNERS = {
    "os": {"system", "popen"},
    "subprocess": {
        "run",
        "call",
        "check_call",
        "check_output",
        "Popen",
        "getoutput",
        "getstatusoutput",
    },
    "asyncio": {"create_subprocess_exec", "create_subprocess_shell"},
}


def _names_gh(word: str) -> bool:
    """Whether a command argument's first word is `gh` (or a path to it)."""
    words = word.split()
    return bool(words) and PurePosixPath(words[0]).name == "gh"


def _spawner_aliases(tree: ast.Module) -> tuple[dict[str, str], set[str]]:
    """How a source names its spawn functions.

    Returns the imported module aliases (`import subprocess as sp`) and the
    directly imported spawner names (`from subprocess import run as spawn`), so
    the scan does not depend on how the call is spelled.
    """
    modules: dict[str, str] = {}
    direct: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _SPAWNERS:
                    modules[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module in _SPAWNERS:
            for alias in node.names:
                if alias.name in _SPAWNERS[node.module]:
                    direct.add(alias.asname or alias.name)
    return modules, direct


def _is_spawner(func: ast.expr, modules: dict[str, str], direct: set[str]) -> bool:
    """Whether a call target is one of the spawn functions."""
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        module = modules.get(func.value.id, func.value.id)
        return func.attr in _SPAWNERS.get(module, set())
    return isinstance(func, ast.Name) and func.id in direct


def _spawns_gh(source: Path) -> list[int]:
    """Line numbers of calls in `source` that spawn `gh`."""
    found: list[int] = []
    tree = ast.parse(source.read_text(encoding="utf-8"))
    modules, direct = _spawner_aliases(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_spawner(node.func, modules, direct):
            continue
        if not node.args:
            continue
        first = node.args[0]
        words: list[str] = []
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            words = [first.value]
        elif isinstance(first, (ast.List, ast.Tuple)):
            words = [
                element.value
                for element in first.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            ]
        if any(_names_gh(word) for word in words):
            found.append(node.lineno)
    return found


@pytest.mark.parametrize(
    ("source", "spawned"),
    [
        ("import subprocess\nsubprocess.run(['gh', 'api'])", [2]),
        ("from subprocess import run\nrun(['gh', 'api'])", [2]),
        ("from subprocess import run as spawn\nspawn(['/usr/bin/gh', 'api'])", [2]),
        ("import subprocess as sp\nsp.run(['gh'])", [2]),
        ("from os import system\nsystem('gh pr view')", [2]),
        ("import subprocess\nsubprocess.run(['git', 'fetch'])", []),
    ],
)
def test_the_gh_scanner_sees_through_import_styles(
    source: str, spawned: list[int], tmp_path: Path
) -> None:
    """A direct import or an alias must not hide a spawn from the scan."""
    path = tmp_path / "probe.py"
    path.write_text(source, encoding="utf-8")
    assert _spawns_gh(path) == spawned


@pytest.mark.parametrize("source", GUARDIAN_SOURCES, ids=lambda path: path.name)
def test_guardian_code_never_spawns_gh(source: Path) -> None:
    """The library and the workflow entry script must run without the CLI installed."""
    assert _spawns_gh(source) == []


def test_no_workflow_step_shells_out_to_gh(
    workflow: dict[Any, Any], caller: dict[Any, Any]
) -> None:
    """A `run:` block that calls gh fails on a runner that does not have it."""
    for name, document in (("guardian-reusable.yml", workflow), ("guardian.yml", caller)):
        for job in document["jobs"].values():
            for step in job.get("steps", []):
                run = str(step.get("run", ""))
                assert not _GH_COMMAND.search(run), (name, step.get("name"))


def test_the_reaction_and_base_branch_go_through_github_script(steps: list[dict[str, Any]]) -> None:
    """The two steps that used gh now call the REST API from actions/github-script."""
    reaction = next(s for s in steps if s.get("name") == "React to trigger comment")
    assert reaction["uses"].startswith("actions/github-script@")
    assert "createForIssueComment" in reaction["with"]["script"]
    assert "context.payload.comment.id" in reaction["with"]["script"]

    base = next(s for s in steps if s.get("name") == "Get base branch")
    assert base["uses"].startswith("actions/github-script@")
    script = base["with"]["script"]
    assert "github.rest.pulls.get" in script
    assert "exportVariable('BASE_BRANCH'" in script

    validate = next(s for s in steps if s.get("name") == "Validate inputs")
    fetch = next(s for s in steps if s.get("name") == "Fetch base branch for diff")
    assert steps.index(validate) < steps.index(base) < steps.index(fetch)


# The pins below are the current majors at the time of #742 — the previous ones
# (checkout v4, setup-uv v3, github-script v7, create-or-update-comment v4,
# upload/download-artifact v4) ran on node20. A bump updates this table with the
# pin, so the majors cannot silently regress.
CURRENT_PINS = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
    "astral-sh/setup-uv": "68559e99cc807c1eb7e9238fd43a15bba460eec2",
    "actions/github-script": "3a2844b7e9c422d3c10d287c895573f7108da1b3",
    "actions/upload-artifact": "cf430e030ddbb5b0abf93d22962f4752f3646cd9",
    "actions/download-artifact": "9000827ccba6bdab643e8b6fd33ac0654aef8333",
    "actions/create-github-app-token": "64ab729a6294000f5aa4a4bc0b73d1f32bc45510",
    "peter-evans/create-or-update-comment": "e8674b075228eee787fea43ef493e45ece1004c9",
}


def test_the_guardian_action_pins_are_current_majors(
    workflow: dict[Any, Any], caller: dict[Any, Any]
) -> None:
    """Every remote action is SHA-pinned, and pinned to the node24 majors."""
    for name, document in (("guardian-reusable.yml", workflow), ("guardian.yml", caller)):
        for job in document["jobs"].values():
            for step in job.get("steps", []):
                uses = str(step.get("uses", ""))
                if not uses or uses.startswith(("./", "docker://")):
                    continue
                action, _, sha = uses.partition("@")
                assert action in CURRENT_PINS, (name, uses)
                assert sha == CURRENT_PINS[action], (name, uses)
