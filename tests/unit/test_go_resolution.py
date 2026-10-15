"""The Go pack's index facts, classification and resolution, end to end (step 7b, Task B4)."""

from pathlib import Path

import pytest

from cgis.core.models import Edge, EdgeType, Node, NodeNamespace
from cgis.languages import extractors
from cgis.languages.go.extractor import GoExtractor
from cgis.pipeline import IngestionPipeline
from cgis.resolver.engine import ResolverEngine

MODULE = {
    "go_module": {
        "path": "example.com/app",
        "requires": ["golang.org/x/text", "github.com/x/y"],
        "replaces": {"example.com/lib": "lib"},
    }
}

FILES = {
    "internal/server/types.go": "package server\ntype Server struct{ Base }\ntype Base struct{}\n",
    "internal/server/base.go": "package server\nfunc (b *Base) Close() {}\nfunc helper() {}\n",
    "internal/server/server.go": (
        "package server\n"
        "func (s *Server) Start() {\n"
        "\ts.Close()\n"
        "\ts.Stop()\n"
        "\thelper()\n"
        "}\n"
        "func (s *Server) Stop() {}\n"
        "func Run() {}\n"
    ),
    "internal/other/other.go": "package other\nfunc helper() {}\n",
    "lib/util/util.go": "package util\nfunc Pad() {}\n",
    "cmd/app/main.go": (
        "package main\n"
        "import (\n"
        '\t"fmt"\n'
        '\t"net/http"\n'
        '\tsrv "example.com/app/internal/server"\n'
        '\t"example.com/lib/util"\n'
        '\t"github.com/x/y"\n'
        '\t"github.com/undeclared/z"\n'
        ")\n"
        "// #include <stdlib.h>\n"
        'import "C"\n'
        "func main() {\n"
        "\tfmt.Println()\n"
        "\thttp.Get()\n"
        "\tsrv.Run()\n"
        "\tutil.Pad()\n"
        "\ty.Do()\n"
        "\tz.Do()\n"
        "\tC.malloc()\n"
        "\tc := &srv.Server{}\n"
        "\tc.Start()\n"
        "}\n"
    ),
}


def _resolve(state: dict[str, object] | None = MODULE) -> tuple[list[Edge], dict[str, Node]]:
    nodes: list[Node] = []
    edges: list[Edge] = []
    for path, code in FILES.items():
        n, e = GoExtractor().parse(code, path)
        nodes += n
        edges += e
    resolved, virtual = ResolverEngine(nodes, edges, project_state=state).resolve()
    return resolved, {n.id: n for n in [*nodes, *virtual]}


def _calls_from(edges: list[Edge], source: str) -> set[str]:
    return {e.target for e in edges if e.type is EdgeType.CALLS and e.source == source}


def test_a_package_call_resolves_through_the_import_and_the_module_path() -> None:
    edges, _ = _resolve()
    calls = _calls_from(edges, "cmd.app.main")
    assert "internal.server.Run" in calls


def test_a_local_type_from_another_package_resolves_its_method() -> None:
    edges, _ = _resolve()
    assert "internal.server.Server.Start" in _calls_from(edges, "cmd.app.main")


def test_an_unqualified_call_prefers_the_same_package() -> None:
    """`helper` is defined in base.go and in another package: the sibling file wins (A2)."""
    edges, _ = _resolve()
    assert "internal.server.helper" in _calls_from(edges, "internal.server.Server.Start")


def test_receiver_calls_reach_own_and_embedded_methods() -> None:
    edges, _ = _resolve()
    calls = _calls_from(edges, "internal.server.Server.Start")
    assert "internal.server.Server.Stop" in calls
    assert "internal.server.Base.Close" in calls  # promoted through embedding (EXTENDS)


def test_targets_are_classified_in_spec_order() -> None:
    edges, nodes = _resolve()
    calls = _calls_from(edges, "cmd.app.main")
    namespace = {t: nodes[t].namespace for t in calls if t in nodes}
    assert namespace["fmt.Println"] is NodeNamespace.STDLIB
    assert namespace["net.http.Get"] is NodeNamespace.STDLIB
    assert namespace["C.malloc"] is NodeNamespace.STDLIB
    assert namespace["github.com.x.y.Do"] is NodeNamespace.EXTERNAL
    assert namespace["github.com.undeclared.z.Do"] is NodeNamespace.UNKNOWN
    assert namespace["internal.server.Run"] is NodeNamespace.INTERNAL


def test_an_internal_replace_resolves_into_the_repository() -> None:
    edges, _ = _resolve()
    assert "lib.util.Pad" in _calls_from(edges, "cmd.app.main")


def test_a_package_import_points_at_its_first_file() -> None:
    """H5: no node per package, so the import names the package's first FILE node."""
    edges, _ = _resolve()
    imports = {
        e.target for e in edges if e.type is EdgeType.IMPORTS and e.source == "cmd.app.main_go"
    }
    assert "internal.server.base_go" in imports
    assert "lib.util.util_go" in imports
    assert "fmt" in imports


def test_without_a_go_mod_a_domain_path_is_external() -> None:
    """No module to compare against: a path whose first segment has a dot is a module."""
    edges, nodes = _resolve(state=None)
    calls = _calls_from(edges, "cmd.app.main")
    assert nodes["github.com.undeclared.z.Do"].namespace is NodeNamespace.EXTERNAL
    assert "fmt.Println" in calls


BUILTINS = (
    "package server\n"
    "func max(a, b int) int { return a }\n"
    "func Use(xs []int) {\n"
    "\t_ = len(xs)\n"
    "\txs = append(xs, 1)\n"
    "\t_ = make([]int, 3)\n"
    "\t_ = string(rune(65))\n"
    "\t_ = max(1, 2)\n"
    "}\n"
)


def _resolve_with(
    files: dict[str, tuple[str, str]], module: dict[str, object] = MODULE
) -> tuple[list[Edge], dict[str, Node]]:
    """Ingest `{path: (language, code)}` with the real extractors of each language."""
    by_ext = extractors([])
    nodes: list[Node] = []
    edges: list[Edge] = []
    for path, (ext, code) in files.items():
        n, e = by_ext[ext].parse(code, path)
        nodes += n
        edges += e
    resolved, virtual = ResolverEngine(nodes, edges, project_state=module).resolve()
    return resolved, {n.id: n for n in [*nodes, *virtual]}


def test_go_builtins_are_stdlib_and_a_package_function_shadows_one() -> None:
    """I4: `len`/`append`/conversions are the language's; `max` here is the package's own."""
    edges, nodes = _resolve_with({"internal/server/use.go": (".go", BUILTINS)})
    calls = _calls_from(edges, "internal.server.Use")
    for builtin in ("len", "append", "make", "string", "rune"):
        assert builtin in calls
        assert nodes[builtin].namespace is NodeNamespace.STDLIB
    assert "internal.server.max" in calls


def test_a_go_builtin_never_lands_on_another_language_s_function() -> None:
    """I4: a Python `make` and a TypeScript `len` are not what a Go `make(...)` calls."""
    edges, _ = _resolve_with(
        {
            "internal/server/use.go": (".go", BUILTINS),
            "tools/ops.py": (".py", "def make():\n    pass\n"),
            "web/util.ts": (".ts", "export function len() {}\n"),
        }
    )
    calls = _calls_from(edges, "internal.server.Use")
    assert "tools.ops.make" not in calls
    assert "web.util.len" not in calls


def test_a_definition_in_several_files_resolves_to_the_same_one_every_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I3: build-tag variants share an FQN; the winner must not be the filesystem's order.

    The walk is handed its names in reverse, as a filesystem may: the pipeline
    sorts them, so the last name sorted wins on any machine.
    """
    tree = tmp_path / "tree" / "sys"
    tree.mkdir(parents=True)
    for name in ("aa_windows.go", "mm_darwin.go", "zz_linux.go"):
        (tree / name).write_text("package sys\nfunc Open() {}\n", encoding="utf-8")
    real_walk = Path.walk

    def reversed_walk(self: Path, *args: object, **kwargs: object) -> object:
        for root, dirs, files in real_walk(self, *args, **kwargs):  # type: ignore[arg-type]
            files.sort(reverse=True)
            yield root, dirs, files

    monkeypatch.setattr(Path, "walk", reversed_walk)
    nodes, _, _ = IngestionPipeline(extractors([])).run(str(tmp_path / "tree"))
    winner = {n.id: n for n in nodes}["sys.Open"]
    assert winner.file_path == "sys/zz_linux.go"


def _go_graph(
    files: dict[str, str], module: dict[str, object]
) -> tuple[list[Edge], dict[str, Node]]:
    """Resolve Go `files` (paths relative to the ingest root) against `go_module` facts."""
    nodes: list[Node] = []
    edges: list[Edge] = []
    for path, code in files.items():
        n, e = GoExtractor().parse(code, path)
        nodes += n
        edges += e
    resolved, virtual = ResolverEngine(nodes, edges, project_state={"go_module": module}).resolve()
    return resolved, {n.id: n for n in [*nodes, *virtual]}


ROOT_PACKAGE = {
    "app.go": "package app\nfunc Top() {}\n",
    # Sorts before app.go: the representative must still be the production file (M6).
    "aa_test.go": "package app\nfunc helperForTests() {}\n",
    "cmd/x/main.go": 'package main\nimport app "example.com/go-app"\nfunc main() { app.Top() }\n',
}


def test_the_root_package_is_reached_by_the_module_path() -> None:
    """I1: a root package named unlike the module's last segment (`go-app` vs `app`)."""
    edges, _ = _go_graph(ROOT_PACKAGE, {"path": "example.com/go-app"})
    assert "app.Top" in _calls_from(edges, "cmd.x.main")


def test_a_root_package_import_names_a_production_file_of_it() -> None:
    """I1, and 7b review M6: the representative is not a `_test.go` file when one isn't."""
    edges, _ = _go_graph(ROOT_PACKAGE, {"path": "example.com/go-app"})
    imports = {
        e.target for e in edges if e.type is EdgeType.IMPORTS and e.source == "cmd.x.main_go"
    }
    assert imports == {"app_go"}


def test_an_ingest_below_the_module_root_still_resolves_across_packages() -> None:
    """I2: `cgis ingest internal/` under a go.mod at the repository root."""
    files = {
        "a/a.go": "package a\nfunc A() {}\n",
        "b/b.go": 'package b\nimport "example.com/app/internal/a"\nfunc B() { a.A() }\n',
    }
    edges, _ = _go_graph(files, {"path": "example.com/app", "offset": "internal"})
    assert "a.A" in _calls_from(edges, "b.B")
    imports = {e.target for e in edges if e.type is EdgeType.IMPORTS and e.source == "b.b_go"}
    assert imports == {"a.a_go"}


def test_a_go_module_path_never_strips_a_python_import() -> None:
    """I5: module `example.com/api` beside a Python `api/` package, which keeps #319's rule.

    `from api.utils import helper` with no `api/utils.py` must not land on a top-level
    `utils.helper`: that is the collision #319 refused, now through Go's module segments.
    """
    edges, _ = _resolve_with(
        {
            "svc/main.go": (".go", "package svc\nfunc Main() {}\n"),
            "api/__init__.py": (".py", ""),
            "api/handlers.py": (
                ".py",
                "from api.utils import helper\n\ndef run():\n    helper()\n",
            ),
            "utils.py": (".py", "def helper():\n    pass\n"),
        },
        module={"go_module": {"path": "example.com/api"}},
    )
    assert "utils.helper" not in _calls_from(edges, "api.handlers.run")


def test_an_import_of_a_directory_names_its_production_package_not_the_test_one() -> None:
    """`srv` and `srv_test` share a directory; importing it means `srv` (delta review)."""
    files = {
        # Sorts first: the external test package must still not stand for the directory.
        "srv/a_test.go": "package srv_test\nfunc Helper() {}\n",
        "srv/srv.go": "package srv\nfunc Helper() {}\n",
        "cmd/x/main.go": (
            'package main\nimport "example.com/app/srv"\nfunc main() { srv.Helper() }\n'
        ),
    }
    edges, _ = _go_graph(files, {"path": "example.com/app"})
    assert "srv.Helper" in _calls_from(edges, "cmd.x.main")


def test_directories_are_walked_in_sorted_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The filesystem hands directories in its own order; the pipeline sorts them (I3)."""
    for name in ("aa", "mm", "zz"):
        (tmp_path / "tree" / name).mkdir(parents=True)
        (tmp_path / "tree" / name / "x.go").write_text(f"package {name}\n", encoding="utf-8")
    real_walk = Path.walk
    visited: list[str] = []

    def reversed_walk(self: Path, *args: object, **kwargs: object) -> object:
        for root, dirs, files in real_walk(self, *args, **kwargs):  # type: ignore[arg-type]
            dirs.sort(reverse=True)
            yield root, dirs, files
            visited.append(root.name)

    monkeypatch.setattr(Path, "walk", reversed_walk)
    IngestionPipeline(extractors([])).run(str(tmp_path / "tree"))
    assert visited == ["tree", "aa", "mm", "zz"]


RECEIVERS = {
    "store/store.go": (
        "package store\n"
        "type Store struct{}\n"
        "func Open() (*Store, error) { return nil, nil }\n"
        "func (s *Store) Save() {}\n"
        "func (s *Store) Query() *Rows { return nil }\n"
        "type Rows struct{}\n"
        "func (r *Rows) Next() {}\n"
    ),
    "server/server.go": (
        "package server\n"
        'import (\n\t"sync"\n\t"example.com/app/store"\n)\n'
        "type Server struct {\n\tdb *store.Store\n\tmu sync.Mutex\n}\n"
        "func New() *Server { return &Server{} }\n"
        "func (s *Server) Parser() *Parser { return nil }\n"
        "type Parser struct{}\n"
        "func (p *Parser) Parse() *store.Rows { return nil }\n"
        "func (s *Server) Start() {\n"
        "\ts.db.Save()\n"
        "\ts.mu.Lock()\n"
        "\tdb, err := store.Open()\n"
        "\tdb.Save()\n"
        "\terr.Error()\n"
        "\tp := s.Parser()\n"
        "\trows := p.Parse()\n"
        "\trows.Next()\n"
        "}\n"
        "func Run() {\n"
        "\tsrv := New()\n"
        "\tsrv.Start()\n"
        "}\n"
    ),
}


def _receivers() -> tuple[set[str], dict[str, Node]]:
    edges, nodes = _go_graph(RECEIVERS, {"path": "example.com/app"})
    calls = _calls_from(edges, "server.Server.Start") | _calls_from(edges, "server.Run")
    return calls, nodes


def test_a_field_s_declared_type_resolves_its_method_across_packages() -> None:
    """#608: `s.db.Save()` with `db *store.Store` — the field's type from another package.

    Checked on the field call's own edge: `db.Save()` below reaches the same method
    through a call's result, and would hide this one staying unresolved.
    """
    edges, _ = _go_graph(RECEIVERS, {"path": "example.com/app"})
    through_field = [e.target for e in edges if "raw_call:self.db.Save" in e.id]
    assert through_field == ["store.Store.Save"]


def test_a_field_of_a_stdlib_type_is_a_stdlib_call() -> None:
    calls, nodes = _receivers()
    assert "sync.Mutex.Lock" in calls
    assert nodes["sync.Mutex.Lock"].namespace is NodeNamespace.STDLIB


def test_a_call_s_results_type_the_variables_it_assigns() -> None:
    """#608: `db, err := store.Open()`; `srv := New()`."""
    calls, nodes = _receivers()
    assert "store.Store.Save" in calls
    assert "server.Server.Start" in calls
    assert "error.Error" in calls
    assert nodes["error.Error"].namespace is NodeNamespace.STDLIB


def test_a_method_called_on_another_call_s_result_resolves() -> None:
    """#608: `p := s.Parser(); rows := p.Parse(); rows.Next()` — two hops of results."""
    calls, _ = _receivers()
    assert "store.Rows.Next" in calls


def test_err_from_two_calls_is_still_an_error() -> None:
    """#608 delta review: two markers that both resolve to `error` keep `err` typed."""
    files = {
        "a/a.go": (
            "package a\n"
            "func Open() (int, error) { return 0, nil }\n"
            "func Stat() (int, error) { return 0, nil }\n"
            "func Run() {\n"
            "\t_, err := Open()\n"
            "\tif _, err := Stat(); err != nil { err.Error() }\n"
            "\terr.Error()\n"
            "}\n"
        )
    }
    edges, _ = _go_graph(files, {"path": "example.com/app"})
    assert "error.Error" in _calls_from(edges, "a.Run")


def test_sibling_blocks_never_lend_each_other_a_type() -> None:
    """#608 delta review: the second `b.Run()` is RB's or nothing — never RA's."""
    files = {
        "a/a.go": (
            "package a\n"
            "type A struct{}\nfunc (x *A) Get() *RA { return nil }\n"
            "type RA struct{}\nfunc (r *RA) Run() {}\n"
            "type B struct{}\nfunc NewB() *B { return nil }\nfunc (x *B) Get() *RB { return nil }\n"
            "type RB struct{}\nfunc (r *RB) Run() {}\n"
            "func Use() {\n"
            "\t{ a := &A{}; b := a.Get(); b.Run() }\n"
            "\t{ a := NewB(); b := a.Get(); b.Run() }\n"
            "}\n"
        )
    }
    edges, _ = _go_graph(files, {"path": "example.com/app"})
    assert "a.RA.Run" not in _calls_from(edges, "a.Use")


def test_types_that_disagree_resolve_to_neither() -> None:
    """#608 review I1: `ty` is an ArrayType in one block and an ObjectType in another."""
    files = {
        "a/a.go": (
            "package a\n"
            "type ArrayType struct{}\nfunc (t *ArrayType) Merge() {}\n"
            "type ObjectType struct{}\nfunc (t *ObjectType) Merge() {}\n"
            "func NewObjectType() *ObjectType { return nil }\n"
            "func Use() {\n"
            "\t{ ty := &ArrayType{}; ty.Merge() }\n"
            "\t{ ty := NewObjectType(); ty.Merge() }\n"
            "}\n"
        )
    }
    edges, _ = _go_graph(files, {"path": "example.com/app"})
    calls = _calls_from(edges, "a.Use")
    assert "a.ArrayType.Merge" not in calls
    assert "a.ObjectType.Merge" not in calls


def test_a_parameter_of_type_error_resolves_its_method() -> None:
    """#608 delta review: `func f(err error) { err.Error() }` (as actionlint's YAML handler)."""
    files = {"a/a.go": "package a\nfunc F(err error) {\n\terr.Error()\n}\n"}
    edges, nodes = _go_graph(files, {"path": "example.com/app"})
    assert "error.Error" in _calls_from(edges, "a.F")
    assert nodes["error.Error"].namespace is NodeNamespace.STDLIB
