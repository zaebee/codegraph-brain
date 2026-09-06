"""Orphan classes — internal classes no production code builds, extends or names (#415).

The question is "does anything still use this?", and it is decided by two
filters that are each load-bearing, because each corresponds to a way real rot
survived review in `Ownima/owner-api`:

* **Tests are not users.** A class constructed only by its own test is exactly
  the shape being hunted. Count tests and #415's six worked rows all read as
  live, both real orphans included — they had three test constructions each.
* **A re-export is not a use.** Every one of those rows, orphans and live alike,
  has an `IMPORTS_SYMBOL` edge from the package's `__init__.py`. Counting it
  would make the query unable to report anything at all.

What *does* count is invocation (`CALLS`, which for a class means construction),
inheritance (`EXTENDS`), and naming (`REFERENCES` — an annotation, or a class
handed to a framework). The third is what makes the query usable on interfaces:
an abstract port is never constructed, and without a reference edge every
`Protocol` nobody implements reads as dead. Measured on owner-api, dropping it
takes the report from 43 classes to 278.

Precision against the name-based sweep this replaces, measured across five
codebases: 0% false positives on four, and 12% on owner-api where every residual
is two classes sharing a short name — which an FQN graph tells apart and a name
sweep cannot.

**That is not the same as "everything reported is deletable", and the difference
is large enough to state.** Hand-checking 14 of owner-api's 43 found 11 genuinely
dead, and three kinds of noise the graph is right about and a reader is not
interested in:

* **Generated code** — 16 of the 43 are betterproto stubs under
  `api/dependencies/grpc/`, carrying a "DO NOT EDIT" header. Really unused,
  never deleted by hand.
* **Alive by metaclass** — a pydantic inner `class Config`, consumed by the
  model's metaclass and never named by anything.
* **Alive by registration** — a `SQLModel` with `table=True` is a table
  definition; the import is the point.

So on that repository the usable signal is roughly 25 of 43. Filtering these is #432
rather than guessed at here, because each needs a marker the
graph does not yet carry.

Two blind spots remain, both under-reporting rather than over-reporting — the
cheap direction for a check whose value is that people trust it: a class named
only inside a decorator (#429), and a class arriving through `from x import *`.
"""

from dataclasses import dataclass

from cgis.core.models import EdgeType, Node, NodeNamespace, NodeType
from cgis.storage.sqlite_store import SQLiteStore

# Incoming edge types that mean somebody uses this class. `IMPORTS_SYMBOL` is
# deliberately absent — see the module docstring. `CONTAINS` is structural: a
# module containing a class is not a user of it.
_USE_EDGE_TYPES: frozenset[EdgeType] = frozenset(
    {EdgeType.CALLS, EdgeType.EXTENDS, EdgeType.REFERENCES}
)


@dataclass(frozen=True)
class OrphanClass:
    """A class nothing in production reaches — enough to jump to it in an editor."""

    fqn: str
    file: str
    line: int


@dataclass(frozen=True)
class OrphanReport:
    """The orphans, and the population they were drawn from.

    `considered` is reported alongside the findings because "3 orphans" means
    something different out of 40 classes than out of 1 800, and because a graph
    ingested before the `is_test` column existed silently has no test nodes at
    all — `test_sources` being 0 in a repository that has tests is the signal to
    re-ingest.
    """

    orphans: list[OrphanClass]
    considered: int
    test_sources: int


def _is_candidate(node: Node, prefix: str | None) -> bool:
    """An internal production class, optionally under a dot-boundary FQN prefix."""
    if node.type != NodeType.CLASS or node.namespace != NodeNamespace.INTERNAL or node.is_test:
        return False
    return prefix is None or node.id == prefix or node.id.startswith(f"{prefix}.")


def find_orphan_classes(
    store: SQLiteStore, *, prefix: str | None = None, include_tests: bool = False
) -> OrphanReport:
    """Report internal classes that no production code builds, extends or names.

    `prefix` narrows the sweep to one package, on a dot boundary so `app.crud`
    cannot pick up `app.crudX`. `include_tests` counts test code as a user,
    which turns the report into "unreachable from anywhere" — useful for finding
    a class only its own deleted test ever touched, and useless as a dead-code
    check, which is why it is off by default.

    An orphan is a *candidate* for deletion, not a proof. The two blind spots in
    the module docstring both under-report, so a class listed here has no
    incoming evidence at all rather than weak evidence.
    """
    prefix = prefix.strip() or None if prefix is not None else None
    nodes = store.get_all_nodes()
    test_sources = sum(1 for node in nodes if node.is_test)
    # The store answers this in SQL: going through `get_all_edges` builds a
    # Pydantic model per edge only to keep the target, which cost 168 MB on a
    # mid-sized backend and 1.8 GB on a million-edge graph.
    used = store.get_referenced_targets(_USE_EDGE_TYPES, from_test_sources=include_tests)
    candidates = [node for node in nodes if _is_candidate(node, prefix)]
    orphans = [
        OrphanClass(fqn=node.id, file=node.file_path, line=node.start_line)
        for node in sorted(candidates, key=lambda n: n.id)
        if node.id not in used
    ]
    return OrphanReport(orphans=orphans, considered=len(candidates), test_sources=test_sources)
