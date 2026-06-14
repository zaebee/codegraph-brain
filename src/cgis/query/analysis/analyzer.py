"""Architectural anti-pattern detector powered by graph traversal algorithms."""

import structlog

from cgis.core.models import Edge, EdgeType, Node, NodeNamespace, NodeType
from cgis.query.analysis.anomaly import AnomalyType, ArchitecturalAnomaly, ArchitecturalReport
from cgis.query.drift._scc import build_adjacency, tarjan_scc
from cgis.storage.sqlite_store import SQLiteStore

log = structlog.getLogger(__name__)

# Thresholds -- tuned for typical Python codebases
_ZONE_OF_PAIN_DISTANCE = (
    0.70  # D-distance threshold; Zone of Pain: D > 0.7 AND I <= 0.3 (stable + concrete)
)
_ZONE_OF_PAIN_MAX_INSTABILITY = 0.30  # I <= 0.3 means "stable" (more dependents than dependencies)
_ZONE_OF_PAIN_MIN_AFFERENT = 3  # require >= 3 callers to avoid flagging obscure low-usage classes
_GOD_OBJECT_MIN_METHODS = 10  # classes below this are too small to qualify regardless of coupling
_GOD_OBJECT_MIN_EFFERENT = 5  # low coupling means cohesion is still acceptable


def _build_method_to_class(nodes: list[Node], edges: list[Edge]) -> dict[str, str]:
    """Return a mapping of method FQN -> parent class FQN."""
    method_ids = {n.id for n in nodes if n.type == NodeType.METHOD}
    result: dict[str, str] = {}
    for edge in edges:
        if edge.type in (EdgeType.CONTAINS, EdgeType.DECLARES) and edge.target in method_ids:
            result[edge.target] = edge.source
    return result


def _compute_class_coupling(
    internal_classes: set[str],
    edges: list[Edge],
    method_to_class: dict[str, str],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Compute afferent (Ca) and efferent (Ce) coupling per internal class."""
    ca: dict[str, set[str]] = {fqn: set() for fqn in internal_classes}
    ce: dict[str, set[str]] = {fqn: set() for fqn in internal_classes}
    for edge in edges:
        if edge.type != EdgeType.CALLS:
            continue
        src = method_to_class.get(edge.source, edge.source)
        tgt = method_to_class.get(edge.target, edge.target)
        if src == tgt:
            continue
        if tgt in internal_classes:
            ca[tgt].add(src)
        if src in internal_classes:
            ce[src].add(tgt)
    return ca, ce


def _get_abstract_fqns(internal_classes: set[str], nodes: list[Node]) -> set[str]:
    """Return FQNs of internal classes that are abstract (metadata only).

    Only metadata-flagged classes qualify; using EXTENDS would misclassify
    concrete subclasses (e.g. UserService(Base)) as abstract.
    """
    return {n.id for n in nodes if n.id in internal_classes and n.metadata.get("is_abstract")}


def _build_class_method_map(
    internal_classes: set[str], nodes: list[Node], edges: list[Edge]
) -> dict[str, set[str]]:
    """Return a mapping of class FQN -> set of its method FQNs."""
    method_ids = {n.id for n in nodes if n.type == NodeType.METHOD}
    result: dict[str, set[str]] = {fqn: set() for fqn in internal_classes}
    for edge in edges:
        if (
            edge.type in (EdgeType.CONTAINS, EdgeType.DECLARES)
            and edge.source in internal_classes
            and edge.target in method_ids
        ):
            result[edge.source].add(edge.target)
    return result


class AnalyzerEngine:
    """Detects architectural anti-patterns from a fully-ingested code graph."""

    def __init__(self, store: SQLiteStore) -> None:
        """Load all nodes and edges from the store for in-memory analysis."""
        self._nodes: list[Node] = store.get_all_nodes()
        self._edges: list[Edge] = store.get_all_edges()

    def detect_cycles(self) -> list[ArchitecturalAnomaly]:
        """Find circular dependencies using Tarjan's SCC on IMPORTS edges.

        Returns one anomaly per cycle, containing the full cycle as a list
        in metrics["cycle_members"].
        """
        internal_fqns = {
            n.id
            for n in self._nodes
            if n.namespace == NodeNamespace.INTERNAL and n.type in (NodeType.FILE, NodeType.MODULE)
        }
        adj = build_adjacency(self._edges, frozenset({EdgeType.IMPORTS}))
        adj = {
            k: [v for v in vs if v in internal_fqns] for k, vs in adj.items() if k in internal_fqns
        }

        anomalies: list[ArchitecturalAnomaly] = []
        for scc in tarjan_scc(adj):
            if len(scc) < 2:
                continue
            sorted_members = sorted(scc)
            severity = min(1.0, (len(scc) - 1) / 4)
            anomalies.append(
                ArchitecturalAnomaly(
                    id="cycle_" + "_".join(sorted_members[:3]),
                    type=AnomalyType.CIRCULAR_DEPENDENCY,
                    focal_fqn=sorted_members[0],
                    severity_score=round(severity, 2),
                    metrics={"cycle_length": len(scc), "cycle_members": sorted_members},
                    refactoring_hint=(
                        "Break the cycle with a Mediator, shared abstraction, or by "
                        "extracting the shared dependency into a third module."
                    ),
                )
            )

        log.info("Cycle detection complete.", cycles_found=len(anomalies))
        return anomalies

    def detect_zone_of_pain(self) -> list[ArchitecturalAnomaly]:
        """Detect classes in Uncle Bob's Zone of Pain (stable + concrete).

        Uses afferent/efferent coupling from CALLS edges to compute instability I,
        and node metadata to determine abstractness A.
        High stability (I->0) + low abstractness (A=0) means D-distance approaches 1.0.
        """
        internal_classes = {
            n.id
            for n in self._nodes
            if n.type == NodeType.CLASS and n.namespace == NodeNamespace.INTERNAL
        }
        if not internal_classes:
            return []

        method_to_class = _build_method_to_class(self._nodes, self._edges)
        ca, ce = _compute_class_coupling(internal_classes, self._edges, method_to_class)
        abstract_fqns = _get_abstract_fqns(internal_classes, self._nodes)

        anomalies: list[ArchitecturalAnomaly] = []
        for fqn in internal_classes:
            ca_count = len(ca[fqn])
            ce_count = len(ce[fqn])
            total = ca_count + ce_count
            if total == 0 or ca_count < _ZONE_OF_PAIN_MIN_AFFERENT:
                continue
            instability = ce_count / total
            abstractness = 1.0 if fqn in abstract_fqns else 0.0
            d_distance = abs(abstractness + instability - 1.0)
            if (
                d_distance >= _ZONE_OF_PAIN_DISTANCE
                and instability <= _ZONE_OF_PAIN_MAX_INSTABILITY
            ):
                anomalies.append(
                    ArchitecturalAnomaly(
                        id=f"zop_{fqn}",
                        type=AnomalyType.ZONE_OF_PAIN,
                        focal_fqn=fqn,
                        severity_score=round(d_distance, 2),
                        metrics={
                            "afferent_coupling": ca_count,
                            "efferent_coupling": ce_count,
                            "instability": round(instability, 3),
                            "abstractness": abstractness,
                            "d_distance": round(d_distance, 3),
                        },
                        refactoring_hint=(
                            "Introduce an Abstract Base Class or Protocol to invert the "
                            "dependency, making this module stable-and-abstract rather than "
                            "stable-and-concrete."
                        ),
                    )
                )

        log.info("Zone of Pain detection complete.", found=len(anomalies))
        return anomalies

    def detect_god_objects(self) -> list[ArchitecturalAnomaly]:
        """Detect God Objects: classes with too many methods and high efferent coupling."""
        internal_classes = {
            n.id
            for n in self._nodes
            if n.type == NodeType.CLASS and n.namespace == NodeNamespace.INTERNAL
        }
        if not internal_classes:
            return []

        class_methods = _build_class_method_map(internal_classes, self._nodes, self._edges)
        method_to_class = {m: cls for cls, methods in class_methods.items() for m in methods}
        efferent: dict[str, set[str]] = {fqn: set() for fqn in internal_classes}
        for edge in self._edges:
            if edge.type != EdgeType.CALLS:
                continue
            src_class = method_to_class.get(edge.source)
            if src_class is None:
                continue
            tgt_class = method_to_class.get(edge.target, edge.target)
            if tgt_class != src_class:
                efferent[src_class].add(tgt_class)

        anomalies: list[ArchitecturalAnomaly] = []
        for fqn in internal_classes:
            m_count = len(class_methods[fqn])
            e_count = len(efferent[fqn])
            if m_count < _GOD_OBJECT_MIN_METHODS or e_count < _GOD_OBJECT_MIN_EFFERENT:
                continue
            severity = min(
                1.0,
                (m_count / _GOD_OBJECT_MIN_METHODS) * (e_count / _GOD_OBJECT_MIN_EFFERENT) / 4,
            )
            anomalies.append(
                ArchitecturalAnomaly(
                    id=f"god_{fqn}",
                    type=AnomalyType.GOD_OBJECT,
                    focal_fqn=fqn,
                    severity_score=round(severity, 2),
                    metrics={"method_count": m_count, "efferent_coupling": e_count},
                    refactoring_hint=(
                        "Apply Single Responsibility Principle: extract cohesive groups of "
                        "methods into focused collaborator classes."
                    ),
                )
            )

        log.info("God Object detection complete.", found=len(anomalies))
        return anomalies

    def run(self) -> ArchitecturalReport:
        """Run all detectors and return a consolidated health report."""
        anomalies = self.detect_cycles() + self.detect_zone_of_pain() + self.detect_god_objects()
        internal_count = sum(1 for n in self._nodes if n.namespace == NodeNamespace.INTERNAL)
        return ArchitecturalReport(
            total_nodes_analyzed=internal_count,
            total_anomalies=len(anomalies),
            anomalies=tuple(anomalies),
        )
