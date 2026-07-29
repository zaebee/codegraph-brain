"""Auto-generate docs/how-to/MCP_REFERENCE.md from MCP tool docstrings.

Introspects the live cgis MCP server via the public mcp.list_tools() API
and renders a Markdown reference table for every registered tool.
Output is deterministic and suitable for committing back to the repository via CI.
"""

import asyncio
from pathlib import Path
from typing import Any

from mcp.types import Tool

from cgis.api.mcp_server import mcp  # registers tools as a side-effect

_OUTPUT = Path("docs/how-to/MCP_REFERENCE.md")


def _format_tool(tool: Tool) -> str:
    """Render a single MCP tool as a Markdown section."""
    description = (tool.description or "").strip()
    lines = [f"## `{tool.name}`", "", description, ""]

    # mcp 2.x renamed the model field to snake_case; the wire format still
    # serialises as `inputSchema` via the alias (#264).
    schema: dict[str, Any] = tool.input_schema if isinstance(tool.input_schema, dict) else {}
    props: dict[str, Any] = schema.get("properties") or {}
    required: list[str] = schema.get("required") or []

    if props:
        lines += [
            "| Argument | Type | Required | Description |",
            "| :--- | :--- | :---: | :--- |",
        ]
        for param, meta in props.items():
            ptype = meta.get("type", "any")
            req = "✓" if param in required else ""
            desc = meta.get("description", "")
            lines.append(f"| `{param}` | `{ptype}` | {req} | {desc} |")

    lines += ["", "---", ""]
    return "\n".join(lines)


def generate_reference() -> None:
    """Write MCP_REFERENCE.md from all registered MCP tool schemas."""
    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    tools: list[Tool] = sorted(asyncio.run(mcp.list_tools()), key=lambda t: t.name)

    header = [
        "# 📑 MCP Tools Reference Manual",
        "",
        "*Auto-compiled from FastMCP docstrings and type annotations.*",
        "*Do not edit manually — regenerate with `python scripts/generate_mcp_ref.py`.*",
        "",
        "---",
        "",
    ]

    body = [_format_tool(tool) for tool in tools]
    _OUTPUT.write_text("\n".join(header + body), encoding="utf-8")
    print(f"✅ Generated {_OUTPUT} ({len(tools)} tools)")


if __name__ == "__main__":
    generate_reference()
