"""A receiver annotated `Any` names no type, so a call on it stays unresolved (#636).

Before the fix, `self.svc.build_cards()` with `svc: Any` resolved to
`typing.Any.build_cards` at confidence 1.0 — a confident wrong edge that the
coverage note cannot flag, because it counts only unresolved calls. owner-api
had 195 of them.
"""

import pytest

from cgis.core.models import EdgeType
from cgis.languages.python.extractor import PythonExtractor
from cgis.resolver.engine import ResolverEngine

_SERVICE = "class CatalogService:\n    def build_cards(self):\n        return []\n"


def _calls_from(code: str, source: str) -> set[str]:
    """Resolve one module next to a CatalogService and return `source`'s CALLS targets."""
    extractor = PythonExtractor()
    nodes_a, edges_a = extractor.parse(_SERVICE, "pkg/catalog.py")
    nodes_b, edges_b = extractor.parse(code, "pkg/user.py")
    resolved, _ = ResolverEngine(nodes_a + nodes_b, edges_a + edges_b).resolve()
    return {e.target for e in resolved if e.type == EdgeType.CALLS and e.source == source}


def _class_self_types(code: str, class_fqn: str) -> dict[str, str]:
    """Parse source and return the self_types metadata of one class node."""
    nodes, _ = PythonExtractor().parse(code, "pkg/user.py")
    types: dict[str, str] = (
        next(n for n in nodes if n.id == class_fqn).metadata.get("self_types") or {}
    )
    return types


@pytest.mark.parametrize(
    ("imports", "annotation"),
    [
        ("from typing import Any\n", "Any"),
        ("import typing\n", "typing.Any"),
        ("import typing as t\n", "t.Any"),
        ("from typing import Any as Anything\n", "Anything"),
        ("from typing_extensions import Any\n", "Any"),
        ("from typing import Any, Optional\n", "Optional[Any]"),
    ],
)
def test_an_attribute_assigned_from_an_any_parameter_has_no_declared_type(
    imports: str, annotation: str
) -> None:
    code = (
        f"{imports}"
        "class Favorites:\n"
        f"    def __init__(self, catalog: {annotation}) -> None:\n"
        "        self.catalog = catalog\n"
    )
    assert _class_self_types(code, "pkg.user.Favorites") == {}


def test_a_class_body_any_annotation_declares_no_type() -> None:
    code = "from typing import Any\nclass Favorites:\n    catalog: Any\n"
    assert _class_self_types(code, "pkg.user.Favorites") == {}


def test_a_call_on_an_any_typed_attribute_does_not_resolve_into_typing() -> None:
    code = (
        "from typing import Any\n"
        "class Favorites:\n"
        "    def __init__(self, catalog: Any) -> None:\n"
        "        self.catalog = catalog\n"
        "    def hydrate(self):\n"
        "        return self.catalog.build_cards()\n"
    )
    targets = _calls_from(code, "pkg.user.Favorites.hydrate")
    assert not any(t.startswith(("typing.", "typing_extensions.")) for t in targets), targets


def test_a_call_on_an_any_typed_parameter_does_not_resolve_into_typing() -> None:
    code = (
        "from typing import Any\ndef fetch_docs(client: Any, ids):\n    return client.mget(ids)\n"
    )
    targets = _calls_from(code, "pkg.user.fetch_docs")
    assert not any(t.startswith("typing.") for t in targets), targets


def test_a_concrete_annotation_still_resolves() -> None:
    """The control: the same shape with a real type keeps its edge."""
    code = (
        "from pkg.catalog import CatalogService\n"
        "class Favorites:\n"
        "    def __init__(self, catalog: CatalogService) -> None:\n"
        "        self.catalog = catalog\n"
        "    def hydrate(self):\n"
        "        return self.catalog.build_cards()\n"
    )
    assert "pkg.catalog.CatalogService.build_cards" in _calls_from(
        code, "pkg.user.Favorites.hydrate"
    )


@pytest.mark.parametrize(
    "body",
    [
        "    def __init__(self, c) -> None:\n        self.catalog: Any = c\n",
        "    catalog: 'Any'\n",
        "    def __init__(self, catalog: AnyDep) -> None:\n        self.catalog = catalog\n",
    ],
    ids=["annotated-self-assignment", "string-annotation", "annotated-di-alias"],
)
def test_other_any_spellings_declare_no_type(body: str) -> None:
    code = (
        "from typing import Annotated, Any\n"
        "from fastapi import Depends\n"
        "AnyDep = Annotated[Any, Depends(make)]\n"
        f"class Favorites:\n{body}"
        "    def hydrate(self):\n"
        "        return self.catalog.build_cards()\n"
    )
    targets = _calls_from(code, "pkg.user.Favorites.hydrate")
    assert not any(t.startswith("typing.") for t in targets), targets


def test_an_any_parameter_does_not_overwrite_a_declared_attribute_type() -> None:
    """`catalog: CatalogService` in the class body survives `self.catalog = <Any param>`."""
    code = (
        "from typing import Any\n"
        "from pkg.catalog import CatalogService\n"
        "class Favorites:\n"
        "    catalog: CatalogService\n"
        "    def __init__(self, catalog: Any) -> None:\n"
        "        self.catalog = catalog\n"
        "    def hydrate(self):\n"
        "        return self.catalog.build_cards()\n"
    )
    assert "pkg.catalog.CatalogService.build_cards" in _calls_from(
        code, "pkg.user.Favorites.hydrate"
    )


def test_a_with_bound_any_declares_no_type() -> None:
    """The constructor/`with` path guards None too (no one writes `Any()`, but the guard exists)."""
    code = (
        "from typing import Any\ndef run():\n    with Any() as c:\n        return c.build_cards()\n"
    )
    # `Any()` itself is a real call to typing.Any; only a method *on* it is wrong.
    assert not any(t.startswith("typing.Any.") for t in _calls_from(code, "pkg.user.run"))
