"""Auto-generate docs/how-to/MCP_REFERENCE.md from FastMCP tool docstrings.

Introspects the live cgis MCP server instance and renders a Markdown
reference table for every registered tool. Output is deterministic and
suitable for committing back to the repository via CI.
"""

from pathlib import Path
from typing import Any

from cgis.api.mcp_server import mcp  # registers tools as a side-effect

_OUTPUT = Path("docs/how-to/MCP_REFERENCE.md")


def _format_tool(name: str) -> str:
    """Render a single MCP tool as a Markdown section."""
    tool = mcp._tool_manager._tools.get(name)  # noqa: SLF001
    if tool is None:
        return ""

    description = (tool.description or "").strip()
    lines = [f"## `{name}`", "", description, ""]

    schema: dict[str, Any] = tool.parameters if isinstance(tool.parameters, dict) else {}
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
    """Write MCP_REFERENCE.md from all registered FastMCP tool schemas."""
    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    tool_names: list[str] = sorted(mcp._tool_manager._tools.keys())  # noqa: SLF001

    header = [
        "# 📑 MCP Tools Reference Manual",
        "",
        "*Auto-compiled from FastMCP docstrings and type annotations.*",
        "*Do not edit manually — regenerate with `python scripts/generate_mcp_ref.py`.*",
        "",
        "---",
        "",
    ]

    body = [_format_tool(name) for name in tool_names]
    _OUTPUT.write_text("\n".join(header + body), encoding="utf-8")
    print(f"✅ Generated {_OUTPUT} ({len(tool_names)} tools)")


if __name__ == "__main__":
    generate_reference()
