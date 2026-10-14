"""The extractor contract: `BaseExtractor`, `ModuleNamer`, and the `LanguagePack` a pack exposes.

In core, not beside the packs: the registry imports each pack and each pack
subclasses the protocol, so the protocol living in the registry would be a cycle.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, runtime_checkable

from cgis.core.index import ClassifyContext, IndexFacts
from cgis.core.models import Edge, Node, NodeNamespace
from cgis.core.project import ProjectReader


@runtime_checkable
class ModuleNamer(Protocol):
    """An extractor that can say which module FQN it gives a file path.

    Structural rather than a base-class method: only some extractors can answer,
    and a caller that needs one — the workspace-package map naming a package's
    directory (#504) — can then ask for it without importing any language's
    extractor, which would load that language's grammar for every run (#506).
    """

    def module_fqn(self, file_path: str) -> str:
        """The module FQN this extractor gives `file_path`."""
        ...


class BaseExtractor(ABC):
    """
    Abstract Base Class for all language-specific extractors.

    Every extractor must implement the 'parse' method, which converts
    raw source code into our standardized Node and Edge atoms.
    """

    def __init__(self, source_roots: list[str] | None = None) -> None:
        """Store optional source roots used to strip prefixes from FQNs."""
        # Pre-normalize once at init; _pick_source_root is called per node during parsing.
        self._source_roots: list[tuple[str, str]] = [
            (sr, sr.replace("\\", "/").strip("/") + "/") for sr in (source_roots or [])
        ]

    def _pick_source_root(self, file_path: str) -> str | None:
        """Return the first source root that matches file_path as a prefix, or None."""
        clean = file_path.replace("\\", "/").lstrip("/")
        for sr, sr_norm in self._source_roots:
            if clean.startswith(sr_norm):
                return sr
        return None

    @abstractmethod
    def parse(self, code: str, file_path: str) -> tuple[list[Node], list[Edge]]:
        """
        Parses the provided code and returns a list of Nodes and Edges.

        Args:
            code: The raw source code as a string.
            file_path: The path to the file (used for FQN construction).

        Returns:
            A tuple containing (list_of_nodes, list_of_edges).
        """


class LanguagePack(Protocol):
    """What one language contributes (spec: The `LanguagePack` contract; step 1 of it)."""

    name: str
    extensions: tuple[str, ...]

    def code_fence(self, path: str) -> str:
        """Markdown fence label for this file's full-text context."""
        ...

    def is_test(self, path: str) -> bool:
        """Whether this file is test code (the `Node.is_test` flag, not the walk's skip)."""
        ...

    def scope_of(self, path: str, source_root: str | None) -> str:
        """The namespace unit this file's definitions live in — its module FQN for py/ts."""
        ...

    def extractor(self, extension: str, source_roots: list[str]) -> BaseExtractor:
        """A new extractor for files of `extension`."""
        ...

    def project_reader(self, root: Path, extractors: Mapping[str, BaseExtractor]) -> ProjectReader:
        """A reader for this pack's configs around `root` (`extractors`: the walk's own)."""
        ...

    def index_facts(self, nodes: Mapping[str, Node], state: Mapping[str, object]) -> IndexFacts:
        """This pack's facts over every node, given the merged project state (step 3, C2)."""
        ...

    def classify(self, fqn: str, ctx: ClassifyContext) -> NodeNamespace:
        """The namespace of `fqn` referenced from one of this pack's files: the whole chain (C1)."""
        ...
