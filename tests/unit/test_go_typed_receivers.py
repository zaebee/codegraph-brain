"""Calls through a receiver whose type is known: promotion and field hops (#643 A1-A4)."""

from cgis.core.models import Edge, EdgeType
from cgis.languages.go.extractor import GoExtractor
from cgis.resolver.engine import ResolverEngine

TYPES = (
    "package app\n"
    "type Logger struct{}\n"
    "func (l *Logger) Print() {}\n"
    "type Base struct{ log *Logger }\n"
    "func (b *Base) Close() {}\n"
    "type Metric struct{}\n"
    "func (m *Metric) Write() {}\n"
    "type Inner struct{ Log *Logger }\n"
    "type Options struct {\n"
    "\tLog   *Logger\n"
    "\tinner Inner\n"
    "}\n"
    "type Server struct {\n"
    "\tBase\n"
    "\t*Metric\n"
    "\topts Options\n"
    "}\n"
)

CALLS = (
    "package app\n"
    "func PromotedOnParam(s *Server) { s.Close() }\n"
    "func PromotedOnLocal() {\n"
    "\tx := &Server{}\n"
    "\tx.Close()\n"
    "}\n"
    "func FieldOfParam(o Options) { o.Log.Print() }\n"
    "func ChainOfParam(s *Server) { s.opts.inner.Log.Print() }\n"
    "func (s *Server) ChainOfReceiver() { s.opts.inner.Log.Print() }\n"
    "func (s *Server) NamedEmbedded() {\n"
    "\ts.Metric.Write()\n"
    "\ts.Base.Close()\n"
    "}\n"
    "func PromotedField(s *Server) { s.log.Print() }\n"
    "func Phantom(s *Server) {\n"
    "\ts.Missing()\n"
    "\ts.opts.Missing()\n"
    "}\n"
)


def _calls() -> list[Edge]:
    nodes, edges = [], []
    for path, code in (("app/types.go", TYPES), ("app/calls.go", CALLS)):
        n, e = GoExtractor().parse(code, path)
        nodes += n
        edges += e
    resolved, _ = ResolverEngine(nodes, edges).resolve()
    return [e for e in resolved if e.type is EdgeType.CALLS]


def _targets(source: str) -> set[str]:
    return {e.target for e in _calls() if e.source == f"app.{source}"}


def test_a_promoted_method_resolves_on_a_typed_parameter() -> None:
    assert _targets("PromotedOnParam") == {"app.Base.Close"}


def test_a_promoted_method_resolves_on_a_typed_local() -> None:
    assert _targets("PromotedOnLocal") == {"app.Base.Close"}


def test_a_field_of_a_parameter_types_its_call() -> None:
    assert _targets("FieldOfParam") == {"app.Logger.Print"}


def test_a_chain_of_fields_from_a_parameter_types_its_call() -> None:
    assert _targets("ChainOfParam") == {"app.Logger.Print"}


def test_a_chain_of_fields_from_the_receiver_types_its_call() -> None:
    assert _targets("Server.ChainOfReceiver") == {"app.Logger.Print"}


def test_an_embedded_field_named_explicitly_is_a_field() -> None:
    assert _targets("Server.NamedEmbedded") == {"app.Metric.Write", "app.Base.Close"}


def test_a_field_promoted_from_an_embedded_struct_types_its_call() -> None:
    assert _targets("PromotedField") == {"app.Logger.Print"}


def test_a_method_no_type_in_the_chain_has_stays_unresolved() -> None:
    """An internal type with no such method is a phantom: no edge is invented (#183)."""
    targets = {e.target for e in _calls() if e.source == "app.Phantom"}
    assert targets.isdisjoint({"app.Server.Missing", "app.Options.Missing"})
    assert all(not t.startswith("app.") for t in targets)


def _resolve(files: dict[str, str], source: str) -> set[str]:
    nodes, edges = [], []
    for path, code in files.items():
        n, e = GoExtractor().parse(code, path)
        nodes += n
        edges += e
    state: dict[str, object] = {"go_module": {"path": "example.com/app"}}
    resolved, _ = ResolverEngine(nodes, edges, project_state=state).resolve()
    return {e.target for e in resolved if e.type is EdgeType.CALLS and e.source == source}


DEPTH = (
    "package app\n"
    "type C struct{}\n"
    "func (c *C) M() {}\n"
    "type A struct{ C }\n"
    "type B struct{}\n"
    "func (b *B) M() {}\n"
    "type S struct {\n"
    "\tA\n"
    "\tB\n"
    "}\n"
    "func Use(s *S) { s.M() }\n"
    "func (s *S) Self() { s.M() }\n"
)


def test_promotion_takes_the_shallowest_method_not_the_first_branch() -> None:
    """Go's rule: B.M at depth 1 beats C.M at depth 2 under the first embedding."""
    assert _resolve({"app/a.go": DEPTH}, "app.Use") == {"app.B.M"}
    assert _resolve({"app/a.go": DEPTH}, "app.S.Self") == {"app.B.M"}


def test_two_methods_at_the_same_depth_resolve_to_nothing() -> None:
    """Go rejects an ambiguous selector; a guess would be a confident wrong edge."""
    code = (
        "package app\n"
        "type A struct{}\n"
        "func (a *A) M() {}\n"
        "type B struct{}\n"
        "func (b *B) M() {}\n"
        "type S struct {\n"
        "\tA\n"
        "\tB\n"
        "}\n"
        "func Use(s *S) { s.M() }\n"
    )
    assert all(not t.startswith("app.") for t in _resolve({"app/a.go": code}, "app.Use"))


def test_a_field_hop_takes_the_shallowest_field() -> None:
    code = (
        "package app\n"
        "type L1 struct{}\n"
        "func (l *L1) P() {}\n"
        "type L2 struct{}\n"
        "func (l *L2) P() {}\n"
        "type Deep struct{ log *L1 }\n"
        "type Mid struct{ Deep }\n"
        "type Shallow struct{ log *L2 }\n"
        "type S struct {\n"
        "\tMid\n"
        "\tShallow\n"
        "}\n"
        "func Use(s *S) { s.log.P() }\n"
    )
    assert _resolve({"app/a.go": code}, "app.Use") == {"app.L2.P"}


def test_an_embedded_type_from_another_package_is_named_without_its_package() -> None:
    files = {
        "other/thing.go": "package other\ntype Thing struct{}\nfunc (t *Thing) Do() {}\n",
        "app/a.go": (
            "package app\n"
            'import "example.com/app/other"\n'
            "type S struct{ other.Thing }\n"
            "func Use(s *S) { s.Thing.Do() }\n"
        ),
    }
    assert _resolve(files, "app.Use") == {"other.Thing.Do"}
