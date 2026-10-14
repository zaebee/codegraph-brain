"""What a language pack contributes to the symbol index and to classification.

Spec: Index and classification. The resolver keeps the language-free indices;
each pack derives what only its language's nodes and configs can say, and
classifies an FQN referenced from one of its files (language-packs step 3).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from cgis.core.models import Node


class IndexFacts(Protocol):
    """One pack's facts over the whole node set; frozen."""

    @property
    def internal_roots(self) -> frozenset[str]:
        """Roots this pack adds to the language-free set (first segment of every node id)."""
        ...

    @property
    def layout_prefixes(self) -> frozenset[str]:
        """Prefixes sources write that node ids do not carry, before internal roots are removed."""
        ...


@dataclass(frozen=True)
class ClassifyContext:
    """What a pack's `classify` may read about one reference."""

    #: The file the reference comes from; None applies the Python reading (spec quirk).
    source_file: str | None
    #: Every internal root, all packs' contributions included.
    internal_roots: frozenset[str]
    nodes: Mapping[str, Node]
    #: This pack's own facts.
    facts: IndexFacts
