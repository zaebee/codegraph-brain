"""What a language pack contributes to resolution (spec: Resolution).

The engine runs a fixed chain — local type (with type normalizers), import map,
steps positioned `after:import_map`, global symbols, fallback — and asks the
source file's pack at each of those points. A rule reads the index through
`ResolutionView`, never the resolver itself, so `cgis.languages` stays clear of
`cgis.resolver` (language-packs step 4).
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from cgis.core.index import IndexFacts
from cgis.core.models import Edge, Node

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

    def map_to_node_fqn(self, imported_fqn: str, source_file: str | None = None) -> str | None:
        """An imported FQN reconciled to a node id, or None; classified as `source_file` reads."""
        ...

    def strip_layout_prefixes(self, fqn: str) -> str | None:
        """The node `fqn` names once corroborated layout prefixes are dropped, or None."""
        ...

    def is_variable_node(self, fqn: str) -> bool:
        """Whether `fqn` is a VARIABLE node."""
        ...


TypeNormalizer = Callable[[str, ResolutionView], str]
#: A declared type → the file whose declaration it expands through, or None (#688).
TypeOrigin = Callable[[str, ResolutionView], str | None]
CallStep = Callable[[str, str, ResolutionView], str | None]
Fallback = Callable[[str, str, ResolutionView], tuple[str, float] | None]
ImportReconciler = Callable[[str, str | None, ResolutionView], str | None]
#: (methods, classes, inheritance) → DISPATCHES edges, for one pack's types (#609).
Dispatcher = Callable[[Sequence[Node], Sequence[Node], Mapping[str, list[str]]], list[Edge]]


@dataclass(frozen=True)
class ResolutionRules:
    """One pack's contribution to the resolver's chain."""

    #: Applied to a declared type before its methods are looked up (e.g. Annotated alias → T).
    type_normalizers: tuple[TypeNormalizer, ...] = ()
    #: The file a normalizer read the declared type from, when it expanded one (a Go
    #: package variable, a call's result): that file named the type with its own
    #: imports, so it, not the caller, says whether the type is a library's (#688).
    type_origin: TypeOrigin | None = None
    #: (position, step): a call step run at that point of the chain.
    steps: tuple[tuple[str, CallStep], ...] = ()
    #: When nothing resolved a call: (target, confidence), or None.
    fallback: Fallback | None = None
    #: An import target with no node of that name: the node it means, or None.
    reconcile_import: ImportReconciler | None = None
    #: Whether `reconcile_import` also maps a reference through an import (a call
    #: target, a local variable's type) that layout reconciliation left without a
    #: node. Go's import paths name directories of a module, which only the pack can
    #: map; Python and TypeScript keep their references to layout rules (step 7b).
    reconciles_references: bool = False
    #: Which methods a call through another type's method may run, as DISPATCHES
    #: edges. None means class hierarchy: an override in a subclass (Python,
    #: TypeScript). Go has no inheritance: embedding promotes, it does not
    #: dispatch, and an interface is satisfied by any type with its methods (#609).
    dispatch: Dispatcher | None = None
    #: How a member is found above a type. False: depth-first in declaration order,
    #: Python's approximation of its MRO. True: Go's promotion rule — the shallowest
    #: depth wins, and two at the same depth are ambiguous, so nothing (#656 review).
    shallowest_member: bool = False
    #: Whether a call through a local name stops at the local's type. True: when the
    #: type does not resolve the call, nothing does — the name is not the import or
    #: package symbol it shadows (Go, #660 review). False: the import map and the
    #: global symbols are tried next.
    locals_shadow: bool = False
    #: CV-1 (docs/specs/core/ontology.yml): a library callable that may invoke some of its
    #: arguments, by resolved target, with the 0-based positions it invokes. A
    #: function value passed there is a call by the function passing it (#668).
    invokers: Mapping[str, frozenset[int]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Refuse a step at a position the engine never runs."""
        unknown = sorted({position for position, _ in self.steps} - _POSITIONS)
        if unknown:
            msg = f"unknown resolution step position(s): {unknown}"
            raise ValueError(msg)
