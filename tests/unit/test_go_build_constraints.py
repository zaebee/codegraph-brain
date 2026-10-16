"""Go files are read for one build: linux/amd64, as `go build`, gopls and go/types do (#657).

Files that build only elsewhere (`//go:build windows`, `foo_windows.go`,
`//go:build !go1.17`) defined the same methods as their default twins, so one node
stood for both and its local types came from whichever file was read last — the
other file's calls resolved against types that were not theirs. Such a file now
keeps its FILE node, marked `build_excluded`, and contributes nothing else.
"""

import time
from pathlib import Path

import pytest

from cgis.core.models import EdgeType, NodeType
from cgis.languages.go.build import build_constraint, excluded, satisfied
from cgis.languages.go.extractor import GoExtractor
from cgis.pipeline import IngestionPipeline
from cgis.query.analysis.overview import build_overview
from cgis.resolver.engine import ResolverEngine
from cgis.storage.sqlite_store import SQLiteStore


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("linux", True),
        ("linux && amd64", True),
        ("windows", False),
        ("!windows", True),
        ("unix", True),
        ("go1.17", True),
        ("!go1.17", False),
        ("go1.21 && !go1.22", False),
        ("ignore", False),
        ("go1", False),  # release tags start at go1.1 (go/build.MatchFile, go1.26)
        ("(linux || darwin) && !cgo", False),
        ("js && !wasm", False),
        ("!js || wasm", True),
        ("linux,amd64", False),
        ("(" * 400 + "linux" + ")" * 400, False),  # nesting past the limit: invalid, no crash
        ("(" * 50 + "linux" + ")" * 50, True),  # not //go:build syntax: an unknown tag, false
    ],
)
def test_a_constraint_is_read_for_linux_amd64(expression: str, expected: bool) -> None:
    assert satisfied(expression) is expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("x_windows.go", True),
        ("x_linux.go", False),
        ("x_arm64.go", True),
        ("x_linux_amd64.go", False),
        ("x_linux_arm64.go", True),
        ("windows.go", False),  # no underscore: not a suffix
        ("x_windows_test.go", True),
        ("x_test.go", False),
        ("process_collector_other.go", False),
    ],
)
def test_a_file_name_suffix_constrains_like_go_build(name: str, expected: bool) -> None:
    assert (excluded("package p\n", name) is not None) is expected


def test_the_constraint_line_comes_before_the_package_clause() -> None:
    assert build_constraint("// Copyright\n\n//go:build !linux\n\npackage p\n") == "!linux"
    assert build_constraint("package p\n//go:build windows\n") is None
    assert build_constraint("// +build windows darwin\n// +build amd64\n\npackage p\n") == (
        "(windows || darwin) && (amd64)"
    )
    assert build_constraint("// +build linux,!cgo\n\npackage p\n") == "(linux && !cgo)"


LINUX = (
    "package app\n"
    "type C struct{}\n"
    "func (c *C) report() {}\n"
    "func (c *C) Collect() {\n\tc.report()\n}\n"
)
OTHER = (
    "//go:build !linux\n\n"
    "package app\n"
    "func (c *C) Collect() {\n\tc.report()\n\tc.other()\n}\n"
    "func (c *C) other() {}\n"
)


def test_an_excluded_file_keeps_only_its_file_node() -> None:
    nodes, edges = GoExtractor().parse(OTHER, "app/c_other.go")
    assert [n.type for n in nodes] == [NodeType.FILE]
    assert nodes[0].metadata["build_excluded"] == "!linux"
    assert nodes[0].metadata["scope"] == "app"
    assert edges == []


def test_a_name_suffix_excludes_the_file_too() -> None:
    nodes, _ = GoExtractor().parse(LINUX, "app/c_windows.go")
    assert [n.metadata.get("build_excluded") for n in nodes] == ["file name: windows"]


def _ingest(tmp_path: Path, files: dict[str, str]) -> SQLiteStore:
    root = tmp_path / "repo"
    for path, code in {"go.mod": "module example.com/m\n\ngo 1.22\n", **files}.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(code)
    store = SQLiteStore(str(tmp_path / "g.db"))
    store.connect()
    IngestionPipeline({".go": GoExtractor()}).run(str(root), store=store)
    return store


def test_the_default_build_s_method_is_the_one_in_the_graph(tmp_path: Path) -> None:
    store = _ingest(tmp_path, {"app/c_linux.go": LINUX, "app/c_other.go": OTHER})
    try:
        collect = store.get_node("app.C.Collect")
        assert collect is not None
        assert collect.file_path == "app/c_linux.go"
        assert store.get_node("app.C.other") is None
        calls = [e for e in store.get_edges_of_type(EdgeType.CALLS) if e.source == "app.C.Collect"]
        assert {e.target for e in calls} == {"app.C.report"}
        assert {e.file_path for e in calls} == {"app/c_linux.go"}
    finally:
        store.disconnect()


def test_the_overview_names_the_files_a_build_leaves_out(tmp_path: Path) -> None:
    store = _ingest(tmp_path, {"app/c_linux.go": LINUX, "app/c_other.go": OTHER})
    try:
        report = build_overview(store)
    finally:
        store.disconnect()
    assert report["build_excluded"] == {
        "files": 1,
        "examples": ["app/c_other.go (!linux)"],
        "build": "linux/amd64",
    }


def test_no_excluded_file_adds_nothing_to_the_overview(tmp_path: Path) -> None:
    store = _ingest(tmp_path, {"app/c_linux.go": LINUX})
    try:
        assert "build_excluded" not in build_overview(store)
    finally:
        store.disconnect()


@pytest.mark.parametrize(
    ("code", "kept"),
    [
        ("// +build windows\npackage p\n", True),  # +build needs a blank line after it
        ("// +build windows\r\n\r\npackage p\r\n", False),
        ("/*\n//go:build windows\n*/\npackage p\n", True),  # inside a block comment
        ("/*\n// +build windows\n*/\n\npackage p\n", True),
        ("/*\npackage x\n*/\n//go:build windows\n\npackage p\n", False),
        ("\ufeff//go:build windows\n\npackage p\n", False),  # a byte-order mark first
        ("/*\npackage docs say\n*/\n// +build windows\n\npackage p\n", True),  # after /* */
        ("//go:build linux\n//go:build amd64\n\npackage p\n", False),  # two: an error
        # A copyright block before `// +build` hides it from go/build; not before //go:build.
        ("/* Copyright 2020 */\n\n// +build windows\n\npackage p\n", True),
        ("/*\nCopyright 2020\n*/\n\n// +build windows\n\npackage p\n", True),
        ("/* Copyright 2020 */\n\n//go:build windows\n\npackage p\n", False),
        ("/* x\n*/ //go:build windows\n\npackage p\n", True),  # began inside a block
        ("/* x */ // c\n//go:build windows\n\npackage p\n", False),  # comment after /* */
        ("/* a */ /* b\n*/\n//go:build windows\n\npackage p\n", False),
        ("//go:build\n\npackage p\n", False),  # no expression: an error
        ("//go:build\vwindows\n\npackage p\n", False),  # any space after the prefix
        ("//go:build\u00a0windows\n\npackage p\n", False),
        ("//go:build !!linux\n\npackage p\n", False),  # double negation: an error
        ("// +build !!linux\n\npackage p\n", False),
        ("// +build linux&&amd64\n\npackage p\n", False),  # not +build syntax
        ("// +build (linux)\n\npackage p\n", False),
        ("//go:buildx windows\n\npackage p\n", True),  # not a constraint line
        ("//go:build goexperiment.greenteagc\n\npackage p\n", True),  # go1.26's defaults
        ("//go:build amd64.v1\n\npackage p\n", True),
        ("//go:build !goexperiment.greenteagc\n\npackage p\n", False),
    ],
)
def test_the_header_is_read_as_go_build_reads_it(code: str, kept: bool) -> None:
    """Differential review of #667 against `go/build.MatchFile` (go1.26, linux/amd64)."""
    assert (excluded(code, "p/x.go") is None) is kept


def test_an_excluded_file_is_never_a_package_s_import_target() -> None:
    """kafka-go: `fuzz.go` (`+build gofuzz`) sorted first and took the package's imports."""
    files = {
        "p/a.go": "//go:build windows\n\npackage p\nfunc Helper() {}\n",
        "p/b.go": "package p\nfunc Helper() {}\n",
        "main.go": 'package main\nimport "example.com/m/p"\nfunc main() { p.Helper() }\n',
    }
    nodes, edges = [], []
    for path, code in files.items():
        n, e = GoExtractor().parse(code, path)
        nodes += n
        edges += e
    state: dict[str, object] = {"go_module": {"path": "example.com/m"}}
    resolved, _ = ResolverEngine(nodes, edges, project_state=state).resolve()
    imports = {e.target for e in resolved if e.type is EdgeType.IMPORTS and e.source == "main_go"}
    assert imports == {"p.b_go"}


def test_a_long_expression_parses_in_linear_time() -> None:
    expression = " || ".join(["windows"] * 20_000) + " || linux"
    started = time.perf_counter()
    assert satisfied(expression)
    assert time.perf_counter() - started < 2.0
