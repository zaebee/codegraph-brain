"""Bindings whose type comes from a container, an assertion or a package variable (#643 B)."""

from cgis.core.models import EdgeType
from cgis.languages.go.extractor import GoExtractor
from cgis.resolver.engine import ResolverEngine

TYPES = (
    "package app\n"
    "type T struct{}\n"
    "func (t *T) M() {}\n"
    "type K struct{}\n"
    "func (k K) M() {}\n"
    "type S struct {\n"
    "\tch    chan *T\n"
    "\tpeers []*T\n"
    "\tbyKey map[string]*T\n"
    "}\n"
    "func List() []*T { return nil }\n"
    "type Snap interface{ Persist() }\n"
    "type FSM interface {\n"
    "\tSnapshot() (Snap, error)\n"
    "}\n"
    "type Reg interface{ Register() }\n"
    "type registry struct{}\n"
    "func (r *registry) Register() {}\n"
    "func (r *registry) Own() {}\n"
    "func NewRegistry() *registry { return nil }\n"
    "type v2 struct{}\n"
    "func (v v2) New() {}\n"
    "type Store interface {\n"
    "\tCreate() (*T, error)\n"
    "}\n"
    "type Holder struct{ store Store }\n"
)

GLOBALS = (
    "package app\n"
    "var Default Reg = NewRegistry()\n"
    "var Fresh = NewRegistry()\n"
    "var V2 = v2{}\n"
    "var (\n"
    "\tA, B = &T{}, K{}\n"
    ")\n"
)

CALLS = (
    "package app\n"
    "func Receive(ch chan *T) {\n"
    "\tx := <-ch\n"
    "\tx.M()\n"
    "}\n"
    "func (s *S) Select() {\n"
    "\tselect {\n"
    "\tcase f := <-s.ch:\n"
    "\t\tf.M()\n"
    "\t}\n"
    "}\n"
    "func RangeSlice(xs []*T) {\n"
    "\tfor _, v := range xs {\n"
    "\t\tv.M()\n"
    "\t}\n"
    "}\n"
    "func RangeChan(ch <-chan *T) {\n"
    "\tfor v := range ch {\n"
    "\t\tv.M()\n"
    "\t}\n"
    "}\n"
    "func (s *S) RangeField() {\n"
    "\tfor _, p := range s.peers {\n"
    "\t\tp.M()\n"
    "\t}\n"
    "}\n"
    "func (s *S) RangeMap() {\n"
    "\tfor _, p := range s.byKey {\n"
    "\t\tp.M()\n"
    "\t}\n"
    "}\n"
    "func RangeCall() {\n"
    "\tfor _, t := range List() {\n"
    "\t\tt.M()\n"
    "\t}\n"
    "}\n"
    "func (s *S) Index(k string) {\n"
    "\tx := s.byKey[k]\n"
    "\tx.M()\n"
    "\ty, ok := s.byKey[k]\n"
    "\t_ = ok\n"
    "\ty.M()\n"
    "}\n"
    "func Assert(w any) {\n"
    "\tz := w.(*T)\n"
    "\tz.M()\n"
    "\tk, ok := w.(K)\n"
    "\t_ = ok\n"
    "\tk.M()\n"
    "}\n"
    "func Interface(f FSM) {\n"
    "\ts, _ := f.Snapshot()\n"
    "\ts.Persist()\n"
    "}\n"
    "func Global() {\n"
    "\tDefault.Register()\n"
    "\tFresh.Own()\n"
    "\tV2.New()\n"
    "\tA.M()\n"
    "\tB.M()\n"
    "}\n"
    "func Shadowed() {\n"
    "\tV2 := &T{}\n"
    "\tV2.M()\n"
    "}\n"
    "func (h *Holder) FieldCall() {\n"
    "\tx, _ := h.store.Create()\n"
    "\tx.M()\n"
    "}\n"
    "func Variadic(opts ...*T) {\n"
    "\tfor _, o := range opts {\n"
    "\t\to.M()\n"
    "\t}\n"
    "}\n"
    "func Made() {\n"
    "\tch := make(chan *T)\n"
    "\tfor v := range ch {\n"
    "\t\tv.M()\n"
    "\t}\n"
    "\tp := new(K)\n"
    "\tp.M()\n"
    "}\n"
    "func Keys(n map[*T]bool) {\n"
    "\tfor k := range n {\n"
    "\t\tk.M()\n"
    "\t}\n"
    "}\n"
    "func RangeIndex(xs []*T) {\n"
    "\tfor i := range xs {\n"
    "\t\t_ = i\n"
    "\t}\n"
    "}\n"
)


def _targets(source: str) -> set[str]:
    nodes, edges = [], []
    for path, code in (
        ("app/types.go", TYPES),
        ("app/globals.go", GLOBALS),
        ("app/calls.go", CALLS),
    ):
        n, e = GoExtractor().parse(code, path)
        nodes += n
        edges += e
    resolved, _ = ResolverEngine(nodes, edges).resolve()
    return {e.target for e in resolved if e.type is EdgeType.CALLS and e.source == f"app.{source}"}


def test_a_value_received_from_a_channel_parameter_has_its_element_type() -> None:
    assert "app.T.M" in _targets("Receive")


def test_a_select_case_receive_from_a_field_has_its_element_type() -> None:
    assert _targets("S.Select") == {"app.T.M"}


def test_a_range_value_over_a_slice_has_its_element_type() -> None:
    assert _targets("RangeSlice") == {"app.T.M"}


def test_the_one_range_variable_over_a_channel_is_its_element() -> None:
    assert _targets("RangeChan") == {"app.T.M"}


def test_a_range_over_a_field_or_a_map_types_its_value() -> None:
    assert _targets("S.RangeField") == {"app.T.M"}
    assert _targets("S.RangeMap") == {"app.T.M"}


def test_a_range_over_a_call_s_result_types_its_value() -> None:
    assert "app.T.M" in _targets("RangeCall")


def test_an_index_into_a_map_has_its_value_type() -> None:
    assert _targets("S.Index") == {"app.T.M"}


def test_a_type_assertion_binds_the_asserted_type() -> None:
    assert _targets("Assert") == {"app.T.M", "app.K.M"}


def test_an_interface_method_s_result_types_the_variable() -> None:
    assert "app.Snap.Persist" in _targets("Interface")


def test_a_package_variable_types_its_calls() -> None:
    targets = _targets("Global")
    assert {"app.Reg.Register", "app.registry.Own", "app.v2.New", "app.T.M", "app.K.M"} <= targets


def test_a_local_shadows_a_package_variable() -> None:
    assert _targets("Shadowed") == {"app.T.M"}


def test_a_method_called_through_a_field_chain_types_its_result() -> None:
    assert _targets("Holder.FieldCall") >= {"app.T.M", "app.Store.Create"}


def test_a_variadic_parameter_is_a_slice_of_its_type() -> None:
    assert _targets("Variadic") == {"app.T.M"}


def test_make_and_new_name_their_type() -> None:
    assert {"app.T.M", "app.K.M"} <= _targets("Made")


def test_the_first_range_variable_over_a_map_is_its_key() -> None:
    assert _targets("Keys") == {"app.T.M"}


def _resolve(code: str, source: str) -> set[str]:
    nodes, edges = GoExtractor().parse(code, "app/a.go")
    resolved, _ = ResolverEngine(nodes, edges).resolve()
    return {e.target for e in resolved if e.type is EdgeType.CALLS and e.source == f"app.{source}"}


_T = "package app\ntype T struct{}\nfunc (t *T) M() {}\ntype K struct{}\nfunc (k *K) M() {}\n"


def test_a_range_index_over_a_slice_is_not_its_element() -> None:
    code = _T + "func F(xs []*T) {\n\tfor i := range xs {\n\t\ti.M()\n\t}\n}\n"
    assert "app.T.M" not in _resolve(code, "F")


def test_a_received_field_promoted_from_an_embedding_types_the_value() -> None:
    code = _T + (
        "type Inner struct{ ch chan *T }\n"
        "type S struct{ Inner }\n"
        "func (s *S) F() {\n\tx := <-s.ch\n\tx.M()\n}\n"
    )
    assert _resolve(code, "S.F") == {"app.T.M"}


def test_two_promoted_fields_of_one_name_at_one_depth_type_nothing() -> None:
    code = _T + (
        "type A struct{ ch chan *T }\n"
        "type B struct{ ch chan *K }\n"
        "type S struct {\n\tA\n\tB\n}\n"
        "func (s *S) F() {\n\tx := <-s.ch\n\tx.M()\n}\n"
    )
    assert _resolve(code, "S.F").isdisjoint({"app.T.M", "app.K.M"})


def test_package_variables_initialised_from_each_other_end() -> None:
    """Go rejects the cycle; the resolver must still stop expanding it."""
    code = _T + "var A = B\nvar B = A\nfunc F() { A.M() }\n"
    assert "app.T.M" not in _resolve(code, "F")


def test_the_comma_ok_flag_takes_no_type() -> None:
    code = _T + 'func F(m map[string]*T) {\n\tv, ok := m["k"]\n\tok.M()\n\tv.M()\n}\n'
    nodes, _ = GoExtractor().parse(code, "app/a.go")
    local = next(n for n in nodes if n.id == "app.F").metadata["local_types"]
    assert "ok" not in local
    assert "v" in local


def test_a_local_named_new_is_not_the_builtin() -> None:
    code = _T + "func F(new func(any) *T) {\n\tp := new(K{})\n\tp.M()\n}\n"
    assert "app.K.M" not in _resolve(code, "F")


_SCOPED = (
    "package app\n"
    'import (\n\t"log"\n\t"strings"\n)\n'
    "type Logger struct{}\n"
    "func (l *Logger) Printf(f string, a ...any) {}\n"
    "func (l *Logger) Println(a ...any) {}\n"
    "type Builder struct{}\n"
    "func (b *Builder) Contains(s, t string) bool { return false }\n"
    "type A struct{}\n"
    "func (a *A) Run() {}\n"
    "type B struct{}\n"
    "func (b *B) Run() {}\n"
    "var it *B\n"
    "func Loop(loggers []*Logger) {\n"
    '\tfor _, log := range loggers {\n\t\tlog.Println("x")\n\t}\n'
    '\tlog.Printf("after")\n'
    "}\n"
    "func Recv(ch chan *Logger) {\n"
    "\tselect {\n\tcase log := <-ch:\n\t\tlog.Println()\n\tdefault:\n\t}\n"
    '\tlog.Println("std")\n'
    "}\n"
    "func Idx(m map[string]*Builder) {\n"
    '\tif strings := m["a"]; strings != nil {\n\t\t_ = strings.Contains("a", "b")\n\t}\n'
    '\t_ = strings.Contains("a", "b")\n'
    "}\n"
    "func Global(items []*A) {\n"
    "\tfor _, it := range items {\n\t\tit.Run()\n\t}\n"
    "\tit.Run()\n"
    "}\n"
    "func Cases(a chan *A, b chan *B) {\n"
    "\tselect {\n\tcase c := <-a:\n\t\tc.Run()\n\tcase c := <-b:\n\t\tc.Run()\n\t}\n"
    "}\n"
    "func Switch(w any) {\n"
    "\tswitch v := w.(type) {\n\tcase *A:\n\t\tv.Run()\n\tcase *B:\n\t\tv.Run()\n"
    "\tcase *A, *B:\n\t\t_ = v\n\t}\n"
    "}\n"
)


def _scoped_calls(source: str) -> list[str]:
    nodes, edges = GoExtractor().parse(_SCOPED, "app/a.go")
    resolved, _ = ResolverEngine(nodes, edges).resolve()
    calls = [e for e in resolved if e.type is EdgeType.CALLS and e.source == f"app.{source}"]
    return [e.target for e in sorted(calls, key=lambda e: e.id)]


def test_a_range_variable_does_not_type_the_import_it_shadows_after_the_loop() -> None:
    targets = _scoped_calls("Loop")
    assert "app.Logger.Println" in targets
    assert "app.Logger.Printf" not in targets


def test_a_select_case_variable_does_not_type_the_import_after_the_select() -> None:
    assert _scoped_calls("Recv").count("app.Logger.Println") == 1


def test_an_if_initialiser_does_not_type_the_import_after_the_if() -> None:
    assert _scoped_calls("Idx").count("app.Builder.Contains") == 1


def test_a_range_variable_does_not_type_the_package_variable_it_shadows() -> None:
    assert sorted(_scoped_calls("Global")) == ["app.A.Run", "app.B.Run"]


def test_one_name_in_two_select_cases_keeps_each_case_s_type() -> None:
    assert sorted(_scoped_calls("Cases")) == ["app.A.Run", "app.B.Run"]


def test_a_type_switch_alias_takes_its_case_s_type() -> None:
    assert sorted(_scoped_calls("Switch")) == ["app.A.Run", "app.B.Run"]


def _resolve_with_lib(lib: str, app: str) -> set[str]:
    """`app/a.go` importing `example.com/m/lib` as `q`: a resolved import reads `lib.*`."""
    nodes, edges = [], []
    for path, code in (("lib/lib.go", lib), ("app/a.go", app)):
        n, e = GoExtractor().parse(code, path)
        nodes += n
        edges += e
    state: dict[str, object] = {"go_module": {"path": "example.com/m"}}
    resolved, _ = ResolverEngine(nodes, edges, project_state=state).resolve()
    return {e.target for e in resolved if e.source == "app.F"}


_IMPORT_Q = 'package app\nimport q "example.com/m/lib"\n'


def test_a_declaration_s_value_sees_the_name_it_shadows() -> None:
    """`q := q.Get()`: the call is the import's; the name is declared after its value."""
    lib = "package lib\ntype T struct{}\nfunc (t *T) Get() {}\nfunc Get() *T { return nil }\n"
    app = _IMPORT_Q + "func F() {\n\tq := q.Get()\n\tq.Get()\n}\n"
    assert _resolve_with_lib(lib, app) == {"lib.Get", "lib.T.Get"}


def test_an_untyped_local_never_falls_back_to_the_import_it_shadows() -> None:
    """`q := g()` of an unknown result is a local: `q.Run()` is not the package's Run."""
    app = _IMPORT_Q + "func F(g func() int) {\n\tq := g()\n\tq.Run()\n}\n"
    assert "lib.Run" not in _resolve_with_lib("package lib\nfunc Run() {}\n", app)


def test_an_index_through_a_pointer_to_an_array_takes_its_element() -> None:
    code = _T + (
        "func F(parr *[3]*T) {\n\tx := (*parr)[1]\n\tx.M()\n"
        "\tfor _, y := range parr {\n\t\ty.M()\n\t}\n}\n"
    )
    assert _resolve(code, "F") == {"app.T.M"}


def test_a_local_of_no_nameable_type_never_falls_back_to_the_import() -> None:
    """`q := w.(interface{ Run() })` has no named type, yet it hides the import `q`."""
    app = _IMPORT_Q + "func F(w any) {\n\tq := w.(interface{ Run() })\n\tq.Run()\n}\n"
    assert "lib.Run" not in _resolve_with_lib("package lib\nfunc Run() {}\n", app)


def test_a_multi_type_case_alias_has_the_switch_value_s_type() -> None:
    """In `case *A, *B:` and `default:` the alias is the switched value's type, as Go says."""
    code = _T + (
        "type Runner interface{ M() }\n"
        "func F(w Runner) {\n"
        "\tswitch v := w.(type) {\n\tcase *T, *K:\n\t\tv.M()\n\tdefault:\n\t\tv.M()\n\t}\n}\n"
    )
    assert _resolve(code, "F") == {"app.Runner.M"}


def test_a_method_expression_on_a_predeclared_type_records_no_site() -> None:
    code = "package app\nfunc F(e error) { _ = error.Error(e) }\n"
    nodes, _ = GoExtractor().parse(code, "app/a.go")
    assert "site_types" not in next(n for n in nodes if n.id == "app.F").metadata


def test_a_predeclared_type_s_method_expression_is_not_a_sibling_block_s_local() -> None:
    """`{ error := T{} }` then `error.Error(e)`: the call is the predeclared type's."""
    code = (
        'package app\ntype T struct{}\nfunc (t T) Error() string { return "" }\n'
        "func F(e error) {\n\t{\n\t\terror := T{}\n\t\t_ = error\n\t}\n\t_ = error.Error(e)\n}\n"
    )
    assert "app.T.Error" not in _resolve(code, "F")
