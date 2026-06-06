"""Base Extractor Interface"""

from abc import ABC, abstractmethod

from cgis.core.models import Edge, Node


class BaseExtractor(ABC):
    """
    Abstract Base Class for all language-specific extractors.

    Every extractor must implement the 'parse' method, which converts
    raw source code into our standardized Node and Edge atoms.
    """

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
        pass
