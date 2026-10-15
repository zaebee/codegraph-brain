"""Logs go to stderr at the entry points; stdout carries only what a command prints (#619).

structlog's default logger writes to stdout, which nothing configured: `cgis
ingest`'s info lines landed among its own output, and the MCP server relied on
the SDK diverting fd 1 during a stdio session to keep JSON-RPC clean.
"""

import io
import subprocess
import sys
from pathlib import Path

import pytest
import structlog

from cgis import log_to_stderr


def _run(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)


def test_cgis_ingest_logs_to_stderr(tmp_path: Path) -> None:
    (tmp_path / "w").mkdir()
    (tmp_path / "w" / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    argv = ["cgis", "ingest", str(tmp_path / "w"), "--output", str(tmp_path / "g.db")]
    result = _run(f"import sys\nfrom cgis.cli import app\nsys.argv = {argv!r}\napp()\n")
    assert result.returncode == 0, result.stderr
    assert "Resolution complete" in result.stderr
    assert "Resolution complete" not in result.stdout
    assert "[info" not in result.stdout


def test_the_mcp_server_logs_to_stderr_without_the_sdk() -> None:
    """Outside a stdio session — at import, or under an SDK that does not divert fd 1."""
    result = _run(
        "import structlog\n"
        "import cgis.api.mcp_server\n"
        "structlog.get_logger('probe').info('probe-line')\n"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert "probe-line" in result.stderr


def test_importing_cgis_as_a_library_leaves_logging_alone() -> None:
    """A host that imports cgis keeps its own logging: only the entry points configure it."""
    result = _run(
        "import structlog\n"
        "import cgis.pipeline, cgis.languages\n"
        "structlog.get_logger('probe').info('probe-line')\n"
    )
    assert result.returncode == 0, result.stderr
    assert "probe-line" in result.stdout  # structlog's own default, untouched


def test_logs_follow_stderr_as_it_is_when_they_are_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bound at configure time, a later sys.stderr swap (a closed capture) broke logging."""
    log_to_stderr()
    try:
        swapped = io.StringIO()
        monkeypatch.setattr(sys, "stderr", swapped)
        structlog.get_logger("probe").info("late-line")
        assert "late-line" in swapped.getvalue()
    finally:
        structlog.reset_defaults()
