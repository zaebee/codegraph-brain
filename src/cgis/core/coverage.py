"""How much of a traversal's neighbourhood the graph could actually place (#201).

`cgis validate` reports resolution health for the whole graph. An agent holding
one `trace_flow` or `analyze_impact` answer needs the local version: were the
calls around *this* result resolved, or is the answer short because they were not?
"""

from collections.abc import Iterable, Mapping
from itertools import islice
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

#: How many unresolved targets a coverage report names. The counts cover all of them.
TOP_UNRESOLVED_LIMIT = 10

#: How many call sites a report gives for each named target (#641).
SITES_PER_NAME = 3

#: What `calls_unresolved` counts, which differs by traversal direction.
#:
#: ``unresolved_calls_made`` — calls the expanded nodes make that resolved to
#: nothing: dead ends downstream. Exact.
#:
#: ``unresolved_calls_by_name`` — unresolved calls anywhere in the graph whose
#: called name matches an expanded node: callers that may be missing upstream.
#: An upper bound — a name is not an identity, and a common one (``items``)
#: matches calls on unrelated objects.
CoverageBasis = Literal["unresolved_calls_made", "unresolved_calls_by_name"]


def rank_unresolved(counts: Mapping[str, int]) -> list[tuple[str, int]]:
    """The most frequent unresolved targets first, ties by name, at most `TOP_UNRESOLVED_LIMIT`."""
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:TOP_UNRESOLVED_LIMIT]


class UnresolvedSite(BaseModel):
    """Where one unresolved call is made: the lead an agent can follow with one read (#641).

    A bare name in ``top_unresolved`` gave no place to look. In exp3b (#640) the one
    production gap, ``self.catalog_service.build_cards``, was listed and no agent
    went to find who made it.
    """

    model_config = ConfigDict(frozen=True)

    target: str
    caller: str
    file_path: str | None = None
    line: int | None = None


def unresolved_sites(
    calls: Iterable[tuple[str, str, str | None, int | None]], top: list[tuple[str, int]]
) -> list["UnresolvedSite"]:
    """Up to `SITES_PER_NAME` sites for each target in `top`, in `top`'s order.

    ``calls`` are (target, caller, file_path, line) for every unresolved call
    counted; within a target the sites are ordered by caller, then line.
    """
    ordered = sorted(calls, key=lambda call: (call[0], call[1], call[3] or 0))
    return [
        UnresolvedSite(target=target, caller=caller, file_path=file_path, line=line)
        for name, _ in top
        for target, caller, file_path, line in islice(
            (call for call in ordered if call[0] == name), SITES_PER_NAME
        )
    ]


class TraversalCoverage(BaseModel):
    """Resolution coverage of the CALLS edges around one traversal result.

    ``top_unresolved`` names what the counts are made of, because a ratio alone
    cannot tell ``logger.info`` from ``billing.charge``. On cgis's own
    ``IngestionPipeline.run`` (``cgis trace --depth 3`` at 7eb324b) 92 of 235
    calls are unresolved, and the most frequent are ``logger.info``,
    ``edge.model_copy`` and ``resolved_edges.append`` — methods on untyped
    locals, none of them a first-party call the flow is missing.
    """

    model_config = ConfigDict(frozen=True)

    basis: CoverageBasis
    calls_examined: int
    calls_unresolved: int
    top_unresolved: list[tuple[str, int]] = Field(default_factory=list)
    #: Where the calls in ``top_unresolved`` are made, a few per name (#641).
    sites: list[UnresolvedSite] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unresolved_ratio(self) -> float:
        """Share of the examined calls that are unresolved; 0.0 when none were examined."""
        if not self.calls_examined:
            return 0.0
        return self.calls_unresolved / self.calls_examined
