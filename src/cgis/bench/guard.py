"""PreToolUse hook that keeps cgis out of reach except through MCP (#543).

Both arms of the agent A/B run with this hook. Without it the control arm is
not a control: codegraph's own benchmark found its control agent invoking their
CLI through Bash in 26 of 28 runs. The treatment arm is blocked too, so it is
measured on the MCP surface alone rather than on whatever mix it improvises.

Run as `python -m cgis.bench.guard`; Claude Code passes the pending tool call as
JSON on stdin, and exit code 2 refuses it with stderr shown to the agent. The
same predicate is applied to finished transcripts to flag contaminated runs, so
"blocked" and "counted as contamination" cannot drift apart.

With `--cgis-first` (the `cgis-forced` arm) the hook also refuses source access
until the session has made one cgis MCP call, recorded as a marker file in the
hook's working directory: the run's fresh worktree, deleted with it. The path is
fixed rather than passed in, so nothing the session controls picks what the
hook writes.
That arm measures what the graph adds once it is used, not whether the agent
chooses to use it.
"""

import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

#: Words that run the command after them: `env cgis`, `timeout 9 sqlite3`, `VAR=x uv`.
_PREFIXES = (
    r"(?:(?:env|exec|nohup|time|timeout\s+\S+|sudo|command|xargs|nice"
    r"|do|then|else|!|\{|\w+=\S*)\s+)*"
)
#: A cgis-adjacent executable in command position: start of a line, or after a
#: separator, a subshell or a pipe, past any prefix word, optionally with a
#: directory in front. A newline starts a command too (#590 review).
_COMMAND = re.compile(
    r"(?:^|[;&|(`\n]|\$\()\s*"
    + _PREFIXES
    + r"(?:\S*/)?(?:cgis|cgis-mcp|codegraph-brain|uvx|uv|sqlite3)\b(?![./-])",
    re.MULTILINE,
)
#: cgis imported the ways Python allows: `-m cgis`, `from cgis…`, `import cgis`,
#: `import os, cgis` (Gemini on #590), `__import__("cgis")`, `import_module("cgis")`.
_PYTHON_IMPORT = re.compile(
    r"-m\s+cgis\b|\bfrom\s+cgis\b|\bimport\s+(?:[\w.]+\s*,\s*)*cgis\b"
    r"|(?:__import__|import_module)\(\s*[\"']cgis\b"
)
#: A Python interpreter named anywhere in the command. An import of cgis only runs
#: cgis when Python reads it: the same text in a grep pattern searched for it in
#: this repository's own source, and was refused (#589). Anywhere, not in command
#: position: `env python3`, `bash -c "python …"`, a newline before `python3` all
#: run it, and a guard that keeps the control arm honest must err towards refusing
#: (#590 review) — a grep that also names python is refused, and that is fine.
_PYTHON = re.compile(r"\b(?:i?python[\d.]*|pypy3?)\b")
_GRAPH_FILE = re.compile(r"\bgraph\.(?:db|json)\b")

_PATH_KEYS = ("file_path", "path", "pattern", "notebook_path")

#: Tools that reach source without the graph; held back in `--cgis-first` mode.
_SOURCE_TOOLS = frozenset({"Read", "Grep", "Glob", "Bash"})
_CGIS_PREFIX = "mcp__cgis__"
CGIS_FIRST_FLAG = "--cgis-first"
#: Created in the hook's working directory by the session's first cgis call.
MARKER_NAME = ".cgis-bench-used"
CGIS_FIRST_REASON = (
    "Query the code graph first: call one of the mcp__cgis__ tools before reading "
    "or searching source."
)


def blocked_reason(tool_name: str, tool_input: Mapping[str, object]) -> str | None:
    """Why this tool call must be refused, or None when it may run."""
    if tool_name == "Bash":
        command = str(tool_input.get("command", ""))
        runs_cgis_in_python = _PYTHON.search(command) and _PYTHON_IMPORT.search(command)
        if _COMMAND.search(command) or runs_cgis_in_python:
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


def main(argv: Sequence[str] | None = None) -> int:
    """Hook entry point: exit 2 with a reason to refuse the call, 0 to allow it."""
    args = sys.argv[1:] if argv is None else argv
    marker = Path.cwd() / MARKER_NAME if CGIS_FIRST_FLAG in args else None
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
