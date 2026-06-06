"""Basic atoms for ontology."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class NodeType(StrEnum):
    FILE = "FILE"
    MODULE = "MODULE"
    CLASS = "CLASS"
    FUNCTION = "FUNCTION"
    METHOD = "METHOD"
    VARIABLE = "VARIABLE"
    IMPORT = "IMPORT"
    EXPORT = "EXPORT"
    DOMAIN_CONCEPT = "DOMAIN_CONCEPT"
    API_ENDPOINT = "API_ENDPOINT"


class EdgeType(StrEnum):
    CALLS = "CALLS"
    IMPORTS = "IMPORTS"
    CONTAINS = "CONTAINS"
    BELONGS_TO = "BELONGS_TO"
    HANDLES = "HANDLES"
    TRANSFORMS = "TRANSFORMS"
    # ... остальные будут подтянуты из онтологии


class Node(BaseModel):
    id: str = Field(..., description="Unique identifier (FQN)")
    type: NodeType
    name: str

    # Location info
    file_path: str
    start_line: int
    end_line: int

    # Metadata & Semantics
    language: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Ontology enrichment
    ontology_class: str | None = None
    domains: list[str] = Field(default_factory=list)
    confidence_score: float = Field(default=1.0, ge=0.0, le=1.0)


class Edge(BaseModel):
    id: str
    source: str  # Node ID
    target: str  # Node ID
    type: EdgeType

    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    context: str | None = None
