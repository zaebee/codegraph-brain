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


def test_module_qualified_constructor_is_recorded() -> None:
    """`self.c = client.SearchClient()` records the type when `client` is imported."""
    code = (
        "from pkg import client\n"
        "class A:\n"
        "    def __init__(self) -> None:\n"
        "        self.c = client.SearchClient()\n"
    )
    assert _class_self_types(code, "pkg.mod.A") == {"c": "pkg.client.SearchClient"}


def test_capitalised_method_on_a_local_object_is_not_a_constructor() -> None:
    """`self.c = factory.Build()` is a result, not a construction — no type recorded.

    The prefix must be a known import alias. Without that test a builder-style
    attribute would be typed as whatever its factory method happens to be called.
    """
    code = "class A:\n    def __init__(self, factory) -> None:\n        self.c = factory.Build()\n"
    assert _class_self_types(code, "pkg.mod.A") == {}


def test_lowercase_call_on_an_imported_module_is_not_a_constructor() -> None:
    """`self.c = client.make_it()` names no class — no type recorded."""
    code = (
        "from pkg import client\n"
        "class A:\n"
        "    def __init__(self) -> None:\n"
        "        self.c = client.make_it()\n"
    )
    assert _class_self_types(code, "pkg.mod.A") == {}


def test_private_class_constructor_is_recorded() -> None:
    """`self.a = _IdAllocator()` is a construction — a leading underscore is a convention.

    This repo has such classes, so a capitalisation test that does not strip the
    underscore silently drops every private collaborator from self_types.
    """
    code = (
        "from pkg.x import _IdAllocator\n"
        "class A:\n"
        "    def __init__(self) -> None:\n"
        "        self.a = _IdAllocator()\n"
    )
    assert _class_self_types(code, "pkg.mod.A") == {"a": "pkg.x._IdAllocator"}


def test_lowercase_factory_call_is_still_not_a_constructor() -> None:
    """Stripping underscores must not weaken the test: `make()` names no class."""
    code = (
        "from pkg.x import make\n"
        "class A:\n"
        "    def __init__(self) -> None:\n"
        "        self.a = make()\n"
    )
    assert _class_self_types(code, "pkg.mod.A") == {}
