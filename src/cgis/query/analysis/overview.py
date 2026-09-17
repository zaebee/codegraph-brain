"""The map an agent reads before it knows a single FQN (#478).

Every other query tool takes a name: `cgis_get_structure` wants a module,
`cgis_analyze_impact` wants a symbol, and `cgis_find_symbol` wants something to
search for. In an unfamiliar repository an agent has none of them, so its first
move is to read files — the thing the graph exists to replace.

This answers "what is in this graph, and where do I start?" and nothing else.
Two rules follow from that:

**It stays small.** The listing is capped and says how much it cut. A map that
costs as much as the files it replaces is not a map.

**Every prefix it prints is usable.** `cgis_get_structure`, `cgis_find_symbol`
(`fqn_prefix`) and `cgis_metrics` (`scope`) all take a package prefix as it
appears here. `get_structure` did not until #487: a package is not a node, so it
now answers a prefix by listing the modules under it.

Entry points are deliberately absent. "A function nothing calls" is not one: on
a 512-file FastAPI backend 4,612 of 5,846 functions and methods have no incoming
CALLS edge, because frameworks, DI and dynamic dispatch do the calling. Listing
them would be noise wearing the name of an answer.
"""

from typing import Any

from cgis.storage.sqlite_store import SQLiteStore

#: Rows per listing. Two of these fit in the budget an agent would otherwise
#: spend opening a single file.
DEFAULT_LIMIT = 15

#: Dot-segments of a package prefix. Two is where a real tree separates:
#: `domains.admin` and `domains.reservation` are different places to look,
#: `domains` alone is not a lead.
DEFAULT_DEPTH = 2


def _package_rows(census: list[tuple[str, int]], limit: int) -> tuple[list[dict[str, Any]], int]:
    """Cap a census, largest first, and report how many rows were dropped."""
    rows = [{"prefix": prefix, "symbols": count} for prefix, count in census[:limit]]
    return rows, max(len(census) - limit, 0)


def build_overview(
    store: SQLiteStore, depth: int = DEFAULT_DEPTH, limit: int = DEFAULT_LIMIT
) -> dict[str, Any]:
    """Sizes, a capped package map, and what to call next.

    Production and test packages are listed separately because on a real tree the
    test packages are the largest ones: on a 512-file backend three of the six
    biggest are under `tests.`, which makes an undivided listing point an agent
    at the tests first. A prefix can appear in both lists when tests live inside
    the package they cover (`domains/admin/tests/`), and the two lists do not sum
    to `symbols` once the cap bites.
    """
    depth = max(depth, 1)
    limit = max(limit, 1)
    stats = store.get_edge_stats()
    production, tests = store.package_census(depth)
    package_rows, omitted = _package_rows(production, limit)
    test_rows, test_omitted = _package_rows(tests, limit)

    report: dict[str, Any] = {
        "symbols": store.symbol_census(),
        "files": store.file_count(),
        "edges": stats.total,
        "unresolved_ratio": stats.unresolved_ratio,
        "packages": package_rows,
        "test_packages": test_rows,
        "next": [
            "cgis_get_structure(<prefix>) — the modules a package holds, or a module's members",
            "cgis_find_symbol(<name>, fqn_prefix=<prefix>) — symbols inside a package",
            "cgis_metrics(scope=[<prefix>]) — coupling and God classes in one package",
            "cgis_validate() — how much of the graph resolved",
        ],
    }
    if omitted:
        report["packages_omitted"] = omitted
    if test_omitted:
        report["test_packages_omitted"] = test_omitted
    return report
