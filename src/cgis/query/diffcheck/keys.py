"""The data keys a diff touched, and the other sites of each (DK-1..DK-3).

A key changes where the diff's lines meet a key edge: in the head graph, or in the
base text re-parsed by `check`. Each changed key lists the other readers, writers and
declarers of every key that can name the same data (`keys_match`), and readers of
paths inside it (`picture.cover` reads into `picture`). Two more kinds compare reads
with declarations: a dotted path read whose parent is declared and the path nowhere
(`undeclared-read`), and a declaration only the base has whose path is still read
(`removed-declaration`). Specificity ranks them and keeps the common keys out.
"""

import math
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from itertools import takewhile

from cgis.core.keys import key_id, parse_key_id
from cgis.core.models import RAW_KEY_PREFIX, Edge, EdgeType, Node, NodeType
from cgis.core.paths import is_test_path
from cgis.languages import names_code, pack_for_path
from cgis.storage.sqlite_store import SQLiteStore

#: Unscoped single-segment keys that name no data in particular: never listed.
COMMON_KEYS = frozenset({"id", "name", "type", "data", "value", "key", "items", "result"})
#: An unscoped single-segment key referenced from more files than this is not listed.
MAX_FILES = 8

_KIND = {
    EdgeType.READS_KEY: "key-reader",
    EdgeType.WRITES_KEY: "key-writer",
    EdgeType.DECLARES_KEY: "key-declarer",
}
_WORD = {
    EdgeType.READS_KEY: "read",
    EdgeType.WRITES_KEY: "write",
    EdgeType.DECLARES_KEY: "declaration",
}
_VERB = {
    EdgeType.READS_KEY: "reads",
    EdgeType.WRITES_KEY: "writes",
    EdgeType.DECLARES_KEY: "declares",
}
#: Of two items for one site and key, the lower rank is the one kept.
_RANK = {"removed-declaration": 0, "undeclared-read": 1}


@dataclass(frozen=True)
class KeyUse:
    """A key edge on a line the diff changed, on the base or the head side."""

    key: str
    relation: EdgeType
    source: str
    file: str
    base: bool


@dataclass(frozen=True)
class KeyItem:
    """One site a changed key leads to; `check` turns it into a `DiffItem`."""

    kind: str
    changed: str
    site: str
    file: str
    line: int | None
    key: str
    reason: str
    score: float


def _show(key: str) -> str:
    """A key id as a reader sees it: `path`, or `path (namespace)`."""
    namespace, path = parse_key_id(key)
    return f"{path} ({namespace})" if namespace else path


class KeyGraph:
    """The head graph's key edges, indexed by key, file, path and parent path."""

    def __init__(self, store: SQLiteStore) -> None:
        """Read every key edge once: the check never walks them through traversals."""
        by_target: defaultdict[str, list[Edge]] = defaultdict(list)
        by_file: defaultdict[str, list[Edge]] = defaultdict(list)
        ids: defaultdict[str, set[str]] = defaultdict(set)  # path -> key ids
        declared: defaultdict[str, dict[str, str]] = defaultdict(dict)  # path -> ns -> file
        # The same, for declaring files only (a mapping, not a model's fields).
        in_files: defaultdict[str, dict[str, str]] = defaultdict(dict)
        declared_in: defaultdict[str, set[str]] = defaultdict(set)  # declaring file -> key ids
        reads_under: defaultdict[str, list[Edge]] = defaultdict(list)  # parent path -> reads
        declaring: dict[str, bool] = {}
        for edge in (e for relation in _KIND for e in store.get_edges_of_type(relation)):
            namespace, path = parse_key_id(edge.target)
            file = edge.file_path or ""
            by_target[edge.target].append(edge)
            by_file[file].append(edge)
            ids[path].add(edge.target)
            if edge.type is EdgeType.DECLARES_KEY:
                if namespace not in declared[path]:
                    declared[path][namespace] = file
                if file not in declaring:
                    declaring[file] = _is_declaring(file)
                if declaring[file]:
                    if namespace not in in_files[path]:
                        in_files[path][namespace] = file
                    declared_in[file].add(edge.target)
            elif edge.type is EdgeType.READS_KEY and "." in path:
                reads_under[_parent(path)].append(edge)
        self.by_target = by_target
        self.by_file = by_file
        self._ids = ids
        self._declared = declared
        self._in_files = in_files
        self._declared_in = declared_in
        self._reads_under = reads_under
        self._paths = sorted(ids)
        self._files: dict[str, int] = {}

    def matching(self, key: str) -> list[str]:
        """The key ids that can name the same data as `key`: same path, namespace agreeing."""
        namespace, path = parse_key_id(key)
        return sorted(k for k in self._ids[path] if _agree(namespace, parse_key_id(k)[0]))

    def inside(self, key: str) -> list[str]:
        """Ids of paths below `key`'s (`picture.cover` under `picture`), namespaces agreeing."""
        namespace, path = parse_key_id(key)
        prefix = path + "."
        start = bisect_left(self._paths, prefix)
        below = takewhile(lambda p: p[: len(prefix)] == prefix, self._paths[start:])
        return [
            k for p in below for k in sorted(self._ids[p]) if _agree(namespace, parse_key_id(k)[0])
        ]

    def files(self, key: str) -> int:
        """How many non-test files reference the key node `key` itself."""
        if key not in self._files:
            self._files[key] = len(
                {
                    edge.file_path
                    for edge in self.by_target[key]
                    if edge.file_path and not is_test_path(edge.file_path)
                }
            )
        return self._files[key]

    def declaring_file(self, namespace: str, path: str, *, files_only: bool = False) -> str | None:
        """A file declaring `path` in a namespace agreeing with `namespace`; None when none does.

        `files_only` counts declaring files alone, not the fields of a model.
        """
        declared = (self._in_files if files_only else self._declared)[path]
        files = [declared[ns] for ns in declared if _agree(namespace, ns)]
        return min(files) if files else None

    def declared_in(self, file: str) -> set[str]:
        """The key ids `file` declares."""
        return self._declared_in[file]

    def reads_under(self, path: str) -> list[Edge]:
        """Reads of the paths one level below `path`."""
        return self._reads_under[path]


def _is_declaring(file: str) -> bool:
    """Whether `file` is a declaring file: one whose pack names no code (JSON, YAML)."""
    pack = pack_for_path(file)
    return pack is not None and not names_code(pack.name)


def _parent(path: str) -> str:
    """The path one level up: `event_type` for `event_type.keyword`; "" at the top."""
    return path.rpartition(".")[0]


def _agree(a: str, b: str) -> bool:
    """Whether two namespaces can hold the same key: equal, or either unscoped."""
    return a == b or not a or not b


def dropped(key: str, graph: KeyGraph) -> bool:
    """An unscoped single-segment key that is common or used everywhere: never listed."""
    namespace, path = parse_key_id(key)
    if namespace or "." in path:
        return False
    return path in COMMON_KEYS or graph.files(key) > MAX_FILES


def _link_score(changed: str, site: str, graph: KeyGraph) -> float:
    """A link between two keys is as specific as its less specific end."""
    return min(specificity(changed, graph), specificity(site, graph))


def specificity(key: str, graph: KeyGraph) -> float:
    """Segments in the path, plus one if scoped, minus log2 of the files referencing it."""
    namespace, path = parse_key_id(key)
    return path.count(".") + 1 + (1 if namespace else 0) - math.log2(max(graph.files(key), 1))


def base_key(target: str, file: Node | None, store: SQLiteStore) -> str:
    """The key id of a re-parsed base edge's `raw_key:` target, as the resolver would make it.

    An `@NAME` namespace is the string constant NAME holds: in the file's own
    `str_constants`, else in those of the module its `import_map` takes NAME from
    (looked up in the head graph). Anything else leaves the key unscoped.
    """
    namespace, _, path = target.removeprefix(RAW_KEY_PREFIX).partition("|")
    if namespace.startswith("@"):
        namespace = _constant(namespace[1:], file, store)
    return key_id(namespace, path)


def _constant(name: str, file: Node | None, store: SQLiteStore) -> str:
    """What the module-level string constant `name` holds where `file` uses it; "" if unknown."""
    metadata = file.metadata if file is not None else {}
    own = _strings(metadata.get("str_constants")).get(name)
    if own is not None:
        return own
    imports = metadata.get("import_map")
    target = imports.get(name) if isinstance(imports, dict) else None
    if not isinstance(target, str) or "." not in target:
        return ""
    module, _, imported = target.rpartition(".")
    parts = module.split(".")
    # The import names the module from the repository's view, the graph from the source root.
    for start in range(len(parts)):
        node = store.get_node(".".join(parts[start:]))
        if node is not None and node.type is NodeType.FILE:
            return _strings(node.metadata.get("str_constants")).get(imported, "")
    return ""


def _strings(value: object) -> dict[str, str]:
    """The string-to-string entries of a metadata value; none when it is not a dict."""
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, str)}


def key_items(graph: KeyGraph, uses: Iterable[KeyUse], head_files: dict[str, str]) -> list[KeyItem]:
    """Every site the changed keys lead to, one item per site and kind, unsorted.

    `head_files` maps a base file path to its head path, so a declaration changed
    on the base side checks the reads against the file as it now stands.
    """
    by_key: defaultdict[str, list[KeyUse]] = defaultdict(list)
    for use in uses:
        # A test's key use changes no production behaviour, and test sites are not listed.
        if not is_test_path(use.file):
            by_key[use.key].append(use)
    items = [
        *_other_sites(graph, by_key),
        *_undeclared(graph, by_key, head_files),
        *_removed(graph, by_key),
    ]
    return _deduplicated(items)


def _other_sites(graph: KeyGraph, by_key: dict[str, list[KeyUse]]) -> Iterator[KeyItem]:
    """The other readers, writers and declarers of each changed key, and readers inside it."""
    for key in sorted(by_key):
        if dropped(key, graph):
            continue
        uses = by_key[key]
        sources = {u.source for u in uses}
        words = "/".join(sorted({_WORD[u.relation] for u in uses}))
        inside = graph.inside(key)
        below = set(inside)
        for match in [*graph.matching(key), *inside]:
            # A common key on the far side joins nothing: `|name` matches every model's name.
            if dropped(match, graph):
                continue
            score = _link_score(key, match, graph)
            for edge in sorted(graph.by_target[match], key=_line):
                if edge.source in sources or (
                    match in below and edge.type is not EdgeType.READS_KEY
                ):
                    continue
                yield KeyItem(
                    kind=_KIND[edge.type],
                    changed=key,
                    site=edge.source,
                    file=edge.file_path or "",
                    line=edge.line_number,
                    key=match,
                    reason=f"{_VERB[edge.type]} {_show(match)}; this diff changes a {words}"
                    f" of {_show(key)}",
                    score=score,
                )


def _undeclared(
    graph: KeyGraph, by_key: dict[str, list[KeyUse]], head_files: dict[str, str]
) -> Iterator[KeyItem]:
    """Dotted reads whose parent a declaring file declares and the path nothing does.

    Checked are the reads the diff changed, and every read below a path that a
    declaring file the diff changed declares now.
    """
    changed_reads = [
        e
        for key in sorted(by_key)
        if any(u.relation is EdgeType.READS_KEY and not u.base for u in by_key[key])
        for e in graph.by_target[key]
        if e.type is EdgeType.READS_KEY
    ]
    files = {
        head_files.get(u.file, u.file) if u.base else u.file
        for key in by_key
        for u in by_key[key]
        if u.relation is EdgeType.DECLARES_KEY
    }
    below_declared = [
        e
        for file in sorted(files)
        for declared in sorted(graph.declared_in(file))
        for e in graph.reads_under(parse_key_id(declared)[1])
        if _agree(parse_key_id(declared)[0], parse_key_id(e.target)[0])
    ]
    for edge in [*changed_reads, *below_declared]:
        namespace, path = parse_key_id(edge.target)
        parent = _parent(path)
        declaring = graph.declaring_file(namespace, parent, files_only=True) if parent else None
        if declaring is None or graph.declaring_file(namespace, path) is not None:
            continue
        yield KeyItem(
            kind="undeclared-read",
            changed=edge.target,
            site=edge.source,
            file=edge.file_path or "",
            line=edge.line_number,
            key=edge.target,
            reason=f"reads {path}, but {declaring} declares {parent} with no {path}",
            score=specificity(edge.target, graph),
        )


def _removed(graph: KeyGraph, by_key: dict[str, list[KeyUse]]) -> Iterator[KeyItem]:
    """Reads of a key whose declaration the diff removed and no head file declares."""
    for key in sorted(by_key):
        gone = [u.file for u in by_key[key] if u.base and u.relation is EdgeType.DECLARES_KEY]
        namespace, path = parse_key_id(key)
        if not gone or dropped(key, graph) or graph.declaring_file(namespace, path) is not None:
            continue
        for match in graph.matching(key):
            if dropped(match, graph):
                continue
            for edge in sorted(graph.by_target[match], key=_line):
                if edge.type is not EdgeType.READS_KEY:
                    continue
                yield KeyItem(
                    kind="removed-declaration",
                    changed=key,
                    site=edge.source,
                    file=edge.file_path or "",
                    line=edge.line_number,
                    key=match,
                    reason=f"reads {path}, whose declaration in {min(gone)} this diff removes",
                    score=_link_score(key, match, graph),
                )


def _line(edge: Edge) -> int:
    """An edge's line, for a stable order; edges with none come first."""
    return edge.line_number or 0


def _deduplicated(items: list[KeyItem]) -> list[KeyItem]:
    """One item per site and kind, the best scored; a site whose key is listed as
    undeclared or removed is not listed again as merely another reader of it."""
    special: dict[tuple[str, str], int] = {}
    for item in items:
        at = (item.site, item.key)
        if item.kind in _RANK and (at not in special or _RANK[item.kind] < special[at]):
            special[at] = _RANK[item.kind]
    best: dict[tuple[str, str], KeyItem] = {}
    for item in items:
        at = (item.site, item.key)
        if at in special and (item.kind not in _RANK or _RANK[item.kind] != special[at]):
            continue
        slot = (item.kind, item.site)
        if slot not in best or item.score > best[slot].score:
            best[slot] = item
    return [best[slot] for slot in best]
