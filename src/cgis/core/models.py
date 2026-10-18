"""Basic atoms for ontology."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cgis.core.paths import is_test_path


class NodeNamespace(StrEnum):
    """Classifies whether a node belongs to the ingested repo, stdlib, or third-party."""

    INTERNAL = "INTERNAL"
    STDLIB = "STDLIB"
    EXTERNAL = "EXTERNAL"
    UNKNOWN = "UNKNOWN"


VIRTUAL_FILE_PATH = "EXTERNAL"

# Edge-target encoding conventions for raw (pre-resolution) targets.
# These mirror the raw_call: / raw_dep: prefixes used by extractors and the
# resolver, but apply to self-method dispatch and class-inheritance edges.
SELF_PREFIX = "self."
#: What an extractor puts before every call's raw name, for the resolver to replace.
#: After resolution it stays only on an unresolved name that equals a real node's
#: id, which no real id can hold (#661).
RAW_CALL_PREFIX = "raw_call:"
#: The context of a raw edge for a function value that may be called (#668), set by
#: an extractor and replaced by the resolver: `value-arg:<i>:<call site>` for argument
#: i of the call at that site (CV-1), `value-local:` for a local bound to the value
#: and called where it is held (CV-2). The resolver keeps such an edge only when the
#: value is a real function or method, and only when CV-1's call is an invoker.
VALUE_ARG_CONTEXT = "value-arg:"
VALUE_LOCAL_CONTEXT = "value-local:"
RAW_CLASS_PREFIX = "raw_class:"
#: What an extractor puts before a data key's `<namespace>|<path>` (DK-1..DK-3), for the
#: resolver to replace. The namespace is empty for an unscoped key, or `@NAME` for a
#: module-level constant the resolver reads the namespace from.
RAW_KEY_PREFIX = "raw_key:"


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
    # A string the code uses to address data, in a namespace (DK-1..DK-3). Virtual:
    # one node per (namespace, path) across the repository.
    DATA_KEY = "DATA_KEY"

    # Semantic (L3/Ontology)
    DOMAIN_CONCEPT = "DOMAIN_CONCEPT"


class EdgeType(StrEnum):
    """All possible relationship types between nodes."""

    # Structural
    CONTAINS = "CONTAINS"
    DECLARES = "DECLARES"
    BELONGS_TO = "BELONGS_TO"
    IMPORTS = "IMPORTS"
    IMPORTS_SYMBOL = "IMPORTS_SYMBOL"
    EXPORTS = "EXPORTS"
    EXTENDS = "EXTENDS"

    # Behavioral/Execution
    CALLS = "CALLS"
    # Base-class method -> an override of it in a subclass (#545): a call through
    # the base may run the override. Synthesized from the class hierarchy, not
    # seen at a call site, so it is kept apart from CALLS and its metrics.
    DISPATCHES = "DISPATCHES"
    INSTANTIATES = "INSTANTIATES"
    REFERENCES = "REFERENCES"
    USES = "USES"
    ROUTES_TO = "ROUTES_TO"
    TRIGGERS = "TRIGGERS"
    EMITS = "EMITS"
    CONSUMES = "CONSUMES"
    DEPENDS_ON = "DEPENDS_ON"
    # Code (or a declaring file) -> a DATA_KEY node. New types rather than reuses of
    # PERSISTS / CONSUMES / DECLARES / REFERENCES, which already feed metrics, drift and
    # impact; they are kept out of the unresolved ratio (`KEY_EDGE_TYPES`).
    WRITES_KEY = "WRITES_KEY"
    READS_KEY = "READS_KEY"
    DECLARES_KEY = "DECLARES_KEY"

    # Semantic (The OWL-Lite Layer)
    HANDLES = "HANDLES"
    TRANSFORMS = "TRANSFORMS"
    VALIDATES = "VALIDATES"
    AUTHORIZES = "AUTHORIZES"
    PERSISTS = "PERSISTS"
    DOMAIN_DEPENDS_ON = "DOMAIN_DEPENDS_ON"


#: The three data-key relations (DK-1..DK-3). Their targets are virtual key nodes that
#: no resolution step looks for, so resolution statistics leave them out.
KEY_EDGE_TYPES: frozenset[EdgeType] = frozenset(
    {EdgeType.WRITES_KEY, EdgeType.READS_KEY, EdgeType.DECLARES_KEY}
)

#: The language a node gets when nothing says otherwise. Python's, for history: the
#: first pack. Virtual nodes take their caller's language instead (#672).
DEFAULT_LANGUAGE = "python"


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
    language: str = Field(default=DEFAULT_LANGUAGE)

    # Semantic enrichment (L2/L3)
    ontology_class: str | None = Field(
        default=None, description="Mapping to the formal ontology class"
    )
    domains: list[str] = Field(default_factory=list)

    # Classification
    namespace: NodeNamespace = Field(default=NodeNamespace.INTERNAL)
    is_test: bool = Field(
        default=False,
        description="Test code rather than production code — derived from file_path (spec D5)",
    )
    is_generated: bool = Field(
        default=False,
        description="Machine-generated source — stamped from the file header at ingest (#432)",
    )

    # Reliability
    confidence_score: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _derive_is_test(cls, data: object) -> object:
        """Fill `is_test` from `file_path`, so no caller has to remember to (spec D5).

        Derived rather than passed in: the orphan query's correctness rests on
        every node agreeing about what a test is, and an extractor that forgot
        the flag would make its own classes look production-reachable. An
        explicit value is honoured — the resolver mints virtual boundary nodes
        with no real file to classify.
        """
        if isinstance(data, dict) and "is_test" not in data and data.get("file_path"):
            return {**data, "is_test": is_test_path(str(data["file_path"]))}
        return data


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
    weight: float = Field(
        default=1.0,
        ge=0.0,
        description="Edge strength: 1.0 for plain edges, aggregated count (>1) on quotient edges",
    )
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    # Contextual info (e.g., the code snippet or line number of the call)
    context: str | None = None
    file_path: str | None = None
    line_number: int | None = None
    #: True when the import runs only for a type checker (`if TYPE_CHECKING:`).
    #: Kept as an edge — "what does this module depend on at type level" is a real
    #: question — but not a runtime dependency, so the cycle query skips it: the
    #: standard way to *break* an import cycle was being reported as one (#499).
    type_only: bool = False


# Roles: what a node is for, beside what it structurally is (#566). A FastAPI
# handler is a FUNCTION — every index, impact walk and orphan query reads it as
# one — that also plays the ROUTE_HANDLER role, recorded by its extractor as
# `metadata["route"]`. Queries that select by type (`cgis audit --from-type`) and
# the ontology uplift ask `answers_to`, so a role selects and classifies like a
# type without moving the node out of the type every other reader relies on.

#: Role type -> the metadata key that marks a node as playing it.
_ROLE_MARKERS: dict[NodeType, str] = {NodeType.ROUTE_HANDLER: "route"}


def role_of(node: Node) -> NodeType | None:
    """The role a node plays, by its markers, or None."""
    return next((role for role, key in _ROLE_MARKERS.items() if key in node.metadata), None)


def answers_to(node: Node, node_type: NodeType) -> bool:
    """Whether `node` is of `node_type`, structurally or by the role it plays."""
    return node.type == node_type or role_of(node) == node_type
