"""What a language pack contributes to resolution (spec: Resolution).

The engine runs a fixed chain — local type (with type normalizers), import map,
steps positioned `after:import_map`, global symbols, fallback — and asks the
source file's pack at each of those points. A rule reads the index through
`ResolutionView`, never the resolver itself, so `cgis.languages` stays clear of
`cgis.resolver` (language-packs step 4).
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from cgis.core.index import IndexFacts
from cgis.core.models import Node

#: The only position the engine runs pack steps at today: after the import map,
#: before the global symbol index (TS `_package_call`).
AFTER_IMPORT_MAP = "after:import_map"
_POSITIONS = frozenset({AFTER_IMPORT_MAP})


class ResolutionView(Protocol):
    """What a resolution rule may read; `SymbolIndex` satisfies it."""

    @property
    def nodes(self) -> Mapping[str, Node]:
        """Every node, by id."""
        ...

    @property
    def facts(self) -> Mapping[str, IndexFacts]:
        """Each pack's index facts, by pack name."""
        ...

    def map_to_node_fqn(self, imported_fqn: str) -> str | None:
        """An imported FQN reconciled to a node id, or None."""
        ...

    def strip_layout_prefixes(self, fqn: str) -> str | None:
        """The node `fqn` names once corroborated layout prefixes are dropped, or None."""
        ...

    def is_variable_node(self, fqn: str) -> bool:
        """Whether `fqn` is a VARIABLE node."""
        ...


TypeNormalizer = Callable[[str, ResolutionView], str]
CallStep = Callable[[str, str, ResolutionView], str | None]
Fallback = Callable[[str, str, ResolutionView], tuple[str, float] | None]
ImportReconciler = Callable[[str, str | None, ResolutionView], str | None]


@dataclass(frozen=True)
class ResolutionRules:
    """One pack's contribution to the resolver's chain."""

    #: Applied to a declared type before its methods are looked up (e.g. Annotated alias → T).
    type_normalizers: tuple[TypeNormalizer, ...] = ()
    #: (position, step): a call step run at that point of the chain.
    steps: tuple[tuple[str, CallStep], ...] = ()
    #: When nothing resolved a call: (target, confidence), or None.
    fallback: Fallback | None = None
    #: An import target with no node of that name: the node it means, or None.
    reconcile_import: ImportReconciler | None = None

    def __post_init__(self) -> None:
        """Refuse a step at a position the engine never runs."""
        unknown = sorted({position for position, _ in self.steps} - _POSITIONS)
        if unknown:
            msg = f"unknown resolution step position(s): {unknown}"
            raise ValueError(msg)
