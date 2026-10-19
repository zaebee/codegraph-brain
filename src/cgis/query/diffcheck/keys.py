"""The data keys a diff touched, and the other sites of each (DK-1..DK-3).

A key changes where the diff's lines meet a key edge: in the head graph, or in the
base text re-parsed by `check`. Each changed key lists the other readers, writers and
declarers of every key that can name the same data (`keys_match`), and readers of
paths inside it (`avatar.url` reads into `avatar`). Two more kinds compare reads
with declarations: a dotted path read whose parent a mapping declares and the path
nothing does (`undeclared-read`), and a declaration only the base has whose path is
still read (`removed-declaration`). Specificity ranks them; an unscoped path made only
of common keys, or used in many files, is never listed.
"""

import math
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from itertools import takewhile

from cgis.core.keys import key_id, parse_key_id
from cgis.core.models import RAW_KEY_PREFIX, Edge, EdgeType, Node
from cgis.core.paths import is_test_path
from cgis.languages import names_code, pack_for_path
from cgis.resolver.keys import constant_namespace
from cgis.storage.sqlite_store import SQLiteStore

#: Keys that name no data in particular: an unscoped path made only of these
#: (`id`, `data.id`, `result.type`) is never listed.
COMMON_KEYS = frozenset({"id", "name", "type", "data", "value", "key", "items", "result"})
#: An unscoped key, dotted or not, referenced from more files than this is not listed.
MAX_FILES = 8
#: Nodes read per suffix lookup of an imported module: enough to see a second FILE.
_SUFFIX_LIMIT = 1000

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
    #: Order among items of equal score and kind: 0 first. An undeclared read under a
    #: leaf-typed parent (`keyword`) is 0, under an object or untyped parent 1.
    tier: int = 0


def _show(key: str) -> str:
    """A key id as a reader sees it: `path`, or `path (namespace)`."""
    namespace, path = parse_key_id(key)
    return f"{path} ({namespace})" if namespace else path


def _record_mapping(
    edge: Edge, in_files: dict[str, str], types: dict[str, str], declared_in: set[str]
) -> None:
    """Note a mapping declaration: the first file per namespace and its type, and the id."""
    namespace = parse_key_id(edge.target)[0]
    if namespace not in in_files:
        in_files[namespace] = edge.file_path or ""
        types[namespace] = edge.context or ""
    declared_in.add(edge.target)


class KeyGraph:
    """The head graph's key edges, indexed by key, file, path and parent path."""

    def __init__(self, store: SQLiteStore) -> None:
        """Read every key edge once: the check never walks them through traversals."""
        by_target: defaultdict[str, list[Edge]] = defaultdict(list)
        by_file: defaultdict[str, list[Edge]] = defaultdict(list)
        ids: defaultdict[str, set[str]] = defaultdict(set)  # path -> key ids
        declared: defaultdict[str, dict[str, str]] = defaultdict(dict)  # path -> ns -> file
        # The same, for mapping declarations only: not a model's fields, and not the keys
        # of a plain document (a ConfigMap's `data:` is no schema of every `resp["data"]`).
        in_files: defaultdict[str, dict[str, str]] = defaultdict(dict)
        # path -> ns -> the mapping type a mapping gives it ("" when none).
        types: defaultdict[str, dict[str, str]] = defaultdict(dict)
        declared_in: defaultdict[str, set[str]] = defaultdict(set)  # mapping file -> key ids
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
                if declaring[file] and _from_mapping(namespace, edge):
                    _record_mapping(edge, in_files[path], types[path], declared_in[file])
            elif edge.type is EdgeType.READS_KEY and "." in path:
                reads_under[_parent(path)].append(edge)
        self.by_target = by_target
        self.by_file = by_file
        self._ids = ids
        self._declared = declared
        self._in_files = in_files
        self._types = types
        self._declared_in = declared_in
        self._reads_under = reads_under
        self._paths = sorted(ids)
        self._files: dict[str, int] = {}

    def matching(self, key: str) -> list[str]:
        """The key ids that can name the same data as `key`: same path, namespace agreeing."""
        namespace, path = parse_key_id(key)
        return sorted(k for k in self._ids[path] if _agree(namespace, parse_key_id(k)[0]))

    def inside(self, key: str) -> list[str]:
        """Ids of paths below `key`'s (`avatar.url` under `avatar`), namespaces agreeing."""
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

    def declaring_file(
        self, namespace: str, path: str, *, mappings_only: bool = False
    ) -> str | None:
        """A file declaring `path` in a namespace agreeing with `namespace`; None when none does.

        `mappings_only` counts a data file's mapping declarations alone: not the fields
        of a model, and not the keys of a plain JSON or YAML document.
        """
        declared = (self._in_files if mappings_only else self._declared)[path]
        files = [declared[ns] for ns in declared if _agree(namespace, ns)]
        return min(files) if files else None

    def leaf_parent(self, namespace: str, path: str) -> bool:
        """Whether a declaring file gives `path` a leaf mapping type (`keyword`, not `object`)."""
        types = self._types[path]
        return any(types[ns] not in ("", "object") for ns in types if _agree(namespace, ns))

    def declared_in(self, file: str) -> set[str]:
        """The key ids `file` declares as a mapping."""
        return self._declared_in[file]

    def reads_under(self, path: str) -> list[Edge]:
        """Reads of the paths one level below `path`."""
        return self._reads_under[path]


def _is_declaring(file: str) -> bool:
    """Whether `file` is a declaring file: one whose pack names no code (JSON, YAML)."""
    pack = pack_for_path(file)
    return pack is not None and not names_code(pack.name)


def _from_mapping(namespace: str, edge: Edge) -> bool:
    """Whether a data file's declaration comes from a mapping, not a plain document.

    The data pack gives a mapping's declarations the property type as `context` and,
    for a file the profile links to an index, that index as namespace; a plain
    document's keys carry neither. A mapping property with no type, in a file linked
    to no index, is missed: it declares no parent here.
    """
    return bool(namespace or edge.context)


def _parent(path: str) -> str:
    """The path one level up: `status` for `status.keyword`; "" at the top."""
    return path.rpartition(".")[0]


def _agree(a: str, b: str) -> bool:
    """Whether two namespaces can hold the same key: equal, or either unscoped."""
    return a == b or not a or not b


def dropped(key: str, graph: KeyGraph) -> bool:
    """An unscoped key made only of common segments, or used everywhere: never listed."""
    namespace, path = parse_key_id(key)
    if namespace:
        return False
    common = all(segment in COMMON_KEYS for segment in path.split("."))
    return common or graph.files(key) > MAX_FILES


def _link_score(changed: str, site: str, graph: KeyGraph) -> float:
    """A link between two keys is as specific as its less specific end."""
    return min(specificity(changed, graph), specificity(site, graph))


def specificity(key: str, graph: KeyGraph) -> float:
    """Segments in the path, plus one if scoped, minus log2 of the files referencing it."""
    namespace, path = parse_key_id(key)
    return path.count(".") + 1 + (1 if namespace else 0) - math.log2(max(graph.files(key), 1))


def base_key(target: str, file: Node | None, store: SQLiteStore) -> str:
    """The key id of a re-parsed base edge's `raw_key:` target, as the resolver would make it.

    An `@NAME` namespace goes through `constant_namespace`, the resolver's rule, with
    the base FILE node's own import map and the head graph for the imported module.
    """
    namespace, _, path = target.removeprefix(RAW_KEY_PREFIX).partition("|")
    if namespace.startswith("@"):
        imports = file.metadata.get("import_map") if file is not None else None
        namespace = constant_namespace(
            namespace[1:],
            file,
            imports if isinstance(imports, dict) else {},
            store.get_node,
            lambda fqn: store.find_nodes_by_suffix(fqn, limit=_SUFFIX_LIMIT),
        )
    return key_id(namespace, path)


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
            yield from _match_sites(graph, key, match, (sources, words), match in below)


def _match_sites(
    graph: KeyGraph, key: str, match: str, seen: tuple[set[str], str], is_below: bool
) -> Iterator[KeyItem]:
    """The sites of `match` other than the changed key's own; `seen` is its sources and words."""
    sources, words = seen
    score = _link_score(key, match, graph)
    for edge in sorted(graph.by_target[match], key=_line):
        if edge.source in sources or (is_below and edge.type is not EdgeType.READS_KEY):
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
    """Dotted reads whose parent a mapping declares and the path nothing does.

    Checked are the reads the diff changed, and every read below a path that a
    mapping the diff changed declares now: the whole mapping, not its changed lines,
    since a mapping change is where a read it never declared breaks (#2075 added
    `details` labels; the read it broke was `event_type.keyword`, on no changed line).
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
        declaring = graph.declaring_file(namespace, parent, mappings_only=True) if parent else None
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
            tier=0 if graph.leaf_parent(namespace, parent) else 1,
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
            yield from _removed_reads(graph, key, match, min(gone))


def _removed_reads(graph: KeyGraph, key: str, match: str, gone: str) -> Iterator[KeyItem]:
    """The reads of `match`, whose declaration in `gone` the diff removes from `key`."""
    path = parse_key_id(key)[1]
    for edge in sorted(graph.by_target[match], key=_line):
        if edge.type is EdgeType.READS_KEY:
            yield KeyItem(
                kind="removed-declaration",
                changed=key,
                site=edge.source,
                file=edge.file_path or "",
                line=edge.line_number,
                key=match,
                reason=f"reads {path}, whose declaration in {gone} this diff removes",
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
