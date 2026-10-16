"""Calls on a value no name holds: a call's result, an assertion, an index (#643 C).

Each function's calls in source order; "-" is a call that must stay unresolved
(an edge whose name reaches the coverage count, but no target).
"""

import pytest

from cgis.core.models import Edge, EdgeType
from cgis.languages.go.extractor import GoExtractor
from cgis.resolver.engine import ResolverEngine

APP = (
    "package app\n"
    'import "strings"\n'
    "type T struct{ f *K }\n"
    "func (t *T) M() {}\n"
    "func (t *T) Get() *T { return t }\n"
    "type K struct{}\n"
    "func (k *K) M() {}\n"
    "func (k *K) Next() *T { return nil }\n"
    "type Raft struct{}\n"
    "func (r *Raft) AddVoter() {}\n"
    "type Cluster interface{ Leader() *Raft }\n"
    "type registry struct{}\n"
    "func (r *registry) MustRegister() {}\n"
    "func NewRegistry() *registry { return nil }\n"
    "func newT() *T { return nil }\n"
    "func Len() {}\n"
    "func Result(t *T) { t.Get().M() }\n"
    "func Package() { NewRegistry().MustRegister() }\n"
    "func Interface(c Cluster) { c.Leader().AddVoter() }\n"
    "func Deep(t *T) { t.Get().Get().Get().M() }\n"
    "func Field() { newT().f.M() }\n"
    "func Assert(w any) { w.(*T).M() }\n"
    'func Index(m map[string]*K, s []*T) {\n\tm["k"].M()\n\ts[0].M()\n}\n'
    "func Receive(ch chan *T) { (<-ch).M() }\n"
    "func Unknown() { missing().M() }\n"
    'func External() { _ = strings.NewReader("").Len() }\n'
    "func FieldCall() { newT().f.Next().M() }\n"
    "func mk() func() *T { return nil }\n"
    "func CallOfCall() {\n\tmk()().M()\n\tx := mk()()\n\tx.M()\n}\n"
    "type B struct{}\n"
    "func (b *B) Get() *T { return nil }\n"
    "func newB() *B { return nil }\n"
    "func Shadow() {\n\tnewT := newB\n\tnewT().Get()\n}\n"
    "func Paren() { (newT()).M() }\n"
)


def _position(edge: Edge) -> tuple[int, int]:
    row, _, col = edge.id.rpartition("@")[2].partition(":")
    return int(row), int(col)


def _calls() -> dict[str, list[tuple[str, str]]]:
    nodes, edges = GoExtractor().parse(APP, "app/a.go")
    resolved, _ = ResolverEngine(nodes, edges).resolve()
    ours = [e for e in resolved if e.type is EdgeType.CALLS]
    calls: dict[str, list[tuple[str, str]]] = {}
    for e in sorted(ours, key=_position):
        raw = e.id.split("->raw_call:", 1)[1].rpartition("@")[0]
        target = e.target if e.confidence != 0.8 else "-"
        calls.setdefault(e.source.removeprefix("app."), []).append((raw, target))
    return calls


@pytest.mark.parametrize(
    ("function", "expected"),
    [
        ("Result", [("Get().M", "app.T.M"), ("t.Get", "app.T.Get")]),
        (
            "Package",
            [
                ("NewRegistry().MustRegister", "app.registry.MustRegister"),
                ("NewRegistry", "app.NewRegistry"),
            ],
        ),
        (
            "Interface",
            [("Leader().AddVoter", "app.Raft.AddVoter"), ("c.Leader", "app.Cluster.Leader")],
        ),
        ("Field", [("newT().f.M", "app.K.M"), ("newT", "app.newT")]),
        ("Assert", [("(T).M", "app.T.M")]),
        ("Index", [("m[].M", "app.K.M"), ("s[].M", "app.T.M")]),
        ("Receive", [("(<-ch).M", "app.T.M")]),
        ("Unknown", [("missing().M", "-"), ("missing", "-")]),
        ("External", [("NewReader().Len", "-"), ("strings.NewReader", "strings.NewReader")]),
        (
            "FieldCall",
            [
                ("newT():f:Next().M", "app.T.M"),
                ("newT().f.Next", "app.K.Next"),
                ("newT", "app.newT"),
            ],
        ),
        # A local func value: never the package's `newT` (the value's type is not tracked).
        ("Shadow", [("newT().Get", "-"), ("newT", "-")]),
        ("Paren", [("(newT()).M", "app.T.M"), ("newT", "app.newT")]),
    ],
)
def test_a_call_on_an_unnamed_value_gets_an_edge(
    function: str, expected: list[tuple[str, str]]
) -> None:
    assert _calls()[function] == expected


def test_a_chain_of_calls_resolves_every_link() -> None:
    targets = [target for _, target in _calls()["Deep"]]
    assert targets.count("app.T.Get") == 3
    assert "app.T.M" in targets


def test_every_link_of_a_chain_has_its_own_edge_id() -> None:
    """`t.Get().Get().Get()`: two links named alike must not share an id the store dedups."""
    _, edges = GoExtractor().parse(APP, "app/a.go")
    ids = [e.id for e in edges if e.type is EdgeType.CALLS]
    assert len(ids) == len(set(ids))


def test_a_call_of_a_call_s_result_is_extracted() -> None:
    """`mk()().M()` and `x := mk()()`: a function value called in turn, no selector to type."""
    assert "CallOfCall" in _calls()
