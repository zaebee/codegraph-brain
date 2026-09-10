"""How much of a traversal's neighbourhood the graph could actually place (#201).

`cgis validate` reports resolution health for the whole graph. An agent holding
one `trace_flow` or `analyze_impact` answer needs the local version: were the
calls around *this* result resolved, or is the answer short because they were not?
"""

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

#: How many unresolved targets a coverage report names. The counts cover all of them.
TOP_UNRESOLVED_LIMIT = 10

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


class TraversalCoverage(BaseModel):
    """Resolution coverage of the CALLS edges around one traversal result.

    ``top_unresolved`` names what the counts are made of, because a ratio alone
    cannot tell ``logger.info`` from ``billing.charge``. On cgis's own
    ``IngestionPipeline.run`` 92 of 235 calls are unresolved, and the most
    frequent are ``get``, ``model_copy``, ``info``, ``append`` — methods on
    untyped locals, none of them a first-party call the flow is missing.
    """

    model_config = ConfigDict(frozen=True)

    basis: CoverageBasis
    calls_examined: int
    calls_unresolved: int
    top_unresolved: list[tuple[str, int]] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unresolved_ratio(self) -> float:
        """Share of the examined calls that are unresolved; 0.0 when none were examined."""
        if not self.calls_examined:
            return 0.0
        return self.calls_unresolved / self.calls_examined
