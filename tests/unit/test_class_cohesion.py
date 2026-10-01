"""LCOM4 per class in `cgis metrics` (#451).

LCOM4 counts the groups a class's methods fall into when two methods are linked
by touching the same receiver attribute or by one calling the other. It needs to
know what each method touches, which the Python extractor now records as
`self_attrs`; these tests cover that record and the metric built on it.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cgis.cli import app
from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.extractors.python_extractor import PythonExtractor
from cgis.pipeline import IngestionPipeline
from cgis.query.render.metrics import ClassCohesion, DuckDBAnalyzer, class_cohesion_metrics
from cgis.storage.sqlite_store import SQLiteStore

runner = CliRunner()


def _attrs(code: str) -> dict[str, list[str] | None]:
    nodes, _ = PythonExtractor().parse(code, "m.py")
    return {n.name: n.metadata.get("self_attrs") for n in nodes if n.type == NodeType.METHOD}


# --- the extractor's record -------------------------------------------------------


def test_a_method_records_what_it_touches_through_self() -> None:
    attrs = _attrs(
        "class A:\n"
        "    def save(self):\n"
        "        self._conn.commit()\n"
        "        self.flush()\n"
        "        self.count = self.count + 1\n"
    )
    assert attrs["save"] == ["_conn", "count", "flush"]


def test_every_method_carries_the_key_even_when_empty() -> None:
    """Absence must mean "built before this existed", never "touches nothing"."""
    assert _attrs("class A:\n    def noop(self):\n        return 1\n")["noop"] == []


def test_the_receiver_is_the_first_parameter_whatever_its_name() -> None:
    code = (
        "class A:\n"
        "    @classmethod\n"
        "    def make(cls):\n"
        "        return cls.DEFAULT\n"
        "    def odd(this, self):\n"
        "        return this.a, self.b\n"
    )
    attrs = _attrs(code)
    assert attrs["make"] == ["DEFAULT"]
    assert attrs["odd"] == ["a"]


def test_a_splat_parameter_is_never_the_receiver() -> None:
    """`def m(*args)` binds a tuple, not the instance; `**kw` binds a dict."""
    code = (
        "class A:\n"
        "    def splat(*args):\n"
        "        return args.count\n"
        "    def kw(self, *rest, **opts):\n"
        "        return self.x, opts.y\n"
    )
    attrs = _attrs(code)
    assert attrs["splat"] == []
    assert attrs["kw"] == ["x"]


def test_a_staticmethod_has_no_receiver() -> None:
    code = "class A:\n    @staticmethod\n    def util(x):\n        return x.y\n"
    assert _attrs(code)["util"] == []


def test_a_closure_counts_and_a_nested_class_does_not() -> None:
    code = (
        "class A:\n"
        "    def outer(self):\n"
        "        def inner():\n"
        "            return self.closed\n"
        "        class B:\n"
        "            def m(self):\n"
        "                return self.other\n"
        "        return inner, B\n"
    )
    attrs = _attrs(code)
    assert attrs["outer"] == ["closed"]
    assert attrs["m"] == ["other"]  # B.m's own receiver, recorded on B.m


def test_a_nested_scope_rebinding_the_receiver_is_not_walked() -> None:
    """Inside `def helper(self)` or `lambda self: ...`, `self` is that scope's own argument."""
    code = (
        "class A:\n"
        "    def outer(self):\n"
        "        def helper(self):\n"
        "            return self.theirs\n"
        "        pick = lambda self: self.also_theirs\n"
        "        keep = lambda other: self.mine\n"
        "        return helper, pick, keep\n"
    )
    assert _attrs(code)["outer"] == ["mine"]


def test_a_function_outside_a_class_has_no_record() -> None:
    nodes, _ = PythonExtractor().parse("def f(self):\n    return self.x\n", "m.py")
    (func,) = (n for n in nodes if n.type == NodeType.FUNCTION)
    assert "self_attrs" not in func.metadata


# --- the metric -------------------------------------------------------------------------


def _lcom(tmp_path: Path, code: str, **kwargs: object) -> dict[str, tuple[int, int]]:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "m.py").write_text(code, encoding="utf-8")
    db = str(tmp_path / "graph.db")
    with SQLiteStore(db) as store:
        IngestionPipeline({".py": PythonExtractor()}).run(str(tmp_path / "pkg"), store=store)
    with DuckDBAnalyzer(db) as analyzer:
        rows: list[ClassCohesion] = class_cohesion_metrics(analyzer, **kwargs)  # type: ignore[arg-type]
    return {r.class_id: (r.lcom4, r.methods) for r in rows}


SPLIT = """
class Mixed:
    def __init__(self):
        self.a = 1
        self.b = 2
    def read_a(self):
        return self.a
    def write_a(self, v):
        self.a = v
    def read_b(self):
        return self.b
"""


def test_two_groups_sharing_nothing_give_lcom4_2(tmp_path: Path) -> None:
    # __init__ touches both a and b; counting it would glue the class into one.
    assert _lcom(tmp_path, SPLIT)["m.Mixed"] == (2, 3)


def test_a_call_links_methods_that_share_no_field(tmp_path: Path) -> None:
    code = SPLIT + "    def both(self):\n        return self.read_a() + self.read_b()\n"
    assert _lcom(tmp_path, code)["m.Mixed"] == (1, 4)


def test_a_reference_to_a_method_links_it_like_a_call(tmp_path: Path) -> None:
    code = SPLIT + "    def callbacks(self):\n        return [self.read_a, self.read_b]\n"
    assert _lcom(tmp_path, code)["m.Mixed"] == (1, 4)


def test_dunder_abstract_static_and_class_methods_are_not_counted(tmp_path: Path) -> None:
    code = (
        "import abc\n"
        "class C(abc.ABC):\n"
        "    def run(self):\n"
        "        return self.x\n"
        "    def __repr__(self):\n"
        "        return 'C'\n"
        "    @abc.abstractmethod\n"
        "    def hook(self): ...\n"
        "    @staticmethod\n"
        "    def util(): return 1\n"
        "    @classmethod\n"
        "    def make(cls): return cls()\n"
    )
    assert _lcom(tmp_path, code)["m.C"] == (1, 1)


def test_a_class_with_nothing_to_count_is_not_listed(tmp_path: Path) -> None:
    code = "class Data:\n    def __init__(self):\n        self.x = 1\n"
    assert "m.Data" not in _lcom(tmp_path, code)


TWO_CLASSES = (
    SPLIT + "class Tight:\n    def a(self):\n        return self.v\n"
    "    def b(self):\n        return self.v\n"
)


def test_ranked_least_cohesive_first(tmp_path: Path) -> None:
    rows = _lcom(tmp_path, TWO_CLASSES)
    assert list(rows) == ["m.Mixed", "m.Tight"]
    assert rows["m.Tight"] == (1, 2)


def test_scope_keeps_only_classes_under_it(tmp_path: Path) -> None:
    assert list(_lcom(tmp_path, TWO_CLASSES, scope=["m.Tight"])) == ["m.Tight"]


def test_exclude_drops_classes_by_segment(tmp_path: Path) -> None:
    assert list(_lcom(tmp_path, TWO_CLASSES, exclude=["Mixed"])) == ["m.Tight"]


def test_a_graph_built_before_self_attrs_reports_nothing_rather_than_noise(tmp_path: Path) -> None:
    """Without the record every method looks like it touches nothing: LCOM4 = methods."""
    cls = Node(
        id="m.Old", type=NodeType.CLASS, name="Old", file_path="m.py", start_line=1, end_line=9
    )
    methods = [
        Node(
            id=f"m.Old.{n}",
            type=NodeType.METHOD,
            name=n,
            file_path="m.py",
            start_line=2,
            end_line=3,
        )
        for n in ("a", "b", "c")
    ]
    edges = [
        Edge(id=f"d{i}", source="m.Old", target=m.id, type=EdgeType.DECLARES)
        for i, m in enumerate(methods)
    ]
    db = str(tmp_path / "old.db")
    with SQLiteStore(db) as store:
        store.save_graph([cls, *methods], edges)
    with DuckDBAnalyzer(db) as analyzer:
        assert class_cohesion_metrics(analyzer) == []


def test_cli_text_and_json_carry_it(tmp_path: Path) -> None:
    _lcom(tmp_path, SPLIT)
    db = str(tmp_path / "graph.db")
    text = runner.invoke(app, ["metrics", "--db", db])
    assert text.exit_code == 0, text.output
    assert "Class cohesion" in text.stdout
    raw = runner.invoke(app, ["metrics", "--db", db, "--format", "json"])
    assert json.loads(raw.stdout)["class_cohesion"] == [
        {"class_id": "m.Mixed", "methods": 3, "lcom4": 2}
    ]


@pytest.mark.parametrize("name", ["__init__", "__repr__"])
def test_dunders_are_recorded_even_though_lcom4_skips_them(name: str) -> None:
    """The record is the extractor's fact; leaving dunders out is the metric's choice."""
    attrs = _attrs(f"class A:\n    def {name}(self):\n        self.x = 1\n")
    assert attrs[name] == ["x"]
