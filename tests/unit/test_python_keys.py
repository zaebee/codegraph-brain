"""Data keys in Python (DK-1..DK-3): which strings address data, and how each is used."""

from cgis.core.models import RAW_KEY_PREFIX, EdgeType, NodeType
from cgis.languages.python.extractor import PythonExtractor

_R, _W, _D = EdgeType.READS_KEY, EdgeType.WRITES_KEY, EdgeType.DECLARES_KEY


def _keys(code: str) -> set[tuple[str, EdgeType, str]]:
    """(source, relation, raw key) for every key edge the extractor emits for m.py."""
    _nodes, edges = PythonExtractor().parse(code, "m.py")
    return {
        (e.source, e.type, e.target.removeprefix(RAW_KEY_PREFIX))
        for e in edges
        if e.target.startswith(RAW_KEY_PREFIX)
    }


def _relations(code: str) -> set[tuple[EdgeType, str]]:
    """(relation, raw key) pairs, whatever owns them."""
    return {(kind, key) for _, kind, key in _keys(code)}


def test_subscript_reads_and_writes() -> None:
    code = (
        "def f(d):\n"
        '    x = d["a"]\n'
        '    d["b"] = 1\n'
        '    del d["c"]\n'
        '    d["e"] += 1\n'
        '    d["f"], y = 1, 2\n'
    )
    assert _relations(code) == {
        (_R, "|a"),
        (_W, "|b"),
        (_W, "|c"),
        (_R, "|e"),
        (_W, "|e"),
        (_W, "|f"),
    }


def test_nested_subscripts_read_one_dotted_path() -> None:
    code = (
        "def f(doc):\n"
        '    brand = doc["details"]["brand"]\n'
        '    doc["details"]["model"] = 1\n'
        '    return doc.get("meta", {}).get("owner")\n'
    )
    assert _relations(code) == {(_R, "|details.brand"), (_W, "|details.model"), (_R, "|meta.owner")}


def test_get_pop_setdefault() -> None:
    code = 'def f(d):\n    d.get("a")\n    d.pop("b", None)\n    d.setdefault("c", [])\n'
    assert _relations(code) == {(_R, "|a"), (_R, "|b"), (_W, "|b"), (_W, "|c")}


def test_membership_reads() -> None:
    code = 'def f(d):\n    if "a" in d and "b" not in d:\n        pass\n    return "c" in d["x"]\n'
    assert _relations(code) == {(_R, "|a"), (_R, "|b"), (_R, "|x.c")}


def test_query_operator_children_are_field_reads() -> None:
    code = 'def f(x):\n    return {"query": {"terms": {"event_type.keyword": x}}}\n'
    assert _relations(code) == {(_R, "|event_type.keyword")}


def test_aggregation_field_value_is_read() -> None:
    code = 'def f():\n    return {"aggs": {"by": {"terms": {"field": "brand", "size": 5}}}}\n'
    assert _relations(code) == {(_R, "|brand")}


def test_sort_and_range_read_their_fields() -> None:
    code = (
        "def f():\n"
        '    return {"sort": [{"created_at": {"order": "desc"}}, "price"],\n'
        '            "range": {"year": {"gte": 2000}}}\n'
    )
    assert _relations(code) == {(_R, "|created_at"), (_R, "|price"), (_R, "|year")}


def test_storage_write_literal_writes_nested_paths() -> None:
    code = (
        "def save(client, b):\n"
        '    client.index(index="vehicles", document={"details": {"brand": b}, "year": 1})\n'
    )
    assert _keys(code) == {
        ("m.save", _W, "vehicles|details.brand"),
        ("m.save", _W, "vehicles|year"),
    }


def test_update_doc_wrapper_is_transparent() -> None:
    code = 'def f(c, v):\n    c.update(index="vehicles", id=1, body={"doc": {"status": v}})\n'
    assert _relations(code) == {(_W, "vehicles|status")}


def test_search_body_reads_scoped_fields_and_writes_nothing() -> None:
    code = (
        "def f(c, x):\n"
        '    c.search(index="events", body={"query": {"term": {"event_type.keyword": x}}})\n'
    )
    assert _relations(code) == {(_R, "events|event_type.keyword")}


def test_constant_index_namespace() -> None:
    code = (
        'VEHICLES = "vehicles"\n'
        "def save(client, b):\n"
        '    client.index(index=VEHICLES, document={"brand": b})\n'
    )
    assert _relations(code) == {(_W, "@VEHICLES|brand")}


def test_model_fields_declare() -> None:
    code = (
        "from typing import ClassVar\n"
        "from pydantic import BaseModel\n"
        "import sqlmodel\n"
        "class Event(BaseModel):\n"
        "    brand: str\n"
        "    year: int = 2000\n"
        "    _private: int = 0\n"
        "    limit: ClassVar[int] = 3\n"
        "    def m(self) -> None:\n"
        "        local: int = 1\n"
        "class Car(sqlmodel.SQLModel, table=True):\n"
        "    plate: str\n"
        "class Plain:\n"
        "    name: str\n"
    )
    assert _keys(code) == {
        ("m.Event", _D, "m.Event|brand"),
        ("m.Event", _D, "m.Event|year"),
        ("m.Car", _D, "m.Car|plate"),
    }


def test_dynamic_keys_make_no_edge() -> None:
    code = (
        "def f(d, k):\n"
        "    d[k]\n"
        '    d[f"x_{k}"] = 1\n'
        '    d.get("a" + k)\n'
        '    d[""]\n'
        "    d[0]\n"
        '    d["a":"b"]\n'
        '    d["a", "b"]\n'
        "    d.get(k)\n"
    )
    assert _relations(code) == set()


def test_type_subscripts_are_not_keys() -> None:
    code = (
        "from typing import Literal\n"
        'Mode = Literal["fast"]\n'
        'def f(x: Literal["slow"], y: list["Car"]) -> dict["str", int]:\n'
        '    z: Literal["mid"] = "mid"\n'
    )
    assert _relations(code) == set()


def test_redis_and_task_namespaces() -> None:
    code = (
        "async def f(self, queue, cache):\n"
        '    await self.redis.get("rate")\n'
        '    await self.redis.set("rate", 1)\n'
        '    await queue.enqueue("send_email", to=1)\n'
        '    cache.get("plain")\n'
    )
    assert _relations(code) == {
        (_R, "redis|rate"),
        (_W, "redis|rate"),
        (_W, "task|send_email"),
        (_R, "|plain"),
    }


def test_key_edges_belong_to_the_enclosing_function() -> None:
    code = (
        'TOP = {}["top"]\n'
        "class C:\n"
        '    seen = {}["cls"]\n'
        "    def m(self, d):\n"
        '        fn = lambda: d["lam"]\n'
        "        def inner(e):\n"
        '            return e["in"]\n'
        '        return d["meth"]\n'
        "def deco(x):\n"
        "    return x\n"
        '@deco({}["dec"])\n'
        'def g(d={}["default"]):\n'
        '    return d["body"]\n'
    )
    assert _keys(code) == {
        ("m", _R, "|top"),
        ("m.C", _R, "|cls"),
        ("m.C.m", _R, "|lam"),
        ("m.C.m.inner", _R, "|in"),
        ("m.C.m", _R, "|meth"),
        ("m.g", _R, "|dec"),
        ("m.g", _R, "|default"),
        ("m.g", _R, "|body"),
    }


def test_key_edges_score_one_and_carry_their_line() -> None:
    _nodes, edges = PythonExtractor().parse('def f(d):\n\n    d["a"]\n', "m.py")
    (edge,) = [e for e in edges if e.target.startswith(RAW_KEY_PREFIX)]
    assert edge.confidence == 1.0
    assert edge.line_number == 3
    assert edge.file_path == "m.py"


def test_module_string_constants_are_recorded_on_the_file() -> None:
    code = (
        'EVENTS = "events"\nOTHER = 3\nJOINED = "a" "b"\nTYPED: str = "t"\n'
        'EVENTS = "events-v2"\nclass C:\n    INNER = "x"\ndef f():\n    LOCAL = "y"\n'
    )
    nodes, _edges = PythonExtractor().parse(code, "m.py")
    (file_node,) = [n for n in nodes if n.type is NodeType.FILE]
    assert file_node.metadata["str_constants"] == {"EVENTS": "events-v2"}
    assert not [n for n in nodes if n.type is NodeType.VARIABLE]


def test_a_file_without_string_constants_records_none() -> None:
    nodes, _edges = PythonExtractor().parse("X = 1\n", "m.py")
    (file_node,) = [n for n in nodes if n.type is NodeType.FILE]
    assert "str_constants" not in file_node.metadata
