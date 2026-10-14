"""The agent A/B runner `scripts/agent_ab.py` (#543), without spending a session."""

import argparse
import json
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import agent_ab_stubs as stub
import pytest

# This repository sets no pytest `pythonpath`; scripts are imported explicitly.
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

import agent_ab as ab

from cgis.bench.agent_task import load_task

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TASK = load_task(REPO_ROOT / "benchmarks" / "agent_ab" / "tasks" / "cgis-impact-language-for.yaml")


def _args(tmp_path: Path, **overrides: object) -> argparse.Namespace:
    args = ab.build_parser().parse_args(["run"])
    args.results = tmp_path / "results.jsonl"
    args.transcripts = tmp_path / "transcripts"
    args.claude = "claude"
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


# --- environment ------------------------------------------------------------


def test_sanitized_path_drops_dirs_holding_cgis_or_uv(tmp_path: Path) -> None:
    clean, venv, uvdir = tmp_path / "clean", tmp_path / "venv", tmp_path / "uv"
    for d in (clean, venv, uvdir):
        d.mkdir()
    (venv / "cgis").touch()
    (uvdir / "uvx").touch()
    joined = ":".join([str(clean), str(venv), "", str(uvdir)])
    assert ab.sanitized_path(joined) == str(clean)


def test_agent_env_drops_the_virtualenv(tmp_path: Path) -> None:
    env = ab.agent_env(
        {"PATH": str(tmp_path), "VIRTUAL_ENV": "/v", "PYTHONPATH": "/p", "HOME": "/h"}
    )
    assert env == {"PATH": str(tmp_path), "HOME": "/h"}


def test_remove_graph_files_finds_nested_and_committed_copies(tmp_path: Path) -> None:
    (tmp_path / "ui" / "public").mkdir(parents=True)
    (tmp_path / ".git").mkdir()
    (tmp_path / "graph.db").touch()
    (tmp_path / "ui" / "public" / "graph.json").touch()
    (tmp_path / ".git" / "graph.db").touch()
    removed = ab.remove_graph_files(tmp_path)
    assert sorted(p.relative_to(tmp_path).as_posix() for p in removed) == [
        "graph.db",
        "ui/public/graph.json",
    ]
    assert (tmp_path / ".git" / "graph.db").exists()


# --- command ------------------------------------------------------------------


def _command(tmp_path: Path, arm: ab.Arm, effort: str | None = None) -> list[str]:
    return ab.build_command(
        claude="claude",
        prompt="Q?",
        arm=arm,
        config_dir=tmp_path,
        model="m",
        effort=effort,
        max_budget_usd=1.5,
    )


def test_control_command_has_no_server_and_no_plugin(tmp_path: Path) -> None:
    cmd = _command(tmp_path, "control")
    assert cmd[:3] == ["claude", "-p", "Q?"]
    assert "--plugin-dir" not in cmd
    assert "--effort" not in cmd
    assert cmd[cmd.index("--max-budget-usd") + 1] == "1.50"
    assert "--strict-mcp-config" in cmd
    assert json.loads((tmp_path / "mcp.json").read_text()) == {"mcpServers": {}}


def test_both_arms_install_the_guard_hook(tmp_path: Path) -> None:
    _command(tmp_path, "control")
    settings = json.loads((tmp_path / "settings.json").read_text())
    hook = settings["hooks"]["PreToolUse"][0]
    assert hook["matcher"] == ".*"
    assert hook["hooks"][0]["command"].endswith("-m cgis.bench.guard")


def test_cgis_command_uses_this_checkouts_server_and_the_plugin_without_mcp_json(
    tmp_path: Path,
) -> None:
    cmd = _command(tmp_path, "cgis", effort="high")
    server = json.loads((tmp_path / "mcp.json").read_text())["mcpServers"]["cgis"]
    assert server["command"].endswith("cgis-mcp")
    plugin = Path(cmd[cmd.index("--plugin-dir") + 1])
    assert (plugin / "skills" / "cgis" / "SKILL.md").is_file()
    assert not (plugin / ".mcp.json").exists()
    assert cmd[cmd.index("--effort") + 1] == "high"


def test_instructed_arm_is_the_cgis_arm_plus_one_system_prompt_line(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    plain = _command(tmp_path / "a", "cgis")
    instructed = _command(tmp_path / "b", "cgis-instructed")
    assert "--append-system-prompt" not in plain
    assert instructed[instructed.index("--append-system-prompt") + 1] == ab.CGIS_INSTRUCTION
    assert "--plugin-dir" in instructed
    server = json.loads((tmp_path / "b" / "mcp.json").read_text())["mcpServers"]["cgis"]
    assert server["command"].endswith("cgis-mcp")


def test_forced_arm_runs_the_guard_in_cgis_first_mode(tmp_path: Path) -> None:
    cmd = _command(tmp_path, "cgis-forced")
    hook = json.loads((tmp_path / "settings.json").read_text())["hooks"]["PreToolUse"][0]
    assert hook["hooks"][0]["command"].endswith(" -m cgis.bench.guard --cgis-first")
    assert cmd[cmd.index("--append-system-prompt") + 1] == ab.CGIS_INSTRUCTION
    assert "--plugin-dir" in cmd


def test_repo_paths_parses_name_path_pairs(tmp_path: Path) -> None:
    repos = ab.repo_paths([f"owner-api={tmp_path}"])
    assert repos["owner-api"] == tmp_path.resolve()
    assert repos["cgis"] == REPO_ROOT


@pytest.mark.parametrize("pair", ["no-equals", "=path", "name="])
def test_repo_paths_rejects_malformed_pairs(pair: str) -> None:
    with pytest.raises(ValueError, match="NAME=PATH"):
        ab.repo_paths([pair])


def test_ingest_reports_failures_with_stderr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fail(*_a: object, **_k: object) -> None:
        raise subprocess.CalledProcessError(1, "cgis", stderr="boom")

    monkeypatch.setattr(ab.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="boom"):
        ab.ingest(tmp_path, "src")


# --- running ------------------------------------------------------------------


@contextmanager
def _fake_worktree(path: Path) -> Iterator[Path]:
    path.mkdir(exist_ok=True)
    (path / "graph.json").touch()
    yield path


def _patch_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[0] == "git":
            return subprocess.CompletedProcess(cmd, 0, stdout="cafe\n", stderr="")
        out = "\n".join(stub.cgis_session())
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(ab, "worktree_at", lambda _sha, _repo: _fake_worktree(tmp_path / "wt"))
    monkeypatch.setattr(ab, "ingest", lambda _wt, _src: 1.25)
    monkeypatch.setattr(ab.subprocess, "run", fake_run)
    return calls


def test_dry_run_prints_commands_and_spends_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _patch_run(monkeypatch, tmp_path)
    args = _args(tmp_path, dry_run=True, runs=1, task=[TASK.id])
    assert ab.cmd_run(args) == 0
    out = capsys.readouterr().out
    assert f"[{TASK.id} control #0]" in out
    assert f"[{TASK.id} cgis #0]" in out
    assert "removed=1" in out
    assert calls == []
    assert not args.results.exists()


def test_a_run_writes_its_transcript_and_a_scored_results_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_run(monkeypatch, tmp_path)
    args = _args(tmp_path, runs=1, task=[TASK.id], arm=["cgis"])
    assert ab.cmd_run(args) == 0
    rows = [json.loads(line) for line in args.results.read_text().splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert (row["task"], row["arm"], row["run"]) == (TASK.id, "cgis", 0)
    assert row["cgis_sha"] == "cafe"
    assert row["ingest_s"] == 1.25
    assert row["cost_usd"] == 0.25
    assert row["recall"] == pytest.approx(4 / 9)
    assert row["cgis_calls"] == 2
    assert row["contaminated"] is False
    assert (args.transcripts / TASK.id / "cgis-cafe-0.jsonl").is_file()


def test_a_failed_session_is_recorded_and_the_batch_goes_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """earlyoom's SIGTERM on one ingest used to end the whole batch (#577)."""
    _patch_run(monkeypatch, tmp_path)
    ingests: list[str] = []

    def flaky_ingest(_wt: Path, _src: str) -> float:
        ingests.append("x")
        if len(ingests) == 1:
            _msg = "cgis ingest failed (rc=-15):"
            raise RuntimeError(_msg)
        return 1.0

    monkeypatch.setattr(ab, "ingest", flaky_ingest)
    args = _args(tmp_path, runs=2, task=[TASK.id], arm=["cgis"])

    assert ab.cmd_run(args) == 1, "a batch with a failure must not exit 0"
    rows = [json.loads(line) for line in args.results.read_text().splitlines()]
    assert [(r["run"], bool(r.get("failed"))) for r in rows] == [(0, True), (1, False)]
    assert "rc=-15" in rows[0]["failed"]
    assert rows[0]["is_error"] is True


def test_a_second_invocation_continues_the_numbering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_run(monkeypatch, tmp_path)
    args = _args(tmp_path, runs=2, task=[TASK.id], arm=["cgis"])

    assert ab.cmd_run(args) == 0
    assert ab.cmd_run(args) == 0

    rows = [json.loads(line) for line in args.results.read_text().splitlines()]
    assert [r["run"] for r in rows] == [0, 1, 2, 3]
    assert len(list((args.transcripts / TASK.id).glob("cgis-*.jsonl"))) == 4


def test_a_failed_runs_number_is_not_reused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The failure row carries the same cgis sha, so the next invocation skips its number."""
    _patch_run(monkeypatch, tmp_path)

    def dead_ingest(_wt: Path, _src: str) -> float:
        _msg = "killed"
        raise RuntimeError(_msg)

    args = _args(tmp_path, runs=1, task=[TASK.id], arm=["cgis"])
    monkeypatch.setattr(ab, "ingest", dead_ingest)
    assert ab.cmd_run(args) == 1
    monkeypatch.setattr(ab, "ingest", lambda _wt, _src: 1.0)
    assert ab.cmd_run(args) == 0

    rows = [json.loads(line) for line in args.results.read_text().splitlines()]
    assert [(r["run"], bool(r.get("failed"))) for r in rows] == [(0, True), (1, False)]


def test_a_session_that_exits_non_zero_is_failed_but_keeps_its_cost(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An OOM-killed `claude -p` scored recall 0 and sank the median (#578 review)."""
    _patch_run(monkeypatch, tmp_path)
    killed = ab.subprocess.run

    def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        done = killed(cmd, **kwargs)
        return subprocess.CompletedProcess(cmd, 137 if cmd[0] != "git" else 0, done.stdout, "")

    monkeypatch.setattr(ab.subprocess, "run", run)
    args = _args(tmp_path, runs=1, task=[TASK.id], arm=["cgis"])

    assert ab.cmd_run(args) == 1
    row = json.loads(args.results.read_text().splitlines()[0])
    assert row["failed"] == "claude -p exited with 137"
    assert row["cost_usd"] == 0.25


def test_a_failed_dry_run_writes_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`--results` defaults to the committed file; a dry run must not add to it."""
    _patch_run(monkeypatch, tmp_path)

    def broken(_sha: str, _repo: Path) -> Iterator[Path]:
        _msg = "no worktree"
        raise RuntimeError(_msg)

    monkeypatch.setattr(ab, "worktree_at", broken)
    args = _args(tmp_path, dry_run=True, runs=1, task=[TASK.id], arm=["cgis"])

    assert ab.cmd_run(args) == 1
    assert not args.results.exists()


def test_a_truncated_line_does_not_stop_the_numbering(tmp_path: Path) -> None:
    results = tmp_path / "results.jsonl"
    good = json.dumps({"task": "t", "arm": "cgis", "cgis_sha": "s", "run": 2})
    results.write_text(good + '\n{"task": "t", "arm": "cg')

    assert ab.next_run(ab.load_results(results), "t", "cgis", "s") == 3


def test_numbering_is_per_task_arm_and_cgis(tmp_path: Path) -> None:
    results = tmp_path / "results.jsonl"
    rows = [
        {"task": "t", "arm": "cgis", "cgis_sha": "old", "run": 5},
        {"task": "t", "arm": "control", "cgis_sha": "new", "run": 7},
        {"task": "t", "arm": "cgis", "cgis_sha": "new", "run": 1},
    ]
    results.write_text("".join(json.dumps(r) + "\n" for r in rows) + "\n")

    loaded = ab.load_results(results)
    assert ab.next_run(loaded, "t", "cgis", "new") == 2
    assert ab.next_run(loaded, "t", "cgis", "newer") == 0
    assert ab.load_results(tmp_path / "none.jsonl") == []


def test_unknown_task_ids_and_missing_repos_are_refused(tmp_path: Path) -> None:
    assert ab.cmd_run(_args(tmp_path, task=["nope"])) == 2
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    text = (REPO_ROOT / "benchmarks/agent_ab/tasks/cgis-impact-language-for.yaml").read_text()
    (tasks / "t.yaml").write_text(text.replace("repo: cgis", "repo: owner-api"))
    assert ab.cmd_run(_args(tmp_path, tasks=tasks)) == 2


# --- reporting ----------------------------------------------------------------


def _row(task: str, arm: str, recall: float, *, contaminated: bool = False) -> dict[str, object]:
    return {
        "task": task,
        "arm": arm,
        "recall": recall,
        "precision": 1.0,
        "cost_usd": 0.3,
        "total_tool_calls": 10,
        "files_read": 4,
        "turns": 8,
        "contaminated": contaminated,
    }


def test_summarize_takes_medians_over_clean_runs_only() -> None:
    rows = [
        _row("t", "cgis", 1.0),
        _row("t", "cgis", 0.5),
        _row("t", "cgis", 0.0, contaminated=True),
        _row("t", "control", 0.2, contaminated=True),
    ]
    summary = {(s["task"], s["arm"]): s for s in ab.summarize(rows)}
    assert summary[("t", "cgis")]["recall"] == 0.75
    assert summary[("t", "cgis")]["runs"] == 3
    assert summary[("t", "cgis")]["contaminated"] == 1
    assert summary[("t", "control")]["recall"] is None


def test_summarize_counts_failed_sessions_apart() -> None:
    rows = [_row("t", "cgis", 1.0), {"task": "t", "arm": "cgis", "failed": "killed"}]
    summary = ab.summarize(rows)[0]

    assert (summary["runs"], summary["failed"], summary["recall"]) == (1, 1, 1.0)


def test_report_prints_a_markdown_table(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    results = tmp_path / "results.jsonl"
    results.write_text(json.dumps(_row("t", "cgis", 1.0)) + "\n\n")
    assert ab.main(["report", "--results", str(results)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("| task | arm |")
    assert out[2] == "| t | cgis | 1 | 0 | 0 | 1.00 | 1.00 | 0.30 | 10.00 | 4.00 | 8.00 |"


def test_report_skips_a_truncated_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    results = tmp_path / "results.jsonl"
    results.write_text(json.dumps(_row("t", "cgis", 1.0)) + '\n{"task": "t", "ar')

    assert ab.main(["report", "--results", str(results)]) == 0
    assert "| t | cgis | 1 |" in capsys.readouterr().out


def test_report_without_results_fails(tmp_path: Path) -> None:
    assert ab.main(["report", "--results", str(tmp_path / "none.jsonl")]) == 1


def test_fmt_renders_missing_values_as_a_dash() -> None:
    assert ab.fmt_cell(None) == "—"
    assert ab.fmt_cell(3) == "3"


def test_repo_paths_strips_whitespace(tmp_path: Path) -> None:
    assert ab.repo_paths([f" owner-api = {tmp_path} "])["owner-api"] == tmp_path.resolve()


@pytest.mark.parametrize(("platform", "suffix"), [("linux", ""), ("win32", ".exe")])
def test_bin_path_adds_exe_on_windows(
    monkeypatch: pytest.MonkeyPatch, platform: str, suffix: str
) -> None:
    monkeypatch.setattr(ab.sys, "platform", platform)
    assert ab.bin_path("cgis").endswith("cgis" + suffix)


@pytest.mark.parametrize("partial", [None, "partial\n", b"bytes\n"])
def test_a_timed_out_session_keeps_its_partial_transcript(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, partial: str | bytes | None
) -> None:
    def timeout(cmd: list[str], **_k: object) -> None:
        raise subprocess.TimeoutExpired(cmd, 5, output=partial)

    monkeypatch.setattr(ab.subprocess, "run", timeout)
    stdout, code = ab.run_session(["claude"], tmp_path, 5)
    assert code == -1
    expected = partial.decode() if isinstance(partial, bytes) else (partial or "")
    assert stdout == expected


def test_the_hook_quotes_an_interpreter_path_with_spaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ab.sys, "platform", "linux")
    monkeypatch.setattr(ab.sys, "executable", "/opt/my env/bin/python")
    hook = ab.hook_settings()["hooks"]["PreToolUse"][0]["hooks"][0]  # type: ignore[index]
    assert hook["command"] == "'/opt/my env/bin/python' -m cgis.bench.guard"


def test_the_hook_uses_cmd_quoting_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """POSIX single quotes mean nothing to cmd.exe."""
    monkeypatch.setattr(ab.sys, "platform", "win32")
    monkeypatch.setattr(ab.sys, "executable", r"C:\Program Files\Python\python.exe")
    hook = ab.hook_settings()["hooks"]["PreToolUse"][0]["hooks"][0]  # type: ignore[index]
    assert hook["command"] == r'"C:\Program Files\Python\python.exe" -m cgis.bench.guard'
