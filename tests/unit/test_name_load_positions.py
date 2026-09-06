"""Every syntactic position a name can hold, and whether it is a use (spec D10).

`is_name_load` is a denylist over a grammar, and a denylist rots. Two independent
reviews of the first version found the same hole — binding positions treated as
uses — so this table is the guard: a position the list has not met fails here
rather than quietly emitting "this class is used" for a loop variable.

A row is a snippet and whether the `Widget` in it references the class. Add a row
before adding an entry to `_NOT_A_LOAD`.
"""

import pytest

from cgis.core.models import EdgeType
from cgis.extractors.python_extractor import PythonExtractor

# (label, snippet, is Widget a use of the class?)
_POSITIONS: list[tuple[str, str, bool]] = [
    # --- uses -------------------------------------------------------------
    ("positional argument", "def f(app):\n    app.add(Widget)\n", True),
    ("keyword argument value", "def f(app):\n    app.add(cls=Widget)\n", True),
    ("attribute head", "def f():\n    return Widget.SIZE\n", True),
    ("classmethod call", "def f():\n    return Widget.build()\n", True),
    ("except clause", "def f():\n    try:\n        pass\n    except Widget:\n        pass\n", True),
    (
        "except clause with alias",
        "def f():\n    try:\n        pass\n    except Widget as e:\n        pass\n",
        True,
    ),
    ("list literal", "REG = [Widget]\n", True),
    ("tuple in a list", "REG = [('w', Widget)]\n", True),
    ("dict value", "REG = {'w': Widget}\n", True),
    ("set literal", "REG = {Widget}\n", True),
    ("nested tuple argument", "def f(app):\n    app.add((1, Widget))\n", True),
    ("assignment right", "def f():\n    x = Widget\n    return x\n", True),
    ("return value", "def f():\n    return Widget\n", True),
    ("comprehension element", "def f(xs):\n    return [Widget for _ in xs]\n", True),
    ("comprehension iterable", "def f():\n    return [x for x in Widget]\n", True),
    ("f-string interpolation", 'def f():\n    return f"{Widget}"\n', True),
    ("lambda default value", "f = lambda a=Widget: a\n", True),
    ("walrus value", "def f():\n    if (x := Widget):\n        return x\n", True),
    ("boolean operator", "def f(a):\n    return a or Widget\n", True),
    ("conditional expression", "def f(a):\n    return Widget if a else None\n", True),
    ("star argument", "def f(app):\n    app.add(*Widget)\n", True),
    ("subscript value", "def f():\n    return Widget[0]\n", True),
    (
        "match class pattern",
        "def f(v):\n    match v:\n        case Widget():\n            pass\n",
        True,
    ),
    (
        "match value pattern",
        "def f(v):\n    match v:\n        case Widget.A:\n            pass\n",
        True,
    ),
    (
        "match keyword pattern value",
        "def f(v):\n    match v:\n        case O(a=Widget):\n            pass\n",
        True,
    ),
    ("assert", "def f():\n    assert Widget\n", True),
    ("raise", "def f():\n    raise Widget\n", True),
    # --- bindings and already-covered positions ---------------------------
    ("call function", "def f():\n    return Widget()\n", False),
    ("attribute member half", "def f(o):\n    return o.Widget\n", False),
    ("keyword argument label", "def f(app):\n    app.add(Widget=1)\n", False),
    ("assignment target", "def f():\n    Widget = 1\n    return 0\n", False),
    ("augmented assignment target", "def f():\n    Widget += 1\n", False),
    ("tuple assignment target", "def f(xs):\n    a, Widget = xs\n", False),
    ("list assignment target", "def f(xs):\n    [a, Widget] = xs\n", False),
    ("star assignment target", "def f(xs):\n    *Widget, a = xs\n", False),
    ("for target", "def f(xs):\n    for Widget in xs:\n        pass\n", False),
    ("for tuple target", "def f(xs):\n    for a, Widget in xs:\n        pass\n", False),
    ("for nested tuple target", "def f(xs):\n    for a, (b, Widget) in xs:\n        pass\n", False),
    ("comprehension target", "def f(xs):\n    return [1 for Widget in xs]\n", False),
    ("dict comprehension target", "def f(xs):\n    return {1: 2 for Widget in xs}\n", False),
    ("walrus target", "def f(x):\n    if (Widget := x):\n        pass\n", False),
    ("with alias", "def f(x):\n    with x as Widget:\n        pass\n", False),
    ("with tuple alias", "def f(x):\n    with x as (a, Widget):\n        pass\n", False),
    (
        "except alias",
        "def f():\n    try:\n        pass\n    except E as Widget:\n        pass\n",
        False,
    ),
    (
        "match capture pattern",
        "def f(v):\n    match v:\n        case Widget:\n            pass\n",
        False,
    ),
    (
        "match as-pattern binding",
        "def f(v):\n    match v:\n        case [1] as Widget:\n            pass\n",
        False,
    ),
    (
        "match keyword pattern label",
        "def f(v):\n    match v:\n        case O(Widget=1):\n            pass\n",
        False,
    ),
    ("parameter name", "def f(Widget):\n    pass\n", False),
    ("star parameter name", "def f(*Widget):\n    pass\n", False),
    ("kwargs parameter name", "def f(**Widget):\n    pass\n", False),
    ("default parameter name", "def f(Widget=1):\n    pass\n", False),
    ("lambda parameter name", "f = lambda Widget: 1\n", False),
    ("function definition name", "def Widget():\n    pass\n", False),
    ("nonlocal", "def o():\n    Widget = 1\n\n    def i():\n        nonlocal Widget\n", False),
    ("global", "def f():\n    global Widget\n", False),
    ("del", "def f():\n    del Widget\n", False),
    ("annotation", "def f(x: Widget):\n    pass\n", False),
    ("return annotation", "def f() -> Widget:\n    pass\n", False),
    ("type parameter", "def f[Widget]():\n    pass\n", False),
    ("PEP 695 alias name", "type Widget = int\n", False),
]


@pytest.mark.parametrize(("label", "snippet", "is_use"), _POSITIONS, ids=[p[0] for p in _POSITIONS])
def test_name_load_positions(label: str, snippet: str, is_use: bool) -> None:
    """One row of the position table: does this snippet name the class or bind it?"""
    code = "from pkg.w import Widget\n\n\n" + snippet
    _nodes, edges = PythonExtractor().parse(code, "pkg/user.py")
    emitted = any(
        e.type == EdgeType.DEPENDS_ON and ":nameref_" in e.id and e.target == "raw_dep:Widget"
        for e in edges
    )
    assert emitted is is_use, (
        f"{label}: expected {'a reference' if is_use else 'no reference'}, got the opposite. "
        "If the grammar changed, fix is_name_load — do not relax this row."
    )
