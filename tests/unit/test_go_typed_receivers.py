"""Calls through a receiver whose type is known: promotion and field hops (#643 A1-A4)."""

from pathlib import Path

from cgis.core.models import Edge, EdgeType
from cgis.languages.go.extractor import GoExtractor
from cgis.pipeline import IngestionPipeline
from cgis.resolver.engine import ResolverEngine
from cgis.storage.sqlite_store import SQLiteStore

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


def test_a_field_of_the_same_name_shadows_a_promoted_method() -> None:
    """`s.Log()` calls the func field, not the embedded type's Log (one namespace per depth)."""
    code = (
        "package app\n"
        "type Inner struct{}\n"
        "func (i *Inner) Log() {}\n"
        "type S struct {\n"
        "\tInner\n"
        "\tLog func()\n"
        "}\n"
        "func Use(s *S) { s.Log() }\n"
    )
    assert all(not t.startswith("app.") for t in _resolve({"app/a.go": code}, "app.Use"))


def test_a_shallower_field_shadows_a_deeper_method() -> None:
    code = (
        "package app\n"
        "type A struct{ M func() }\n"
        "type C struct{}\n"
        "func (c *C) M() {}\n"
        "type B struct{ C }\n"
        "type S struct {\n"
        "\tA\n"
        "\tB\n"
        "}\n"
        "func Use(s *S) { s.M() }\n"
    )
    assert all(not t.startswith("app.") for t in _resolve({"app/a.go": code}, "app.Use"))


def test_an_embedding_cycle_ends() -> None:
    code = (
        "package app\n"
        "type A struct{ *B }\n"
        "type B struct{ *A }\n"
        "func (b *B) M() {}\n"
        "func Missing(a *A) { a.Missing() }\n"
        "func Found(a *A) { a.M() }\n"
    )
    assert all(not t.startswith("app.") for t in _resolve({"app/a.go": code}, "app.Missing"))
    assert _resolve({"app/a.go": code}, "app.Found") == {"app.B.M"}


def test_an_embedded_type_s_name_shadows_a_deeper_method() -> None:
    """An embedded `*Log` is a field named Log: `s.Log()` is not Inner's method."""
    code = (
        "package app\n"
        "type Log struct{}\n"
        "type Inner struct{}\n"
        "func (i *Inner) Log() {}\n"
        "type Mid struct{ Inner }\n"
        "type S struct {\n"
        "\tMid\n"
        "\t*Log\n"
        "}\n"
        "func Use(s *S) { s.Log() }\n"
    )
    assert all(not t.startswith("app.") for t in _resolve({"app/a.go": code}, "app.Use"))


def test_a_shallower_untyped_field_shadows_a_deeper_typed_one() -> None:
    """`X struct{...}` has no named type, yet it is the X that `s.X` reads."""
    code = (
        "package app\n"
        "type Foo struct{}\n"
        "func (f *Foo) Bar() {}\n"
        "type Foo2 struct{}\n"
        "func (f *Foo2) Bar() {}\n"
        "type Inner struct{ X *Foo2 }\n"
        "type S struct {\n"
        "\tInner\n"
        "\tX struct{ *Foo }\n"
        "}\n"
        "func Use(s *S) { s.X.Bar() }\n"
    )
    assert "app.Foo2.Bar" not in _resolve({"app/a.go": code}, "app.Use")


def test_an_incremental_ingest_sees_a_new_shadowing_field_in_another_file(tmp_path: Path) -> None:
    """The field lives in a.go, the call in unchanged b.go: `fields` must cross files."""
    root = tmp_path / "repo"
    (root / "app").mkdir(parents=True)
    (root / "go.mod").write_text("module example.com/m\n\ngo 1.22\n")
    a = root / "app" / "a.go"
    a.write_text(
        "package app\ntype Inner struct{}\nfunc (i *Inner) Log() {}\ntype S struct{ Inner }\n"
    )
    (root / "app" / "b.go").write_text("package app\nfunc Use(s *S) { s.Log() }\n")
    db = str(tmp_path / "g.db")

    def calls() -> set[str]:
        with SQLiteStore(db) as store:
            IngestionPipeline({".go": GoExtractor()}).run(str(root), store=store)
            edges = store.get_edges_of_type(EdgeType.CALLS)
            return {e.target for e in edges if e.source == "app.Use"}

    assert calls() == {"app.Inner.Log"}
    a.write_text(
        "package app\ntype Inner struct{}\nfunc (i *Inner) Log() {}\n"
        "type S struct {\n\tInner\n\tLog func()\n}\n"
    )
    assert "app.Inner.Log" not in calls()


DECLARES_ONCE = (
    'package app\nimport "sync"\ntype C struct{ once sync.Once }\nfunc (c *C) init() {}\n'
)


def _calls_from(files: dict[str, str], source: str) -> dict[str, float]:
    nodes, edges = [], []
    for path, code in files.items():
        n, e = GoExtractor().parse(code, path)
        nodes += n
        edges += e
    state: dict[str, object] = {"go_module": {"path": "example.com/app"}}
    resolved, _ = ResolverEngine(nodes, edges, project_state=state).resolve()
    return {
        e.target: e.confidence for e in resolved if e.type is EdgeType.CALLS and e.source == source
    }


def test_a_field_type_resolves_through_the_declaring_file_s_imports_on_the_receiver() -> None:
    """b.go does not import sync; the field's file does, and that decides (#685)."""
    files = {
        "app/a.go": DECLARES_ONCE,
        "app/b.go": "package app\nfunc (c *C) B() { c.once.Do(c.init) }\n",
    }
    assert _calls_from(files, "app.C.B").get("sync.Once.Do") == 1.0


def test_a_field_type_resolves_through_the_declaring_file_s_imports_on_a_parameter() -> None:
    """The same call through a typed parameter, not the receiver (#685)."""
    files = {
        "app/a.go": DECLARES_ONCE,
        "app/b.go": "package app\nfunc Use(c *C) { c.once.Do(c.init) }\n",
    }
    assert _calls_from(files, "app.Use").get("sync.Once.Do") == 1.0


def test_a_field_chain_resolves_through_the_last_hop_s_file() -> None:
    """`o.c.once.Do`: the type holding `once` is declared in a.go, the outer one in b.go (#685)."""
    files = {
        "app/a.go": DECLARES_ONCE,
        "app/b.go": (
            "package app\ntype O struct{ c C }\nfunc (o *O) B() { o.c.once.Do(o.c.init) }\n"
        ),
    }
    assert _calls_from(files, "app.O.B").get("sync.Once.Do") == 1.0


def test_a_package_variable_s_type_resolves_through_its_declaring_file_s_imports() -> None:
    """`once` is declared in a.go, which imports sync; b.go calls it and does not (#688)."""
    files = {
        "app/a.go": 'package app\nimport "sync"\nvar once sync.Once\nfunc setup() {}\n',
        "app/b.go": "package app\nfunc B() { once.Do(setup) }\n",
    }
    assert _calls_from(files, "app.B").get("sync.Once.Do") == 1.0


def test_a_call_result_s_type_resolves_through_the_callee_s_file() -> None:
    """`NewOnce` returns *sync.Once in a.go; b.go holds it and does not import sync (#688)."""
    files = {
        "app/a.go": (
            'package app\nimport "sync"\nfunc NewOnce() *sync.Once { return &sync.Once{} }\n'
            "func setup() {}\n"
        ),
        "app/b.go": "package app\nfunc B() {\n\to := NewOnce()\n\to.Do(setup)\n}\n",
    }
    assert _calls_from(files, "app.B").get("sync.Once.Do") == 1.0


def test_a_field_copied_to_a_local_resolves_through_the_struct_s_file() -> None:
    """`x := c.once` holds a `go-field:` marker that expands through C in a.go (#688)."""
    files = {
        "app/a.go": DECLARES_ONCE,
        "app/b.go": "package app\nfunc B(c *C) {\n\tx := c.once\n\tx.Do(c.init)\n}\n",
    }
    assert _calls_from(files, "app.B").get("sync.Once.Do") == 1.0


def test_a_field_hop_on_an_expanded_library_type_is_kept_as_the_library_s() -> None:
    """`srv.Handler.ServeHTTP`: no declared field to walk on http.Server, so the call is
    kept as a library call, classified through a.go, which imports net/http (#688)."""
    files = {
        "app/a.go": 'package app\nimport "net/http"\nvar srv http.Server\n',
        "app/b.go": "package app\nfunc B() { srv.Handler.ServeHTTP(nil, nil) }\n",
    }
    assert _calls_from(files, "app.B").get("net.http.Server.Handler.ServeHTTP") == 1.0


def test_the_origin_is_the_file_that_gave_the_last_step() -> None:
    """`f.Get()`: f is declared in a.go (no sync import), Get returns *sync.Once in c.go;
    the type is read from c.go, the last declaration the expansion went through (#688)."""
    files = {
        "app/a.go": "package app\nvar f F\nfunc setup() {}\n",
        "app/c.go": (
            'package app\nimport "sync"\ntype F struct{}\n'
            "func (F) Get() *sync.Once { return nil }\n"
        ),
        "app/b.go": "package app\nfunc B() {\n\to := f.Get()\n\to.Do(setup)\n}\n",
    }
    assert _calls_from(files, "app.B").get("sync.Once.Do") == 1.0


SHADOWS_STDLIB = (
    'package errors\nimport std "errors"\nvar _ = std.New\ntype E struct{ i I }\ntype I struct{}\n'
)


def test_a_missing_method_on_an_own_type_named_like_an_import_is_no_library_call() -> None:
    """Package `errors` imports the stdlib's: `errors.E` is still its own type, so a
    method it lacks is a phantom, never `errors.E.Missing` in the stdlib (#691 review)."""
    files = {
        "errors/a.go": SHADOWS_STDLIB + "var e E\n",
        "errors/b.go": "package errors\nfunc B() { e.Missing() }\n",
    }
    assert "errors.E.Missing" not in _calls_from(files, "errors.B")


def test_a_missing_method_after_a_field_hop_on_an_own_type_is_no_library_call() -> None:
    """The same through a field hop, which #687's walk keeps as a library call (#691 review)."""
    files = {
        "errors/a.go": SHADOWS_STDLIB + "var e E\n",
        "errors/b.go": "package errors\nfunc B() { e.i.Missing() }\n",
    }
    assert "errors.I.Missing" not in _calls_from(files, "errors.B")


def test_a_library_type_mapped_onto_an_own_type_by_suffix_is_still_the_library_s() -> None:
    """An own package `cdc/regexp` also declares `Regexp`; a stdlib `*regexp.Regexp` call it
    lacks stays the stdlib's (#691: the phantom guard must not read the mapped type)."""
    files = {
        "cdc/regexp/re.go": (
            'package regexp\ntype Regexp struct{}\nfunc (r Regexp) String() string { return "" }\n'
        ),
        "app/a.go": 'package app\nimport "regexp"\nvar re *regexp.Regexp\n',
        "app/b.go": 'package app\nfunc B() bool { return re.MatchString("y") }\n',
    }
    assert _calls_from(files, "app.B").get("regexp.Regexp.MatchString") == 1.0
