"""PreToolUse hook that keeps cgis out of reach except through MCP (#543).

Both arms of the agent A/B run with this hook. Without it the control arm is
not a control: codegraph's own benchmark found its control agent invoking their
CLI through Bash in 26 of 28 runs. The treatment arm is blocked too, so it is
measured on the MCP surface alone rather than on whatever mix it improvises.

Run as `python -m cgis.bench.guard`; Claude Code passes the pending tool call as
JSON on stdin, and exit code 2 refuses it with stderr shown to the agent. The
same predicate is applied to finished transcripts to flag contaminated runs, so
"blocked" and "counted as contamination" cannot drift apart.
"""

import json
import re
import sys
from collections.abc import Mapping

#: A cgis-adjacent executable in command position: start of the command, or after
#: a separator, a subshell or a pipe, optionally with a directory in front.
_COMMAND = re.compile(
    r"(?:^|[;&|(`]|\$\()\s*(?:\S*/)?(?:cgis|cgis-mcp|codegraph-brain|uvx|uv|sqlite3)\b(?![./-])"
)
_PYTHON_IMPORT = re.compile(r"-m\s+cgis\b|\b(?:import|from)\s+cgis\b")
_GRAPH_FILE = re.compile(r"\bgraph\.(?:db|json)\b")

_PATH_KEYS = ("file_path", "path", "pattern", "notebook_path")


def blocked_reason(tool_name: str, tool_input: Mapping[str, object]) -> str | None:
    """Why this tool call must be refused, or None when it may run."""
    if tool_name == "Bash":
        command = str(tool_input.get("command", ""))
        if _COMMAND.search(command) or _PYTHON_IMPORT.search(command):
            return "The cgis CLI, uv and sqlite3 are unavailable in this benchmark."
        if _GRAPH_FILE.search(command):
            return "The graph database is unavailable in this benchmark."
        return None
    for key in _PATH_KEYS:
        if _GRAPH_FILE.search(str(tool_input.get(key, ""))):
            return "The graph database is unavailable in this benchmark."
    return None


def main() -> int:
    """Hook entry point: exit 2 with a reason to refuse the call, 0 to allow it."""
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    if not isinstance(event, dict):
        return 0
    tool_input = event.get("tool_input")
    reason = blocked_reason(
        str(event.get("tool_name", "")), tool_input if isinstance(tool_input, dict) else {}
    )
    if reason is None:
        return 0
    print(reason, file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
