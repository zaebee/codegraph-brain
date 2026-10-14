"""The TypeScript language pack: `.ts` and `.tsx`, one grammar each."""

from cgis.core.extractor import BaseExtractor
from cgis.core.paths import is_test_path
from cgis.languages.typescript.extractor import (
    TYPESCRIPT_LANG,
    TypeScriptExtractor,
    file_path_to_module_fqn,
)


class TypeScriptPack:
    """`LanguagePack` for `.ts`/`.tsx` — the extension picks the grammar (ruling R2)."""

    name: str = TYPESCRIPT_LANG
    extensions: tuple[str, ...] = (".ts", ".tsx")

    def code_fence(self, path: str) -> str:
        """`tsx` for `.tsx`, `ts` for the rest."""
        return "tsx" if path.endswith(".tsx") else "ts"

    def is_test(self, path: str) -> bool:
        """The core rule — `tests/` segments; `foo.test.ts` never reaches a node (R1)."""
        return is_test_path(path)

    def scope_of(self, path: str, source_root: str | None) -> str:
        """The module FQN the extractor gives this file's FILE node (`/index` collapses)."""
        return file_path_to_module_fqn(path, source_root)

    def extractor(self, extension: str, source_roots: list[str]) -> BaseExtractor:
        """The TSX grammar for `.tsx`, the TypeScript one otherwise."""
        return TypeScriptExtractor(tsx=extension == ".tsx", source_roots=source_roots)


PACK = TypeScriptPack()
