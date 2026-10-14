"""Tests for the language registry (#344, language-packs step 1).

The registry exists because several sites had to agree about what a file
extension means and did not. The tests that matter hold those sites together —
above all `TestModuleFqnRoundTrip`, which ingests real source and asserts the
registry derives the ids the extractors actually emitted.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from cgis.core.models import Node, NodeNamespace, NodeType
from cgis.core.paths import is_test_path
from cgis.languages import (
    MANIFESTS,
    Manifest,
    classifying_pack,
    extractors,
    is_supported,
    pack_for_node,
    pack_for_path,
    pack_named,
    scope_of,
    skips,
)
from cgis.pipeline import IngestionPipeline
from cgis.resolver.indices import IndexBuilder

TS_SOURCE = """
export function helper(value: number): number {
  return value * 2;
}
"""

TSX_SOURCE = """
export const Widget = () => {
  return null;
};
"""

PY_SOURCE = """
def helper(value: int) -> int:
    return value * 2
"""


def _ingest(tmp_path: Path, files: dict[str, str]) -> list[Node]:
    for relative, source in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    nodes, _, _ = IngestionPipeline(extractors([])).run(str(tmp_path))
    return nodes


class TestPackForPath:
    """The lookup every caller must branch on rather than defaulting."""

    @pytest.mark.parametrize(
        ("path", "name"),
        [
            ("src/cgis/pipeline.py", "python"),
            ("src/app/handler.ts", "typescript"),
            ("src/app/Widget.tsx", "typescript"),
            ("src/app/types.d.ts", "typescript"),
            ("src\\app\\handler.ts", "typescript"),
        ],
    )
    def test_recognises_supported_extensions(self, path: str, name: str) -> None:
        pack = pack_for_path(path)
        assert pack is not None
        assert pack.name == name
        assert is_supported(path)

    @pytest.mark.parametrize("path", ["README.md", "src/app/old.js", "src/app/old.jsx", "Makefile"])
    def test_returns_none_for_anything_without_an_extractor(self, path: str) -> None:
        """.js and .jsx especially: the TS FQN helper strips them, but nothing parses them."""
        assert pack_for_path(path) is None
        assert not is_supported(path)


class TestCaseSensitivity:
    """The registry must agree with the ingest pipeline, including where both say no."""

    def test_an_uppercase_extension_is_unsupported_because_ingest_skips_it(
        self, tmp_path: Path
    ) -> None:
        """`Foo.TS` is never parsed; calling it supported would recreate #344's silent miss."""
        nodes = _ingest(tmp_path, {"Foo.TS": TS_SOURCE})
        assert [n for n in nodes if n.type is NodeType.FILE] == []
        assert pack_for_path("Foo.TS") is None


class TestPacks:
    """Each pack is what its manifest declares, and answers the step-1 contract."""

    @pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda m: m.name)
    def test_a_pack_is_what_its_manifest_says(self, manifest: Manifest) -> None:
        pack = pack_named(manifest.name)
        assert pack is not None
        assert (pack.name, pack.extensions) == (manifest.name, manifest.extensions)

    def test_an_unknown_language_has_no_pack(self) -> None:
        assert pack_named("rust") is None
        node = Node(
            id="m.f", type=NodeType.FUNCTION, name="f", file_path="m.rs",
            start_line=1, end_line=1, language="rust",
        )  # fmt: skip
        assert pack_for_node(node) is None

    @pytest.mark.parametrize(
        ("path", "fence"), [("a.py", "python"), ("a.ts", "ts"), ("a.tsx", "tsx")]
    )
    def test_fence_matches_the_file(self, path: str, fence: str) -> None:
        pack = pack_for_path(path)
        assert pack is not None
        assert pack.code_fence(path) == fence

    @pytest.mark.parametrize(
        "path",
        ["tests/a.py", "src/test_a.py", "src/a.py", "tests/a.ts", "src/a.test.ts", "contest/a.ts"],
    )
    def test_is_test_is_the_core_rule_for_both_packs(self, path: str) -> None:
        """Ruling R1: the flag stays `is_test_path`; the Node validator still calls it."""
        for manifest in MANIFESTS:
            pack = pack_named(manifest.name)
            assert pack is not None
            assert pack.is_test(path) is is_test_path(path)

    def test_every_node_names_the_pack_that_reads_its_file(self, tmp_path: Path) -> None:
        """`pack_for_node` (by `Node.language`) and `pack_for_path` agree on extracted nodes.

        Synthesized rows do not carry a language (the "python" default), which is why
        `mermaid.py` asks by path — `test_a_package_row_keeps_its_full_slug`.
        """
        files = {"pkg/mod.py": PY_SOURCE, "app/h.ts": TS_SOURCE, "app/W.tsx": TSX_SOURCE}
        nodes = _ingest(tmp_path, files)
        extracted = [n for n in nodes if n.file_path in files]
        assert {n.file_path for n in extracted} == set(files)
        for node in extracted:
            assert pack_for_node(node) is pack_for_path(node.file_path), node.id


class TestExtractors:
    """The pipeline's mapping comes from the registry, so it cannot drift from it."""

    def test_covers_every_registered_extension(self) -> None:
        assert set(extractors([])) == {ext for m in MANIFESTS for ext in m.extensions}

    def test_source_roots_reach_the_extractors(self) -> None:
        """A root that does not reach the extractor silently changes every FQN."""
        nodes, _ = extractors(["src"])[".ts"].parse(TS_SOURCE, "src/app/handler.ts")
        assert nodes[0].id == "app.handler"

    def test_tsx_gets_the_tsx_grammar(self) -> None:
        """Ruling R2: one pack, two grammars, chosen by extension."""
        jsx = b"const a = <div />;\n"
        found = extractors([])
        assert not found[".tsx"]._parser.parse(jsx).root_node.has_error  # noqa: SLF001
        assert found[".ts"]._parser.parse(jsx).root_node.has_error  # noqa: SLF001

    def test_membership_and_iteration_build_nothing(self) -> None:
        """Run in a fresh interpreter: this process has long since loaded both grammars."""
        script = (
            "import sys\n"
            "from cgis.languages import extractors\n"
            "m = extractors([])\n"
            "assert '.ts' in m and '.tsx' in m and len(list(m)) == 3\n"
            "print(sorted(x for x in sys.modules if x.startswith('tree_sitter_')))\n"
        )
        out = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, check=False
        )
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "[]"


class TestModuleFqnRoundTrip:
    """The acceptance test of #344: derive the id the extractor really emitted."""

    @staticmethod
    def _files(tmp_path: Path, files: dict[str, str]) -> dict[str, str]:
        nodes = _ingest(tmp_path, files)
        return {n.file_path: n.id for n in nodes if n.type is NodeType.FILE}

    def test_typescript(self, tmp_path: Path) -> None:
        ingested = self._files(
            tmp_path,
            {
                "app/handler.ts": TS_SOURCE,
                "app/Widget.tsx": TSX_SOURCE,
                "components/index.tsx": TSX_SOURCE,
            },
        )
        assert ingested, "ingest produced no FILE nodes — the fixture is wrong, not the code"
        for relative, node_id in ingested.items():
            assert scope_of(relative, None) == node_id

    def test_barrel_file_collapses_like_the_extractor_does(self, tmp_path: Path) -> None:
        ingested = self._files(tmp_path, {"components/index.tsx": TSX_SOURCE})
        assert ingested["components/index.tsx"] == "components"

    def test_python(self, tmp_path: Path) -> None:
        ingested = self._files(tmp_path, {"pkg/mod.py": PY_SOURCE, "pkg/__init__.py": ""})
        assert ingested
        for relative, node_id in ingested.items():
            assert scope_of(relative, None) == node_id

    def test_the_python_rule_would_have_failed_this(self, tmp_path: Path) -> None:
        """Pins the #344 defect: the Python rule leaves `.ts` on."""
        ingested = self._files(tmp_path, {"app/handler.ts": TS_SOURCE})
        python = pack_named("python")
        assert python is not None
        assert python.scope_of("app/handler.ts", None) == "app.handler.ts"
        assert python.scope_of("app/handler.ts", None) != ingested["app/handler.ts"]

    def test_a_path_no_pack_reads_gets_the_python_rule(self) -> None:
        """Ruling R5: the CLI's extensionless fallback, kept in one place."""
        assert scope_of("src/cgis/pipeline", None) == "src.cgis.pipeline"


# The pipeline's pattern before step 1, copied so `skips` is held to it exactly.
_OLD_SKIP = re.compile(r"\.(test|spec)\.(py|ts|tsx|js|jsx)$", re.IGNORECASE)


@pytest.mark.parametrize(
    "name",
    [
        "a.test.py", "a.spec.ts", "a.TEST.tsx", "a.spec.jsx", "a.test.js", "a.test.TS",
        "a_test.py", "test_a.py", "a.py", "a.ts", "contest.ts", "a.test.pyi", "a.spec.d.ts",
    ],
)  # fmt: skip
def test_skips_is_the_pipeline_pattern_it_replaced(name: str) -> None:
    assert skips(name) is bool(_OLD_SKIP.search(name))


def _cli_ingest_loads(tmp_path: Path, files: dict[str, str]) -> list[str]:
    """Grammar packages a fresh `cgis ingest` of `files` imported, in a clean interpreter.

    The pipeline logs through structlog, which may write to the real stdout around
    `CliRunner`; the probe reads only its own last line.
    """
    repo = tmp_path / "repo"
    for relative, source in files.items():
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        (repo / relative).write_text(source, encoding="utf-8")
    script = (
        "import json, sys\n"
        "from typer.testing import CliRunner\n"
        "from cgis.cli import app\n"
        f"argv = ['ingest', {str(repo)!r}, '--output', {str(tmp_path / 'g.db')!r}]\n"
        "r = CliRunner().invoke(app, argv)\n"
        "assert r.exit_code == 0, r.output\n"
        "loaded = {m.split('.')[0] for m in sys.modules if m.startswith('tree_sitter_')}\n"
        "print(json.dumps(sorted(loaded)))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert out.returncode == 0, out.stderr
    loaded: list[str] = json.loads(out.stdout.strip().splitlines()[-1])
    return loaded


def test_a_python_only_ingest_never_loads_the_typescript_grammar(tmp_path: Path) -> None:
    """Spec, Registry: loading every grammar eagerly is what the registry exists to avoid.

    The README matters: a file no pack reads is what makes a value-walking lookup
    build every extractor on its way to "no match".
    """
    files = {"pkg/a.py": PY_SOURCE, "README.md": "# demo\n"}
    assert _cli_ingest_loads(tmp_path, files) == ["tree_sitter_python"]


def test_a_typescript_file_does_load_it(tmp_path: Path) -> None:
    """The control: without it, the test above would pass on a probe that sees nothing."""
    loaded = _cli_ingest_loads(tmp_path, {"pkg/a.py": PY_SOURCE, "app/h.ts": TS_SOURCE})
    assert "tree_sitter_typescript" in loaded


def test_importing_the_packs_loads_no_grammar() -> None:
    """Ruling S2: readers need every pack from the first file; grammars wait for a parse."""
    script = (
        "import json, sys\n"
        "from cgis.languages import pack_named\n"
        "for name in ('python', 'typescript'):\n"
        "    pack = pack_named(name)\n"
        "    pack.scope_of('src/app/x.tsx', 'src')\n"
        "    pack.code_fence('a.py')\n"
        "loaded = {m.split('.')[0] for m in sys.modules if m.startswith('tree_sitter_')}\n"
        "print(json.dumps(sorted(loaded)))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip().splitlines()[-1]) == []


def test_a_typescript_only_ingest_never_loads_the_python_grammar(tmp_path: Path) -> None:
    """Every pack's reader exists from the first file now; the Python grammar must still wait."""
    files = {"app/h.ts": TS_SOURCE, "README.md": "# demo\n"}
    assert _cli_ingest_loads(tmp_path, files) == ["tree_sitter_typescript"]


def test_a_root_package_json_does_not_load_the_typescript_grammar(tmp_path: Path) -> None:
    """A tooling manifest at a Python repo's root (prettier, commitlint) names no package."""
    files = {
        "pkg/a.py": PY_SOURCE,
        "package.json": '{"name": "tooling", "devDependencies": {"prettier": "3"}}',
    }
    assert _cli_ingest_loads(tmp_path, files) == ["tree_sitter_python"]


def test_no_source_classifies_as_python() -> None:
    """Spec quirk, kept in the registry: classify_fqn(fqn) without a source reads it as Python."""
    pack = classifying_pack(None)
    assert pack is not None
    assert pack.name == "python"
    index = IndexBuilder().build([])
    assert index.classify_fqn("os.path") is NodeNamespace.STDLIB
    assert index.classify_fqn("os.path", "app/x.ts") is NodeNamespace.UNKNOWN


@pytest.mark.parametrize("source", ["web/a.js", "types/x.pyi", "Foo.PY"])
def test_a_source_no_pack_reads_gets_the_generic_tail(source: str) -> None:
    """Internal root → INTERNAL, the JS root → STDLIB, anything else UNKNOWN — as before."""
    assert classifying_pack(source) is None
    node = Node(
        id="app.f", type=NodeType.FUNCTION, name="f", file_path="app/f.py", start_line=1, end_line=1
    )
    index = IndexBuilder().build([node])
    assert index.classify_fqn("app.g", source) is NodeNamespace.INTERNAL
    assert index.classify_fqn("js_builtins.Math", source) is NodeNamespace.STDLIB
    assert index.classify_fqn("os.path", source) is NodeNamespace.UNKNOWN
