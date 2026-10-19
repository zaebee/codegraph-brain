"""Map a git range onto the graph: the subclasses and callers of what it changed,
and the other sites of the data keys it touched (`keys`)."""

from collections import deque
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Final

from cgis.core.extractor import BaseExtractor
from cgis.core.models import KEY_EDGE_TYPES, Edge, EdgeType, Node, NodeType
from cgis.languages import extractors, is_supported
from cgis.query.diffcheck.hunks import FileChange, changed_files, read_at, resolve_rev
from cgis.query.diffcheck.keys import KeyGraph, KeyUse, base_key, key_items
from cgis.storage.sqlite_store import SQLiteStore

#: Node types whose lines count as "changed code" (a FILE node spans the whole file).
_SYMBOLS: Final = frozenset({NodeType.CLASS, NodeType.FUNCTION, NodeType.METHOD})


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


def _graph_path(path: str | None, prefix: str) -> str | None:
    """The graph's file path for a repository path; None outside the ingest root."""
    if path is None:
        return None
    if not prefix:
        return path
    return path[len(prefix) + 1 :] if path.startswith(prefix + "/") else None


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

    None for a language with no pack.
    """
    if change.old_path is None or not is_supported(graph_old):
        return [], []
    ext = PurePosixPath(graph_old).suffix
    extractor = parsers[ext]
    nodes, edges = extractor.parse(read_at(repo, base, change.old_path), graph_old)
    file = next((n for n in nodes if n.type is NodeType.FILE), None)
    keys = [
        KeyUse(base_key(e.target, file, store), e.type, e.source, graph_old, base=True)
        for e in edges
        if e.type in KEY_EDGE_TYPES and e.line_number in change.old_lines
    ]
    return [n for n in nodes if n.type in _SYMBOLS], keys


def _changed(
    store: SQLiteStore, graph: KeyGraph, repo: Path, base: str, head: str, prefix: str
) -> _Changes:
    """Head symbols the diff touched, base symbols that no longer exist, key edges met."""
    touched: dict[str, Node] = {}
    removed: dict[str, Node] = {}
    keys: list[KeyUse] = []
    head_files: dict[str, str] = {}
    # Re-parse the old text the way the graph was built, or ids would differ.
    options = store.get_ingest_options()
    parsers = extractors(options[0] if options else [])
    base_sha = resolve_rev(repo, base)
    for change in changed_files(repo, base, head):
        graph_old = _graph_path(change.old_path, prefix)
        graph_new = _graph_path(change.new_path, prefix)
        if graph_new is not None and change.new_lines:
            for node in store.get_nodes_by_file(graph_new):
                if node.type in _SYMBOLS and _meets(node, change.new_lines):
                    touched[node.id] = node
            keys.extend(
                KeyUse(e.target, e.type, e.source, graph_new, base=False)
                for e in graph.by_file.get(graph_new, [])
                if e.line_number in change.new_lines
            )
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
        for old in old_nodes:
            if not whole and not _meets(old, change.old_lines):
                continue
            # A node of that id in another file is the resolver's stand-in for a
            # target it could not find (a base class that is gone), not the symbol.
            current = store.get_node(old.id)
            if current is None or current.file_path != old.file_path:
                removed[old.id] = old
            else:
                touched[current.id] = current
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
    prefix: str = "",
) -> DiffReport:
    """List what depends on the symbols and data keys `base..head` changed.

    `prefix` is the ingest root's path inside the repository: diff paths map to
    graph files by stripping it, and paths outside it are ignored. Sites in test
    files are counted, not listed; the rest are ranked by score, then kind, and
    cut to `cap`. Raises `DiffError` when git cannot read the range.
    """
    prefix = prefix.strip("/")
    graph = KeyGraph(store)
    changes = _changed(store, graph, repo, base, head, prefix)
    nodes, removed = changes.touched, changes.removed
    keyed = [
        DiffItem(k.kind, k.changed, k.site, k.file, k.line, k.reason, k.score, k.key)
        for k in key_items(graph, changes.keys, changes.head_files)
    ]
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
    shown.sort(key=lambda i: (-i.score, i.kind, i.file, i.line or 0, i.site))
    symbols = sorted({n.id for n in [*nodes, *removed]})
    keys = sorted({u.key for u in changes.keys})
    return DiffReport(shown[:cap], max(len(shown) - cap, 0), in_tests, symbols, keys)


def report_to_dict(report: DiffReport) -> dict[str, object]:
    """The report as plain data, for JSON."""
    return asdict(report)


def render_text(report: DiffReport, base: str, head: str) -> str:
    """The report as the short list an agent reads."""
    if not report.changed_symbols and not report.changed_keys:
        return f"No indexed symbol or data key changed in {base}..{head}."
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
    return "\n".join(lines)


def check_database(
    db_path: str, repo: Path, base: str, head: str = "HEAD", cap: int = 5, *, prefix: str = ""
) -> DiffReport:
    """`run_diff_check` over the graph database at `db_path`, opened and closed here.

    For the surfaces (CLI, MCP): they hold a path, not a store, and storage is
    not theirs to import.
    """
    with SQLiteStore(db_path) as store:
        return run_diff_check(store, repo, base, head, cap, prefix=prefix)
