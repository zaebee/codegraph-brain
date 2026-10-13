"""Server instructions in `initialize`, and the `CGIS_MCP_TOOLS` allowlist (#542).

The round trips go through the SDK's in-memory client, so what is checked is what
a host receives — `initialize`, `tools/list`, `tools/call` — not the Python
functions behind them.
"""

import asyncio
import json
import os
import re
import subprocess
import sys

import pytest
from mcp.client import Client
from mcp.types import CallToolResult

from cgis.api.mcp_server import mcp
from cgis.api.server_instructions import SERVER_INSTRUCTIONS
from cgis.api.tool_allowlist import TOOLS_ENV, CgisServer, parse_allowlist


def _listed(server: CgisServer) -> list[str]:
    """Tool names a client sees in `tools/list`."""

    async def run() -> list[str]:
        async with Client(server) as client:
            return [tool.name for tool in (await client.list_tools()).tools]

    return asyncio.run(run())


def _call(server: CgisServer, name: str) -> CallToolResult:
    """`tools/call` with no arguments, as a client sends it."""

    async def run() -> CallToolResult:
        async with Client(server) as client:
            return await client.call_tool(name, {})

    return asyncio.run(run())


def _toy_server(allowlist: str | None) -> CgisServer:
    """Two tools, then the allowlist — the order `mcp_server` registers in."""
    server = CgisServer("toy")

    @server.tool()
    def cgis_alpha() -> str:
        """First tool."""
        return "alpha ran"

    @server.tool()
    def cgis_beta() -> str:
        """Second tool."""
        return "beta ran"

    server.restrict_tools(allowlist)
    return server


def test_initialize_carries_the_instructions() -> None:
    async def run() -> str | None:
        async with Client(mcp) as client:
            return client.instructions

    assert asyncio.run(run()) == SERVER_INSTRUCTIONS


def test_the_instructions_name_only_tools_that_exist() -> None:
    """A renamed or removed tool must not stay recommended in `initialize`."""
    named = set(re.findall(r"\bcgis_[a-z_]+", SERVER_INSTRUCTIONS))
    listed = set(_listed(mcp))

    assert named, "the instructions name no tool — the check below would pass on nothing"
    assert named <= listed, f"named but not served: {sorted(named - listed)}"


def test_every_tool_is_listed_without_an_allowlist() -> None:
    assert len(_listed(mcp)) == 15


@pytest.mark.parametrize("raw", [None, "", " , "])
def test_unset_or_blank_is_no_allowlist(raw: str | None) -> None:
    assert parse_allowlist(raw, ["cgis_alpha"]) == (None, [])


def test_entries_are_trimmed_and_prefixed() -> None:
    allowed, unknown = parse_allowlist(" alpha , cgis_beta,", ["cgis_alpha", "cgis_beta"])

    assert allowed == {"cgis_alpha", "cgis_beta"}
    assert unknown == []


def test_an_entry_naming_no_tool_is_reported() -> None:
    allowed, unknown = parse_allowlist("alpha,gamma", ["cgis_alpha", "cgis_beta"])

    assert allowed == {"cgis_alpha"}
    assert unknown == ["cgis_gamma"]


def test_a_tool_left_out_is_not_listed() -> None:
    assert _listed(_toy_server("alpha")) == ["cgis_alpha"]


def test_a_tool_left_out_says_it_is_disabled_when_called() -> None:
    result = _call(_toy_server("alpha"), "cgis_beta")

    assert result.is_error
    text = result.content[0].text  # type: ignore[union-attr]
    assert "cgis_beta is disabled" in text
    assert TOOLS_ENV in text


def test_an_allowed_tool_still_runs() -> None:
    result = _call(_toy_server("alpha"), "cgis_alpha")

    assert not result.is_error
    assert result.content[0].text == "alpha ran"  # type: ignore[union-attr]


def test_an_allowlist_matching_nothing_keeps_every_tool() -> None:
    """A typo is not a request for an empty server."""
    assert sorted(_listed(_toy_server("alhpa"))) == ["cgis_alpha", "cgis_beta"]


def test_the_server_reads_the_variable_at_startup() -> None:
    """`cgis-mcp` runs `mcp.run` on import, so the module itself must apply it."""
    script = (
        "import asyncio, json\n"
        "from cgis.api.mcp_server import mcp\n"
        "print(json.dumps([t.name for t in asyncio.run(mcp.list_tools())]))\n"
    )
    env = {**os.environ, TOOLS_ENV: "analyze_impact,trace_flow"}
    out = subprocess.run(
        [sys.executable, "-c", script], env=env, capture_output=True, text=True, check=True
    )

    assert sorted(json.loads(out.stdout.strip().splitlines()[-1])) == [
        "cgis_analyze_impact",
        "cgis_trace_flow",
    ]
