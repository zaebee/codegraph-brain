"""Go dispatch: interface satisfaction in, embedding out (#609, #642).

Go types satisfy interfaces without declaring it, so a call on an interface value
resolved to the interface method and reached no implementation: impact on a
concrete method missed every caller going through the interface (about 85% of
missed callers on hashicorp/raft and prometheus/client_golang). And embedding was
read as inheritance, so a method on the embedded type "dispatched" to the outer
type's same-named method, which Go never does.
"""

from pathlib import Path

from cgis.core.models import Edge, EdgeType, Node
from cgis.languages import extractors
from cgis.languages.go.extractor import GoExtractor
from cgis.pipeline import IngestionPipeline
from cgis.resolver.engine import ResolverEngine
from cgis.storage.sqlite_store import SQLiteStore

FILES = {
    "store/store.go": (
        "package store\n"
        "type LogStore interface {\n"
        "\tStoreLogs()\n"
        "\tFirstIndex() int\n"
        "}\n"
        "type Closer interface{ Close() }\n"
        "type ClosingStore interface {\n"
        "\tLogStore\n"
        "\tCloser\n"
        "\tSync()\n"
        "}\n"
    ),
    "store/inmem.go": (
        "package store\n"
        "type InmemStore struct{}\n"
        "func (s *InmemStore) StoreLogs() {}\n"
        "func (s *InmemStore) FirstIndex() int { return 0 }\n"
        "func (s *InmemStore) Sync() {}\n"
    ),
    # Satisfies LogStore only through a promoted method.
    "store/wrapped.go": (
        "package store\n"
        "type base struct{}\n"
        "func (b *base) FirstIndex() int { return 1 }\n"
        "type Wrapped struct{ base }\n"
        "func (w *Wrapped) StoreLogs() {}\n"
        "func (w *Wrapped) Close() {}\n"
        "func (w *Wrapped) Sync() {}\n"
    ),
    # One of two methods: not a LogStore.
    "store/partial.go": (
        "package store\ntype Partial struct{}\nfunc (p *Partial) StoreLogs() {}\n"
    ),
    # Embedding with a same-named method on both sides (#642).
    "raft/raft.go": (
        "package raft\n"
        "type raftState struct{}\n"
        "func (r *raftState) setState() {}\n"
        "type Raft struct{ raftState }\n"
        "func (r *Raft) setState() {}\n"
        "func (r *Raft) run(s store.LogStore) { s.StoreLogs() }\n"
    ),
}


def _resolve() -> tuple[list[Edge], list[Node]]:
    nodes: list[Node] = []
    edges: list[Edge] = []
    for path, code in FILES.items():
        n, e = GoExtractor().parse(code, path)
        nodes += n
        edges += e
    resolved, virtual = ResolverEngine(nodes, edges).resolve()
    return resolved, [*nodes, *virtual]


def _dispatches(edges: list[Edge]) -> set[tuple[str, str]]:
    return {(e.source, e.target) for e in edges if e.type is EdgeType.DISPATCHES}


def test_an_interface_is_marked() -> None:
    _, nodes = _resolve()
    flags = {n.id: n.metadata.get("interface") for n in nodes if n.id.startswith("store.")}
    assert flags["store.LogStore"] is True
    assert flags["store.InmemStore"] is None


def test_an_interface_method_dispatches_to_each_implementation() -> None:
    edges, _ = _resolve()
    d = _dispatches(edges)
    assert ("store.LogStore.StoreLogs", "store.InmemStore.StoreLogs") in d
    assert ("store.LogStore.FirstIndex", "store.InmemStore.FirstIndex") in d


def test_a_promoted_method_counts_and_the_edge_points_at_its_definition() -> None:
    edges, _ = _resolve()
    d = _dispatches(edges)
    assert ("store.LogStore.StoreLogs", "store.Wrapped.StoreLogs") in d
    assert ("store.LogStore.FirstIndex", "store.base.FirstIndex") in d


def test_a_type_missing_a_method_does_not_satisfy() -> None:
    edges, _ = _resolve()
    assert not {t for _, t in _dispatches(edges) if t.startswith("store.Partial.")}


def test_an_embedded_interface_contributes_its_methods() -> None:
    """ClosingStore = LogStore + Closer + Sync. InmemStore has Sync but no Close."""
    edges, _ = _resolve()
    targets = {t for s, t in _dispatches(edges) if s == "store.ClosingStore.Sync"}
    assert targets == {"store.Wrapped.Sync"}


def test_embedding_emits_no_dispatch() -> None:
    """Calling raftState.setState never runs Raft.setState (#642)."""
    edges, _ = _resolve()
    assert ("raft.raftState.setState", "raft.Raft.setState") not in _dispatches(edges)
    # The embedding itself is still recorded: it is what promotes methods.
    assert ("raft.Raft", "raft.raftState") in {
        (e.source, e.target) for e in edges if e.type is EdgeType.EXTENDS
    }


def test_dispatch_survives_the_stored_graph_refresh(tmp_path: Path) -> None:
    """The pipeline recomputes DISPATCHES over the stored graph after every write."""
    root = tmp_path / "repo"
    for path, code in FILES.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(code)
    with SQLiteStore(str(tmp_path / "g.db")) as store:
        IngestionPipeline(extractors([])).run(str(root), store=store)
        d = {(e.source, e.target) for e in store.get_edges_of_type(EdgeType.DISPATCHES)}
    assert ("store.LogStore.StoreLogs", "store.InmemStore.StoreLogs") in d
    assert ("raft.raftState.setState", "raft.Raft.setState") not in d


def _dispatch_of(files: dict[str, str]) -> set[tuple[str, str]]:
    nodes: list[Node] = []
    edges: list[Edge] = []
    for path, code in files.items():
        n, e = GoExtractor().parse(code, path)
        nodes += n
        edges += e
    resolved, _ = ResolverEngine(nodes, edges).resolve()
    return _dispatches(resolved)


_DOER = "package a\ntype Doer interface{ Do() }\n"


def test_a_struct_embedding_an_interface_makes_no_self_loop() -> None:
    d = _dispatch_of({"a/d.go": _DOER, "a/w.go": "package a\ntype Wrap struct{ Doer }\n"})
    assert ("a.Doer.Do", "a.Doer.Do") not in d


def test_a_shallower_method_shadows_the_embedded_one() -> None:
    d = _dispatch_of(
        {
            "a/d.go": _DOER,
            "a/o.go": (
                "package a\n"
                "type Inner struct{}\nfunc (i *Inner) Do() {}\n"
                "type Outer struct{ Inner }\nfunc (o *Outer) Do() {}\n"
            ),
        }
    )
    assert ("a.Doer.Do", "a.Outer.Do") in d
    assert ("a.Doer.Do", "a.Inner.Do") in d  # Inner satisfies Doer on its own
    targets_of_doer = {c for s, c in d if s == "a.Doer.Do"}
    assert targets_of_doer == {"a.Outer.Do", "a.Inner.Do"}


def test_a_test_file_method_does_not_complete_a_production_type() -> None:
    """`Extra` exists only in a _test.go file, so Impl does not satisfy Doer2."""
    d = _dispatch_of(
        {
            "a/d.go": "package a\ntype Doer2 interface {\n\tDo()\n\tExtra()\n}\n",
            "a/impl.go": "package a\ntype Impl struct{}\nfunc (i *Impl) Do() {}\n",
            "a/d_test.go": "package a\nfunc (i *Impl) Extra() {}\n",
        }
    )
    assert d == set()


def test_a_test_type_does_not_implement() -> None:
    """A fake embedding X and Y would be the only AB; production has none."""
    d = _dispatch_of(
        {
            "a/ab.go": (
                "package a\n"
                "type AB interface {\n\tA()\n\tB()\n}\n"
                "type X struct{}\nfunc (x *X) A() {}\n"
                "type Y struct{}\nfunc (y *Y) B() {}\n"
            ),
            "a/ab_test.go": "package a\ntype fake struct{\n\tX\n\tY\n}\n",
        }
    )
    assert d == set()


def test_a_struct_embedding_an_interface_redispatches_to_it() -> None:
    """Wrap holds a Doer, so a call through Other runs Doer's dynamic method."""
    d = _dispatch_of(
        {
            "a/d.go": _DOER + "type Other interface{ Do() }\n",
            "a/w.go": "package a\ntype Wrap struct{ Doer }\n",
        }
    )
    assert ("a.Other.Do", "a.Doer.Do") in d


def test_an_interface_embedding_one_outside_the_graph_matches_on_what_is_seen() -> None:
    """`RC` needs io.Reader's methods too, which the graph cannot see: an upper bound.

    Skipping such interfaces lost 9 true edges on hashicorp/raft and prevented none.
    """
    d = _dispatch_of(
        {
            "a/rc.go": 'package a\nimport "io"\ntype RC interface {\n\tio.Reader\n\tClose()\n}\n',
            "a/c.go": "package a\ntype C struct{}\nfunc (c *C) Close() {}\n",
        }
    )
    assert ("a.RC.Close", "a.C.Close") in d


def test_dispatch_survives_an_incremental_body_edit(tmp_path: Path) -> None:
    """A body edit re-extracts one file; the edge into it is recomputed from the store."""
    root = tmp_path / "repo"
    files = {
        "a/d.go": _DOER,
        "a/impl.go": "package a\ntype Impl struct{}\nfunc (i *Impl) Do() {}\n",
    }
    for path, code in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(code)
    with SQLiteStore(str(tmp_path / "g.db")) as store:
        IngestionPipeline(extractors([])).run(str(root), store=store)
        (root / "a/impl.go").write_text(
            "package a\ntype Impl struct{}\nfunc (i *Impl) Do() { _ = 1 }\n"
        )
        IngestionPipeline(extractors([])).run(str(root), store=store)
        d = {(e.source, e.target) for e in store.get_edges_of_type(EdgeType.DISPATCHES)}
    assert d == {("a.Doer.Do", "a.Impl.Do")}
