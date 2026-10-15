"""The Go extractor: tree-sitter-go AST -> Nodes and Edges (step 7b, Task B3)."""

from typing import override

from cgis.core.extractor import BaseExtractor
from cgis.core.models import Edge, Node


class GoExtractor(BaseExtractor):
    """Extracts a Go file's package, declarations, imports and calls."""

    @override
    def parse(self, code: str, file_path: str) -> tuple[list[Node], list[Edge]]:
        """Nothing yet: Task B3 walks the AST."""
        return [], []
