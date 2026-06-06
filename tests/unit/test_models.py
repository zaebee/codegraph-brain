"""Unit test cases for models.py"""

import pytest
from pydantic import ValidationError

from cgis.core.models import Edge, EdgeType, Node, NodeType


def test_valid_node_creation() -> None:
    """Verify that a standard function node is created correctly."""
    node = Node(
        id="src.auth.service.login",
        type=NodeType.FUNCTION,
        name="login",
        file_path="src/auth/service.py",
        start_line=10,
        end_line=25,
        language="python",
    )
    assert node.id == "src.auth.service.login"
    assert node.type == NodeType.FUNCTION
    assert node.confidence_score == pytest.approx(1.0)


def test_invalid_confidence_score() -> None:
    """Verify that Pydantic catches confidence scores out of bounds."""
    with pytest.raises(ValueError, match="Input should be less than or equal to 1"):
        Node(
            id="bad.node",
            type=NodeType.FUNCTION,
            name="bad",
            file_path="err.py",
            start_line=1,
            end_line=2,
            confidence_score=1.5,
        )


def test_edge_creation() -> None:
    """Verify that an edge correctly links two FQNs."""
    edge = Edge(
        id="edge_1",
        source="src.api.handler.get_user",
        target="src.auth.service.login",
        type=EdgeType.CALLS,
    )
    assert edge.source == "src.api.handler.get_user"
    assert edge.type == EdgeType.CALLS


def test_immutability() -> None:
    """Verify that nodes are frozen (cannot be changed after creation)."""
    node = Node(
        id="immut.node",
        type=NodeType.FUNCTION,
        name="immut",
        file_path="immut.py",
        start_line=1,
        end_line=2,
    )
    with pytest.raises(ValidationError):
        node.name = "new_name"  # type: ignore
