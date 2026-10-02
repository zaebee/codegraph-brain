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
    assert (args.transcripts / TASK.id / "cgis-0.jsonl").is_file()


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


def test_report_prints_a_markdown_table(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    results = tmp_path / "results.jsonl"
    results.write_text(json.dumps(_row("t", "cgis", 1.0)) + "\n\n")
    assert ab.main(["report", "--results", str(results)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("| task | arm |")
    assert out[2] == "| t | cgis | 1 | 0 | 1.00 | 1.00 | 0.30 | 10.00 | 4.00 | 8.00 |"


def test_report_without_results_fails(tmp_path: Path) -> None:
    assert ab.main(["report", "--results", str(tmp_path / "none.jsonl")]) == 1


def test_fmt_renders_missing_values_as_a_dash() -> None:
    assert ab.fmt_cell(None) == "—"
    assert ab.fmt_cell(3) == "3"
