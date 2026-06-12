"""Auto-propose a starter patterns.yaml from the measured graph (#174).

Measure-then-label: discover candidate domains from FQN structure, fit each
against the bundled pattern templates by scoring with the existing
DriftScorer, and emit a ready-to-edit ontology whose tolerances are the
measured values plus a margin — green by construction on the same graph.
"""

from cgis.core.models import VIRTUAL_FILE_PATH, Node


def discover_domains(nodes: list[Node], depth: int | None = None) -> list[str]:
    """Candidate domain prefixes from node FQNs (spec §2.1).

    Auto-descent: walk down from the FQN roots while a level has a single
    child; the first level with >= 2 children yields the candidates. An
    explicit ``depth`` (segment count) overrides auto-descent. Virtual
    boundary nodes are excluded. Sorted, deduplicated.
    """
    real_ids = [n.id for n in nodes if n.file_path != VIRTUAL_FILE_PATH]
    if not real_ids:
        return []
    if depth is not None:
        return sorted(
            {".".join(i.split(".")[:depth]) for i in real_ids if i.count(".") >= depth - 1}
        )
    prefix = ""
    while True:
        level = {
            i[len(prefix) :].split(".")[0]
            for i in real_ids
            if i.startswith(prefix) and len(i) > len(prefix)
        }
        if len(level) != 1:
            break
        prefix = f"{prefix}{next(iter(level))}."
    return sorted({f"{prefix}{seg}" for seg in level})


def propose_ontology(
    db_path: str,
    margin: float = 0.03,
    min_nodes: int = 10,
    depth: int | None = None,
) -> str:
    """Return a ready-to-edit patterns.yaml as text (implemented in Task 2)."""
    raise NotImplementedError
