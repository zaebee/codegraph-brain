"""The TypeScript language pack: `.ts` and `.tsx`, one grammar each."""

from collections.abc import Mapping
from pathlib import Path
from typing import override

from cgis.core.extractor import BaseExtractor, LanguagePack
from cgis.core.index import ClassifyContext, IndexFacts
from cgis.core.models import Node, NodeNamespace
from cgis.core.paths import is_test_path
from cgis.core.project import ProjectReader
from cgis.core.resolution import ResolutionRules
from cgis.languages.typescript import index
from cgis.languages.typescript.naming import TYPESCRIPT_LANG, file_path_to_module_fqn
from cgis.languages.typescript.project import TypeScriptProjectReader


class TypeScriptPack(LanguagePack):
    """`LanguagePack` for `.ts`/`.tsx` — the extension picks the grammar (ruling R2)."""

    name: str = TYPESCRIPT_LANG
    extensions: tuple[str, ...] = (".ts", ".tsx")

    @override
    def code_fence(self, path: str) -> str:
        """`tsx` for `.tsx`, `ts` for the rest."""
        return "tsx" if path.endswith(".tsx") else "ts"

    @override
    def is_test(self, path: str) -> bool:
        """The core rule — `tests/` segments; `foo.test.ts` never reaches a node (R1)."""
        return is_test_path(path)

    @override
    def scope_of(self, path: str, source_root: str | None) -> str:
        """The module FQN the extractor gives this file's FILE node (`/index` collapses)."""
        return file_path_to_module_fqn(path, source_root)

    @override
    def extractor(self, extension: str, source_roots: list[str]) -> BaseExtractor:
        """The TSX grammar for `.tsx`, the TypeScript one otherwise — loaded here, on first use."""
        from cgis.languages.typescript.extractor import TypeScriptExtractor  # noqa: PLC0415

        return TypeScriptExtractor(tsx=extension == ".tsx", source_roots=source_roots)

    @override
    def project_reader(self, root: Path, extractors: Mapping[str, BaseExtractor]) -> ProjectReader:
        """This pack's config reader for the project at `root`."""
        return TypeScriptProjectReader(root, extractors)

    @override
    def index_facts(self, nodes: Mapping[str, Node], state: Mapping[str, object]) -> IndexFacts:
        """See `cgis.languages.typescript.index.index_facts`."""
        return index.index_facts(nodes, state)

    @override
    def classify(self, fqn: str, ctx: ClassifyContext) -> NodeNamespace:
        """See `cgis.languages.typescript.index.classify`."""
        return index.classify(fqn, ctx)

    @override
    def resolution(self) -> ResolutionRules:
        """This pack's rules — filled in by language-packs step 4, one branch per commit."""
        return ResolutionRules()


PACK = TypeScriptPack()
