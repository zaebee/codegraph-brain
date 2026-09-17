"""Tests for the architecture graph injector (scripts/inject_architecture_graph.py).

The injector rewrites a slice of a checked-in doc on every autodoc run. Two ways
it can hurt without anyone noticing: eating the prose around the anchors, or
drifting between runs so the autodoc PR is never empty (#460 is that PR).
"""

import subprocess
import sys
from pathlib import Path

import pytest

from cgis.extractors.registry import build_extractors
from cgis.pipeline import IngestionPipeline
from cgis.storage.sqlite_store import SQLiteStore

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

import inject_architecture_graph as iag

_MERMAID = "graph TD\n    a --> b"
_DOC = (
    "# How it works\n\nIntro prose.\n\n"
    "<!-- START_CGIS_GRAPH -->\nstale block\n<!-- END_CGIS_GRAPH -->\n\nOutro prose.\n"
)


@pytest.fixture
def doc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A target doc with anchors and a diagram file, wired into the module."""
    target = tmp_path / "HOW_IT_WORKS.md"
    target.write_text(_DOC, encoding="utf-8")
    diagram = tmp_path / "pipeline_flow.mermaid"
    diagram.write_text(_MERMAID + "\n", encoding="utf-8")
    monkeypatch.setattr(iag, "_TARGET", target)
    monkeypatch.setattr(iag, "_DIAGRAM", diagram)
    return target


def test_replaces_only_the_anchored_block(doc: Path) -> None:
    """The stale block goes; the prose on both sides survives verbatim."""
    iag.inject_graph()

    text = doc.read_text(encoding="utf-8")
    assert "stale block" not in text
    assert text.startswith("# How it works\n\nIntro prose.\n\n<!-- START_CGIS_GRAPH -->\n")
    assert text.endswith("<!-- END_CGIS_GRAPH -->\n\nOutro prose.\n")
    assert f"```mermaid\n{_MERMAID}\n```" in text


def test_is_idempotent(doc: Path) -> None:
    """A second run is byte-identical — otherwise autodoc opens a PR every push."""
    iag.inject_graph()
    first = doc.read_text(encoding="utf-8")
    iag.inject_graph()
    assert doc.read_text(encoding="utf-8") == first


def test_missing_anchors_raise_and_leave_the_doc_untouched(doc: Path) -> None:
    """No anchors means no write — never append a graph to an arbitrary file."""
    doc.write_text("# No anchors here\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Anchor tags not found"):
        iag.inject_graph()

    assert doc.read_text(encoding="utf-8") == "# No anchors here\n"


def test_missing_target_raises(doc: Path) -> None:
    """A moved or renamed target doc fails the workflow loudly."""
    doc.unlink()
    with pytest.raises(FileNotFoundError, match="Target doc not found"):
        iag.inject_graph()


def test_missing_diagram_raises(doc: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the diagram the step must fail, not inject an empty fence."""
    monkeypatch.setattr(iag, "_DIAGRAM", doc.parent / "absent.mermaid")
    with pytest.raises(FileNotFoundError, match="Diagram not found"):
        iag.inject_graph()


def _graph_db(tmp_path: Path) -> str:
    src = tmp_path / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "core.py").write_text(
        "def helper():\n    return 1\n\n\ndef entry():\n    return helper()\n",
        encoding="utf-8",
    )
    db = str(tmp_path / "graph.db")
    # Autodoc ingests ./src, so stored paths lack the prefix that --path-prefix restores.
    with SQLiteStore(db) as store:
        IngestionPipeline(build_extractors([])).run(str(tmp_path / "src"), store=store)
    return db


def test_node_table_links_internal_symbols_to_github(
    doc: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With --db, each traced symbol gets a row linking to its source line."""
    db = _graph_db(tmp_path)
    monkeypatch.setattr(iag, "_repo_url", lambda: "https://github.com/o/r")

    iag.inject_graph(db=db, fqn="pkg.core.entry", depth=1, path_prefix="src/")

    text = doc.read_text(encoding="utf-8")
    assert "| Symbol | Type | File |" in text
    link = "https://github.com/o/r/blob/main/src/pkg/core.py#L1"
    assert f"| `helper` | FUNCTION | [`core.py:1`]({link}) |" in text


def test_node_table_without_remote_prints_plain_locations(
    doc: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No git remote → no dead links, just file:line."""
    db = _graph_db(tmp_path)
    monkeypatch.setattr(iag, "_repo_url", lambda: "")

    iag.inject_graph(db=db, fqn="pkg.core.entry", depth=1)

    assert "| `helper` | FUNCTION | `core.py:1` |" in doc.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("git@github.com:zaebee/codegraph-brain.git", "https://github.com/zaebee/codegraph-brain"),
        (
            "https://github.com/zaebee/codegraph-brain.git",
            "https://github.com/zaebee/codegraph-brain",
        ),
    ],
)
def test_repo_url_normalises_ssh_and_https(
    remote: str, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SSH and HTTPS remotes both become a browsable https URL without `.git`."""
    monkeypatch.setattr(iag.subprocess, "check_output", lambda *_a, **_k: remote + "\n")
    assert iag._repo_url() == expected  # noqa: SLF001  # white-box: remote parsing


def test_repo_url_is_empty_without_a_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    """A checkout with no `origin` degrades to plain locations instead of crashing."""

    def _no_remote(*_a: object, **_k: object) -> str:
        raise subprocess.CalledProcessError(2, ["git"])

    monkeypatch.setattr(iag.subprocess, "check_output", _no_remote)
    assert iag._repo_url() == ""  # noqa: SLF001  # white-box: no-remote fallback


def test_the_real_target_doc_carries_both_anchors() -> None:
    """Autodoc's actual target has the anchors — a move that drops them fails here, not in CI."""
    target = Path(__file__).parent.parent.parent / iag._TARGET  # noqa: SLF001  # the wired path
    text = target.read_text(encoding="utf-8")
    assert text.count("<!-- START_CGIS_GRAPH -->") == 1
    assert text.count("<!-- END_CGIS_GRAPH -->") == 1
