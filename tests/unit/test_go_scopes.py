"""Go block scope: a name bound in a block is gone after it (#643 B8, #660 reviews).

Each function's calls, in source order, as go/types reads them; "-" is a call
that must stay unresolved. Every case was a mutant the suite missed, or an edge
a review found wrong or lost.
"""

import pytest

from cgis.core.models import Edge, EdgeType
from cgis.languages.go.extractor import GoExtractor
from cgis.resolver.engine import ResolverEngine

LIB = (
    "package lib\n"
    "type T struct{}\n"
    "func (t *T) Get() *T { return nil }\n"
    "func (t *T) Run() {}\n"
    "func Get() *T { return nil }\n"
    "func Run() {}\n"
    "func List() []*T { return nil }\n"
    "func Chan() chan *T { return nil }\n"
)

APP = (
    "package app\n"
    'import q "example.com/m/lib"\n'
    "type A struct{}\n"
    "func (a *A) Run() {}\n"
    "func (a *A) Get() *A { return nil }\n"
    "type B struct{}\n"
    "func (b *B) Run() {}\n"
    "func newA() *A { return nil }\n"
    "func Fblock() {\n"
    "\t{\n"
    "\t\tq := newA()\n"
    "\t\tq.Run()\n"
    "\t}\n"
    "\tq.Run()\n"
    "}\n"
    "func Ffunclit() {\n"
    "\tfunc(q *A) { q.Run() }(nil)\n"
    "\tq.Run()\n"
    "}\n"
    "func Fexprswitch() {\n"
    "\tswitch q := newA(); q {\n"
    "\tdefault:\n"
    "\t\tq.Run()\n"
    "\t}\n"
    "\tq.Run()\n"
    "}\n"
    "func Ftypeswitch(w any) {\n"
    "\tswitch q := w.(type) {\n"
    "\tcase *A:\n"
    "\t\tq.Run()\n"
    "\t}\n"
    "\tq.Run()\n"
    "}\n"
    "func Fexprcase(n int) {\n"
    "\tswitch n {\n"
    "\tcase 1:\n"
    "\t\tq := newA()\n"
    "\t\tq.Run()\n"
    "\tcase 2:\n"
    "\t\tq.Run()\n"
    "\t}\n"
    "}\n"
    "func Fdefaultcase(n int) {\n"
    "\tswitch n {\n"
    "\tdefault:\n"
    "\t\tq := newA()\n"
    "\t\tq.Run()\n"
    "\tcase 2:\n"
    "\t\tq.Run()\n"
    "\t}\n"
    "}\n"
    "func Ftypecase(w any) {\n"
    "\tswitch w.(type) {\n"
    "\tcase *A:\n"
    "\t\tq := newA()\n"
    "\t\tq.Run()\n"
    "\tcase *B:\n"
    "\t\tq.Run()\n"
    "\t}\n"
    "}\n"
    "func Fcommcase(ch chan int) {\n"
    "\tselect {\n"
    "\tcase <-ch:\n"
    "\t\tq := newA()\n"
    "\t\tq.Run()\n"
    "\tdefault:\n"
    "\t\tq.Run()\n"
    "\t}\n"
    "}\n"
    "func Fvar() {\n"
    "\tvar q = q.Get()\n"
    "\tq.Run()\n"
    "}\n"
    "func Frange() {\n"
    "\tfor _, q := range q.List() {\n"
    "\t\tq.Run()\n"
    "\t}\n"
    "}\n"
    "func Frecv() {\n"
    "\tselect {\n"
    "\tcase q := <-q.Chan():\n"
    "\t\tq.Run()\n"
    "\t}\n"
    "}\n"
    "func Fcontainer(w any) {\n"
    "\tswitch v := w.(type) {\n"
    "\tcase []*A:\n"
    "\t\tx := v[0]\n"
    "\t\tx.Run()\n"
    "\t}\n"
    "}\n"
    "func Funqualified() {\n"
    "\t{\n"
    "\t\tnewA := func() *A { return nil }\n"
    "\t\tnewA()\n"
    "\t}\n"
    "\tnewA()\n"
    "}\n"
    "var max *B\n"
    "func Fmax() {\n"
    "\t{\n"
    "\t\tmax := newA()\n"
    "\t\tmax.Run()\n"
    "\t}\n"
    "\tmax.Run()\n"
    "}\n"
    "func (b *B) Fshadow() {\n"
    "\t{\n"
    "\t\tb := newA()\n"
    "\t\tb.Run()\n"
    "\t}\n"
    "\tb.Run()\n"
    "}\n"
    "func Ftypeinit(w any) {\n"
    "\tswitch q := newA(); w.(type) {\n"
    "\tcase *A:\n"
    "\t\tq.Run()\n"
    "\t}\n"
    "\tq.Run()\n"
    "}\n"
    "type I interface{ Run() }\n"
    "func Fself(x I) {\n"
    "\tswitch x := x.(type) {\n"
    "\tcase *A:\n"
    "\t\tx.Run()\n"
    "\tdefault:\n"
    "\t\tx.Run()\n"
    "\t}\n"
    "}\n"
)


def _position(edge: Edge) -> tuple[int, int]:
    row, _, col = edge.id.rpartition("@")[2].partition(":")
    return int(row), int(col)


def _calls() -> dict[str, list[str]]:
    nodes, edges = [], []
    for path, code in (("lib/lib.go", LIB), ("app/a.go", APP)):
        n, e = GoExtractor().parse(code, path)
        nodes += n
        edges += e
    state: dict[str, object] = {"go_module": {"path": "example.com/m"}}
    resolved, _ = ResolverEngine(nodes, edges, project_state=state).resolve()
    ours = [e for e in resolved if e.type is EdgeType.CALLS and e.source.startswith("app.")]
    calls: dict[str, list[str]] = {}
    for e in sorted(ours, key=_position):
        target = e.target if e.confidence != 0.8 else "-"
        calls.setdefault(e.source.removeprefix("app."), []).append(target)
    return calls


@pytest.mark.parametrize(
    ("function", "expected"),
    [
        ("Fblock", ["app.newA", "app.A.Run", "lib.Run"]),
        ("Fcommcase", ["app.newA", "app.A.Run", "lib.Run"]),
        ("Fcontainer", ["app.A.Run"]),
        ("Fdefaultcase", ["app.newA", "app.A.Run", "lib.Run"]),
        ("Fexprcase", ["app.newA", "app.A.Run", "lib.Run"]),
        ("Fexprswitch", ["app.newA", "app.A.Run", "lib.Run"]),
        ("Ffunclit", ["app.A.Run", "lib.Run"]),
        ("Frange", ["lib.List", "lib.T.Run"]),
        ("Frecv", ["lib.Chan", "lib.T.Run"]),
        ("Ftypecase", ["app.newA", "app.A.Run", "lib.Run"]),
        ("Ftypeswitch", ["app.A.Run", "lib.Run"]),
        ("Fvar", ["lib.Get", "lib.T.Run"]),
        ("Funqualified", ["-", "app.newA"]),
        ("Fmax", ["app.newA", "app.A.Run", "app.B.Run"]),
        ("B.Fshadow", ["app.newA", "app.A.Run", "app.B.Run"]),
        ("Fself", ["app.A.Run", "app.I.Run"]),
        ("Ftypeinit", ["app.newA", "app.A.Run", "lib.Run"]),
    ],
)
def test_each_call_reads_the_binding_in_its_scope(function: str, expected: list[str]) -> None:
    assert _calls()[function] == expected
