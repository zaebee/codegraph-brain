"""Basic atoms for ontology."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class NodeType(StrEnum):
    """All possible node types in the Code Graph."""

    # Structural (L0/L1)
    FILE = "FILE"
    MODULE = "MODULE"
    CLASS = "CLASS"
    FUNCTION = "FUNCTION"
    METHOD = "METHOD"
    VARIABLE = "VARIABLE"
    IMPORT = "IMPORT"
    EXPORT = "EXPORT"

    # Runtime/Behavioral (L1/L2)
    API_ENDPOINT = "API_ENDPOINT"
    ROUTE_HANDLER = "ROUTE_HANDLER"
    DB_TABLE = "DB_TABLE"
    DB_QUERY = "DB_QUERY"
    EVENT = "EVENT"

    # Semantic (L3/Ontology)
    DOMAIN_CONCEPT = "DOMAIN_CONCEPT"


class EdgeType(StrEnum):
    """All possible relationship types between nodes."""

    # Structural
    CONTAINS = "CONTAINS"
    DECLARES = "DECLARES"
    BELONGS_TO = "BELONGS_TO"
    IMPORTS = "IMPORTS"
    EXPORTS = "EXPORTS"

    # Behavioral/Execution
    CALLS = "CALLS"
    INSTANTIATES = "INSTANTIATES"
    REFERENCES = "REFERENCES"
    USES = "USES"
    ROUTES_TO = "ROUTES_TO"
    TRIGGERS = "TRIGGERS"
    EMITS = "EMITS"
    CONSUMES = "CONSUMES"

    # Semantic (The OWL-Lite Layer)
    HANDLES = "HANDLES"
    TRANSFORMS = "TRANSFORMS"
    VALIDATES = "VALIDATES"
    AUTHORIZES = "AUTHORIZES"
    PERSISTS = "PERSISTS"
    DOMAIN_DEPENDS_ON = "DOMAIN_DEPENDS_ON"


class Node(BaseModel):
    """
    Represents a single semantic entity in the code graph.
    The 'id' must be a Fully Qualified Name (FQN).
    """

    model_config = ConfigDict(frozen=True)  # Nodes should be immutable once created

    id: str = Field(..., description="Fully Qualified Name (e.g., 'pkg.mod.Class.method')")
    type: NodeType
    name: str

    # Location metadata
    file_path: str
    start_line: int
    end_line: int

    # Language context
    language: str = Field(default="python")

    # Semantic enrichment (L2/L3)
    ontology_class: str | None = Field(
        default=None, description="Mapping to the formal ontology class"
    )
    domains: list[str] = Field(default_factory=list)

    # Reliability
    confidence_score: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Edge(BaseModel):
    """
    Represents a directed relationship between two nodes.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., description="Unique edge identifier")
    source: str = Field(..., description="Source Node FQN")
    target: str = Field(..., description="Target Node FQN")
    type: EdgeType

    # Strength and certainty
    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    # Contextual info (e.g., the code snippet or line number of the call)
    context: str | None = None
    file_path: str | None = None
    line_number: int | None = None
