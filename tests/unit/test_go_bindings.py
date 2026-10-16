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
