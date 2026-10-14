"""The Python language pack: tree-sitter-python, module FQNs from file paths."""

from collections.abc import Mapping
from pathlib import Path
from typing import override

from cgis.core.extractor import BaseExtractor, LanguagePack
from cgis.core.index import ClassifyContext, IndexFacts
from cgis.core.models import Node, NodeNamespace
from cgis.core.paths import is_test_path
from cgis.core.project import ProjectReader
from cgis.core.resolution import ResolutionRules
from cgis.languages.python import index, resolution
from cgis.languages.python._syntax import PYTHON_LANG, file_path_to_module_fqn
from cgis.languages.python.project import PythonProjectReader


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
        """One grammar for the one extension — loaded here, on first use."""
        from cgis.languages.python.extractor import PythonExtractor  # noqa: PLC0415

        return PythonExtractor(source_roots=source_roots)

    @override
    def project_reader(self, root: Path, extractors: Mapping[str, BaseExtractor]) -> ProjectReader:
        """This pack's config reader for the project at `root`."""
        return PythonProjectReader(root)

    @override
    def index_facts(self, nodes: Mapping[str, Node], state: Mapping[str, object]) -> IndexFacts:
        """See `cgis.languages.python.index.index_facts`."""
        return index.index_facts(nodes, state)

    @override
    def classify(self, fqn: str, ctx: ClassifyContext) -> NodeNamespace:
        """See `cgis.languages.python.index.classify`."""
        return index.classify(fqn, ctx)

    @override
    def resolution(self) -> ResolutionRules:
        """See `cgis.languages.python.resolution`."""
        return resolution.RULES


PACK = PythonPack()
