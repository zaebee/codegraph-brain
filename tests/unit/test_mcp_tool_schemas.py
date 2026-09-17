"""The MCP tool schemas an agent actually receives.

An agent picks arguments from the JSON schema, not from the docstring: the SDK
copies a docstring into the tool description but leaves every parameter bare.
Directories score this too — Glama rated all 53 parameters undescribed, and the
server announced an empty version to every client that connected.
"""

import asyncio

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
