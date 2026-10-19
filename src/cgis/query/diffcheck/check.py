"""Map a git range onto the graph: the subclasses and callers of what it changed,
and the other sites of the data keys it touched (`keys`)."""

from collections import deque
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Final

from cgis.core.extractor import BaseExtractor
from cgis.core.models import KEY_EDGE_TYPES, Edge, EdgeType, Node, NodeType
from cgis.core.paths import is_excluded_dir
from cgis.languages import extractors, is_supported, skips
from cgis.query.diffcheck.hunks import (
    DiffError,
    FileChange,
    changed_files,
    read_at,
    resolve_rev,
    toplevel,
)
from cgis.query.diffcheck.keys import KeyGraph, KeyUse, base_key, key_items
from cgis.storage.sqlite_store import SQLiteStore

#: Node types whose lines count as "changed code" (a FILE node spans the whole file).
_SYMBOLS: Final = frozenset({NodeType.CLASS, NodeType.FUNCTION, NodeType.METHOD})
#: Order of kinds among items of equal score, fixed before any noise or held-out run:
#: a declaration the diff broke first, a plain other user of a key last.
_KIND_ORDER: Final = {
    kind: n
    for n, kind in enumerate(
        (
            "removed-declaration",
            "undeclared-read",
            "subclass",
            "caller",
            "key-writer",
            "key-declarer",
            "key-reader",
        )
    )
}


@dataclass(frozen=True)
class DiffItem:
    """One thing the diff may have broken: `site` depends on `changed`."""

    kind: str
    changed: str
    site: str
    file: str
    line: int | None
    reason: str
    score: float
    #: For a data-key item, the key id at the site (it may differ from `changed`).
    key: str = ""


@dataclass(frozen=True)
class DiffReport:
    """What the check found: shown items, how many more were cut, test sites counted."""

    items: list[DiffItem]
    hidden: int
    in_tests: int
    changed_symbols: list[str]
    changed_keys: list[str] = field(default_factory=list)
    #: Whether the graph was ingested with the opt-in data-key layer; off, no key item.
    data_keys: bool = True
    #: One line for the reader: how to turn data keys on, when they are off.
    note: str = ""


#: What a report on a graph without data keys says, once.
DATA_KEYS_OFF_NOTE: Final = (
    "Data keys are off in this graph, so no key items: re-ingest with --data-keys "
    "(MCP: cgis_ingest(data_keys=True)) to list them."
)


def _graph_path(path: str | None, prefix: str) -> str | None:
    """The graph's file path for a repository path; None outside the ingest root."""
    if path is None:
        return None
    if not prefix:
        return path
    return path[len(prefix) + 1 :] if path.startswith(prefix + "/") else None


def _walked(path: str, data_keys: bool) -> bool:
    """Whether an ingest of the graph's root reads `path`: a pack's, outside skipped dirs."""
    parts = PurePosixPath(path).parts
    return (
        is_supported(path, data_keys=data_keys)
        and not skips(parts[-1])
        and not any(is_excluded_dir(d) for d in parts[:-1])
    )


def default_prefix(store: SQLiteStore, repo: Path) -> str:
    """The recorded ingest root's path inside `repo`'s working tree; "" when none is known.

    "" also for a root outside the repository (a graph of an exported tree): the
    mapping check in `_changed` then says so instead of guessing.
    """
    state = store.get_ingest_state()
    if state is None:
        return ""
    try:
        rel = Path(state[0]).resolve().relative_to(toplevel(repo))
    except ValueError:
        return ""
    return "" if rel == Path() else rel.as_posix()


def _meets(node: Node, lines: frozenset[int]) -> bool:
    """Whether any of `lines` falls inside the node's span."""
    return any(node.start_line <= n <= node.end_line for n in lines)


@dataclass(frozen=True)
class _Changes:
    """What a range changed: head symbols touched, base symbols gone, key edges met."""

    touched: list[Node]
    removed: list[Node]
    keys: list[KeyUse]
    #: Base path -> head path of every changed file that still exists.
    head_files: dict[str, str]


def _base_parse(
    store: SQLiteStore,
    repo: Path,
    base: str,
    change: FileChange,
    graph_old: str,
    parsers: Mapping[str, BaseExtractor],
) -> tuple[list[Node], list[KeyUse]]:
    """The symbols of the file as it was at `base`, and its key edges on removed lines.

    Empty for a file the graph's ingest does not read (no pack, or data keys off).
    """
    ext = PurePosixPath(graph_old).suffix
    if change.old_path is None or not is_supported(graph_old) or ext not in parsers:
        return [], []
    extractor = parsers[ext]
    nodes, edges = extractor.parse(read_at(repo, base, change.old_path), graph_old)
    file = next((n for n in nodes if n.type is NodeType.FILE), None)
    keys = [
        KeyUse(base_key(e.target, file, store), e.type, e.source, graph_old, base=True)
        for e in edges
        if e.type in KEY_EDGE_TYPES and e.line_number in change.old_lines
    ]
    return [n for n in nodes if n.type in _SYMBOLS], keys


def _require_mapped(
    store: SQLiteStore, files: list[FileChange], prefix: str, data_keys: bool
) -> None:
    """`DiffError` when changed files under `prefix` that an ingest reads exist and none
    is in the graph.

    A wrong prefix re-parses the base under ids the graph never had, and every
    symbol of a changed file would read as removed (final review I3).
    """
    expected = [
        path
        for path in (_graph_path(change.new_path, prefix) for change in files)
        if path is not None and _walked(path, data_keys)
    ]
    if not expected or any(store.get_nodes_by_file(path) for path in expected):
        return
    message = (
        f"none of the {len(expected)} changed file(s) under prefix {prefix!r} is in the "
        f"graph (first: {expected[0]}). Pass the ingested directory's path inside the "
        "repository as the prefix, or re-ingest if the graph predates these files."
    )
    raise DiffError(message)


def _head_side(
    store: SQLiteStore,
    graph: KeyGraph,
    lines: frozenset[int],
    path: str,
    found: tuple[dict[str, Node], list[KeyUse]],
) -> None:
    """Add the head symbols and key edges of `path` that sit on the diff's new `lines`."""
    touched, keys = found
    for node in store.get_nodes_by_file(path):
        if node.type in _SYMBOLS and _meets(node, lines):
            touched[node.id] = node
    keys.extend(
        KeyUse(e.target, e.type, e.source, path, base=False)
        for e in graph.by_file.get(path, [])
        if e.line_number in lines
    )


def _base_side(
    store: SQLiteStore,
    old_nodes: list[Node],
    lines: frozenset[int] | None,
    touched: dict[str, Node],
    removed: dict[str, Node],
) -> None:
    """Sort the base symbols into changed and removed; `lines` None takes them all."""
    for old in old_nodes:
        if lines is not None and not _meets(old, lines):
            continue
        # A node of that id in another file is the resolver's stand-in for a
        # target it could not find (a base class that is gone), not the symbol.
        current = store.get_node(old.id)
        if current is None or current.file_path != old.file_path:
            removed[old.id] = old
        else:
            touched[current.id] = current


def _changed(
    store: SQLiteStore,
    graph: KeyGraph,
    repo: Path,
    rev: tuple[str, str],
    prefix: str,
    data_keys: bool,
) -> _Changes:
    """Head symbols the diff touched, base symbols that no longer exist, key edges met."""
    touched: dict[str, Node] = {}
    removed: dict[str, Node] = {}
    keys: list[KeyUse] = []
    head_files: dict[str, str] = {}
    base, head = rev
    # Re-parse the old text the way the graph was built, or ids would differ.
    options = store.get_ingest_options()
    parsers = extractors(options[0] if options else [], data_keys=data_keys)
    base_sha = resolve_rev(repo, base)
    files = changed_files(repo, base, head)
    _require_mapped(store, files, prefix, data_keys)
    for change in files:
        graph_old = _graph_path(change.old_path, prefix)
        graph_new = _graph_path(change.new_path, prefix)
        if graph_new is not None and change.new_lines:
            _head_side(store, graph, change.new_lines, graph_new, (touched, keys))
        if graph_old is None:
            continue
        if graph_new is not None:
            head_files[graph_old] = graph_new
        # A deleted or moved file takes every symbol it held to a new id (or none),
        # whether or not a hunk says so: a pure rename has no hunks at all. Its keys
        # keep their ids, so only the edges on removed lines count.
        whole = change.old_path != change.new_path
        old_nodes, old_keys = _base_parse(store, repo, base_sha, change, graph_old, parsers)
        keys.extend(old_keys)
        lines = None if whole else change.old_lines
        _base_side(store, old_nodes, lines, touched, removed)
    gone = [n for _, n in sorted(removed.items()) if n.id not in touched]
    return _Changes(list(touched.values()), gone, keys, head_files)


def _subclasses(store: SQLiteStore, cls: str) -> list[tuple[str, Edge, int]]:
    """Every transitive subclass of `cls` with the edge that reached it and its depth."""
    found: list[tuple[str, Edge, int]] = []
    seen = {cls}
    queue = deque([(cls, 0)])
    while queue:
        current, depth = queue.popleft()
        for edge in store.get_incoming_edges(current):
            if edge.type != EdgeType.EXTENDS or edge.source in seen:
                continue
            seen.add(edge.source)
            found.append((edge.source, edge, depth + 1))
            queue.append((edge.source, depth + 1))
    return found


def _items(store: SQLiteStore, nodes: list[Node], removed: list[Node]) -> list[DiffItem]:
    """Subclass and caller items for every changed or removed symbol, unsorted."""
    items: list[DiffItem] = []
    for node in [*nodes, *removed]:
        verb = "removed" if node in removed else "changed"
        if node.type == NodeType.CLASS:
            for site, edge, depth in _subclasses(store, node.id):
                items.append(
                    DiffItem(
                        kind="subclass",
                        changed=node.id,
                        site=site,
                        file="",
                        line=edge.line_number,
                        reason=f"extends {node.name}, which this diff {verb}"
                        + ("" if depth == 1 else f" ({depth} levels)"),
                        score=edge.confidence / depth,
                    )
                )
        else:
            items.extend(_callers(store, node.id, node.name, verb))
    return items


def _callers(store: SQLiteStore, target: str, name: str, verb: str) -> list[DiffItem]:
    """Items for the direct callers of `target`."""
    items: list[DiffItem] = []
    for edge in store.get_incoming_edges(target):
        if edge.type != EdgeType.CALLS or edge.source == target:
            continue
        items.append(
            DiffItem(
                kind="caller",
                changed=target,
                site=edge.source,
                file="",
                line=edge.line_number,
                reason=f"calls {name}, which this diff {verb}",
                score=edge.confidence,
            )
        )
    return items


def run_diff_check(
    store: SQLiteStore,
    repo: Path,
    base: str,
    head: str = "HEAD",
    cap: int = 5,
    *,
    prefix: str | None = None,
) -> DiffReport:
    """List what depends on the symbols and data keys `base..head` changed.

    `prefix` is the ingest root's path inside the repository: diff paths map to
    graph files by stripping it, and paths outside it are ignored. None takes it
    from the graph's recorded ingest root (`default_prefix`). When changed files
    under the prefix exist and none is in the graph, `DiffError` says so rather
    than reporting their symbols as removed. Sites in test
    files are counted, not listed; the rest are ranked by score, then `_KIND_ORDER`,
    then (undeclared reads) a leaf-typed parent first, then file and line, and cut
    to `cap`. Raises `DiffError` when git cannot read the range.
    """
    prefix = (default_prefix(store, repo) if prefix is None else prefix).strip("/")
    graph = KeyGraph(store)
    # A graph whose options predate recording the mode says it by its key edges.
    recorded = store.get_ingest_data_keys()
    data_keys = bool(graph.by_file) if recorded is None else recorded
    changes = _changed(store, graph, repo, (base, head), prefix, data_keys)
    nodes, removed = changes.touched, changes.removed
    found = key_items(graph, changes.keys, changes.head_files)
    keyed = [
        DiffItem(k.kind, k.changed, k.site, k.file, k.line, k.reason, k.score, k.key) for k in found
    ]
    tiers = {(k.kind, k.changed, k.site): k.tier for k in found}
    shown: list[DiffItem] = []
    in_tests = 0
    seen: set[tuple[str, str, str]] = set()
    for raw in [*_items(store, nodes, removed), *keyed]:
        key = (raw.kind, raw.changed, raw.site)
        site = store.get_node(raw.site)
        if key in seen or site is None:
            continue
        seen.add(key)
        if site.is_test:
            in_tests += 1
            continue
        shown.append(
            DiffItem(
                raw.kind,
                raw.changed,
                raw.site,
                site.file_path,
                raw.line or site.start_line,
                raw.reason,
                raw.score,
                raw.key,
            )
        )
    shown.sort(
        key=lambda i: (
            -i.score,
            _KIND_ORDER.get(i.kind, len(_KIND_ORDER)),
            tiers.get((i.kind, i.changed, i.site), 0),
            i.file,
            i.line or 0,
            i.site,
        )
    )
    symbols = sorted({n.id for n in [*nodes, *removed]})
    keys = sorted({u.key for u in changes.keys})
    note = "" if data_keys else DATA_KEYS_OFF_NOTE
    return DiffReport(
        shown[:cap], max(len(shown) - cap, 0), in_tests, symbols, keys, data_keys, note
    )


def report_to_dict(report: DiffReport) -> dict[str, object]:
    """The report as plain data, for JSON."""
    return asdict(report)


def render_text(report: DiffReport, base: str, head: str) -> str:
    """The report as the short list an agent reads."""
    if not report.changed_symbols and not report.changed_keys:
        empty = f"No indexed symbol or data key changed in {base}..{head}."
        return f"{empty}\n{report.note}" if report.note else empty
    lines = [
        f"{len(report.changed_symbols)} symbol(s) and {len(report.changed_keys)} data key(s) "
        f"changed in {base}..{head}; what else depends on them:"
    ]
    for item in report.items:
        where = f"{item.file}:{item.line}" if item.line else item.file
        lines.append(f"  [{item.kind}] {item.site} ({where}) - {item.reason}")
    if not report.items:
        lines.append("  nothing outside tests")
    if report.hidden:
        lines.append(f"  ... and {report.hidden} more (raise the cap to list them)")
    if report.in_tests:
        lines.append(f"  {report.in_tests} site(s) in tests, not listed")
    if report.note:
        lines.append(report.note)
    return "\n".join(lines)


def check_database(
    db_path: str,
    repo: Path,
    base: str,
    head: str = "HEAD",
    cap: int = 5,
    *,
    prefix: str | None = None,
) -> DiffReport:
    """`run_diff_check` over the graph database at `db_path`, opened and closed here.

    For the surfaces (CLI, MCP): they hold a path, not a store, and storage is
    not theirs to import.
    """
    with SQLiteStore(db_path) as store:
        return run_diff_check(store, repo, base, head, cap, prefix=prefix)
