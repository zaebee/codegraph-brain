"""Function values in Python: invoked parameters and value candidates (#632, CV-1/CV-4)."""

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser

from cgis.languages.python._values import (
    _bodies,
    _own_nodes,
    held_values,
    invoked_slots,
    local_value_calls,
    value_candidates,
    value_name,
)
from cgis.languages.python.extractor import PythonExtractor

_PARSER = Parser(Language(tspython.language()))


def _parse(code: str) -> tuple[Node, bytes]:
    data = code.encode()
    return _PARSER.parse(data).root_node, data


def _first(root: Node, node_type: str) -> Node:
    stack = [root]
    while stack:
        node = stack.pop(0)
        if node.type == node_type:
            return node
        stack.extend(node.children)
    msg = f"no {node_type}"
    raise AssertionError(msg)


def _slots(code: str, skip_first: bool = False) -> list[int | str]:
    root, data = _parse(code)
    return invoked_slots(_first(root, "function_definition"), data, skip_first=skip_first)


def _candidates(code: str, file_names: frozenset[str]) -> list[tuple[str, str]]:
    root, data = _parse(code)
    func = _first(root, "function_definition")
    edges = value_candidates(func, data, "m.outer", "m.py", file_names)
    return [(e.target.removeprefix("raw_call:"), e.context or "") for e in edges]


def test_invoked_slots_of_a_function() -> None:
    code = "def run(name, fn, *, cb):\n    fn()\n    cb()\n"
    assert _slots(code) == [1, "fn", "cb"]


def test_invoked_slots_skip_self_but_not_for_a_staticmethod() -> None:
    assert _slots("def m(self, fn):\n    fn()\n", skip_first=True) == [0, "fn"]
    assert _slots("def m(fn):\n    fn()\n") == [0, "fn"]


def test_positional_only_and_after_star_args() -> None:
    code = "def run(fn, /, other, *args, late):\n    fn()\n    other()\n    late()\n"
    assert _slots(code) == [0, 1, "other", "late"]


def test_an_awaited_or_splatted_call_counts() -> None:
    assert _slots("async def run(fn, *a):\n    await fn(*a)\n") == [0, "fn"]


def test_a_parameter_rebound_before_its_call_is_not_invoked() -> None:
    assert _slots("def f(cb):\n    cb = g\n    cb()\n") == []


def test_a_parameter_rebound_after_its_call_is_still_invoked() -> None:
    assert _slots("def f(cb):\n    cb()\n    cb = None\n") == [0, "cb"]


def test_a_call_in_a_nested_def_does_not_make_the_outer_an_invoker() -> None:
    code = "def f(cb):\n    def inner():\n        cb()\n    return inner\n"
    assert _slots(code) == []
    assert _slots("def f(cb):\n    return lambda: cb()\n") == []


def test_a_parameter_named_like_a_function_is_no_value() -> None:
    code = "def outer(handler):\n    run(handler)\n"
    assert _candidates(code, frozenset({"handler", "run"})) == []


def test_a_local_named_like_a_function_is_no_value() -> None:
    code = "def outer():\n    handler = make()\n    run(handler)\n"
    assert _candidates(code, frozenset({"handler", "run", "make"})) == []


def test_value_name_forms() -> None:
    names = frozenset({"double", "mod", "partial", "functools", "C"})
    root, data = _parse(
        "double\nself.run\nmod.f\npartial(double, 1)\nfunctools.partial(mod.f)\nC.m\n"
        "cls.m\nx[0]\nlambda: 0\ng()\nunknown\nother.f\n"
    )
    exprs = [s.named_children[0] for s in root.named_children]
    got = [value_name(e, data, names, frozenset()) for e in exprs]
    assert got == [
        "double",
        "self.run",
        "mod.f",
        "double",
        "mod.f",
        "C.m",
        None,
        None,
        None,
        None,
        None,
        None,
    ]


def test_slots_skip_comments_and_stop_after_a_splat() -> None:
    code = "def outer(xs):\n    run(a,  # c\n        f, *xs, g, key=h)\n"
    got = _candidates(code, frozenset({"a", "f", "g", "h", "run"}))
    assert got == [
        ("a", "value-arg:0:run@1:4"),
        ("f", "value-arg:1:run@1:4"),
        ("h", "value-arg:key:run@1:4"),
    ]


def test_a_candidate_id_is_the_value_span() -> None:
    root, data = _parse("def outer():\n    run(target=f)\n")
    func = _first(root, "function_definition")
    (edge,) = value_candidates(func, data, "m.outer", "m.py", frozenset({"f", "run"}))
    value = data.index(b"f)")
    assert edge.id == f"m.py:value_{value}_{value + 1}"
    assert edge.source == "m.outer"
    assert edge.line_number == 2


def _local_calls(code: str, file_names: frozenset[str] = frozenset()) -> list[str]:
    """The raw names of the CV-2 edges the first function of `code` emits."""
    root, data = _parse(code)
    func = _first(root, "function_definition")
    held = held_values(func, data, file_names)
    return [
        e.target.removeprefix("raw_call:") for e in local_value_calls(func, "m.f", "m.py", held)
    ]


def test_a_local_holds_a_method_value() -> None:
    assert _local_calls("def f(self):\n    cb = self.handle\n    cb()\n") == ["self.handle"]


def test_a_rebinding_in_the_same_block_replaces() -> None:
    code = "def f(self):\n    cb = self.a\n    cb = self.b\n    cb()\n"
    assert _local_calls(code) == ["self.b"]


def test_a_rebinding_to_no_function_clears_the_local() -> None:
    assert _local_calls("def f(self):\n    cb = self.a\n    cb = make()\n    cb()\n") == []


def test_a_rebinding_in_an_if_adds() -> None:
    code = "def f(self, x):\n    cb = self.a\n    if x:\n        cb = self.b\n    cb()\n"
    assert _local_calls(code) == ["self.a", "self.b"]


def test_a_call_before_the_binding_sees_nothing() -> None:
    assert _local_calls("def f(self):\n    cb()\n    cb = self.a\n") == []


def test_a_tuple_loop_binds_each_value() -> None:
    code = "def f(self):\n    for cb in (self.a, self.b):\n        cb()\n"
    assert _local_calls(code) == ["self.a", "self.b"]


def test_a_copy_holds_what_the_original_holds() -> None:
    code = "def f(self):\n    cb = self.a\n    other = cb\n    other()\n"
    assert _local_calls(code) == ["self.a"]


def test_a_bare_module_function_is_held() -> None:
    assert _local_calls("def f():\n    cb = work\n    cb()\n", frozenset({"work"})) == ["work"]


def test_a_held_local_passed_to_an_invoker_passes_its_values() -> None:
    root, data = _parse("def f(self):\n    cb = self.a\n    run(cb)\n")
    func = _first(root, "function_definition")
    held = held_values(func, data, frozenset({"run"}))
    edges = value_candidates(func, data, "m.f", "m.py", frozenset({"run"}), held.at)
    assert [(e.target, e.context) for e in edges] == [("raw_call:self.a", "value-arg:0:run@2:4")]


def _invokes(code: str) -> dict[str, object]:
    """Each function's `invokes`, as the extractor records it for `m.py`."""
    nodes, _ = PythonExtractor().parse(code, "m.py")
    return {n.id: n.metadata.get("invokes") for n in nodes if n.type.name in ("FUNCTION", "METHOD")}


def test_a_redefined_function_keeps_the_last_definitions_slots() -> None:
    # Review M1: the later `apply` never calls `fn`, and it is the one that runs.
    code = "def apply(fn):\n    return fn(1)\n\n\ndef apply(fn):\n    return None\n"
    assert _invokes(code)["m.apply"] is None


def test_a_comprehension_variable_shadows_the_parameter() -> None:
    # Review M3: `fn` in the comprehension is the loop variable, not the parameter.
    assert _invokes("def apply(fn):\n    return [fn(x) for fn in []]\n")["m.apply"] is None


def test_a_call_through_a_shadowed_builtin_hands_nothing_over() -> None:
    # Review M2: `sorted` is the parameter here, not the builtin invoker.
    code = "def g(xs, sorted):\n    return sorted(xs, key=handler)\n"
    assert _candidates(code, frozenset({"handler"})) == []


_TRICKY = """
def outer(a, b=default()):
    @decorate(arg())
    def inner(c):
        c()
    x = [fn(y) for y in z]
    lambda: hidden()
    class K:
        k = made()
        def m(self):
            self.n()
    a(b)
    return inner
"""


def test_the_query_sweep_owns_what_the_walk_owns() -> None:
    # Review I2: production reads `_bodies`; the other tests read `_own_nodes`.
    root, _ = _parse(_TRICKY)
    events = {
        "call",
        "assignment",
        "augmented_assignment",
        "for_statement",
        "named_expression",
        "as_pattern_target",
        "for_in_clause",
    }
    for definition, owned in _bodies(root):
        walked = [n for n in _own_nodes(definition) if n.type in events]
        assert [(n.type, n.start_byte) for n in owned] == [(n.type, n.start_byte) for n in walked]


def test_a_typed_default_parameter_is_named() -> None:
    # Gemini on #701: its `name` field is a plain identifier, so it is read as one.
    assert _slots("def f(cb: Callable = g):\n    cb()\n") == [0, "cb"]


def test_an_attribute_name_is_no_read_of_a_local() -> None:
    # Gemini on #701: in `self.cb` the `cb` is the attribute, not the local `cb`.
    root, data = _parse("def f(self):\n    cb = self.a\n    self.cb\n")
    func = _first(root, "function_definition")
    attribute = _first(func.child_by_field_name("body").children[1], "attribute")
    name = attribute.child_by_field_name("attribute")
    assert held_values(func, data, frozenset()).at(name) == []
