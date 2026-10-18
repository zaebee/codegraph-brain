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
from cgis.languages.go.profile import INVOKERS
from cgis.languages.python.profile import INVOKERS as PYTHON_INVOKERS


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
    server = CgisServer("toy", instructions="Use cgis_alpha, then cgis_beta.")

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


def _instructions(server: CgisServer) -> str | None:
    """The instructions a client receives in `initialize`."""

    async def run() -> str | None:
        async with Client(server) as client:
            return client.instructions

    return asyncio.run(run())


def test_a_narrowed_server_says_which_tools_it_lists() -> None:
    """The instructions name tools by name; an agent must not chase a hidden one."""
    text = _instructions(_toy_server("alpha"))

    assert text is not None
    assert text.startswith("Use cgis_alpha, then cgis_beta.")
    assert "lists only: cgis_alpha." in text


def test_without_an_allowlist_the_instructions_are_untouched() -> None:
    assert _instructions(_toy_server(None)) == "Use cgis_alpha, then cgis_beta."


def test_a_mistake_in_the_variable_never_reaches_stdout() -> None:
    """stdout is the JSON-RPC channel; the import-time warning once landed on it."""
    script = "import cgis.api.mcp_server\n"
    env = {**os.environ, TOOLS_ENV: "analyze_impact,bogus"}
    out = subprocess.run(
        [sys.executable, "-c", script], env=env, capture_output=True, text=True, check=True
    )

    assert out.stdout == ""
    assert "no such tool: cgis_bogus" in out.stderr


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


def test_instructions_say_go_callers_need_confirming() -> None:
    # exp4 decision: Go is supported for structure and metrics; impact answers are a lead.
    assert re.search(
        r"\nGo is supported for structure and metrics only\. Go callers .*confirm them by reading",
        SERVER_INSTRUCTIONS,
    )
    assert "not measured on applications" in SERVER_INSTRUCTIONS
    assert "Python callers" not in SERVER_INSTRUCTIONS


def test_instructions_say_which_go_function_values_have_edges() -> None:
    # #695: a held value is measured (20/20 held out); an invoker argument barely is, and a
    # framework callback is no call (CV-3). Only that sentence changed; dispatch stays.
    assert (
        "A function value held in a local and called through it has an edge" in SERVER_INSTRUCTIONS
    )
    assert "this is barely measured" in SERVER_INSTRUCTIONS
    assert "has no caller: treat it as an entry point" in SERVER_INSTRUCTIONS
    assert "handed over as a value" not in SERVER_INSTRUCTIONS
    assert "Some interface dispatch is missing" in SERVER_INSTRUCTIONS


def test_every_invoker_the_go_caveat_names_is_on_the_invoker_list() -> None:
    # The caveat says its examples are listed: a profile change must not quietly falsify it.
    for name in ("sync.Once.Do", "time.AfterFunc", "sort.Slice"):
        assert name in SERVER_INSTRUCTIONS
        assert name in INVOKERS


#: The Python invokers the coverage bullet names, as it spells them.
_PYTHON_NAMED = {
    "map": "map",
    "sorted(key=)": "sorted",
    "threading.Thread(target=)": "threading.Thread",
}


def test_instructions_say_which_python_function_values_have_edges() -> None:
    # #632: CV-1 (listed invokers), CV-4 (a project function calling its parameter),
    # CV-2 (a local), CV-3 (a stored callback) — and no recall figure until it is measured.
    text = " ".join(SERVER_INSTRUCTIONS.split())
    assert "to a project function that calls that parameter, has an edge" in text
    assert "as does one held in a local and called" in text
    assert "one stored for a framework to call has no caller" in text


def test_every_invoker_the_python_coverage_names_is_on_the_invoker_list() -> None:
    text = " ".join(SERVER_INSTRUCTIONS.split())
    for spelled, key in _PYTHON_NAMED.items():
        assert spelled in text
        assert key in PYTHON_INVOKERS
