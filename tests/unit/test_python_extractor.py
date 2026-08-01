"""Unit test cases for extractor."""

import pytest

from cgis.core.models import Edge, EdgeType, NodeType
from cgis.extractors._python_ast import resolve_relative_module
from cgis.extractors.python_extractor import PythonExtractor, file_path_to_module_fqn


@pytest.fixture
def extractor() -> PythonExtractor:
    return PythonExtractor()


def test_resolve_relative_module_no_leading_dots_returns_full_module() -> None:
    """leading_dots=0 must return the module unchanged (regression: [:-0] == [])."""
    assert resolve_relative_module("a.b.c", 0, "") == "a.b.c"
    assert resolve_relative_module("a.b.c", 0, "x") == "a.b.c.x"


def test_resolve_relative_module_trims_one_level_per_dot() -> None:
    """Each leading dot trims one trailing segment before appending the relative path."""
    assert resolve_relative_module("src.cgis.extractors.mod", 2, "core") == "src.cgis.core"
    assert resolve_relative_module("src.cgis.mod", 1, "") == "src.cgis"


def test_resolve_relative_module_package_keeps_own_segment() -> None:
    """Inside a package __init__.py the module FQN already IS the package (#194).

    `file_path_to_module_fqn` strips the /__init__ suffix, so one leading dot
    refers to the package itself and must trim one fewer segment than a regular
    module would.
    """
    # module_fqn "src.cgis" is the package src/cgis/__init__.py
    assert resolve_relative_module("src.cgis", 1, "", is_package=True) == "src.cgis"
    assert resolve_relative_module("src.cgis", 1, "sub", is_package=True) == "src.cgis.sub"
    assert resolve_relative_module("src.cgis", 2, "other", is_package=True) == "src.other"


def test_relative_import_in_package_init_keeps_package(extractor: PythonExtractor) -> None:
    """Relative imports written inside __init__.py resolve against the package (#194)."""
    nodes, _ = extractor.parse(
        "from . import pipeline\nfrom .sub import X\n", "src/cgis/__init__.py"
    )
    import_map = nodes[0].metadata["import_map"]
    assert import_map["pipeline"] == "src.cgis.pipeline"
    assert import_map["X"] == "src.cgis.sub.X"


def test_annotated_param_type_unwraps_to_inner_type(extractor: PythonExtractor) -> None:
    """`Annotated[T, ...]` param annotations resolve to T, not the wrapper (#194)."""
    code = "def f(db: Annotated[Session, Depends(get_db)]):\n    pass\n"
    _, edges = extractor.parse(code, "m.py")
    dep_targets = [e.target for e in edges if e.target.startswith("raw_dep:")]
    assert "raw_dep:Session" in dep_targets
    assert "raw_dep:Annotated" not in dep_targets


def test_annotated_nested_generic_first_arg_not_truncated(extractor: PythonExtractor) -> None:
    """`Annotated[dict[str, int], ...]` unwraps to the base `dict`, not `dict[str` (#194).

    Guards the bracket-depth-aware first-arg split: a naive comma split would
    truncate the nested generic at its inner comma.
    """
    code = "def f(db: Annotated[dict[str, int], Depends(get_db)]):\n    pass\n"
    _, edges = extractor.parse(code, "m.py")
    dep_targets = [e.target for e in edges if e.target.startswith("raw_dep:")]
    assert "raw_dep:dict" in dep_targets


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


def test_depends_in_param_default_emits_depends_on(extractor: PythonExtractor) -> None:
    """Depends(fn) as a plain default value emits one DEPENDS_ON edge from the handler."""
    code = """
from fastapi import Depends

def get_db():
    pass

def handler(db = Depends(get_db)):
    pass
"""
    _nodes, edges = extractor.parse(code, "api.py")

    dep_edges = [e for e in edges if e.type == EdgeType.DEPENDS_ON]
    assert len(dep_edges) == 1
    assert dep_edges[0].source == "api.handler"
    assert dep_edges[0].target == "raw_call:get_db"
    # The plain CALLS edge to Depends itself is unchanged (regression guard)
    assert any(e.type == EdgeType.CALLS and e.target == "raw_call:Depends" for e in edges)


def test_depends_inside_annotated_emits_depends_on(extractor: PythonExtractor) -> None:
    """Depends(fn) nested inside Annotated[...] type annotation emits a DEPENDS_ON edge."""
    code = """
from typing import Annotated
from fastapi import Depends

def resolve_owner():
    pass

def handler(owner: Annotated[object, Depends(resolve_owner)]):
    pass
"""
    _nodes, edges = extractor.parse(code, "api.py")

    dep_edges = [e for e in edges if e.type == EdgeType.DEPENDS_ON]
    assert any(
        e.source == "api.handler" and e.target == "raw_call:resolve_owner" for e in dep_edges
    )


def test_depends_under_union_wrapper_emits_depends_on(extractor: PythonExtractor) -> None:
    """Depends(fn) inside Annotated[...] | None still emits a DEPENDS_ON edge."""
    code = """
from typing import Annotated
from fastapi import Depends

def resolve_owner():
    pass

def handler(owner: Annotated[object, Depends(resolve_owner)] | None = None):
    pass
"""
    _nodes, edges = extractor.parse(code, "api.py")

    assert any(
        e.type == EdgeType.DEPENDS_ON and e.target == "raw_call:resolve_owner" for e in edges
    )


def test_security_call_emits_depends_on(extractor: PythonExtractor) -> None:
    """Security(fn) as a parameter default emits a DEPENDS_ON edge, same as Depends."""
    code = """
from fastapi import Security

def get_scopes():
    pass

def handler(scopes = Security(get_scopes)):
    pass
"""
    _nodes, edges = extractor.parse(code, "api.py")

    assert any(e.type == EdgeType.DEPENDS_ON and e.target == "raw_call:get_scopes" for e in edges)


def test_argless_depends_emits_no_depends_on(extractor: PythonExtractor) -> None:
    """Depends() with no argument emits no DEPENDS_ON edge (no provider to link)."""
    code = """
from fastapi import Depends

def handler(db = Depends()):
    pass
"""
    _nodes, edges = extractor.parse(code, "api.py")

    assert not any(e.type == EdgeType.DEPENDS_ON for e in edges)


def test_depends_with_lambda_arg_emits_no_depends_on(extractor: PythonExtractor) -> None:
    """Depends(lambda: ...) with a non-name first arg emits no DEPENDS_ON edge (spec §3.2b)."""
    code = """
from fastapi import Depends

def handler(db = Depends(lambda: None)):
    pass
"""
    _nodes, edges = extractor.parse(code, "api.py")

    assert not any(e.type == EdgeType.DEPENDS_ON for e in edges)


def test_module_level_di_alias_annotated_form(extractor: PythonExtractor) -> None:
    """Module-level Annotated[..., Depends(...)] emits VARIABLE node and DEPENDS_ON edge."""
    code = """
from typing import Annotated
from fastapi import Depends

def resolve_owner():
    pass

ResolvedOwnerDep = Annotated[object, Depends(resolve_owner)]
"""
    nodes, edges = extractor.parse(code, "deps.py")

    alias = next(n for n in nodes if n.name == "ResolvedOwnerDep")
    assert alias.type == NodeType.VARIABLE
    assert alias.id == "deps.ResolvedOwnerDep"

    dep_edges = [e for e in edges if e.type == EdgeType.DEPENDS_ON]
    assert len(dep_edges) == 1
    assert dep_edges[0].source == "deps.ResolvedOwnerDep"
    assert dep_edges[0].target == "raw_call:resolve_owner"


def test_module_level_di_alias_direct_form(extractor: PythonExtractor) -> None:
    """Module-level DbDep = Depends(get_db) emits VARIABLE node and DEPENDS_ON edge."""
    code = """
from fastapi import Depends

def get_db():
    pass

DbDep = Depends(get_db)
"""
    nodes, edges = extractor.parse(code, "deps.py")

    alias = next(n for n in nodes if n.name == "DbDep")
    assert alias.type == NodeType.VARIABLE
    assert any(
        e.type == EdgeType.DEPENDS_ON and e.source == "deps.DbDep" and e.target == "raw_call:get_db"
        for e in edges
    )


def test_plain_module_constants_emit_no_nodes(extractor: PythonExtractor) -> None:
    """Plain module-level constants without DI calls produce no VARIABLE nodes or DEPENDS_ON."""
    code = """
LIMIT = 10
NAMES = ["a", "b"]
ALIAS = SomeType
"""
    nodes, edges = extractor.parse(code, "consts.py")

    assert not any(n.type == NodeType.VARIABLE for n in nodes)
    assert not any(e.type == EdgeType.DEPENDS_ON for e in edges)


def test_argless_depends_alias_node_without_edge(extractor: PythonExtractor) -> None:
    """Module-level Annotated[..., Depends()] emits a VARIABLE node but no DEPENDS_ON edge."""
    code = """
from typing import Annotated
from fastapi import Depends

AutoDep = Annotated[object, Depends()]
"""
    nodes, edges = extractor.parse(code, "deps.py")

    assert any(n.type == NodeType.VARIABLE and n.name == "AutoDep" for n in nodes)
    assert not any(e.type == EdgeType.DEPENDS_ON for e in edges)


def test_typed_param_emits_raw_dep_candidate(extractor: PythonExtractor) -> None:
    """Typed parameter annotation emits a speculative raw_dep: DEPENDS_ON candidate edge."""
    code = """
def handler(owner: PublishedOwnerDep):
    pass
"""
    _nodes, edges = extractor.parse(code, "routes.py")

    cands = [e for e in edges if e.target == "raw_dep:PublishedOwnerDep"]
    assert len(cands) == 1
    assert cands[0].type == EdgeType.DEPENDS_ON
    assert cands[0].source == "routes.handler"
    assert cands[0].confidence == pytest.approx(0.1)


def test_untyped_param_emits_no_raw_dep_candidate(extractor: PythonExtractor) -> None:
    """Untyped parameter produces no raw_dep: candidate edge."""
    code = """
def handler(owner):
    pass
"""
    _nodes, edges = extractor.parse(code, "routes.py")

    assert not any(e.target.startswith("raw_dep:") for e in edges)


def test_tuple_target_di_assignment_skipped(extractor: PythonExtractor) -> None:
    """Tuple-target assignments like `a, b = ...` are skipped (no VARIABLE node or DEPENDS_ON)."""
    code = """
from fastapi import Depends

def get_db():
    pass

a, b = Depends(get_db), None
"""
    nodes, edges = extractor.parse(code, "deps.py")

    assert not any(n.type == NodeType.VARIABLE for n in nodes)
    assert not any(e.type == EdgeType.DEPENDS_ON for e in edges)


def test_annotated_module_assignment_di_alias(extractor: PythonExtractor) -> None:
    """An annotated module assignment (X: T = Depends(f)) still yields an alias node + edge."""
    code = """
from fastapi import Depends

def get_db():
    pass

DbDep: object = Depends(get_db)
"""
    nodes, edges = extractor.parse(code, "deps.py")

    assert any(n.type == NodeType.VARIABLE and n.id == "deps.DbDep" for n in nodes)
    assert any(
        e.type == EdgeType.DEPENDS_ON and e.source == "deps.DbDep" and e.target == "raw_call:get_db"
        for e in edges
    )


def test_class_body_di_assignment_skipped(extractor: PythonExtractor) -> None:
    """Class-body DI assignments are out of scope and must not produce nodes or edges."""
    code = """
from fastapi import Depends

def get_db():
    pass

class Config:
    db = Depends(get_db)
"""
    nodes, edges = extractor.parse(code, "deps.py")

    assert not any(n.type == NodeType.VARIABLE for n in nodes)
    assert not any(e.type == EdgeType.DEPENDS_ON for e in edges)


# ---------------------------------------------------------------------------
# IMPORTS_SYMBOL emission (#161 slice 2)
# ---------------------------------------------------------------------------


def _symbol_edges(edges: list[Edge]) -> list[Edge]:
    """Filter IMPORTS_SYMBOL edges (slice-2 helper)."""
    return [e for e in edges if e.type == EdgeType.IMPORTS_SYMBOL]


def test_from_import_emits_symbol_edge(extractor: PythonExtractor) -> None:
    """`from a.b import X` emits IMPORTS_SYMBOL with raw_import: target at conf 0.1."""
    _, edges = extractor.parse("from a.b import X\n", "src/pkg/mod.py")
    sym = _symbol_edges(edges)
    assert len(sym) == 1
    assert sym[0].source == "src.pkg.mod"
    assert sym[0].target == "raw_import:a.b.X"
    assert sym[0].confidence == pytest.approx(0.1)


def test_from_import_alias_symbol_edge_keeps_real_name(extractor: PythonExtractor) -> None:
    """`from a import X as Y` — the local alias maps, but the edge targets the real symbol."""
    _, edges = extractor.parse("from a import X as Y\n", "mod.py")
    assert _symbol_edges(edges)[0].target == "raw_import:a.X"


def test_relative_from_import_symbol_edge(extractor: PythonExtractor) -> None:
    """`from .sib import X` inside pkg/mod.py resolves the dots to the sibling module."""
    _, edges = extractor.parse("from .sib import X\n", "pkg/mod.py")
    assert _symbol_edges(edges)[0].target == "raw_import:pkg.sib.X"


def test_plain_import_emits_no_symbol_edge(extractor: PythonExtractor) -> None:
    """`import os` stays module-level only — the imported name IS the module."""
    _, edges = extractor.parse("import os\n", "mod.py")
    assert _symbol_edges(edges) == []


def test_wildcard_import_emits_no_symbol_edge(extractor: PythonExtractor) -> None:
    """`from a.b import *` has no symbol list — nothing is emitted (spec §2.3)."""
    _, edges = extractor.parse("from a.b import *\n", "mod.py")
    assert _symbol_edges(edges) == []


def test_multi_symbol_import_one_edge_each(extractor: PythonExtractor) -> None:
    """`from a import X, Y` emits one IMPORTS_SYMBOL edge per imported name."""
    _, edges = extractor.parse("from a import X, Y\n", "mod.py")
    targets = {e.target for e in _symbol_edges(edges)}
    assert targets == {"raw_import:a.X", "raw_import:a.Y"}


def test_module_imports_edge_coexists_with_symbol_edges(extractor: PythonExtractor) -> None:
    """The module-level IMPORTS edge is untouched — both granularities coexist."""
    _, edges = extractor.parse("from a.b import X\n", "mod.py")
    module_edges = [e for e in edges if e.type == EdgeType.IMPORTS]
    assert len(module_edges) == 1
    assert module_edges[0].target == "a.b"


# ---------------------------------------------------------------------------
# Transparent re-export detection (#182)
# ---------------------------------------------------------------------------


def _reexports(extractor: PythonExtractor, code: str) -> dict[str, str]:
    """Return the FILE node's recorded re-exports for a snippet."""
    nodes, _ = extractor.parse(code, "pkg/mod.py")
    file_node = next(n for n in nodes if n.type == NodeType.FILE)
    return file_node.metadata.get("reexports") or {}


def test_unused_import_is_recorded_as_a_reexport(extractor: PythonExtractor) -> None:
    """`X as X` with no use in the body is the laundering shape from #182."""
    code = "from pkg.other import Thing as Thing\n"

    assert _reexports(extractor, code) == {"Thing": "pkg.other.Thing"}


def test_used_import_is_not_a_reexport(extractor: PythonExtractor) -> None:
    """A re-export that the module also uses is not transparent.

    Regression guard for the real case in guardian/chunker.py, where
    `split_diff_by_file` is both re-exported and called — a detector that
    flagged every `import X as X` would be wrong about it.
    """
    code = "from pkg.other import Thing as Thing\n\ndef go():\n    return Thing()\n"

    assert _reexports(extractor, code) == {}


def test_import_used_only_in_a_quoted_annotation_is_not_a_reexport(
    extractor: PythonExtractor,
) -> None:
    """A forward reference holds no identifier nodes — but it is still a use.

    Regression: this reported cgis.pipeline's TYPE_CHECKING import of SQLiteStore
    as a re-export, because `store: "SQLiteStore | None"` is a string.
    """
    code = 'from pkg.store import Store\n\ndef go(s: "Store | None") -> None:\n    pass\n'

    assert _reexports(extractor, code) == {}


def test_a_name_mentioned_only_in_a_docstring_is_still_a_reexport(
    extractor: PythonExtractor,
) -> None:
    """Only annotation strings count as uses — otherwise prose could mask laundering."""
    code = 'from pkg.other import Thing as Thing\n\ndef go():\n    """Mentions Thing."""\n'

    assert _reexports(extractor, code) == {"Thing": "pkg.other.Thing"}
