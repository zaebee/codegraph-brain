"""Map a git range onto the graph: the subclasses and callers of what it changed."""

from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Final

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.languages import extractors, is_supported
from cgis.query.diffcheck.hunks import FileChange, changed_files, read_at
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


def _base_nodes(repo: Path, base: str, change: FileChange, graph_old: str) -> list[Node]:
    """The symbols of the file as it was at `base`; none for a language with no pack."""
    if change.old_path is None or not is_supported(graph_old):
        return []
    ext = PurePosixPath(graph_old).suffix
    extractor = extractors([])[ext]
    nodes, _ = extractor.parse(read_at(repo, base, change.old_path), graph_old)
    return [n for n in nodes if n.type in _SYMBOLS]


def _changed(
    store: SQLiteStore, repo: Path, base: str, head: str, prefix: str
) -> tuple[list[Node], list[str]]:
    """Head symbols the diff touched, and ids of base symbols that no longer exist."""
    touched: dict[str, Node] = {}
    removed: list[str] = []
    for change in changed_files(repo, base, head):
        graph_old = _graph_path(change.old_path, prefix)
        graph_new = _graph_path(change.new_path, prefix)
        if graph_new is not None and change.new_lines:
            for node in store.get_nodes_by_file(graph_new):
                if node.type in _SYMBOLS and _meets(node, change.new_lines):
                    touched[node.id] = node
        if graph_old is None:
            continue
        # A deleted or moved file takes every symbol it held to a new id (or none),
        # whether or not a hunk says so: a pure rename has no hunks at all.
        whole = change.old_path != change.new_path
        for old in _base_nodes(repo, base, change, graph_old):
            if not whole and not _meets(old, change.old_lines):
                continue
            if (current := store.get_node(old.id)) is None:
                removed.append(old.id)
            else:
                touched[current.id] = current
    return list(touched.values()), sorted(set(removed) - set(touched))


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


def _items(store: SQLiteStore, nodes: list[Node], removed: list[str]) -> list[DiffItem]:
    """Subclass and caller items for every changed or removed symbol, unsorted."""
    items: list[DiffItem] = []
    for node in nodes:
        if node.type == NodeType.CLASS:
            for site, edge, depth in _subclasses(store, node.id):
                items.append(
                    DiffItem(
                        kind="subclass",
                        changed=node.id,
                        site=site,
                        file="",
                        line=edge.line_number,
                        reason=f"extends {node.name}"
                        + ("" if depth == 1 else f" ({depth} levels)"),
                        score=edge.confidence / depth,
                    )
                )
        else:
            items.extend(_callers(store, node.id, node.name, "changed"))
    for gone in removed:
        items.extend(_callers(store, gone, gone.rsplit(".", 1)[-1], "removed"))
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
    """List what depends on the symbols `base..head` changed.

    `prefix` is the ingest root's path inside the repository: diff paths map to
    graph files by stripping it, and paths outside it are ignored. Sites in test
    files are counted, not listed; the rest are ranked by score and cut to `cap`.
    Raises `DiffError` when git cannot read the range.
    """
    prefix = prefix.strip("/")
    nodes, removed = _changed(store, repo, base, head, prefix)
    shown: list[DiffItem] = []
    in_tests = 0
    seen: set[tuple[str, str, str]] = set()
    for raw in _items(store, nodes, removed):
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
            )
        )
    shown.sort(key=lambda i: (-i.score, i.file, i.line or 0, i.site))
    symbols = sorted({n.id for n in nodes} | set(removed))
    return DiffReport(shown[:cap], max(len(shown) - cap, 0), in_tests, symbols, [])


def report_to_dict(report: DiffReport) -> dict[str, object]:
    """The report as plain data, for JSON."""
    return asdict(report)


def render_text(report: DiffReport, base: str, head: str) -> str:
    """The report as the short list an agent reads."""
    if not report.changed_symbols:
        return f"No indexed symbol changed in {base}..{head}."
    lines = [
        f"{len(report.changed_symbols)} symbol(s) changed in {base}..{head}; "
        "what else depends on them:"
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
