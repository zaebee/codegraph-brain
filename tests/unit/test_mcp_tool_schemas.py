"""The MCP tool schemas an agent actually receives.

An agent picks arguments from the JSON schema, not from the docstring: the SDK
copies a docstring into the tool description but leaves every parameter bare.
Directories score this too — Glama rated all 53 parameters undescribed, and the
server announced an empty version to every client that connected.
"""

import asyncio
import re

import pytest

from cgis import __version__
from cgis.api.mcp_server import mcp


def _tool_params() -> list[tuple[str, str, dict[str, object]]]:
    tools = asyncio.run(mcp.list_tools())
    return [
        (tool.name, name, prop)
        for tool in tools
        for name, prop in tool.input_schema.get("properties", {}).items()
    ]


_PARAMS = _tool_params()


def test_every_tool_exposes_parameters() -> None:
    """Guards the parametrized test below from passing over an empty list."""
    assert len(_PARAMS) >= 50


@pytest.mark.parametrize(
    ("tool", "param", "schema"), _PARAMS, ids=[f"{t}.{p}" for t, p, _ in _PARAMS]
)
def test_every_parameter_is_described(tool: str, param: str, schema: dict[str, object]) -> None:
    """Each argument carries a description in the schema the client sees."""
    description = schema.get("description")
    assert isinstance(description, str), f"{tool}.{param} has no description"
    assert len(description) >= 15, f"{tool}.{param}: {description!r} says too little"


def test_server_announces_the_package_version() -> None:
    """`initialize` reports the installed version, not an empty string."""
    assert mcp.version == __version__
    assert mcp.version


def _descriptions() -> dict[str, str]:
    return {tool.name: tool.description or "" for tool in asyncio.run(mcp.list_tools())}


def test_tool_summaries_carry_no_issue_numbers() -> None:
    """The first paragraph is what an agent weighs when choosing; `(#19)` tells it nothing."""
    for name, description in _descriptions().items():
        summary = description.strip().split("\n\n")[0]
        assert not re.search(r"\(#\d+\)", summary), f"{name}: {summary!r}"


@pytest.mark.parametrize(
    ("tool", "siblings"),
    [
        ("cgis_trace_flow", {"cgis_analyze_impact", "cgis_get_structure", "cgis_context"}),
        ("cgis_analyze_impact", {"cgis_trace_flow", "cgis_get_structure", "cgis_context"}),
        ("cgis_get_structure", {"cgis_trace_flow", "cgis_analyze_impact"}),
        ("cgis_context", {"cgis_trace_flow", "cgis_analyze_impact"}),
    ],
)
def test_overlapping_graph_tools_say_when_to_use_a_sibling(tool: str, siblings: set[str]) -> None:
    """All four return a subgraph around one FQN; each names the ones it could be taken for."""
    description = _descriptions()[tool]
    missing = {sibling for sibling in siblings if sibling not in description}
    assert not missing, f"{tool} never mentions {sorted(missing)}"
