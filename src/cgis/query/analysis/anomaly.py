"""Pydantic models for architectural anomaly reporting."""

from enum import StrEnum

from pydantic import BaseModel, Field


class AnomalyType(StrEnum):
    """Categories of architectural anti-patterns detectable from the code graph."""

    CIRCULAR_DEPENDENCY = "CIRCULAR_DEPENDENCY"
    ZONE_OF_PAIN = "ZONE_OF_PAIN"
    GOD_OBJECT = "GOD_OBJECT"


class ArchitecturalAnomaly(BaseModel, frozen=True):
    """A single detected architectural anti-pattern with supporting metrics."""

    id: str = Field(..., description="Unique anomaly identifier (deterministic)")
    type: AnomalyType
    focal_fqn: str = Field(..., description="FQN of the primary node violating the pattern")
    severity_score: float = Field(..., ge=0.0, le=1.0, description="Normalised severity 0-1")
    metrics: dict[str, float | int | list[str]] = Field(
        ..., description="Raw computed metrics supporting this finding"
    )
    refactoring_hint: str = Field(..., description="Suggested untangling strategy")


class ArchitecturalReport(BaseModel, frozen=True):
    """Full architectural health report for a codebase snapshot."""

    total_nodes_analyzed: int
    total_anomalies: int
    anomalies: tuple[ArchitecturalAnomaly, ...]
