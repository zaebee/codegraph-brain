"""Unit test cases for extractor."""

import pytest

from cgis.core.models import EdgeType, NodeType
from cgis.extractors.python_extractor import PythonExtractor, file_path_to_module_fqn


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

    call_edges = [e for e in edges if e.type == EdgeType.CALLS]
    assert len(call_edges) == 1
    assert call_edges[0].target == "raw_call:print"


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
    call_edges = [e for e in edges if e.type == EdgeType.CALLS]
    assert len(call_edges) == 1
    assert call_edges[0].target == "raw_call:print"


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
    """`from cgis.pipeline import IngestionPipeline` populates import_map correctly."""
    nodes, edges = extractor.parse("from cgis.pipeline import IngestionPipeline\n", "mod.py")
    file_node = next(n for n in nodes if n.type == NodeType.FILE)
    assert (
        file_node.metadata["import_map"]["IngestionPipeline"] == "cgis.pipeline.IngestionPipeline"
    )
    imports_edge = next(e for e in edges if e.type == EdgeType.IMPORTS)
    assert imports_edge.target == "cgis.pipeline"


def test_extract_from_import_aliased(extractor: PythonExtractor) -> None:
    """`import X as Y` alias stored under alias key in import_map."""
    nodes, _ = extractor.parse("from cgis.pipeline import IngestionPipeline as IP\n", "mod.py")
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
    """`from . import base` resolves to sibling module in import_map."""
    nodes, _ = extractor.parse("from . import base\n", "src/cgis/extractors/python_extractor.py")
    file_node = next(n for n in nodes if n.type == NodeType.FILE)
    assert file_node.metadata["import_map"]["base"] == "src.cgis.extractors.base"


def test_extract_relative_import_two_dots(extractor: PythonExtractor) -> None:
    """`from ..core import models` in src.cgis.extractors.x → {"models": "src.cgis.core.models"}."""
    nodes, _ = extractor.parse("from ..core import models\n", "src/cgis/extractors/x.py")
    file_node = next(n for n in nodes if n.type == NodeType.FILE)
    assert file_node.metadata["import_map"]["models"] == "src.cgis.core.models"


def test_extract_dotted_import_maps_local_name(extractor: PythonExtractor) -> None:
    """`import os.path` → import_map["os"] == "os", IMPORTS edge targets full module."""
    nodes, edges = extractor.parse("import os.path\n", "mod.py")
    file_node = next(n for n in nodes if n.type == NodeType.FILE)
    assert file_node.metadata["import_map"]["os"] == "os"
    imports_edge = next(e for e in edges if e.type == EdgeType.IMPORTS)
    assert imports_edge.target == "os.path"


def test_extract_parenthesized_import(extractor: PythonExtractor) -> None:
    """`from typing import (Dict, List)` — import_list node flattened correctly."""
    nodes, _ = extractor.parse("from typing import (\n    Dict,\n    List,\n)\n", "mod.py")
    file_node = next(n for n in nodes if n.type == NodeType.FILE)
    imap = file_node.metadata["import_map"]
    assert imap["Dict"] == "typing.Dict"
    assert imap["List"] == "typing.List"


def test_extract_wildcard_import_does_not_crash(extractor: PythonExtractor) -> None:
    """`from module import *` must not raise and produces an IMPORTS edge."""
    nodes, edges = extractor.parse("from module import *\n", "mod.py")
    file_node = next(n for n in nodes if n.type == NodeType.FILE)
    assert isinstance(file_node.metadata["import_map"], dict)
    imports_edge = next(e for e in edges if e.type == EdgeType.IMPORTS)
    assert imports_edge.target == "module"


# --- OOP & Decorator semantics tests ---


def test_extract_class_inheritance(extractor: PythonExtractor) -> None:
    """class Child(Parent) emits EXTENDS edge with raw_class: target."""
    _nodes, edges = extractor.parse("class Child(Parent): pass\n", "mod.py")
    extends_edges = [e for e in edges if e.type == EdgeType.EXTENDS]
    assert len(extends_edges) == 1
    assert extends_edges[0].source == "mod.Child"
    assert extends_edges[0].target == "raw_class:Parent"


def test_extract_multiple_bases(extractor: PythonExtractor) -> None:
    """class C(A, B) emits two EXTENDS edges."""
    _nodes, edges = extractor.parse("class C(A, B): pass\n", "mod.py")
    extends_edges = [e for e in edges if e.type == EdgeType.EXTENDS]
    targets = {e.target for e in extends_edges}
    assert targets == {"raw_class:A", "raw_class:B"}


def test_extract_abstract_class_marker(extractor: PythonExtractor) -> None:
    """class Base(ABC) sets metadata['is_abstract'] = True."""
    nodes, _edges = extractor.parse("class Base(ABC): pass\n", "mod.py")
    class_node = next(n for n in nodes if n.name == "Base")
    assert class_node.metadata.get("is_abstract") is True


def test_extract_abstract_method_marker(extractor: PythonExtractor) -> None:
    """@abstractmethod sets metadata['is_abstract'] = True on the method."""
    code = "class Base:\n    @abstractmethod\n    def run(self): pass\n"
    nodes, _edges = extractor.parse(code, "mod.py")
    method_node = next(n for n in nodes if n.name == "run")
    assert method_node.metadata.get("is_abstract") is True


def test_extract_decorator_stored_in_metadata(extractor: PythonExtractor) -> None:
    """@app.command() stores 'app.command' in metadata['decorators']."""
    code = "@app.command()\ndef cmd(): pass\n"
    nodes, _edges = extractor.parse(code, "mod.py")
    func_node = next(n for n in nodes if n.name == "cmd")
    assert "app.command" in func_node.metadata.get("decorators", [])


def test_extract_decorator_emits_calls_edge(extractor: PythonExtractor) -> None:
    """@app.command() emits a CALLS edge from the function to raw_call:app.command."""
    code = "@app.command()\ndef cmd(): pass\n"
    _nodes, edges = extractor.parse(code, "mod.py")
    deco_edges = [e for e in edges if e.type == EdgeType.CALLS and e.source == "mod.cmd"]
    assert any(e.target == "raw_call:app.command" for e in deco_edges)


def test_extract_decorated_method_correct_fqn(extractor: PythonExtractor) -> None:
    """Decorated method inside a class gets the correct FQN and NodeType.METHOD."""
    code = "class API:\n    @mcp.tool()\n    def search(self): pass\n"
    nodes, _edges = extractor.parse(code, "svc.py")
    method_node = next(n for n in nodes if n.name == "search")
    assert method_node.id == "svc.API.search"
    assert method_node.type == NodeType.METHOD


def test_extract_abstract_class_via_metaclass_kwarg(extractor: PythonExtractor) -> None:
    """class Foo(metaclass=ABCMeta) sets metadata['is_abstract'] = True."""
    nodes, _edges = extractor.parse("class Foo(metaclass=ABCMeta): pass\n", "mod.py")
    class_node = next(n for n in nodes if n.name == "Foo")
    assert class_node.metadata.get("is_abstract") is True


def test_extract_abstract_class_via_qualified_metaclass(extractor: PythonExtractor) -> None:
    """class Foo(metaclass=abc.ABCMeta) also sets is_abstract."""
    nodes, _edges = extractor.parse("class Foo(metaclass=abc.ABCMeta): pass\n", "mod.py")
    class_node = next(n for n in nodes if n.name == "Foo")
    assert class_node.metadata.get("is_abstract") is True


def test_extract_metaclass_does_not_emit_extends_edge(extractor: PythonExtractor) -> None:
    """metaclass= kwarg must NOT produce an EXTENDS edge."""
    _nodes, edges = extractor.parse("class Foo(metaclass=ABCMeta): pass\n", "mod.py")
    extends = [e for e in edges if e.type == EdgeType.EXTENDS]
    assert not extends


# --- Coverage gap tests ---


def test_file_path_to_module_fqn_windows_path() -> None:
    """Windows drive letter (C:\\...) is stripped before conversion."""
    result = file_path_to_module_fqn("C:\\path\\to\\mod.py")
    assert result == "path.to.mod"


def test_extract_call_inside_parenthesized_expression(extractor: PythonExtractor) -> None:
    """A call whose function is a parenthesized expression is still extracted."""
    code = "def fn():\n    (my_func)()\n"
    _nodes, edges = extractor.parse(code, "mod.py")
    call_edges = [e for e in edges if e.type == EdgeType.CALLS]
    assert any("my_func" in e.target for e in call_edges)


# --- source_root tests ---


def test_file_path_to_module_fqn_with_source_root() -> None:
    """source_root strips the leading directory before dotting."""
    assert file_path_to_module_fqn("src/cgis/pipeline.py", source_root="src") == "cgis.pipeline"


def test_file_path_to_module_fqn_source_root_init() -> None:
    """__init__.py suffix is still dropped after source_root stripping."""
    assert file_path_to_module_fqn("src/cgis/__init__.py", source_root="src") == "cgis"


def test_file_path_to_module_fqn_source_root_trailing_slash() -> None:
    """Trailing slash in source_root is handled gracefully."""
    assert file_path_to_module_fqn("src/cgis/pipeline.py", source_root="src/") == "cgis.pipeline"


def test_file_path_to_module_fqn_source_root_no_match() -> None:
    """When file_path does not start with source_root, no stripping occurs."""
    assert file_path_to_module_fqn("lib/utils.py", source_root="src") == "lib.utils"


def test_python_extractor_source_roots_strips_prefix() -> None:
    """PythonExtractor(source_roots=['src']) yields FQNs without the 'src.' prefix."""
    code = "def hello(): pass\n"
    ext = PythonExtractor(source_roots=["src"])
    nodes, _ = ext.parse(code, "src/mymod/utils.py")
    fqns = [n.id for n in nodes]
    assert all(not fqn.startswith("src.") for fqn in fqns)
    assert any(fqn.startswith("mymod.") for fqn in fqns)


def test_python_extractor_source_roots_none_is_unchanged() -> None:
    """PythonExtractor() without source_roots behaves as before."""
    code = "def hello(): pass\n"
    nodes, _ = PythonExtractor().parse(code, "src/mymod/utils.py")
    fqns = [n.id for n in nodes]
    assert any(fqn.startswith("src.mymod.") for fqn in fqns)


def test_python_extractor_source_roots_unmatched_root() -> None:
    """If no source_root matches the file, FQN is built from the full path."""
    code = "def hello(): pass\n"
    ext = PythonExtractor(source_roots=["lib"])
    nodes, _ = ext.parse(code, "src/mymod/utils.py")
    fqns = [n.id for n in nodes]
    assert any(fqn.startswith("src.mymod.") for fqn in fqns)


def test_file_path_to_module_fqn_dot_slash_source_root() -> None:
    """CI ingests with `./src` — the ./ prefix must not defeat root stripping."""
    assert file_path_to_module_fqn("src/cgis/pipeline.py", "./src") == "cgis.pipeline"
