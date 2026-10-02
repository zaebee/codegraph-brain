"""PreToolUse hook that keeps cgis out of reach except through MCP (#543).

Both arms of the agent A/B run with this hook. Without it the control arm is
not a control: codegraph's own benchmark found its control agent invoking their
CLI through Bash in 26 of 28 runs. The treatment arm is blocked too, so it is
measured on the MCP surface alone rather than on whatever mix it improvises.

Run as `python -m cgis.bench.guard`; Claude Code passes the pending tool call as
JSON on stdin, and exit code 2 refuses it with stderr shown to the agent. The
same predicate is applied to finished transcripts to flag contaminated runs, so
"blocked" and "counted as contamination" cannot drift apart.

With `--cgis-first MARKER` (the `cgis-forced` arm) the hook also refuses source
access until the session has made one cgis MCP call, recorded by creating MARKER.
That arm measures what the graph adds once it is used, not whether the agent
chooses to use it.
"""

import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

#: A cgis-adjacent executable in command position: start of the command, or after
#: a separator, a subshell or a pipe, optionally with a directory in front.
_COMMAND = re.compile(
    r"(?:^|[;&|(`]|\$\()\s*(?:\S*/)?(?:cgis|cgis-mcp|codegraph-brain|uvx|uv|sqlite3)\b(?![./-])"
)
_PYTHON_IMPORT = re.compile(r"-m\s+cgis\b|\b(?:import|from)\s+cgis\b")
_GRAPH_FILE = re.compile(r"\bgraph\.(?:db|json)\b")

_PATH_KEYS = ("file_path", "path", "pattern", "notebook_path")

#: Tools that reach source without the graph; held back in `--cgis-first` mode.
_SOURCE_TOOLS = frozenset({"Read", "Grep", "Glob", "Bash"})
_CGIS_PREFIX = "mcp__cgis__"
CGIS_FIRST_REASON = (
    "Query the code graph first: call one of the mcp__cgis__ tools before reading "
    "or searching source."
)


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


def cgis_first_reason(tool_name: str, marker: Path) -> str | None:
    """Gate source tools behind one cgis call; a cgis call creates `marker`."""
    if tool_name.startswith(_CGIS_PREFIX):
        marker.touch()
        return None
    if tool_name in _SOURCE_TOOLS and not marker.exists():
        return CGIS_FIRST_REASON
    return None


def _marker(argv: Sequence[str]) -> Path | None:
    """The `--cgis-first MARKER` path, when given."""
    if len(argv) >= 2 and argv[0] == "--cgis-first":
        return Path(argv[1])
    return None


def main(argv: Sequence[str] | None = None) -> int:
    """Hook entry point: exit 2 with a reason to refuse the call, 0 to allow it."""
    marker = _marker(sys.argv[1:] if argv is None else argv)
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    if not isinstance(event, dict):
        return 0
    tool_name = str(event.get("tool_name", ""))
    tool_input = event.get("tool_input")
    reason = blocked_reason(tool_name, tool_input if isinstance(tool_input, dict) else {})
    if reason is None and marker is not None:
        reason = cgis_first_reason(tool_name, marker)
    if reason is None:
        return 0
    print(reason, file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
