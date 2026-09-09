"""Whether the graph still matches the tree it was built from (#175).

A query answers from whatever `graph.db` holds, and a graph ingested before the
last edit answers confidently and wrongly. Two reports already carry hand-rolled
staleness counters — `OrphanReport.test_sources` and `.generated_excluded`, each
documented as "zero here means re-ingest" — because there was no way to ask the
question directly. This is that way, and it is why a third such counter should
not be added when a fourth column lands.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class FreshnessState(StrEnum):
    """Whether the graph matches the tree, or whether that is even knowable."""

    FRESH = "FRESH"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class Freshness(BaseModel):
    """The probe's answer: a state, what it counted, and why it could not look.

    `UNKNOWN` is a state of its own rather than a pessimistic `STALE` or an
    optimistic `FRESH`. A signal that reads the same when all is well and when it
    could not look is exactly the `generated_excluded == 0` failure #441 found,
    where one number answered two questions and was wrong at the common one.
    """

    model_config = ConfigDict(frozen=True)

    state: FreshnessState
    changed: int = Field(default=0, ge=0)
    missing: int = Field(default=0, ge=0)
    reason: str | None = None
