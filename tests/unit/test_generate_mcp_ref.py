"""Tests for the MCP reference generator (scripts/generate_mcp_ref.py).

The generator reads tool schemas off the live server, which makes it the one
place a breaking change in the mcp library's Tool model shows up — and the unit
suite never touched it before, so the mcp 2.x rename of `inputSchema` to
`input_schema` (#264) would have slipped straight through 59 green MCP tests.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

from generate_mcp_ref import generate_reference


def test_reference_renders_tools_with_their_argument_schemas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every registered tool reaches the doc, with its arguments read off the schema."""
    out = tmp_path / "MCP_REFERENCE.md"
    monkeypatch.setattr("generate_mcp_ref._OUTPUT", out)

    generate_reference()

    text = out.read_text(encoding="utf-8")
    assert "## `cgis_ingest`" in text
    assert "## `cgis_context`" in text
    # The argument table proves the input schema was actually read, not skipped.
    assert "| Argument | Type | Required | Description |" in text
    assert "| `fqn` |" in text
    assert "| `db_path` |" in text


def test_reference_marks_required_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Required vs optional comes from the schema's `required` list."""
    out = tmp_path / "MCP_REFERENCE.md"
    monkeypatch.setattr("generate_mcp_ref._OUTPUT", out)

    generate_reference()

    lines = out.read_text(encoding="utf-8").splitlines()
    fqn_rows = [line for line in lines if line.startswith("| `fqn` |")]
    assert fqn_rows, "cgis_context/cgis_trace_flow take an fqn argument"
    assert any("✓" in row for row in fqn_rows), "fqn is required and must be marked"
