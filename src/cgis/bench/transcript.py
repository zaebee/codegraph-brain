"""Read a headless Claude Code transcript (`--output-format stream-json`) into metrics (#543).

The stream is one JSON event per line: a `system`/`init` event, `assistant`
events carrying `tool_use` blocks and token usage, `user` events carrying the
matching `tool_result` blocks, and one closing `result` event with the answer,
cost and turn count. Everything here is pure, so the metrics are tested on
recorded transcripts without spending anything.

Two transcript metrics come from codegraph's agent-eval notes and exist to steer
#220, not to rank arms:

- **sufficiency**: what the agent did right after each cgis answer. Answering
  means the answer sufficed; reading a file the answer named means the budget was
  spent badly (the source was not included); reading a file it did not name means
  the answer missed something; searching means it was not trusted.
- **allocation**: of the files cgis answers named, the share the final answer
  relied on. Measured per file, not per byte: cgis output does not delimit bytes
  by file, and a byte split would be an invention.
"""

import json
import re
from collections import Counter
from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, Field

from cgis.bench.agent_task import file_matches, normalize_file
from cgis.bench.guard import blocked_reason

CGIS_TOOL_PREFIX = "mcp__cgis__"

Sufficiency = Literal[
    "answered", "called_again", "read_returned", "read_other", "searched", "other"
]

_SEARCH_TOOLS = frozenset({"Grep", "Glob", "Bash"})
#: Path-like tokens, then filtered by suffix: one character class with no overlapping
#: suffix group, so matching stays linear (Sonar S8786).
_PATH_TOKEN = re.compile(r"[\w./-]+")
_SOURCE_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx")


class ToolCall(BaseModel, frozen=True):
    """One tool invocation and its result, in transcript order."""

    id: str
    name: str
    input: dict[str, object]
    result: str = ""
    is_error: bool = False
    #: The Task/Agent call this ran under, or None on the main thread.
    parent: str | None = None


class Usage(BaseModel, frozen=True):
    """Token counts as the API reports them."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def context(self) -> int:
        """Tokens the request carried in its context window."""
        return self.input_tokens + self.cache_creation_input_tokens + self.cache_read_input_tokens


class Transcript(BaseModel, frozen=True):
    """A parsed run."""

    cwd: str = ""
    model: str = ""
    mcp_servers: dict[str, str] = Field(default_factory=dict)
    calls: list[ToolCall] = Field(default_factory=list)
    answer: str = ""
    subtype: str = ""
    is_error: bool = False
    cost_usd: float = 0.0
    turns: int = 0
    duration_ms: int = 0
    usage: Usage = Field(default_factory=Usage)
    #: Context size of the last main-thread request: what the session ends holding.
    residual_context: int = 0


class RunMetrics(BaseModel, frozen=True):
    """Everything one results row records about agent behaviour."""

    tool_calls: dict[str, int]
    total_tool_calls: int
    cgis_calls: int
    files_read: int
    bytes_read: int
    cli_attempts: int
    contaminated: bool
    sufficiency: dict[str, int]
    allocation: float | None


def _result_text(content: object) -> str:
    """Flatten a tool_result `content` (a string or a list of blocks) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(block.get("text", "")) for block in content if isinstance(block, dict))
    return ""


def _blocks(event: dict[str, object]) -> list[dict[str, object]]:
    """The content blocks of an assistant or user event."""
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _usage(raw: object) -> Usage:
    """A Usage from an API usage dict, ignoring fields it does not model."""
    if not isinstance(raw, dict):
        return Usage()
    return Usage.model_validate({k: v for k, v in raw.items() if k in Usage.model_fields})


class _Builder:
    """Accumulates events; split out so `parse_transcript` stays a plain loop."""

    def __init__(self) -> None:
        self.fields: dict[str, object] = {}
        self.calls: dict[str, ToolCall] = {}
        self.residual = 0

    def init(self, event: dict[str, object]) -> None:
        """Record the session's working directory, model and MCP server states."""
        self.fields["cwd"] = str(event.get("cwd", ""))
        self.fields["model"] = str(event.get("model", ""))
        servers = event.get("mcp_servers")
        if isinstance(servers, list):
            self.fields["mcp_servers"] = {
                str(s.get("name")): str(s.get("status")) for s in servers if isinstance(s, dict)
            }

    def assistant(self, event: dict[str, object]) -> None:
        """Record tool calls, and the context size of main-thread requests."""
        parent = event.get("parent_tool_use_id")
        parent_id = parent if isinstance(parent, str) else None
        for block in _blocks(event):
            if block.get("type") == "tool_use":
                call_id = str(block.get("id"))
                raw_input = block.get("input")
                self.calls[call_id] = ToolCall(
                    id=call_id,
                    name=str(block.get("name")),
                    input=raw_input if isinstance(raw_input, dict) else {},
                    parent=parent_id,
                )
        message = event.get("message")
        if parent_id is None and isinstance(message, dict):
            self.residual = _usage(message.get("usage")).context

    def user(self, event: dict[str, object]) -> None:
        """Attach each tool_result to the call it answers."""
        for block in _blocks(event):
            call = self.calls.get(str(block.get("tool_use_id")))
            if block.get("type") == "tool_result" and call is not None:
                self.calls[call.id] = call.model_copy(
                    update={
                        "result": _result_text(block.get("content")),
                        "is_error": bool(block.get("is_error", False)),
                    }
                )

    def result(self, event: dict[str, object]) -> None:
        """Record the final answer, cost and totals."""
        self.fields.update(
            answer=str(event.get("result", "")),
            subtype=str(event.get("subtype", "")),
            is_error=bool(event.get("is_error", False)),
            cost_usd=float(str(event.get("total_cost_usd", 0) or 0)),
            turns=int(str(event.get("num_turns", 0) or 0)),
            duration_ms=int(str(event.get("duration_ms", 0) or 0)),
            usage=_usage(event.get("usage")),
        )


def parse_transcript(lines: Iterable[str]) -> Transcript:
    """Parse stream-json lines; blank and non-JSON lines are skipped."""
    builder = _Builder()
    handlers = {
        "system": builder.init,
        "assistant": builder.assistant,
        "user": builder.user,
        "result": builder.result,
    }
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        handler = handlers.get(str(event.get("type")))
        if handler is not None and (
            event.get("type") != "system" or event.get("subtype") == "init"
        ):
            handler(event)
    return Transcript.model_validate(
        {
            **builder.fields,
            "calls": list(builder.calls.values()),
            "residual_context": builder.residual,
        }
    )


def is_cgis_call(call: ToolCall) -> bool:
    """True for a call to a cgis MCP tool."""
    return call.name.startswith(CGIS_TOOL_PREFIX)


def _relative(path: str, cwd: str) -> str:
    """`path` relative to the session's working directory when it lies inside it."""
    prefix = cwd.rstrip("/") + "/"
    return path[len(prefix) :] if cwd and path.startswith(prefix) else path


def returned_files(call: ToolCall) -> set[str]:
    """Source paths a tool result names."""
    tokens = (t.rstrip(".") for t in _PATH_TOKEN.findall(call.result))
    return {t for t in tokens if t.endswith(_SOURCE_SUFFIXES)}


def classify_next(returned: set[str], following: ToolCall | None, cwd: str) -> Sufficiency:
    """What the agent did right after a cgis answer naming `returned` files."""
    if following is None:
        return "answered"
    if is_cgis_call(following):
        return "called_again"
    if following.name == "Read":
        path = _relative(str(following.input.get("file_path", "")), cwd)
        hit = any(file_matches(path, f) for f in returned)
        return "read_returned" if hit else "read_other"
    if following.name in _SEARCH_TOOLS:
        return "searched"
    return "other"


def sufficiency(transcript: Transcript) -> Counter[str]:
    """Count of `classify_next` outcomes over every main-thread cgis call."""
    main = [c for c in transcript.calls if c.parent is None]
    counts: Counter[str] = Counter()
    for i, call in enumerate(main):
        if is_cgis_call(call):
            following = main[i + 1] if i + 1 < len(main) else None
            counts[classify_next(returned_files(call), following, transcript.cwd)] += 1
    return counts


def allocation(transcript: Transcript, answer_files: list[str]) -> float | None:
    """Share of files named by cgis answers that the final answer relied on, pooled.

    Counted per call, not per distinct file: a file returned by two calls was paid
    for twice, and allocation measures where the returned budget went.
    """
    named = 0
    used = 0
    for call in transcript.calls:
        if not is_cgis_call(call):
            continue
        files = returned_files(call)
        named += len(files)
        used += sum(1 for f in files if any(file_matches(a, f) for a in answer_files))
    return used / named if named else None


def run_metrics(transcript: Transcript, answer_files: list[str]) -> RunMetrics:
    """Every behavioural metric for one run."""
    counts = Counter(c.name for c in transcript.calls)
    reads = [c for c in transcript.calls if c.name == "Read" and not c.is_error]
    attempts = [c for c in transcript.calls if blocked_reason(c.name, c.input) is not None]
    return RunMetrics(
        tool_calls=dict(sorted(counts.items())),
        total_tool_calls=len(transcript.calls),
        cgis_calls=sum(1 for c in transcript.calls if is_cgis_call(c)),
        files_read=len(
            {
                normalize_file(_relative(str(c.input.get("file_path", "")), transcript.cwd))
                for c in reads
            }
        ),
        bytes_read=sum(len(c.result.encode("utf-8")) for c in reads),
        cli_attempts=len(attempts),
        contaminated=any(not c.is_error for c in attempts),
        sufficiency=dict(sorted(sufficiency(transcript).items())),
        allocation=allocation(transcript, answer_files),
    )
