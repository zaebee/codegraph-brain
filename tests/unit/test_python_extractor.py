"""Unit test cases for extractor."""

import pytest

from cgis.core.models import EdgeType, NodeType
from cgis.extractors.python_extractor import PythonExtractor


@pytest.fixture
def extractor() -> PythonExtractor:
    return PythonExtractor()


def test_extract_simple_function(extractor: PythonExtractor) -> None:
    code = """
def hello():
    print("world")
"""
    nodes, edges = extractor.parse(code, "test.py")

    # Check node
    func_node = next(n for n in nodes if n.name == "hello")
    assert func_node.type == NodeType.FUNCTION
    assert func_node.file_path == "test.py"

    assert len(edges) == 1
    assert edges[0].target == "raw_call:print"


def test_extract_class_and_method(extractor: PythonExtractor) -> None:
    code = """
class UserService:
    def get_user(self):
        pass

def top_level():
    pass
"""
    nodes, _edges = extractor.parse(code, "test.py")

    # Check class
    class_node = next(n for n in nodes if n.name == "UserService")
    assert class_node.type == NodeType.CLASS

    # Check method
    method_node = next(n for n in nodes if n.name == "get_user")
    assert method_node.type == NodeType.METHOD
    assert method_node.id == "test.UserService.get_user"

    # Check function
    func_node = next(n for n in nodes if n.name == "top_level")
    assert func_node.type == NodeType.FUNCTION


def test_extract_call_and_attribute(extractor: PythonExtractor) -> None:
    code = """
def target_func():
    pass

class MyClass:
    def method_a(self):
        target_func()
        self.method_b()

    def method_b(self):
        pass
"""
    _nodes, edges = extractor.parse(code, "test.py")

    # 1. Check direct call function: target_func()
    call_edge = next(e for e in edges if e.target == "raw_call:target_func")
    assert call_edge.type == EdgeType.CALLS
    assert call_edge.source == "test.MyClass.method_a"

    # 2. Check call via attribute: self.method_b()
    attr_call_edge = next(e for e in edges if e.target == "raw_call:self.method_b")
    assert attr_call_edge.type == EdgeType.CALLS
    assert attr_call_edge.source == "test.MyClass.method_a"


def test_nested_function_isolation(extractor: PythonExtractor) -> None:
    """Check that calls inside inner function don't belong to outer."""
    code = """
def outer():
    def inner():
        call_me()
    inner()
"""
    _nodes, edges = extractor.parse(code, "test.py")

    call_edge = next(e for e in edges if e.target == "raw_call:call_me")

    assert call_edge.source == "test.outer.inner"


def test_extract_async_function(extractor: PythonExtractor) -> None:
    """Verify that async functions are correctly extracted as FUNCTION nodes."""
    code = """
async def fetch_data():
    await print("done")
"""
    nodes, edges = extractor.parse(code, "test.py")
    func_node = next(n for n in nodes if n.name == "fetch_data")
    assert func_node.type == NodeType.FUNCTION
    assert len(edges) == 1
    assert edges[0].target == "raw_call:print"


# --- Import extraction tests ---


def test_extract_creates_file_node(extractor: PythonExtractor) -> None:
    """parse() always produces a FILE node for the given file."""
    nodes, _ = extractor.parse("def f(): pass", "src/mod.py")
    file_node = next(n for n in nodes if n.type == NodeType.FILE)
    assert file_node.id == "src.mod"
    assert file_node.file_path == "src/mod.py"


def test_extract_simple_import(extractor: PythonExtractor) -> None:
    """`import os` → import_map has {"os": "os"} and an IMPORTS edge."""
    nodes, edges = extractor.parse("import os\n", "mod.py")
    file_node = next(n for n in nodes if n.type == NodeType.FILE)
    assert file_node.metadata["import_map"]["os"] == "os"
    imports_edge = next(e for e in edges if e.type == EdgeType.IMPORTS)
    assert imports_edge.target == "os"


def test_extract_aliased_import(extractor: PythonExtractor) -> None:
    """`import json as js` → import_map has {"js": "json"}."""
    nodes, _ = extractor.parse("import json as js\n", "mod.py")
    file_node = next(n for n in nodes if n.type == NodeType.FILE)
    assert file_node.metadata["import_map"]["js"] == "json"


def test_extract_from_import_direct(extractor: PythonExtractor) -> None:
    """`from cgis.pipeline import IngestionPipeline` → {"IngestionPipeline": "cgis.pipeline.IngestionPipeline"}."""
    nodes, edges = extractor.parse(
        "from cgis.pipeline import IngestionPipeline\n", "mod.py"
    )
    file_node = next(n for n in nodes if n.type == NodeType.FILE)
    assert file_node.metadata["import_map"]["IngestionPipeline"] == "cgis.pipeline.IngestionPipeline"
    imports_edge = next(e for e in edges if e.type == EdgeType.IMPORTS)
    assert imports_edge.target == "cgis.pipeline"


def test_extract_from_import_aliased(extractor: PythonExtractor) -> None:
    """`from cgis.pipeline import IngestionPipeline as IP` → {"IP": "cgis.pipeline.IngestionPipeline"}."""
    nodes, _ = extractor.parse(
        "from cgis.pipeline import IngestionPipeline as IP\n", "mod.py"
    )
    file_node = next(n for n in nodes if n.type == NodeType.FILE)
    assert file_node.metadata["import_map"]["IP"] == "cgis.pipeline.IngestionPipeline"


def test_extract_from_import_multiple_names(extractor: PythonExtractor) -> None:
    """`from typing import Dict, List` → both mapped."""
    nodes, _ = extractor.parse("from typing import Dict, List\n", "mod.py")
    file_node = next(n for n in nodes if n.type == NodeType.FILE)
    imap = file_node.metadata["import_map"]
    assert imap["Dict"] == "typing.Dict"
    assert imap["List"] == "typing.List"


def test_extract_relative_import_one_dot(extractor: PythonExtractor) -> None:
    """`from . import base` in src.cgis.extractors.python_extractor → {"base": "src.cgis.extractors.base"}."""
    nodes, _ = extractor.parse(
        "from . import base\n", "src/cgis/extractors/python_extractor.py"
    )
    file_node = next(n for n in nodes if n.type == NodeType.FILE)
    assert file_node.metadata["import_map"]["base"] == "src.cgis.extractors.base"


def test_extract_relative_import_two_dots(extractor: PythonExtractor) -> None:
    """`from ..core import models` in src.cgis.extractors.x → {"models": "src.cgis.core.models"}."""
    nodes, _ = extractor.parse(
        "from ..core import models\n", "src/cgis/extractors/x.py"
    )
    file_node = next(n for n in nodes if n.type == NodeType.FILE)
    assert file_node.metadata["import_map"]["models"] == "src.cgis.core.models"


def test_extract_wildcard_import_does_not_crash(extractor: PythonExtractor) -> None:
    """`from module import *` must not raise and produces an IMPORTS edge."""
    nodes, edges = extractor.parse("from module import *\n", "mod.py")
    file_node = next(n for n in nodes if n.type == NodeType.FILE)
    assert isinstance(file_node.metadata["import_map"], dict)
    imports_edge = next(e for e in edges if e.type == EdgeType.IMPORTS)
    assert imports_edge.target == "module"
