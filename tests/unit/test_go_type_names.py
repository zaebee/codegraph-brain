"""A name in type position that is not the package's type: a type parameter, a local type.

`func Idt[A any](x A) A` read `A` as the package's `A`, so `Idt(c).Run()` resolved to
`A.Run` — a confident wrong edge (#662 review, H2). A type declared inside a function
shadows the package's of the same name (H3). Both must type nothing, or the right thing.
"""

from cgis.core.models import EdgeType
from cgis.languages.go.extractor import GoExtractor
from cgis.resolver.engine import ResolverEngine

APP = (
    "package app\n"
    "type A struct{}\n"
    "func (a *A) Run() {}\n"
    "func (a *A) Get() *A { return a }\n"
    "type B struct{}\n"
    "func (b *B) Run() {}\n"
    "func (b *B) Get() *B { return b }\n"
    "type C struct{}\n"
    "func (c *C) Run() {}\n"
    "type T struct{}\n"
    "func (t *T) Run() {}\n"
    "func newC() *C { return nil }\n"
    "func Idt[A any](x A) A { return x }\n"
    "type Box[T any] struct{ v T }\n"
    "func (b *Box[T]) Get() T { return b.v }\n"
    "func Generic() { Idt(newC()).Run() }\n"
    "func Bound[A interface{ Run() }](x A) { x.Run() }\n"
    "func Boxed(b *Box[*C]) { b.Get().Run() }\n"
    "func Local[A interface{ Run() }](x A) {\n\tvar y A = x\n\ty.Run()\n}\n"
    "func Alias(w any) {\n\ttype A = B\n\tw.(*A).Run()\n\tvar x *A\n\tx.Run()\n}\n"
    "func Defined() {\n\ttype A struct{ n int }\n\tvar x A\n\t_ = x\n\tx.Get()\n}\n"
    "func After(w any) {\n\t{\n\t\ttype A = B\n\t\t_ = w\n\t}\n\tw.(*A).Run()\n}\n"
    "func Field(b *Box[*C]) { b.v.Run() }\n"
    "type Getter[T any] interface{ Get() T }\n"
    "func Iface(g Getter[*C]) { g.Get().Run() }\n"
)


def _targets(source: str) -> list[str]:
    nodes, edges = GoExtractor().parse(APP, "app/a.go")
    resolved, _ = ResolverEngine(nodes, edges).resolve()
    return sorted(
        e.target if e.confidence != 0.8 else "-"
        for e in resolved
        if e.type is EdgeType.CALLS and e.source == f"app.{source}"
    )


def test_a_generic_function_s_result_is_not_the_package_type_of_its_parameter_s_name() -> None:
    assert "app.A.Run" not in _targets("Generic")


def test_a_parameter_of_a_type_parameter_type_is_not_the_package_type() -> None:
    assert _targets("Bound") == ["-"]


def test_a_generic_receiver_s_type_parameter_is_not_the_package_type() -> None:
    assert "app.T.Run" not in _targets("Boxed")


def test_a_local_declared_with_a_type_parameter_is_not_the_package_type() -> None:
    assert "app.A.Run" not in _targets("Local")


def test_a_local_alias_names_its_target() -> None:
    assert _targets("Alias") == ["app.B.Run", "app.B.Run"]


def test_a_type_defined_in_the_function_is_not_the_package_type() -> None:
    assert "app.A.Get" not in _targets("Defined")


def test_a_local_alias_ends_with_its_block() -> None:
    assert _targets("After") == ["app.A.Run"]


def test_a_generic_struct_s_field_of_its_parameter_is_not_the_package_type() -> None:
    assert "app.T.Run" not in _targets("Field")


def test_a_generic_interface_s_result_of_its_parameter_is_not_the_package_type() -> None:
    assert "app.T.Run" not in _targets("Iface")
