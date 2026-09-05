"""The four sources that populate a class's self_types map (spec D1)."""

from cgis.extractors.python_extractor import PythonExtractor


def _class_self_types(code: str, class_fqn: str) -> dict[str, str]:
    """Parse source and return the self_types metadata of one class node."""
    nodes, _ = PythonExtractor().parse(code, "pkg/mod.py")
    for node in nodes:
        if node.id == class_fqn:
            types: dict[str, str] = node.metadata.get("self_types") or {}
            return types
    msg = f"no node {class_fqn} in {[n.id for n in nodes]}"
    raise AssertionError(msg)


def test_init_parameter_assigned_to_attribute() -> None:
    code = (
        "from pkg.client import SearchClient\n"
        "class A:\n"
        "    def __init__(self, client: SearchClient) -> None:\n"
        "        self.client = client\n"
    )
    assert _class_self_types(code, "pkg.mod.A") == {"client": "pkg.client.SearchClient"}


def test_class_body_annotation() -> None:
    code = "from pkg.client import SearchClient\nclass A:\n    client: SearchClient\n"
    assert _class_self_types(code, "pkg.mod.A") == {"client": "pkg.client.SearchClient"}


def test_annotated_attribute_assignment() -> None:
    code = (
        "from pkg.client import SearchClient\n"
        "class A:\n"
        "    def __init__(self) -> None:\n"
        "        self.client: SearchClient = build()\n"
    )
    assert _class_self_types(code, "pkg.mod.A") == {"client": "pkg.client.SearchClient"}


def test_constructor_assignment() -> None:
    code = (
        "from pkg.client import SearchClient\n"
        "class A:\n"
        "    def __init__(self) -> None:\n"
        "        self.client = SearchClient()\n"
    )
    assert _class_self_types(code, "pkg.mod.A") == {"client": "pkg.client.SearchClient"}


def test_unannotated_attribute_is_not_guessed() -> None:
    code = "class A:\n    def __init__(self, client) -> None:\n        self.client = client\n"
    assert _class_self_types(code, "pkg.mod.A") == {}


def test_generic_annotation_records_the_container_type() -> None:
    code = "from pkg.models import Node\nclass A:\n    items: list[Node]\n"
    assert _class_self_types(code, "pkg.mod.A") == {"items": "pkg.mod.list"}


def test_annotated_local_variable_in_a_method_is_not_a_self_type() -> None:
    """An annotated local variable inside a method is not a class attribute.

    Only `assigned_attr_name` sees the bare identifier `y` here, so without
    checking that the assignment is at true class-body level (not inside a
    method), the identifier+type branch would misfile it as if it were an
    attribute of A — corrupting the map with data that has nothing to do
    with `self`.
    """
    code = (
        "from pkg.client import SearchClient\n"
        "class A:\n"
        "    def m(self) -> None:\n"
        "        y: SearchClient = get_client()\n"
    )
    assert _class_self_types(code, "pkg.mod.A") == {}


def test_two_classes_keep_separate_maps() -> None:
    code = (
        "from pkg.client import SearchClient\n"
        "from pkg.other import Other\n"
        "class A:\n"
        "    client: SearchClient\n"
        "class B:\n"
        "    client: Other\n"
    )
    assert _class_self_types(code, "pkg.mod.A") == {"client": "pkg.client.SearchClient"}
    assert _class_self_types(code, "pkg.mod.B") == {"client": "pkg.other.Other"}
