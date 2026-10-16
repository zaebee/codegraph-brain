"""The Go extractor: tree-sitter-go AST -> Nodes and Edges (step 7b, Task B3)."""

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.languages.go._syntax import ANY_OF, base_type, selector_chain
from cgis.languages.go.extractor import GoExtractor, _embedded, _File, _method

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


def test_builtin_types_are_not_local_types_but_error_is() -> None:
    """`error` is the one predeclared type with a method to resolve: `err.Error()` (#608)."""
    nodes, _ = _parse("package server\nfunc Run(s string, n int, e error) {}\n")
    assert nodes["internal.server.Run"].metadata["local_types"] == {"e": "error"}


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


def test_a_parenthesised_type_block_declares_every_type() -> None:
    """`type ( A struct{}; B interface{} )`: tree-sitter-go keeps the specs direct (PR #607)."""
    code = "package p\ntype (\n\tA struct{ Base }\n\tB interface{ M() }\n)\nfunc (a *A) Run() {}\n"
    nodes, edges = _parse(code, "p/x.go")
    assert {"p.A", "p.B", "p.B.M", "p.A.Run"} <= set(nodes)
    assert ("p.A", "raw_class:Base") in _edges(edges, EdgeType.EXTENDS)
    # A.Run's type is declared in this very file, inside the block.
    assert ("p.x_go", "p.A.Run") not in _edges(edges, EdgeType.CONTAINS)


class _Broken:
    """A syntax node as tree-sitter leaves one in code still being typed: fields missing."""

    def __init__(self, node_type: str) -> None:
        self.type = node_type
        self.named_children: list[object] = []
        self.text = b""

    def child_by_field_name(self, _name: str) -> None:
        return None


def test_a_selector_with_no_operand_reads_as_no_name() -> None:
    """It recursed into itself forever.

    Hypothetical: tree-sitter-go fills gaps with MISSING nodes, so this is a guard.
    """
    assert selector_chain(_Broken("selector_expression")) == []  # type: ignore[arg-type]


def test_a_pointer_with_nothing_under_it_names_no_type() -> None:
    """It raised IndexError. A defensive guard: no malformed input tried produced this shape."""
    assert base_type(_Broken("pointer_type")) is None  # type: ignore[arg-type]


class _Fake:
    """A syntax node with chosen children and fields, for shapes the parser can leave."""

    def __init__(
        self, node_type: str, children: list["_Fake"] | None = None, **fields: "_Fake"
    ) -> None:
        self.type = node_type
        self.children = children or []
        self.named_children = self.children
        self.text = b""
        self.start_point = self.end_point = (0, 0)
        self._fields = fields

    def child_by_field_name(self, name: str) -> "_Fake | None":
        return self._fields.get(name)

    def children_by_field_name(self, name: str) -> list["_Fake"]:
        found = self._fields.get(name)
        return [found] if found else []


def test_an_empty_interface_element_embeds_nothing() -> None:
    """`_embedded` indexed `named_children[0]` of an element with none (hypothetical shape)."""
    assert _embedded(_Fake("interface_type", [_Fake("type_elem")])) == []  # type: ignore[arg-type]


def test_a_receiver_with_no_type_declares_no_method() -> None:
    """No receiver type: no `scope..Start` method (hypothetical shape, defensive guard)."""
    ctx = _File(
        path="p/x.go",
        node_id="p.x_go",
        directory="p",
        scope="p",
        is_test=False,
        import_map={},
        own_types=frozenset(),
    )
    receiver = _Fake("parameter_list", [_Fake("parameter_declaration")])
    method = _Fake("method_declaration", receiver=receiver, name=_Fake("field_identifier"))
    nodes: list[Node] = []
    edges: list[Edge] = []
    _method(ctx, method, nodes, edges)  # type: ignore[arg-type]
    assert nodes == []
    assert edges == []


def test_struct_fields_are_the_type_s_self_types() -> None:
    """#608: `s.db.Save()` needs the field's type, as Python's `self.x: T` records it."""
    code = (
        "package server\n"
        'import "example.com/app/internal/store"\n'
        "type Server struct {\n"
        "\tBase\n"
        "\tdb *store.Store\n"
        "\tcache, spare Cache\n"
        "\tname string\n"
        "\titems []Item\n"
        "}\n"
    )
    nodes, _ = _parse(code)
    assert nodes["internal.server.Server"].metadata["self_types"] == {
        "Base": "internal.server.Base",  # embedded: named after its type (#643 A4)
        "db": "example.com.app.internal.store.Store",
        "cache": "internal.server.Cache",
        "spare": "internal.server.Cache",
        "items": "go-elem:seq:internal.server.Item",  # a container's element (#643 B)
    }


def test_a_function_records_its_result_types() -> None:
    """#608: what `x := f()` gets; predeclared `error` kept by name so `err.Error()` classifies."""
    code = (
        "package server\n"
        'import "example.com/app/internal/store"\n'
        "func New() *Server { return nil }\n"
        "func Open() (*store.Store, error) { return nil, nil }\n"
        "func Named() (n int, err error) { return }\n"
        "func Nothing() {}\n"
    )
    nodes, _ = _parse(code)
    assert nodes["internal.server.New"].metadata["returns"] == ["internal.server.Server"]
    assert nodes["internal.server.Open"].metadata["returns"] == [
        "example.com.app.internal.store.Store",
        "error",
    ]
    assert nodes["internal.server.Named"].metadata["returns"] == ["int", "error"]
    assert "returns" not in nodes["internal.server.Nothing"].metadata


def test_a_variable_assigned_a_call_records_which_result_it_holds() -> None:
    """#608: `x := f()` -> a marker the Go type normalizer turns into f's result type."""
    code = (
        "package server\n"
        'import "example.com/app/internal/store"\n'
        "type Server struct{}\n"
        "func (s *Server) Run() {\n"
        "\ta := New()\n"
        "\tdb, err := store.Open()\n"
        "\tp := s.Parser()\n"
        "\tr := p.Parse()\n"
        "\tvar q = New()\n"
        "\t_ = len(a)\n"
        "}\n"
    )
    nodes, _ = _parse(code)
    types = nodes["internal.server.Server.Run"].metadata["local_types"]
    assert types["a"] == "go-returns:0:internal.server.New"
    assert types["db"] == "go-returns:0:example.com.app.internal.store.Open"
    assert types["err"] == "go-returns:1:example.com.app.internal.store.Open"
    assert types["p"] == "go-returns:0:internal.server.Server.Parser"
    assert types["r"] == "go-returns:0:go-returns:0:internal.server.Server.Parser|Parse"
    assert types["q"] == "go-returns:0:internal.server.New"
    assert "_" not in types


def _local_types(body: str) -> dict[str, str]:
    """The local types of `func Run()` with `body`, in package `server`."""
    nodes, _ = _parse(f"package server\nfunc Run() {{\n{body}}}\n")
    types: dict[str, str] = nodes["internal.server.Run"].metadata.get("local_types", {})
    return types


def test_a_name_bound_to_different_types_holds_all_of_them() -> None:
    """#608 review: one dict per function; the resolver keeps a type only if all agree."""
    types = _local_types("\t{ ty := &ArrayType{} }\n\t{ ty := NewObjectType() }\n")
    assert (
        types["ty"] == "go-any:internal.server.ArrayType;go-returns:0:internal.server.NewObjectType"
    )


def test_a_name_bound_once_to_something_untyped_stays_untyped() -> None:
    """#608 delta review: `x := m[k]` in one block must not inherit `&A{}` from another.

    Since #643 an index, a range or a receive records a marker rather than nothing,
    so the name holds `go-any:` of both, which resolves only if every binding agrees.
    """
    for body in (
        "\t{ x := m[k] }\n\t{ x := &A{} }\n",
        "\t{ x := &A{} }\n\t{ x := m[k] }\n",
        "\t{ a := &A{}; b := a.Get() }\n\t{ a := NewB(); b := a.Get() }\n",
        "\tx := &A{}\n\tfor _, x := range xs { _ = x }\n",
        "\tx := &A{}\n\tswitch x := v.(type) { default: _ = x }\n",
        "\tx := &A{}\n\tgo func(x int) {}(1)\n",
    ):
        types = _local_types(body)
        assert types.get("x", ANY_OF).startswith(ANY_OF), body
        assert types.get("b", ANY_OF).startswith(ANY_OF), body


def test_a_closure_parameter_of_the_same_type_keeps_the_name() -> None:
    """`t.Run(name, func(t *testing.T) {...})`: the closure's `t` is the outer `t`'s type."""
    nodes, _ = _parse(
        'package server\nimport "testing"\n'
        'func TestRun(t *testing.T) {\n\tt.Run("x", func(t *testing.T) { t.Fatal() })\n}\n',
        "internal/server/server_test.go",
    )
    assert nodes["internal.server.TestRun"].metadata["local_types"] == {"t": "testing.T"}


def test_the_same_type_bound_twice_is_kept() -> None:
    assert _local_types("\t{ t := &T{} }\n\t{ t := &T{} }\n")["t"] == "internal.server.T"


def test_the_blank_identifier_is_never_typed() -> None:
    assert "_" not in _local_types("\t_ := New()\n\t_, err := Open()\n\t_ = err\n")


def test_a_local_shadowing_an_import_is_the_local() -> None:
    """#608 review M1: `store := New(); x := store.Open()` calls the local's method."""
    code = (
        "package server\n"
        'import "example.com/app/store"\n'
        "func Run() {\n\tstore := New()\n\tx := store.Open()\n\t_ = x\n}\n"
    )
    nodes, _ = _parse(code)
    types = nodes["internal.server.Run"].metadata["local_types"]
    assert types["x"] == "go-returns:0:go-returns:0:internal.server.New|Open"


def test_a_chain_of_results_stops_at_a_bounded_depth() -> None:
    """#608 review I2: each marker inlined its predecessor — quadratic, then RecursionError."""
    hops = "".join(f"\tx{i + 1} := x{i}.Next()\n" for i in range(50))
    types = _local_types(f"\tx0 := New()\n{hops}")
    assert types["x8"].count("|") == 8  # the last hop kept
    assert "x9" not in types
    assert max(len(v) for v in types.values()) < 400


def test_an_error_declared_by_name_keeps_err_typed() -> None:
    """#608 delta review: `var err error`, then `_, err = F()`, then `err.Error()`."""
    types = _local_types("\tvar err error\n\t{ _, err := Open(); _ = err }\n")
    assert types["err"].startswith("go-any:")
    assert _local_types("\tvar err error\n")["err"] == "error"


def test_named_results_bind_like_parameters() -> None:
    """#608 delta review: `func F() (x *A)` — a block's `x := &B{}` must not lend B to `x`."""
    nodes, _ = _parse(
        "package server\nfunc F() (x *A, err error) {\n\t{ x := &B{}; _ = x }\n\treturn\n}\n"
    )
    types = nodes["internal.server.F"].metadata["local_types"]
    assert types["x"] == "go-any:internal.server.A;internal.server.B"
    assert types["err"] == "error"


def test_a_select_receive_binds_an_untyped_name() -> None:
    """#608 delta review: `case v := <-ch` is a binding the walk must see."""
    types = _local_types("\t{ v := &A{}; _ = v }\n\tselect {\n\tcase v := <-ch:\n\t\t_ = v\n\t}\n")
    assert types.get("v", ANY_OF).startswith(ANY_OF)
