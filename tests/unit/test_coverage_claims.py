"""What cgis tells agents and users it covers must be what the registry holds.

The MCP instructions, the registry listing and both plugin skills said "Python and
TypeScript only" — and that Go was invisible — for as long as the Go pack had
shipped: an agent on a Go repository was told to read the files instead.
"""

import re
from pathlib import Path

import pytest

from cgis.api.server_instructions import SERVER_INSTRUCTIONS
from cgis.languages import MANIFESTS

REPO = Path(__file__).resolve().parent.parent.parent
#: Every place that names the languages cgis covers, for people or agents to read.
CLAIMS = [
    "server.json",
    "README.md",
    "plugin/skills/cgis/SKILL.md",
    "plugin/skills/ingest/SKILL.md",
    "plugin/README.md",
    "docs/AUDIT.md",
]


def test_the_instructions_name_every_language_and_extension() -> None:
    lines = [line for line in SERVER_INSTRUCTIONS.splitlines() if line.startswith("Coverage:")]
    assert lines, "the instructions have no Coverage: line"
    coverage = lines[0]
    for manifest in MANIFESTS:
        assert manifest.title in coverage, manifest.name
        for extension in manifest.extensions:
            assert extension in coverage, extension


@pytest.mark.parametrize("path", CLAIMS)
def test_every_coverage_claim_names_every_language(path: str) -> None:
    text = (REPO / path).read_text(encoding="utf-8")
    # A whole word: "Go" must not pass for "Google" or "Golang".
    missing = [m.title for m in MANIFESTS if not re.search(rf"\b{re.escape(m.title)}\b", text)]
    assert not missing, f"{path} does not name {missing}"


@pytest.mark.parametrize("path", CLAIMS)
def test_no_claim_calls_a_covered_language_invisible(path: str) -> None:
    """The old wording listed Go among the languages "invisible to the graph".

    Only the clause that says so is read — the sentence before "invisible" — since
    the same line rightly names the covered languages a sentence earlier.
    """
    text = (REPO / path).read_text(encoding="utf-8")
    for line in text.splitlines():
        if "invisible to the graph" not in line:
            continue
        clause = line.split("invisible to the graph")[0].rsplit(". ", 1)[-1]
        # A whole word, whatever wraps it: `**Go**`, `Go,` and `Go` alike.
        named = [m.title for m in MANIFESTS if re.search(rf"\b{re.escape(m.title)}\b", clause)]
        assert not named, f"{path}: {clause.strip()[:120]}"
