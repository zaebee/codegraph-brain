"""The Go language pack: `.go`, one grammar, a package per directory (step 7b)."""

from collections.abc import Mapping
from pathlib import Path
from typing import override

from cgis.core.extractor import BaseExtractor, LanguagePack
from cgis.core.index import ClassifyContext, IndexFacts
from cgis.core.models import Node, NodeNamespace
from cgis.core.project import ProjectReader
from cgis.core.resolution import ResolutionRules
from cgis.languages.go import index, naming, resolution
from cgis.languages.go.project import GoProjectReader


class GoPack(LanguagePack):
    """`LanguagePack` for `.go`: a file's FILE node and its scope differ (step 7, H1)."""

    name: str = naming.GO_LANG
    extensions: tuple[str, ...] = (".go",)

    @override
    def code_fence(self, path: str) -> str:
        """Every Go file fences as `go`."""
        return "go"

    @override
    def is_test(self, path: str) -> bool:
        """Go's rule: the name ends in `_test.go`."""
        return naming.is_test(path)

    @override
    def scope_of(self, path: str, source_root: str | None) -> str:
        """The package directory, dotted; empty at the root (the clause names it there)."""
        return naming.scope_of(path, source_root)

    @override
    def file_node_id(self, path: str, source_root: str | None) -> str:
        """`<package scope>.<stem>_go`: unique per file, though the package is shared."""
        return naming.file_node_id(path, source_root)

    @override
    def extractor(
        self, extension: str, source_roots: list[str], *, data_keys: bool = False
    ) -> BaseExtractor:
        """The Go grammar — loaded here, on first use."""
        from cgis.languages.go.extractor import GoExtractor  # noqa: PLC0415

        return GoExtractor(source_roots=source_roots)

    @override
    def project_reader(self, root: Path, extractors: Mapping[str, BaseExtractor]) -> ProjectReader:
        """The `go.mod` reader for the project at `root`."""
        return GoProjectReader(root)

    @override
    def index_facts(self, nodes: Mapping[str, Node], state: Mapping[str, object]) -> IndexFacts:
        """See `cgis.languages.go.index.index_facts`."""
        return index.index_facts(nodes, state)

    @override
    def classify(self, fqn: str, ctx: ClassifyContext) -> NodeNamespace:
        """See `cgis.languages.go.index.classify`."""
        return index.classify(fqn, ctx)

    @override
    def resolution(self) -> ResolutionRules:
        """See `cgis.languages.go.resolution`."""
        return resolution.RULES


PACK = GoPack()
