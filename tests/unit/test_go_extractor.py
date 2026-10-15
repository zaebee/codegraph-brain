"""The Go extractor: tree-sitter-go AST -> Nodes and Edges (step 7b, Task B3)."""

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.languages.go.extractor import GoExtractor

SERVER = "internal/server/server.go"


def _parse(code: str, path: str = SERVER) -> tuple[dict[str, Node], list[Edge]]:
    nodes, edges = GoExtractor().parse(code, path)
    return {n.id: n for n in nodes}, edges


def _edges(edges: list[Edge], edge_type: EdgeType) -> set[tuple[str, str]]:
    return {(e.source, e.target) for e in edges if e.type is edge_type}


def test_the_file_node_names_its_file_and_declares_its_package_scope() -> None:
    nodes, _ = _parse("package server\n")
    file_node = nodes["internal.server.server_go"]
    assert file_node.type is NodeType.FILE
    assert file_node.metadata["scope"] == "internal.server"
    assert file_node.language == "go"
    assert file_node.is_test is False


def test_functions_and_types_live_in_the_package_scope() -> None:
    code = (
        "package server\n"
        "func Run() {}\n"
        "type Server struct{}\n"
        "type Handler interface{ Serve() }\n"
        "type Celsius float64\n"
    )
    nodes, edges = _parse(code)
    assert nodes["internal.server.Run"].type is NodeType.FUNCTION
    assert nodes["internal.server.Server"].type is NodeType.CLASS
    assert nodes["internal.server.Handler"].type is NodeType.CLASS
    assert nodes["internal.server.Celsius"].type is NodeType.CLASS
    assert nodes["internal.server.Handler.Serve"].type is NodeType.METHOD
    contains = _edges(edges, EdgeType.CONTAINS)
    assert ("internal.server.server_go", "internal.server.Run") in contains
    assert ("internal.server.server_go", "internal.server.Server") in contains
    assert all(n.language == "go" for n in nodes.values())


def test_a_type_alias_is_not_a_type_of_its_own() -> None:
    nodes, _ = _parse("package server\ntype Alias = Server\ntype Server struct{}\n")
    assert "internal.server.Server" in nodes
    assert "internal.server.Alias" not in nodes


def test_methods_hang_under_their_receiver_type() -> None:
    code = (
        "package server\ntype Server struct{}\n"
        "func (s *Server) Start() {}\nfunc (s Server) Stop() {}\n"
    )
    nodes, edges = _parse(code)
    assert nodes["internal.server.Server.Start"].type is NodeType.METHOD
    assert nodes["internal.server.Server.Stop"].type is NodeType.METHOD
    declares = _edges(edges, EdgeType.DECLARES)
    assert ("internal.server.Server", "internal.server.Server.Start") in declares
    assert ("internal.server.Server", "internal.server.Server.Stop") in declares


def test_a_generic_receiver_names_the_type_without_its_arguments() -> None:
    nodes, _ = _parse("package server\nfunc (b Box[T]) Get() T { return b.v }\n")
    assert "internal.server.Box.Get" in nodes


def test_methods_in_another_file_share_the_type() -> None:
    """Review Focus: the type in types.go, its method in server.go — one `<scope>.T`."""
    types, _ = _parse("package server\ntype Server struct{}\n", "internal/server/types.go")
    nodes, edges = _parse("package server\nfunc (s *Server) Start() {}\n")
    assert "internal.server.Server" in types
    assert "internal.server.Server.Start" in nodes
    assert ("internal.server.Server", "internal.server.Server.Start") in _edges(
        edges, EdgeType.DECLARES
    )
    # The type is not in this file, so the file holds the method itself.
    assert ("internal.server.server_go", "internal.server.Server.Start") in _edges(
        edges, EdgeType.CONTAINS
    )


def test_root_package_takes_its_clause_name() -> None:
    nodes, _ = _parse("package main\nfunc main() {}\n", "main.go")
    assert nodes["main_go"].metadata["scope"] == "main"
    assert nodes["main.main"].type is NodeType.FUNCTION


def test_imports_map_aliases_to_dotted_paths() -> None:
    code = (
        "package server\n"
        "import (\n"
        '\t"fmt"\n'
        '\tsrv "example.com/app/internal/server"\n'
        '\t_ "embed"\n'
        '\t. "math"\n'
        '\t"gopkg.in/yaml.v3"\n'
        '\t"github.com/x/y/v2"\n'
        ")\n"
    )
    nodes, edges = _parse(code)
    assert nodes["internal.server.server_go"].metadata["import_map"] == {
        "fmt": "fmt",
        "srv": "example.com.app.internal.server",
        "yaml": "gopkg.in.yaml.v3",
        "y": "github.com.x.y.v2",
    }
    imports = {t for s, t in _edges(edges, EdgeType.IMPORTS)}
    assert {"fmt", "example.com.app.internal.server", "embed", "math"} <= imports


def test_calls_are_raw_and_receiver_calls_are_self_calls() -> None:
    code = (
        "package server\n"
        "type Server struct{}\n"
        "func (s *Server) Start() {\n"
        "\thelper()\n"
        "\tsrv.Run()\n"
        "\ts.helper()\n"
        "\ts.db.Query()\n"
        "\tx := NewThing()\n"
        "\tx.Do()\n"
        "\tfunc() { inner() }()\n"
        "}\n"
    )
    _, edges = _parse(code)
    calls = {t for s, t in _edges(edges, EdgeType.CALLS) if s == "internal.server.Server.Start"}
    assert calls == {
        "raw_call:helper",
        "raw_call:srv.Run",
        "raw_call:self.helper",
        "raw_call:self.db.Query",
        "raw_call:NewThing",
        "raw_call:x.Do",
        "raw_call:inner",
    }
    assert all(e.confidence == 0.1 for e in edges if e.type is EdgeType.CALLS)


def test_embedding_is_extends() -> None:
    code = (
        "package server\n"
        "type Server struct{ Base; *pkg.Other; name string }\n"
        "type Handler interface{ io.Reader; Serve() }\n"
    )
    _, edges = _parse(code)
    assert _edges(edges, EdgeType.EXTENDS) == {
        ("internal.server.Server", "raw_class:Base"),
        ("internal.server.Server", "raw_class:pkg.Other"),
        ("internal.server.Handler", "raw_class:io.Reader"),
    }


def test_local_and_parameter_types_are_recorded_as_fqns() -> None:
    code = (
        "package server\n"
        'import srv "example.com/app/internal/server"\n'
        "func Run(h Handler, c *srv.Config) {\n"
        "\tvar x *Thing\n"
        "\ty := &Thing{}\n"
        "\tz := srv.Thing{}\n"
        "\tn := 3\n"
        "}\n"
    )
    nodes, _ = _parse(code)
    assert nodes["internal.server.Run"].metadata["local_types"] == {
        "h": "internal.server.Handler",
        "c": "example.com.app.internal.server.Config",
        "x": "internal.server.Thing",
        "y": "internal.server.Thing",
        "z": "example.com.app.internal.server.Thing",
    }


def test_builtin_types_are_not_local_types() -> None:
    nodes, _ = _parse("package server\nfunc Run(s string, n int, e error) {}\n")
    assert "local_types" not in nodes["internal.server.Run"].metadata


def test_every_node_of_a_test_file_is_flagged() -> None:
    nodes, _ = _parse(
        "package server\nfunc TestRun(t *testing.T) {}\n", "internal/server/server_test.go"
    )
    assert nodes
    assert all(n.is_test for n in nodes.values())


def test_a_go_file_under_a_tests_directory_is_not_a_test() -> None:
    """Go's rule is `_test.go` alone: the core `tests/` rule does not apply."""
    nodes, _ = _parse("package fixtures\nfunc Load() {}\n", "tests/fixtures/load.go")
    assert "tests.fixtures.Load" in nodes
    assert not any(n.is_test for n in nodes.values())


def test_a_source_root_is_stripped() -> None:
    nodes, _ = GoExtractor(source_roots=["src"]).parse(
        "package server\n", "src/internal/server/server.go"
    )
    assert [n.id for n in nodes] == ["internal.server.server_go"]


def test_an_external_test_package_has_a_scope_of_its_own() -> None:
    """I3: `package server_test` beside `package server` is another package (Go's rule)."""
    code = "package server_test\nfunc Helper() {}\n"
    nodes, _ = _parse(code, "internal/server/helper_test.go")
    assert "internal.server_test.Helper" in nodes
    assert nodes["internal.server.helper_test_go"].metadata["scope"] == "internal.server_test"


def test_every_file_s_init_is_its_own_node() -> None:
    """I3: a package may hold one `init` per file; one FQN would keep only the last."""
    a, _ = _parse("package server\nfunc init() {}\n", "internal/server/a.go")
    b, _ = _parse("package server\nfunc init() {}\n", "internal/server/b.go")
    assert "internal.server.init_a_go" in a
    assert "internal.server.init_b_go" in b
    assert a["internal.server.init_a_go"].name == "init"
