"""The Go pack's index facts, classification and resolution, end to end (step 7b, Task B4)."""

from cgis.core.models import Edge, EdgeType, Node, NodeNamespace
from cgis.languages.go.extractor import GoExtractor
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
