"""The Python language pack: tree-sitter-python, module FQNs from file paths."""

from typing import override

from cgis.core.extractor import BaseExtractor, LanguagePack
from cgis.core.paths import is_test_path
from cgis.languages.python._syntax import PYTHON_LANG
from cgis.languages.python.extractor import PythonExtractor, file_path_to_module_fqn


class PythonPack(LanguagePack):
    """`LanguagePack` for `.py`."""

    name: str = PYTHON_LANG
    extensions: tuple[str, ...] = (".py",)

    @override
    def code_fence(self, path: str) -> str:
        """Every Python file fences as `python`."""
        return "python"

    @override
    def is_test(self, path: str) -> bool:
        """The core rule (`tests/`, `test_*.py`, `conftest.py`, …) — ruling R1."""
        return is_test_path(path)

    @override
    def scope_of(self, path: str, source_root: str | None) -> str:
        """The module FQN the extractor gives this file's FILE node."""
        return file_path_to_module_fqn(path, source_root)

    @override
    def extractor(self, extension: str, source_roots: list[str]) -> BaseExtractor:
        """One grammar for the one extension."""
        return PythonExtractor(source_roots=source_roots)


PACK = PythonPack()
