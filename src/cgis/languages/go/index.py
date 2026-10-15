"""The Go pack's index facts and classification (spec: Go pilot, classify)."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from cgis.core.index import ClassifyContext
from cgis.core.models import Node, NodeNamespace


@dataclass(frozen=True)
class GoIndexFacts:
    """What Go nodes and `go.mod` add to the language-free index."""

    internal_roots: frozenset[str] = frozenset()
    reexports: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    layout_prefixes: frozenset[str] = frozenset()


def index_facts(
    nodes: Mapping[str, Node],  # noqa: ARG001 — read from Task B4 on
    state: Mapping[str, object],  # noqa: ARG001
) -> GoIndexFacts:
    """Go's facts over every node, given the merged project state."""
    return GoIndexFacts()


def classify(fqn: str, ctx: ClassifyContext) -> NodeNamespace:  # noqa: ARG001 — Task B4
    """The namespace of `fqn` referenced from a Go file."""
    return NodeNamespace.UNKNOWN
