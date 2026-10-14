"""The TypeScript pack's index facts and classification (language-packs step 3)."""

from types import MappingProxyType

from cgis.core.index import ClassifyContext
from cgis.core.models import Node, NodeNamespace, NodeType
from cgis.languages.typescript.index import (
    TypeScriptIndexFacts,
    classify,
    index_facts,
    package_namespace,
)


def _file(fqn: str, path: str, **metadata: object) -> Node:
    return Node(
        id=fqn,
        type=NodeType.FILE,
        name=path.rsplit("/", 1)[-1],
        file_path=path,
        start_line=1,
        end_line=1,
        language="typescript",
        metadata=metadata,
    )


def _facts(nodes: list[Node], npm: list[str]) -> TypeScriptIndexFacts:
    return index_facts(MappingProxyType({n.id: n for n in nodes}), {"npm_dependencies": npm})


def test_only_typescript_files_carry_bare_imports() -> None:
    nodes = [
        _file("app.h", "app/h.ts", bare_imports=["react", "node:fs"]),
        _file("app.plain", "app/plain.ts"),
    ]
    facts = _facts(nodes, ["react"])
    assert facts.file_bare_imports == {"app/h.ts": frozenset({"react", "node:fs"})}
    assert facts.internal_roots == frozenset() == facts.layout_prefixes


def test_a_package_the_file_imports_and_the_manifest_declares_is_external() -> None:
    facts = _facts([_file("app.h", "app/h.ts", bare_imports=["react", "fs", "lodash"])], ["react"])
    assert package_namespace("react.useState", "app/h.ts", facts) is NodeNamespace.EXTERNAL
    assert package_namespace("fs.readFile", "app/h.ts", facts) is NodeNamespace.STDLIB
    assert package_namespace("lodash.map", "app/h.ts", facts) is None  # undeclared: a lost edge
    assert package_namespace("react.useState", "app/other.ts", facts) is None  # not imported there


def test_classification_chain() -> None:
    facts = _facts([_file("app.h", "app/h.ts", bare_imports=["react"])], ["react"])
    ctx = ClassifyContext(
        source_file="app/h.ts",
        internal_roots=frozenset({"app", "json"}),
        nodes=MappingProxyType({}),
        facts=facts,
    )
    assert classify("react.useState", ctx) is NodeNamespace.EXTERNAL
    assert classify("app.util", ctx) is NodeNamespace.INTERNAL
    assert classify("js_builtins.Math", ctx) is NodeNamespace.STDLIB
    assert (
        classify("json.parse", ctx) is NodeNamespace.INTERNAL
    )  # an internal root, not Python's json
    assert classify("os.path", ctx) is NodeNamespace.UNKNOWN  # #454: Python names mean nothing here


def test_workspace_packages_are_kept_longest_name_first() -> None:
    """`a.b` is both "package a, subpath b" and "package a.b": the longer name wins (#504)."""
    facts = index_facts(
        MappingProxyType({}),
        {"workspace_packages": {"a": "x", "a.b": "y"}, "npm_dependencies": []},
    )
    assert list(facts.workspace_packages) == ["a.b", "a"]


def test_path_aliases_are_ordered_per_project() -> None:
    facts = index_facts(
        MappingProxyType({}),
        {"tsconfig_paths": {"apps/web": {"@.*": ["apps.web.src.*"]}}, "npm_dependencies": []},
    )
    (alias,) = facts.path_aliases["apps/web"]
    assert (alias.prefix, alias.wildcard, alias.targets) == ("@.", True, ("apps.web.src.*",))
