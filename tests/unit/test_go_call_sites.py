"""A Go call edge says where it is, so the coverage line names a place (#663).

The coverage line gives each unresolved name its first call site as `file:line`
(#641). Go CALLS edges carried their position only inside the edge id, so a Go gap
was named `at <caller>` with nothing to open.
"""

from pathlib import Path

from cgis.api.mcp_server import _coverage_text
from cgis.core.models import EdgeType
from cgis.languages.go.extractor import GoExtractor
from cgis.pipeline import IngestionPipeline
from cgis.query.engine import QueryEngine
from cgis.storage.sqlite_store import SQLiteStore

CODE = (
    "package app\n"
    "type T struct{}\n"
    "func (t *T) Run() {}\n"
    "func Target() {}\n"
    "func Caller(w any) {\n"
    "\tTarget()\n"
    "\tw.(interface{ Run() }).Run()\n"
    "\tmissing().Run()\n"
    "}\n"
)


def test_a_call_edge_carries_its_file_and_line() -> None:
    _, edges = GoExtractor().parse(CODE, "app/a.go")
    calls = {e.target: e for e in edges if e.type is EdgeType.CALLS}
    target = calls["raw_call:Target"]
    assert (target.file_path, target.line_number) == ("app/a.go", 6)
    assert calls["raw_call:missing().Run"].line_number == 8


def test_the_coverage_line_names_a_go_gap_by_file_and_line(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "app").mkdir(parents=True)
    (root / "go.mod").write_text("module example.com/m\n\ngo 1.22\n")
    (root / "app" / "a.go").write_text(CODE)
    with SQLiteStore(str(tmp_path / "g.db")) as store:
        IngestionPipeline({".go": GoExtractor()}).run(str(root), store=store)
        result = QueryEngine(store).get_flow_result("app.Caller", max_depth=1)
    assert result.coverage is not None
    line = _coverage_text(result.coverage)
    assert "app/a.go:8" in line
