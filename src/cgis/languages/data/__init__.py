"""The data language pack: JSON and YAML files, which declare data keys and nothing else.

No symbols, calls or imports: a declaring file adds its FILE node and DECLARES_KEY
edges (DK-3), so its project reader reads nothing, it contributes no index facts,
and a reference from it is never resolved.
"""

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import override

from cgis.core.extractor import BaseExtractor, LanguagePack
from cgis.core.index import ClassifyContext, IndexFacts
from cgis.core.models import Node, NodeNamespace
from cgis.core.paths import is_test_path
from cgis.core.project import ProjectFacts, ProjectReader
from cgis.core.resolution import ResolutionRules
from cgis.languages.data.naming import DATA_LANG, file_node_id

_RULES = ResolutionRules()


class _NoFacts:
    """Nothing a data file says bears on resolving another file's names."""

    @property
    def internal_roots(self) -> frozenset[str]:
        """No root: a data file's id names no package code imports."""
        return frozenset()

    @property
    def reexports(self) -> Mapping[str, Mapping[str, str]]:
        """No re-export."""
        return MappingProxyType({})

    @property
    def layout_prefixes(self) -> frozenset[str]:
        """No layout prefix."""
        return frozenset()


class _NoConfigs:
    """A data file has no project config of its own: nothing to note, nothing to state."""

    def note(self, path: Path) -> bool:
        """Never one of this pack's configs."""
        del path
        return False

    def note_above(self, directories: list[Path], bounded: bool) -> None:
        """Nothing to read above the root."""

    def facts(self) -> ProjectFacts:
        """No facts."""
        return self

    def state(self) -> Mapping[str, object]:
        """No `ingest_state` keys."""
        return {}


class DataPack(LanguagePack):
    """`LanguagePack` for `.json`, `.yaml` and `.yml`: one pack, two parsers."""

    name: str = DATA_LANG
    extensions: tuple[str, ...] = (".json", ".yaml", ".yml")

    @override
    def code_fence(self, path: str) -> str:
        """`json` for `.json`, `yaml` for the rest."""
        return "json" if path.endswith(".json") else "yaml"

    @override
    def is_test(self, path: str) -> bool:
        """The core rule: a fixture under `tests/` is test data."""
        return is_test_path(path)

    @override
    def scope_of(self, path: str, source_root: str | None) -> str:
        """A data file is its own scope: its FILE node id."""
        return file_node_id(path, source_root)

    @override
    def file_node_id(self, path: str, source_root: str | None) -> str:
        """See `cgis.languages.data.naming.file_node_id`."""
        return file_node_id(path, source_root)

    @override
    def extractor(
        self, extension: str, source_roots: list[str], *, data_keys: bool = False
    ) -> BaseExtractor:
        """One extractor for both formats — the YAML parser is imported here, on first use."""
        from cgis.languages.data.extractor import DataExtractor  # noqa: PLC0415

        return DataExtractor(source_roots=source_roots)

    @override
    def project_reader(self, root: Path, extractors: Mapping[str, BaseExtractor]) -> ProjectReader:
        """A reader that reads nothing."""
        return _NoConfigs()

    @override
    def index_facts(self, nodes: Mapping[str, Node], state: Mapping[str, object]) -> IndexFacts:
        """No facts: no root, no re-export, no layout prefix."""
        return _NO_FACTS

    @override
    def classify(self, fqn: str, ctx: ClassifyContext) -> NodeNamespace:
        """A data file references no code, so nothing it names is known."""
        return NodeNamespace.UNKNOWN

    @override
    def resolution(self) -> ResolutionRules:
        """The defaults: a data file emits no call to resolve."""
        return _RULES


_NO_FACTS = _NoFacts()
PACK = DataPack()
