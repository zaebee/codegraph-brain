"""Tests for the language registry (#344).

The registry exists because four sites had to agree about what a file extension
means and did not. The tests that matter here are therefore the ones that hold
those sites together — above all `TestModuleFqnRoundTrip`, which ingests real
source and asserts the registry derives the ids the extractors actually emitted.
A test asserting `"src.app.foo"` by hand would keep passing while the two FQN
helpers drifted apart again, which is exactly how the bug arrived.
"""

from pathlib import Path

import pytest

from cgis.core.models import NodeType
from cgis.extractors.python_extractor import file_path_to_module_fqn as python_fqn
from cgis.extractors.registry import (
    LANGUAGES,
    build_extractors,
    is_supported,
    language_for,
)
from cgis.pipeline import IngestionPipeline

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


class TestLanguageFor:
    """The lookup every caller must branch on rather than defaulting."""

    @pytest.mark.parametrize(
        ("path", "extension"),
        [
            ("src/cgis/pipeline.py", ".py"),
            ("src/app/handler.ts", ".ts"),
            ("src/app/Widget.tsx", ".tsx"),
            ("src/app/types.d.ts", ".ts"),
            ("src\\app\\handler.ts", ".ts"),
        ],
    )
    def test_recognises_supported_extensions(self, path: str, extension: str) -> None:
        language = language_for(path)
        assert language is not None
        assert language.extension == extension

    @pytest.mark.parametrize("path", ["README.md", "src/app/old.js", "src/app/old.jsx", "Makefile"])
    def test_returns_none_for_anything_without_an_extractor(self, path: str) -> None:
        """.js and .jsx especially: the TS FQN helper strips them, but nothing parses them.

        Registering them would let the collector derive a plausible FQN for a
        file the graph has no nodes for — the silent miss (#344) from the other
        direction.
        """
        assert language_for(path) is None
        assert not is_supported(path)


class TestCaseSensitivity:
    """The registry must agree with the ingest pipeline, including where both say no."""

    def test_an_uppercase_extension_is_unsupported_because_ingest_skips_it(
        self, tmp_path: Path
    ) -> None:
        """Deliberate, and verified by execution rather than asserted from the code.

        `IngestionPipeline._get_extractor` matches with `filename.endswith(ext)`
        against lowercase keys, so `Foo.TS` is never parsed and the graph holds
        no node for it. Lowercasing the lookup here would therefore *create* the
        bug #344 removed: the collector would call the file supported, derive
        `foo` as its FQN, miss in the graph, log at debug and produce a review
        with no context and no complaint.

        Case-insensitivity is defensible, but only if the pipeline and the
        registry gain it together — never here alone.
        """
        (tmp_path / "Foo.TS").write_text(TS_SOURCE, encoding="utf-8")
        nodes, _, _ = IngestionPipeline(build_extractors([])).run(str(tmp_path))
        assert [n for n in nodes if n.type is NodeType.FILE] == []
        assert language_for("Foo.TS") is None


class TestCodeFence:
    """Full-file context announces the language it is actually showing."""

    @pytest.mark.parametrize(
        ("path", "fence"),
        [("a.py", "python"), ("a.ts", "ts"), ("a.tsx", "tsx")],
    )
    def test_fence_matches_the_language(self, path: str, fence: str) -> None:
        language = language_for(path)
        assert language is not None
        assert language.code_fence == fence


class TestBuildExtractors:
    """The pipeline's mapping comes from the registry, so it cannot drift from it."""

    def test_covers_every_registered_language(self) -> None:
        extractors = build_extractors([])
        assert set(extractors) == {lang.extension for lang in LANGUAGES}

    def test_source_roots_reach_the_extractors(self) -> None:
        """A root that does not reach the extractor silently changes every FQN."""
        extractors = build_extractors(["src"])
        nodes, _ = extractors[".ts"].parse(TS_SOURCE, "src/app/handler.ts")
        assert nodes[0].id == "app.handler"


class TestModuleFqnRoundTrip:
    """The acceptance test of #344: derive the id the extractor really emitted.

    `_graph_sections` looks a changed file up in `graph.db` by the FQN the
    registry derives. If that string is not byte-identical to the node id the
    extractor wrote, the lookup misses, the collector logs at debug and
    continues, and the review completes with no graph context and no complaint.
    Comparing against ingested nodes is the only form of this test that can
    catch the two helpers diverging.
    """

    @staticmethod
    def _ingest(tmp_path: Path, files: dict[str, str]) -> dict[str, str]:
        """Write files, ingest them, and return {relative path: FILE node id}."""
        for relative, source in files.items():
            path = tmp_path / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
        nodes, _, _ = IngestionPipeline(build_extractors([])).run(str(tmp_path))
        # file_path is already relative to the ingested root, and no source_root
        # is configured — so the ids below are derived from these exact strings.
        return {n.file_path: n.id for n in nodes if n.type is NodeType.FILE}

    def test_typescript(self, tmp_path: Path) -> None:
        ingested = self._ingest(
            tmp_path,
            {
                "app/handler.ts": TS_SOURCE,
                "app/Widget.tsx": TSX_SOURCE,
                "components/index.tsx": TSX_SOURCE,
            },
        )
        assert ingested, "ingest produced no FILE nodes — the fixture is wrong, not the code"
        for relative, node_id in ingested.items():
            language = language_for(relative)
            assert language is not None
            assert language.file_path_to_module_fqn(relative, None) == node_id

    def test_barrel_file_collapses_like_the_extractor_does(self, tmp_path: Path) -> None:
        """`/index` for TS, not `/__init__` — the Python helper collapses neither."""
        ingested = self._ingest(tmp_path, {"components/index.tsx": TSX_SOURCE})
        assert ingested["components/index.tsx"] == "components"

    def test_python(self, tmp_path: Path) -> None:
        ingested = self._ingest(tmp_path, {"pkg/mod.py": PY_SOURCE, "pkg/__init__.py": ""})
        for relative, node_id in ingested.items():
            language = language_for(relative)
            assert language is not None
            assert language.file_path_to_module_fqn(relative, None) == node_id

    def test_the_python_helper_would_have_failed_this(self, tmp_path: Path) -> None:
        """Pins the actual defect, so a regression is legible rather than a diff.

        Feeding a `.ts` path to the Python helper leaves the extension on and
        yields an id no node carries. This is what the collector did on every
        TypeScript file before #344.
        """
        ingested = self._ingest(tmp_path, {"app/handler.ts": TS_SOURCE})
        node_id = ingested["app/handler.ts"]
        assert python_fqn("app/handler.ts", None) == "app.handler.ts"
        assert python_fqn("app/handler.ts", None) != node_id
