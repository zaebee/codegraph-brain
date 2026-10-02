"""The PreToolUse guard that keeps cgis out of both A/B arms except via MCP (#543)."""

import io
import json
from pathlib import Path

import pytest

from cgis.bench import guard
from cgis.bench.guard import blocked_reason


@pytest.mark.parametrize(
    "command",
    [
        "cgis impact cgis.cli.ingest",
        "cd src && cgis find language_for",
        "/home/u/.venv/bin/cgis trace x",
        "uv run cgis overview",
        "uvx codegraph-brain",
        "echo $(cgis-mcp)",
        "sqlite3 graph.db 'select 1'",
        "python3 -m cgis overview",
        'python3 -c "from cgis.cli import app"',
        "ls -la graph.db",
        "cat ui/public/graph.json | head",
        "find . | xargs grep x; uv pip list",
    ],
)
def test_bash_routes_to_the_graph_are_blocked(command: str) -> None:
    assert blocked_reason("Bash", {"command": command}) is not None


@pytest.mark.parametrize(
    "command",
    [
        "grep -rn language_for src/cgis",
        "ls src/cgis/extractors",
        "cat uv.lock | head",
        "git log --oneline -3",
        "rg 'def ingest' src/cgis/cli.py",
        "python3 -c 'print(1)'",
    ],
)
def test_ordinary_exploration_is_allowed(command: str) -> None:
    """This repository's own paths contain `cgis`; only command position counts."""
    assert blocked_reason("Bash", {"command": command}) is None


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Read", {"file_path": "/w/graph.db"}),
        ("Read", {"file_path": "/w/ui/public/graph.json"}),
        ("Grep", {"pattern": "x", "path": "graph.db-wal"}),
        ("Glob", {"pattern": "**/graph.json"}),
    ],
)
def test_file_tools_cannot_open_graph_files(tool: str, tool_input: dict[str, object]) -> None:
    assert blocked_reason(tool, tool_input) is not None


def test_file_tools_on_source_are_allowed() -> None:
    assert blocked_reason("Read", {"file_path": "/w/src/cgis/cli.py"}) is None
    assert blocked_reason("mcp__cgis__cgis_context", {"fqn": "cgis.cli.ingest"}) is None


def _run_main(monkeypatch: pytest.MonkeyPatch, stdin: str) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    return guard.main([])


def test_main_refuses_with_exit_2_and_a_reason(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    event = {"tool_name": "Bash", "tool_input": {"command": "cgis overview"}}
    assert _run_main(monkeypatch, json.dumps(event)) == 2
    assert "unavailable" in capsys.readouterr().err


def test_main_allows_ordinary_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    event = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
    assert _run_main(monkeypatch, json.dumps(event)) == 0


@pytest.mark.parametrize("stdin", ["not json", "[1]", '{"tool_name": "Bash", "tool_input": 3}'])
def test_main_lets_malformed_events_through(monkeypatch: pytest.MonkeyPatch, stdin: str) -> None:
    """Fail open: a malformed event is still caught afterwards as contamination."""
    assert _run_main(monkeypatch, stdin) == 0


@pytest.mark.parametrize("path", ["docs/dependency_graph.json", "paragraph.db"])
def test_other_files_ending_in_graph_names_are_allowed(path: str) -> None:
    assert blocked_reason("Read", {"file_path": path}) is None
    assert blocked_reason("Bash", {"command": f"cat {path}"}) is None


def test_cgis_first_holds_source_tools_until_a_cgis_call(tmp_path: Path) -> None:
    marker = tmp_path / "cgis_used"
    assert guard.cgis_first_reason("Grep", marker) == guard.CGIS_FIRST_REASON
    assert guard.cgis_first_reason("ToolSearch", marker) is None
    assert guard.cgis_first_reason("mcp__cgis__cgis_analyze_impact", marker) is None
    assert marker.exists()
    assert guard.cgis_first_reason("Read", marker) is None


def _event(tool_name: str) -> str:
    return json.dumps({"tool_name": tool_name, "tool_input": {"file_path": "/w/src/a.py"}})


def test_main_applies_cgis_first_only_when_asked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(_event("Read")))
    assert guard.main([]) == 0
    monkeypatch.setattr("sys.stdin", io.StringIO(_event("Read")))
    assert guard.main([guard.CGIS_FIRST_FLAG]) == 2


def test_the_cgis_first_marker_lives_in_the_working_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The run's worktree, so a later run never inherits an earlier run's marker."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(_event("mcp__cgis__cgis_context")))
    assert guard.main([guard.CGIS_FIRST_FLAG]) == 0
    assert (tmp_path / guard.MARKER_NAME).exists()
    monkeypatch.setattr("sys.stdin", io.StringIO(_event("Read")))
    assert guard.main([guard.CGIS_FIRST_FLAG]) == 0
