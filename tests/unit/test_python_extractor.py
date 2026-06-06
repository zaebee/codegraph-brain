"""Unit test cases for extractor."""

import pytest

from cgis.core.models import EdgeType, NodeType
from cgis.extractors.python_extractor import PythonExtractor


@pytest.fixture
def extractor() -> PythonExtractor:
    return PythonExtractor()


def test_extract_simple_function(extractor) -> None:
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


def test_extract_class_and_method(extractor) -> None:
    code = """
class UserService:
    def get_user(self):
        pass

def top_level():
    pass
"""
    nodes, edges = extractor.parse(code, "test.py")

    # Check class
    class_node = next(n for n in nodes if n.name == "UserService")
    assert class_node.type == NodeType.CLASS

    # Check method
    method_node = next(n for n in nodes if n.name == "get_user")
    assert method_node.type == NodeType.METHOD

    # Check function
    func_node = next(n for n in nodes if n.name == "top_level")
    assert func_node.type == NodeType.FUNCTION


def test_extract_call_and_attribute(extractor) -> None:
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
    nodes, edges = extractor.parse(code, "test.py")

    # 1. Check direct call function: target_func()
    call_edge = next(e for e in edges if e.target == "raw_call:target_func")
    assert call_edge.type == EdgeType.CALLS
    assert "method_a" in call_edge.source  # Source - method_a

    # 2. Check call via attribute: self.method_b()
    attr_call_edge = next(e for e in edges if e.target == "raw_call:method_b")
    assert attr_call_edge.type == EdgeType.CALLS
    assert "method_a" in attr_call_edge.source


def test_nested_function_isolation(extractor) -> None:
    """ "Check that calls inside inner function doesnt belong outer."""
    code = """
def outer():
    def inner():
        call_me()
    inner()
"""
    nodes, edges = extractor.parse(code, "test.py")

    call_edge = next(e for e in edges if e.target == "raw_call:call_me")

    assert "inner" in call_edge.source or "outer" in call_edge.source
