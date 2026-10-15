"""The surfaces that find a file's FILE node, on Go (step 7b; #606 review I3, M8).

For Python and TypeScript `file_node_id` equals `scope_of`, so 7a could not tell
the call sites apart; a Go file's FILE node is `<scope>.<stem>_go`, its
definitions `<scope>.<Name>`, and each surface must reach the right one.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from cgis.cli import app
from cgis.core.models import Node, NodeNamespace, NodeType
from cgis.guardian.collector import ContextCollector
from cgis.languages import extractors
from cgis.pipeline import IngestionPipeline
from cgis.query.render.mermaid import _node_slug
from cgis.storage.sqlite_store import SQLiteStore

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

import guardian_martian as gm

SERVER = "package server\ntype Server struct{}\nfunc (s *Server) Start() {}\nfunc Run() {}\n"


def _go_db(tmp_path: Path) -> Path:
    tree = tmp_path / "tree"
    (tree / "internal" / "server").mkdir(parents=True)
    (tree / "internal" / "server" / "server.go").write_text(SERVER, encoding="utf-8")
    db = tmp_path / "g.db"
    with SQLiteStore(str(db)) as store:
        IngestionPipeline(extractors([])).run(str(tree), store=store)
    return db


def test_cli_structure_takes_a_go_file_path(tmp_path: Path) -> None:
    """`cli.py`: a path normalises to the file's FILE node, not its package scope."""
    db = _go_db(tmp_path)
    result = CliRunner().invoke(app, ["structure", "internal/server/server.go", "--db", str(db)])
    assert result.exit_code == 0, result.output
    assert "internal.server.server_go" in result.output


def test_the_collector_asks_for_a_go_file_s_impact_by_its_file_node(tmp_path: Path) -> None:
    """`guardian/collector.py`: the impact graph of a changed Go file starts at its FILE node."""
    db = tmp_path / "graph.db"
    db.touch()
    engine = MagicMock()
    engine.get_impact_graph.return_value = ([], [])
    collector = ContextCollector(project_root=tmp_path, db_path=db)
    with (
        patch.object(
            collector.source,
            "get_changed_source_files",
            return_value=["src/internal/server/server.go"],
        ),
        patch("cgis.guardian.collector.SQLiteStore") as store_cls,
        patch("cgis.guardian.collector.QueryEngine", return_value=engine),
    ):
        store_cls.return_value.__enter__ = MagicMock(return_value=MagicMock())
        store_cls.return_value.__exit__ = MagicMock(return_value=False)
        collector.collect_graph_context()
    engine.get_impact_graph.assert_called_once_with("internal.server.server_go", max_depth=2)


def test_the_martian_alignment_finds_a_go_file(tmp_path: Path) -> None:
    """`guardian_martian.py`: a changed Go file the graph holds counts as found."""
    db = _go_db(tmp_path)
    assert gm.graph_alignment(db, ["internal/server/server.go"]) == (1, 1)


def _go_node(fqn: str, node_type: NodeType) -> Node:
    return Node(
        id=fqn,
        name=fqn.rsplit(".", 1)[-1],
        type=node_type,
        file_path="internal/server/server.go",
        start_line=1,
        end_line=1,
        language="go",
        namespace=NodeNamespace.INTERNAL,
    )


def test_mermaid_slugs_go_definitions_by_their_package() -> None:
    """The slug peels the package scope; a Go FILE node keeps its `_go` stem (M8)."""
    assert _node_slug(_go_node("internal.server.Run", NodeType.FUNCTION)) == "server_Run"
    assert _node_slug(_go_node("internal.server.server_go", NodeType.FILE)) == "server_server_go"
