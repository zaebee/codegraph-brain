"""Module graph of one package (#741): modules as nodes, imports/extends/calls as edges."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cgis.api import mcp_server
from cgis.api.mcp_server import cgis_module_graph
from cgis.cli import app
from cgis.languages import extractors
from cgis.pipeline import IngestionPipeline
from cgis.query.analysis.module_graph import (
    ModuleEdge,
    ModuleGraph,
    module_graph,
    render_mermaid,
    transitive_reduction,
)
from cgis.storage.sqlite_store import SQLiteStore

PACKAGE = {
    "pkg/__init__.py": "",
    "pkg/effects.py": "class Effects:\n    pass\n",
    "pkg/core.py": (
        "from pkg.effects import Effects\n\n\n"
        "def helper():\n    return 1\n\n\n"
        "class Core(Effects):\n    def run(self):\n        return helper()\n"
    ),
    # creation -> effects is implied by creation -> core -> effects
    "pkg/creation.py": (
        "from pkg.core import Core\nfrom pkg.effects import Effects\n\n\n"
        "class Creation(Core):\n    def make(self):\n        return Effects()\n"
    ),
    # service -> core is implied by service -> creation -> core
    "pkg/service.py": (
        "from pkg.creation import Creation\nfrom pkg.core import Core\n"
        "from other.out import ext\n\n\n"
        "class Service(Creation):\n    def go(self):\n        return ext(), Core()\n"
    ),
    "pkg/cyc1.py": "from pkg import cyc2\n\n\ndef one():\n    return cyc2.two()\n",
    "pkg/cyc2.py": "from pkg import cyc1\n\n\ndef two():\n    return cyc1.one()\n",
    "pkg/sub/__init__.py": "",
    "pkg/sub/a.py": "from pkg.core import Core\n\n\nclass A(Core):\n    pass\n",
    "pkg/sub/b.py": "from pkg.sub.a import A\n\n\ndef b():\n    return A()\n",
    "pkg/tests/__init__.py": "",
    "pkg/tests/test_core.py": (
        "from pkg.core import Core\n\n\ndef test_core():\n    assert Core()\n"
    ),
    "other/__init__.py": "",
    "other/out.py": "def ext():\n    return 2\n",
    "lib/__init__.py": "",
    "lib/deep/__init__.py": "def util():\n    return 3\n",
    "lib/deep/mod.py": "X = 1\n",
    "pkg/uses_lib.py": "from lib.deep import util\n\n\ndef f():\n    return util()\n",
}


@pytest.fixture(scope="module")
def db(tmp_path_factory: pytest.TempPathFactory) -> str:
    """A graph of PACKAGE, ingested the way the CLI does."""
    root = tmp_path_factory.mktemp("repo")
    for rel, text in PACKAGE.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    db_path = str(root.parent / "g.db")
    store = SQLiteStore(db_path)
    store.connect()
    with store:
        IngestionPipeline(extractors([])).run(str(root), store=store, rebuild=True)
    return db_path


def _pairs(graph: ModuleGraph, kind: str) -> set[tuple[str, str]]:
    return {(e.source, e.target) for e in graph.edges if e.kind == kind}


def test_nodes_are_the_packages_children_with_subpackages_collapsed(db: str) -> None:
    """Each module is a node, `sub/` is one node, and the test package is left out."""
    graph = module_graph(db, "pkg")
    assert set(graph.nodes) == {
        "pkg.core",
        "pkg.creation",
        "pkg.cyc1",
        "pkg.cyc2",
        "pkg.effects",
        "pkg.service",
        "pkg.sub",
        "pkg.uses_lib",
    }
    assert graph.subpackages == ["pkg.sub"]


def test_imports_are_reduced_to_the_direct_layering(db: str) -> None:
    """An import implied by a longer path is dropped; the layering stays."""
    graph = module_graph(db, "pkg", layers=("imports",))
    assert _pairs(graph, "imports") == {
        ("pkg.core", "pkg.effects"),
        ("pkg.creation", "pkg.core"),
        ("pkg.service", "pkg.creation"),
        ("pkg.sub", "pkg.core"),
        ("pkg.cyc1", "pkg.cyc2"),
        ("pkg.cyc2", "pkg.cyc1"),
    }
    assert graph.dropped == {"imports": 2}


def test_without_reduction_every_import_is_kept(db: str) -> None:
    """`reduce=False` keeps the implied edges too."""
    graph = module_graph(db, "pkg", layers=("imports",), reduce=False)
    assert {("pkg.creation", "pkg.effects"), ("pkg.service", "pkg.core")} <= _pairs(
        graph, "imports"
    )
    assert graph.dropped == {"imports": 0}


def test_inheritance_is_its_own_layer(db: str) -> None:
    """Extends edges show Effects <- Core <- Creation <- Service, and sub's A <- Core."""
    graph = module_graph(db, "pkg", layers=("extends",))
    assert _pairs(graph, "extends") == {
        ("pkg.core", "pkg.effects"),
        ("pkg.creation", "pkg.core"),
        ("pkg.service", "pkg.creation"),
        ("pkg.sub", "pkg.core"),
    }


def test_calls_are_available_and_off_by_default(db: str) -> None:
    """The default layers are imports and extends; calls must be asked for."""
    assert {e.kind for e in module_graph(db, "pkg").edges} == {"imports", "extends"}
    calls = _pairs(module_graph(db, "pkg", layers=("calls",)), "calls")
    assert ("pkg.cyc1", "pkg.cyc2") in calls


def test_test_modules_are_left_out_unless_asked(db: str) -> None:
    """Production only by default; `exclude_tests=False` brings the test package back."""
    assert "pkg.tests" not in module_graph(db, "pkg").nodes
    with_tests = module_graph(db, "pkg", exclude_tests=False)
    assert ("pkg.tests", "pkg.core") in _pairs(with_tests, "imports")


def test_expand_shows_the_subpackages_modules(db: str) -> None:
    """`expand` lists the files below the package instead of one node per sub-package."""
    graph = module_graph(db, "pkg", layers=("imports",), expand=True)
    assert {"pkg.sub.a", "pkg.sub.b"} <= set(graph.nodes)
    assert ("pkg.sub.b", "pkg.sub.a") in _pairs(graph, "imports")
    assert graph.subpackages == []


def test_outbound_dependencies_are_summarised_by_package(db: str) -> None:
    """A dependency outside the package is listed per module, not drawn."""
    graph = module_graph(db, "pkg")
    assert graph.outside == {"pkg.service": ["other"], "pkg.uses_lib": ["lib.deep"]}


def test_an_unknown_prefix_gives_an_empty_graph(db: str) -> None:
    """No module under the prefix: an empty graph, not an error."""
    graph = module_graph(db, "nope")
    assert graph.nodes == []
    assert graph.edges == []


def test_a_missing_database_is_an_error(tmp_path: Path) -> None:
    """Run ingest first; say so."""
    missing = str(tmp_path / "missing.db")
    with pytest.raises(FileNotFoundError):
        module_graph(missing, "pkg")


def test_reduction_never_drops_an_edge_of_a_cycle() -> None:
    """An edge that closes a cycle stays, or the cycle would vanish from the picture."""
    edges = [
        ModuleEdge("a", "b", "imports"),
        ModuleEdge("b", "c", "imports"),
        ModuleEdge("c", "a", "imports"),
        ModuleEdge("a", "c", "imports"),
        ModuleEdge("c", "d", "imports"),
        ModuleEdge("d", "e", "imports"),
        ModuleEdge("a", "e", "imports"),
    ]
    kept = {(e.source, e.target) for e in transitive_reduction(edges)}
    # inside the cycle {a, b, c} nothing is dropped, not even the shortcut a -> c;
    # a -> e is implied by the cycle's way out, c -> d -> e
    assert kept == {("a", "b"), ("b", "c"), ("c", "a"), ("a", "c"), ("c", "d"), ("d", "e")}


def test_mermaid_styles_kinds_and_guards_keywords() -> None:
    """Imports -->, extends ==>, calls -.->; ids are prefixed, so `end` is safe."""
    graph = ModuleGraph(
        prefix="p",
        nodes=["p.end", "p.core", "p.sub"],
        subpackages=["p.sub"],
        edges=[
            ModuleEdge("p.end", "p.core", "imports"),
            ModuleEdge("p.end", "p.core", "extends"),
            ModuleEdge("p.sub", "p.core", "calls"),
        ],
        dropped={"imports": 0},
        outside={"p.end": ["q", "r"]},
    )
    text = render_mermaid(graph)
    assert "m_end --> m_core" in text
    assert "m_end ==> m_core" in text
    assert "m_sub -.-> m_core" in text
    assert 'm_end["end"]' in text
    assert 'm_sub[["sub/"]]' in text
    assert "| end | q, r |" in text


# --- surfaces: the CLI command and the MCP tool use the same service ---


def test_cli_prints_a_mermaid_diagram(db: str) -> None:
    """`cgis module-graph pkg` draws the reduced picture."""
    result = CliRunner().invoke(app, ["module-graph", "pkg", "--db", db])
    assert result.exit_code == 0, result.output
    assert "flowchart TD" in result.stdout
    assert "m_creation --> m_core" in result.stdout
    assert "m_creation --> m_effects" not in result.stdout


def test_cli_json_with_calls_and_an_unknown_layer(db: str) -> None:
    """--format json is data; an unknown --layer is a usage error, not a traceback."""
    runner = CliRunner()
    ok = runner.invoke(app, ["module-graph", "pkg", "--db", db, "-l", "calls", "-f", "json"])
    assert ok.exit_code == 0, ok.output
    payload = json.loads(ok.stdout)
    assert {e["kind"] for e in payload["edges"]} == {"calls"}
    bad = runner.invoke(app, ["module-graph", "pkg", "--db", db, "-l", "vibes"])
    assert bad.exit_code == 2


def test_cli_unknown_prefix_exits_1(db: str) -> None:
    """Nothing under the prefix is a failure the caller can see."""
    assert CliRunner().invoke(app, ["module-graph", "nope", "--db", db]).exit_code == 1


def test_mcp_tool_answers_in_both_formats(db: str) -> None:
    """The MCP tool returns the diagram by default and JSON on request."""
    assert "flowchart TD" in cgis_module_graph(prefix="pkg", db_path=db)
    payload = json.loads(cgis_module_graph(prefix="pkg", db_path=db, output_format="json"))
    assert ("pkg.core", "pkg.effects") in {(e["source"], e["target"]) for e in payload["edges"]}
    assert "No modules under" in cgis_module_graph(prefix="nope", db_path=db)


def test_reduction_keeps_the_only_way_into_a_cycle() -> None:
    """Two parallel edges into one cycle are not each other's justification."""
    edges = [
        ModuleEdge("x", "a1", "imports"),
        ModuleEdge("x", "a2", "imports"),
        ModuleEdge("a1", "a2", "imports"),
        ModuleEdge("a2", "a1", "imports"),
    ]
    kept = {(e.source, e.target) for e in transitive_reduction(edges)}
    assert kept & {("x", "a1"), ("x", "a2")}


def test_reduction_keeps_the_link_between_two_cycles() -> None:
    """Two cycles joined by two edges stay joined."""
    edges = [
        ModuleEdge("a1", "a2", "imports"),
        ModuleEdge("a2", "a1", "imports"),
        ModuleEdge("b1", "b2", "imports"),
        ModuleEdge("b2", "b1", "imports"),
        ModuleEdge("a1", "b1", "imports"),
        ModuleEdge("a2", "b2", "imports"),
    ]
    kept = {(e.source, e.target) for e in transitive_reduction(edges)}
    assert kept & {("a1", "b1"), ("a2", "b2")}


def test_reduction_drops_a_cycle_shortcut_implied_through_another_component() -> None:
    """x -> c is implied by x -> {a1, a2} -> c, however the cycle is entered."""
    edges = [
        ModuleEdge("x", "a1", "imports"),
        ModuleEdge("a1", "a2", "imports"),
        ModuleEdge("a2", "a1", "imports"),
        ModuleEdge("a2", "c", "imports"),
        ModuleEdge("x", "c", "imports"),
    ]
    kept = {(e.source, e.target) for e in transitive_reduction(edges)}
    assert ("x", "c") not in kept
    assert {("x", "a1"), ("a2", "c")} <= kept


def test_repeated_or_empty_layers(db: str) -> None:
    """A layer named twice is one layer; no layer at all is an error."""
    twice = module_graph(db, "pkg", layers=("imports", "imports"))
    once = module_graph(db, "pkg", layers=("imports",))
    assert twice.edges == once.edges
    with pytest.raises(ValueError, match="layer"):
        module_graph(db, "pkg", layers=())


def test_expand_draws_no_edge_to_an_init_only_imported_through(tmp_path: Path) -> None:
    """`from pkg.sub import a` depends on pkg.sub.a, not on the empty pkg/sub/__init__."""
    root = tmp_path / "repo"
    for rel, text in {
        "pkg/__init__.py": "",
        "pkg/b.py": "from pkg.sub import a\n\n\ndef f():\n    return a.X\n",
        "pkg/sub/__init__.py": "",
        "pkg/sub/a.py": "X = 1\n",
    }.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    db_path = str(tmp_path / "g.db")
    store = SQLiteStore(db_path)
    store.connect()
    with store:
        IngestionPipeline(extractors([])).run(str(root), store=store, rebuild=True)
    pairs = _pairs(module_graph(db_path, "pkg", layers=("imports",), expand=True), "imports")
    assert ("pkg.b", "pkg.sub.a") in pairs
    assert ("pkg.b", "pkg.sub") not in pairs


def test_mcp_mermaid_carries_the_freshness_note(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A diagram from a stale graph says so above it, as the other diagram tools do."""
    monkeypatch.setattr(mcp_server, "_freshness_note", lambda _db: "STALE NOTE\n")
    assert cgis_module_graph(prefix="pkg", db_path=db).startswith("STALE NOTE\n```mermaid")
