"""`CGIS_MCP_TOOLS`: which tools the MCP server lists (#542).

Every listed tool's schema costs context in every session, and a long menu
invites mis-picks. The variable is a comma-separated allowlist — `cgis_` may be
left off, so `analyze_impact,trace_flow` works. Unset or blank lists all tools,
which is the default until an A/B run says which smaller set steers agents
better.

A tool left out is not listed, but it is not unknown either: calling it by name
returns a message saying it is disabled and how to enable it, rather than the
SDK's bare "Unknown tool" — an agent that learned the name elsewhere (a skill, a
previous session) should be told why, not left to guess.
"""

from collections.abc import Iterable
from typing import Any

import structlog
from mcp.server.mcpserver import Context, MCPServer
from mcp.types import CallToolResult, InputRequiredResult, TextContent

logger = structlog.getLogger(__name__)

TOOLS_ENV = "CGIS_MCP_TOOLS"
_PREFIX = "cgis_"


def parse_allowlist(raw: str | None, known: Iterable[str]) -> tuple[set[str] | None, list[str]]:
    """The allowed tool names in `raw`, and the entries that name no tool.

    None means "no allowlist": the variable is unset, or holds no name at all
    (blank, or only commas). Entries are trimmed, and get the `cgis_` prefix
    when they lack it.
    """
    entries = (entry.strip() for entry in (raw or "").split(","))
    wanted = {e if e.startswith(_PREFIX) else _PREFIX + e for e in entries if e}
    if not wanted:
        return None, []
    names = set(known)
    return wanted & names, sorted(wanted - names)


class CgisServer(MCPServer[Any]):
    """An MCPServer whose tools can be narrowed by `CGIS_MCP_TOOLS`."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401 — passes through
        """Start with no tool disabled."""
        super().__init__(*args, **kwargs)
        self._disabled: dict[str, str] = {}

    def restrict_tools(self, raw: str | None) -> None:
        """Unlist every tool `raw` does not allow; call once all tools are registered.

        An allowlist that matches no tool at all is a typo, not a request for an
        empty server: every tool stays listed and the mistake is logged.
        """
        names = [tool.name for tool in self._tool_manager.list_tools()]
        allowed, unknown = parse_allowlist(raw, names)
        if unknown:
            logger.warning("CGIS_MCP_TOOLS names no such tool", unknown=unknown, known=names)
        if allowed is None:
            return
        if not allowed:
            logger.error("CGIS_MCP_TOOLS allows no tool; listing all of them", value=raw)
            return
        for name in names:
            if name not in allowed:
                self.remove_tool(name)
                self._disabled[name] = (
                    f"❌ {name} is disabled on this server: {TOOLS_ENV}={raw} leaves it out. "
                    f"Add it to {TOOLS_ENV} in the server's environment to enable it."
                )

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        context: Context[Any, Any] | None = None,
    ) -> CallToolResult | InputRequiredResult:
        """Say that a disabled tool is disabled; call any other as usual."""
        if name in self._disabled:
            return CallToolResult(
                content=[TextContent(type="text", text=self._disabled[name])], is_error=True
            )
        return await super().call_tool(name, arguments, context)
