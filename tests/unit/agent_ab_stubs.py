"""Builders for synthetic stream-json transcripts in the shape `claude -p` emits (#543)."""

import json


def init(cwd: str = "/w", servers: dict[str, str] | None = None) -> dict[str, object]:
    """The session's opening `system`/`init` event."""
    return {
        "type": "system",
        "subtype": "init",
        "cwd": cwd,
        "model": "claude-sonnet-5-5",
        "mcp_servers": [{"name": k, "status": v} for k, v in (servers or {}).items()],
    }


def tool_use(
    call_id: str,
    name: str,
    tool_input: dict[str, object],
    *,
    context: int = 1000,
    parent: str | None = None,
) -> dict[str, object]:
    """An assistant event issuing one tool call; `context` is its cache-read size."""
    return {
        "type": "assistant",
        "parent_tool_use_id": parent,
        "message": {
            "content": [{"type": "tool_use", "id": call_id, "name": name, "input": tool_input}],
            "usage": {"input_tokens": 10, "cache_read_input_tokens": context, "output_tokens": 5},
        },
    }


def tool_result(call_id: str, content: object, *, is_error: bool = False) -> dict[str, object]:
    """The user event carrying a tool's result."""
    return {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": call_id,
                    "content": content,
                    "is_error": is_error,
                }
            ]
        },
    }


def result(answer: str, *, cost: float = 0.25, turns: int = 6) -> dict[str, object]:
    """The closing `result` event."""
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "duration_ms": 41000,
        "num_turns": turns,
        "result": answer,
        "total_cost_usd": cost,
        "usage": {
            "input_tokens": 120,
            "output_tokens": 900,
            "cache_creation_input_tokens": 30000,
            "cache_read_input_tokens": 400000,
        },
    }


def answer_text(symbols: list[str], files: list[str]) -> str:
    """A final answer ending in the required JSON block."""
    block = json.dumps({"symbols": symbols, "files": files})
    return f"Here is what I found.\n\n```json\n{block}\n```"


def lines(*events: dict[str, object]) -> list[str]:
    """Events serialised one per line, as the CLI writes them."""
    return [json.dumps(e) for e in events]


def cgis_session() -> list[str]:
    """A treatment-arm session: context, a read of a returned file, a grep, an answer."""
    return lines(
        init(servers={"cgis": "connected"}),
        tool_use("c1", "mcp__cgis__cgis_analyze_impact", {"fqn": "language_for"}, context=2000),
        tool_result(
            "c1",
            [
                {
                    "type": "text",
                    "text": "callers: cgis/cli.py structure; cgis/guardian/collector.py",
                }
            ],
        ),
        tool_use("r1", "Read", {"file_path": "/w/src/cgis/cli.py"}, context=5000),
        tool_result("r1", "def structure(): ...\n"),
        tool_use("c2", "mcp__cgis__cgis_context", {"fqn": "x"}, context=6000),
        tool_result("c2", "see cgis/query/render/mermaid.py"),
        tool_use("g1", "Grep", {"pattern": "language_for", "path": "/w/src"}, context=7000),
        tool_result("g1", "src/cgis/extractors/registry.py:88"),
        tool_use("s1", "Read", {"file_path": "/w/x.py"}, context=9999, parent="task-1"),
        result(
            answer_text(
                ["cgis.cli.structure", "cgis.extractors.registry.is_supported"],
                ["src/cgis/cli.py", "src/cgis/extractors/registry.py"],
            )
        ),
    )
