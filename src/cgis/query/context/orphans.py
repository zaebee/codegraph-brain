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

* **Generated code** — betterproto stubs carrying a "DO NOT EDIT" header.
  Really unused, never deleted by hand. **Now filtered by default** (#432): the
  pipeline stamps `Node.is_generated` from the file header, and
  `include_generated=True` puts them back. On owner-api at `b7d02fe6` that takes
  the report from 6 to 1 and the population from 699 classes to 488.
* **Alive by metaclass** — a pydantic inner `class Config`, consumed by the
  model's metaclass and never named by anything. Still reported, and on that
  repository it is now the *only* row: the question of whether nested classes
  belong in this report at all is a scope decision rather than a filter, and is
  still open in #432.
* **Alive by registration** — a `SQLModel` with `table=True` is a table
  definition; the import is the point. Not reported on the current ref, so it
  needs a repository where it recurs before it can be measured.

One blind spot remains, and it under-reports rather than over-reports — the
cheap direction for a check whose value is that people trust it: a class
arriving through `from x import *` is not gated in, because the gate is the
module's `import_map` keys and a star import contributes none. Classes named
only inside a decorator used to be a second one; #429 closed it.
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

    `generated_excluded` is the same kind of signal for `is_generated`, and needs
    it more: that column has no backfill, because the marker lives in the file
    header rather than in the database. A zero on a repository with generated
    code means the graph predates the column, not that there is none (#432).
    """

    orphans: list[OrphanClass]
    considered: int
    test_sources: int
    generated_excluded: int = 0


def _is_candidate(node: Node, prefix: str | None) -> bool:
    """An internal production class, optionally under a dot-boundary FQN prefix."""
    if node.type != NodeType.CLASS or node.namespace != NodeNamespace.INTERNAL or node.is_test:
        return False
    return prefix is None or node.id == prefix or node.id.startswith(f"{prefix}.")


def find_orphan_classes(
    store: SQLiteStore,
    *,
    prefix: str | None = None,
    include_tests: bool = False,
    include_generated: bool = False,
) -> OrphanReport:
    """Report internal classes that no production code builds, extends or names.

    `prefix` narrows the sweep to one package, on a dot boundary so `app.crud`
    cannot pick up `app.crudX`. `include_tests` counts test code as a user,
    which turns the report into "unreachable from anywhere" — useful for finding
    a class only its own deleted test ever touched, and useless as a dead-code
    check, which is why it is off by default.

    `include_generated` puts machine-generated classes back into the report.
    They are excluded by default because the query is *right* about them and a
    reader still will not act: nothing constructs a betterproto stub, and nobody
    hand-deletes one either. Measured on owner-api at b7d02fe6, five of the six
    reported orphans were generated entities and the sixth was a nested pydantic
    `Config`, so the default report there had no actionable row left (#432).

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
    if not include_generated:
        # Counted before dropping, and counted among the *unused* only: a
        # generated class production still calls was never going to be reported,
        # so including it would overstate what the filter removed.
        generated_excluded = sum(1 for n in candidates if n.is_generated and n.id not in used)
        candidates = [n for n in candidates if not n.is_generated]
    else:
        generated_excluded = 0
    orphans = [
        OrphanClass(fqn=node.id, file=node.file_path, line=node.start_line)
        for node in sorted(candidates, key=lambda n: n.id)
        if node.id not in used
    ]
    return OrphanReport(
        orphans=orphans,
        considered=len(candidates),
        test_sources=test_sources,
        generated_excluded=generated_excluded,
    )
