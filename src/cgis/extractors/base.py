"""Base Extractor Interface"""

from abc import ABC, abstractmethod

from cgis.core.models import Edge, Node


class BaseExtractor(ABC):
    """
    Abstract Base Class for all language-specific extractors.

    Every extractor must implement the 'parse' method, which converts
    raw source code into our standardized Node and Edge atoms.
    """

    def __init__(self, source_roots: list[str] | None = None) -> None:
        """Store optional source roots used to strip prefixes from FQNs."""
        self._source_roots: list[str] = source_roots or []

    def _pick_source_root(self, file_path: str) -> str | None:
        """Return the first source root that matches file_path as a prefix, or None."""
        clean = file_path.replace("\\", "/").lstrip("/")
        for sr in self._source_roots:
            sr_norm = sr.replace("\\", "/").strip("/") + "/"
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
