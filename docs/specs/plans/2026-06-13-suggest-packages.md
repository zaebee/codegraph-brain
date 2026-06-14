# `cgis suggest-packages` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `cgis suggest-packages <prefix>` — detect a package's dependency communities (modularity Q via greedy CNM), measure how far its directory layout diverges from them (1−NMI), and emit a Q∧D-gated, directional verdict; surfaced via CLI and MCP. Report-only.

**Architecture:** Mirrors the `drift` split. Pure logic in `query/cohesion.py` (graph build with root-agnostic suffix reconciliation, greedy modularity, divergence, direction, verdict). Orchestration in `query/suggest_service.py` (reads the store, assembles a frozen `SuggestReport`). CLI command + MCP tool serialize the same report. No NetworkX (consistent with the tricode census).

**Tech Stack:** Python 3.12, frozen dataclasses, Typer + Rich (CLI), FastMCP (MCP), pytest, mypy strict, ruff. Spec: `docs/specs/2026-06-13-suggest-packages-design.md`.

---

## File Structure

- Create `src/cgis/query/cohesion.py` — pure: `FileGraph`, `build_file_graph`, `greedy_modularity`, `partition_divergence`, `layout_direction`, `classify_verdict`. No I/O, no store. Edge reconciliation is in-memory (dot-boundary suffix within the under-prefix file set) so the whole module is unit-testable without a DB.
- Create `src/cgis/query/suggest_service.py` — orchestration: `SuggestReport`, `Community`, `Bridge`, `suggest_packages(db, prefix, with_calls, min_q)`. Reads `SQLiteStore`, uses `resolve_fqn` (#145) for friendly prefix handling, returns the report. Mirror of `drift_service.py`.
- Modify `src/cgis/cli.py` — add `SuggestOutputFormat` enum, the `suggest-packages` command, and `_render_suggest` helper.
- Modify `src/cgis/api/mcp_server.py` — add `cgis_suggest_packages` tool.
- Create `tests/unit/test_cohesion.py`, `tests/unit/test_suggest_service.py`; extend `tests/unit/test_cli.py`, `tests/unit/test_mcp_server.py`, and `tests/self_parsing/test_suggest.py`.

**Determinism:** every community / list output is sorted; CNM tie-breaks on the lexicographically smallest community-label pair. Same graph → same result.

---

## Task 1: `cohesion.py` — `FileGraph` + `build_file_graph` (root-agnostic)

**Files:**
- Create: `src/cgis/query/cohesion.py`
- Test: `tests/unit/test_cohesion.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_cohesion.py
from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.query.cohesion import FileGraph, build_file_graph


def _file(fqn: str, path: str) -> Node:
    return Node(id=fqn, type=NodeType.FILE, name=fqn.rsplit(".", 1)[-1],
                file_path=path, start_line=0, end_line=0)


def _imp(src: str, tgt: str) -> Edge:
    return Edge(id=f"{src}:IMPORTS:{tgt}", source=src, target=tgt,
                type=EdgeType.IMPORTS, weight=1.0, confidence=1.0)


def test_build_file_graph_aggregates_internal_imports() -> None:
    nodes = [_file("p.a", "p/a.py"), _file("p.b", "p/b.py"), _file("p.c", "p/c.py")]
    edges = [_imp("p.a", "p.b"), _imp("p.a", "p.b"), _imp("p.b", "p.c")]
    g = build_file_graph(nodes, edges, prefix="p", with_calls=False)
    assert set(g.files) == {"p.a", "p.b", "p.c"}
    # undirected, weighted, symmetric; a-b carried two import edges
    assert g.adj["p.a"]["p.b"] == 2.0
    assert g.adj["p.b"]["p.a"] == 2.0
    assert g.adj["p.b"]["p.c"] == 1.0


def test_build_file_graph_reconciles_cross_rooted_targets() -> None:
    # Files rooted at p.* (ingest of the package's own dir), but import targets
    # written with the full path cgis.p.* — must still link (the #242 🔴 case).
    nodes = [_file("p.a", "p/a.py"), _file("p.b", "p/b.py")]
    edges = [_imp("p.a", "cgis.p.b")]  # target carries the literal import path
    g = build_file_graph(nodes, edges, prefix="p", with_calls=False)
    assert g.adj["p.a"]["p.b"] == 1.0


def test_build_file_graph_keeps_isolated_files() -> None:
    nodes = [_file("p.a", "p/a.py"), _file("p.b", "p/b.py"), _file("p.leaf", "p/leaf.py")]
    edges = [_imp("p.a", "p.b")]
    g = build_file_graph(nodes, edges, prefix="p", with_calls=False)
    assert "p.leaf" in g.files          # counted even with no edges
    assert g.adj.get("p.leaf", {}) == {}


def test_build_file_graph_excludes_external_and_self_loops() -> None:
    nodes = [_file("p.a", "p/a.py"), _file("p.b", "p/b.py")]
    edges = [_imp("p.a", "enum"), _imp("p.a", "p.a"), _imp("p.a", "p.b")]
    g = build_file_graph(nodes, edges, prefix="p", with_calls=False)
    assert g.adj["p.a"] == {"p.b": 1.0}  # external 'enum' dropped, self-loop dropped


def test_build_file_graph_with_calls_adds_calls_layer() -> None:
    nodes = [_file("p.a", "p/a.py"), _file("p.b", "p/b.py")]
    call = Edge(id="p.a:CALLS:p.b", source="p.a", target="p.b",
                type=EdgeType.CALLS, weight=1.0, confidence=1.0)
    g = build_file_graph(nodes, [call], prefix="p", with_calls=True)
    assert g.adj["p.a"]["p.b"] == 1.0
    g_imports_only = build_file_graph(nodes, [call], prefix="p", with_calls=False)
    assert g_imports_only.adj == {}     # CALLS ignored without the flag
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd .claude/worktrees/issue-242 && uv run pytest tests/unit/test_cohesion.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cgis.query.cohesion'`

- [ ] **Step 3: Implement `cohesion.py` graph build**

```python
# src/cgis/query/cohesion.py
"""Package-cohesion analysis: community detection over a package's file graph (#242).

Pure logic, no I/O. The intra-package file graph is undirected and weighted; edge
reconciliation is done in-memory by dot-boundary FQN suffix so the result does not
depend on the ingest root (src/ vs src/cgis/ — the #242 load-bearing fix).
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Hashable, Mapping
from dataclasses import dataclass

from cgis.core.models import Edge, EdgeType, Node, NodeType

_FILE_TYPES = frozenset({NodeType.FILE, NodeType.MODULE})


@dataclass(frozen=True)
class FileGraph:
    """Undirected, weighted intra-package file graph.

    ``files`` lists every file under the package prefix (sorted, including
    isolated leaves). ``adj`` is a symmetric weighted adjacency: ``adj[a][b]``
    == ``adj[b][a]`` is the aggregated import (and optionally call) weight
    between two files. Isolated files have no ``adj`` entry.
    """

    files: tuple[str, ...]
    adj: dict[str, dict[str, float]]


def _under(fqn: str, prefix: str) -> bool:
    """True iff fqn is the prefix itself or a dot-boundary child of it."""
    return fqn == prefix or fqn.startswith(prefix + ".")


def _build_suffix_index(file_ids: list[str]) -> dict[str, str]:
    """Map every dot-boundary suffix of each file id to that id.

    A suffix that is shared by two files maps to the sentinel ``""`` (ambiguous —
    never reconcile to it). Used to link an import target written with a
    different root (``cgis.p.b``) back to the in-graph file (``p.b``).
    """
    index: dict[str, str] = {}
    for fid in file_ids:
        parts = fid.split(".")
        for i in range(len(parts)):
            suffix = ".".join(parts[i:])
            index[suffix] = "" if suffix in index and index[suffix] != fid else fid
    return index


def _resolve_to_file(node_id: str, file_set: set[str], suffix_index: dict[str, str]) -> str | None:
    """Resolve an edge endpoint to a file under the prefix, or None.

    Exact membership wins; otherwise the longest dot-boundary suffix of
    ``node_id`` that uniquely names a file. Ambiguous or absent → None.
    """
    if node_id in file_set:
        return node_id
    parts = node_id.split(".")
    for i in range(len(parts)):  # longest suffix first
        hit = suffix_index.get(".".join(parts[i:]))
        if hit:
            return hit
    return None


def build_file_graph(
    nodes: list[Node], edges: list[Edge], prefix: str, with_calls: bool
) -> FileGraph:
    """Build the undirected weighted file graph for the package at ``prefix``.

    Files = FILE/MODULE nodes under ``prefix``. Edges = IMPORTS (and CALLS when
    ``with_calls``) whose BOTH endpoints reconcile to distinct files under the
    prefix; weights aggregate. Endpoints are reconciled by dot-boundary suffix
    so a target written with a different root still links (root-agnostic, #242).
    """
    file_ids = sorted(n.id for n in nodes if n.type in _FILE_TYPES and _under(n.id, prefix))
    file_set = set(file_ids)
    suffix_index = _build_suffix_index(file_ids)
    wanted = {EdgeType.IMPORTS} | ({EdgeType.CALLS} if with_calls else set())

    adj: dict[str, dict[str, float]] = {}
    for e in edges:
        if e.type not in wanted:
            continue
        a = _resolve_to_file(e.source, file_set, suffix_index)
        b = _resolve_to_file(e.target, file_set, suffix_index)
        if a is None or b is None or a == b:
            continue
        adj.setdefault(a, {})[b] = adj.setdefault(a, {}).get(b, 0.0) + e.weight
        adj.setdefault(b, {})[a] = adj.setdefault(b, {}).get(a, 0.0) + e.weight
    return FileGraph(files=tuple(file_ids), adj=adj)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_cohesion.py -q`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cgis/query/cohesion.py tests/unit/test_cohesion.py
git commit -m "feat(cohesion): root-agnostic intra-package file graph (#242)"
```

---

## Task 2: `greedy_modularity` — Clauset-Newman-Moore CNM

**Files:**
- Modify: `src/cgis/query/cohesion.py`
- Test: `tests/unit/test_cohesion.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_cohesion.py
import pytest
from cgis.query.cohesion import greedy_modularity


def _clique(prefix: str, names: list[str]) -> dict[str, dict[str, float]]:
    adj: dict[str, dict[str, float]] = {}
    ids = [f"{prefix}.{n}" for n in names]
    for a in ids:
        for b in ids:
            if a != b:
                adj.setdefault(a, {})[b] = 1.0
    return adj


def test_modularity_two_disconnected_cliques() -> None:
    adj = {**_clique("p", ["a", "b", "c"]), **_clique("p", ["x", "y", "z"])}
    g = FileGraph(files=tuple(sorted(adj)), adj=adj)
    communities, q = greedy_modularity(g)
    assert len(communities) == 2
    assert q == pytest.approx(0.5, abs=0.05)
    # each clique is one community
    assert {frozenset(c) for c in communities} == {
        frozenset({"p.a", "p.b", "p.c"}), frozenset({"p.x", "p.y", "p.z"})
    }


def test_modularity_single_clique_is_one_community() -> None:
    adj = _clique("p", ["a", "b", "c"])
    g = FileGraph(files=tuple(sorted(adj)), adj=adj)
    communities, q = greedy_modularity(g)
    assert len(communities) == 1
    assert q == pytest.approx(0.0, abs=0.05)


def test_modularity_isolated_files_are_singletons() -> None:
    adj = _clique("p", ["a", "b", "c"])
    g = FileGraph(files=(*sorted(adj), "p.leaf"), adj=adj)
    communities, q = greedy_modularity(g)
    assert ["p.leaf"] in communities          # isolated → its own singleton
    assert q == pytest.approx(0.0, abs=0.05)   # isolated node is Q-neutral


def test_modularity_is_deterministic() -> None:
    adj = {**_clique("p", ["a", "b", "c"]), **_clique("p", ["x", "y", "z"])}
    g = FileGraph(files=tuple(sorted(adj)), adj=adj)
    assert greedy_modularity(g) == greedy_modularity(g)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_cohesion.py -k modularity -q`
Expected: FAIL — `ImportError: cannot import name 'greedy_modularity'`

- [ ] **Step 3: Implement CNM in `cohesion.py`**

```python
# append to src/cgis/query/cohesion.py

_MIN_GAIN = 1e-12  # ignore non-positive / floating-noise merges


def _modularity(graph: FileGraph, communities: list[list[str]], deg: dict[str, float],
                m2: float) -> float:
    """Newman modularity Q for a partition (m2 == 2m == Σ weighted degrees)."""
    q = 0.0
    for c in communities:
        members = set(c)
        l_c = sum(w for f in c for g, w in graph.adj.get(f, {}).items() if g in members)
        d_c = sum(deg[f] for f in c)
        q += l_c / m2 - (d_c / m2) ** 2
    return q


def greedy_modularity(graph: FileGraph) -> tuple[list[list[str]], float]:
    """Greedy (Clauset-Newman-Moore) community detection; returns (communities, Q).

    Each file starts in its own community; the connected pair with the maximum
    positive ΔQ = 2·(e_ij − a_i·a_j) is merged until no merge improves Q. Ties
    break on the lexicographically smallest label pair (deterministic). Isolated
    files stay singletons and contribute 0 to Q. Returns communities sorted
    (members sorted, then the list sorted).
    """
    files = list(graph.files)
    if not files:
        return [], 0.0
    deg = {f: sum(graph.adj.get(f, {}).values()) for f in files}
    m2 = sum(deg.values())
    if m2 == 0.0:  # all isolated
        return [[f] for f in files], 0.0

    members: dict[str, set[str]] = {f: {f} for f in files}

    def a_of(c: str) -> float:
        return sum(deg[f] for f in members[c]) / m2

    def between(c1: str, c2: str) -> float:
        return sum(graph.adj.get(f, {}).get(g, 0.0) for f in members[c1] for g in members[c2])

    improved = True
    while improved and len(members) > 1:
        improved = False
        best_gain, best_pair = _MIN_GAIN, None
        labels = sorted(members)
        for i, c1 in enumerate(labels):
            for c2 in labels[i + 1:]:
                e_ij = between(c1, c2) / m2
                if e_ij == 0.0:
                    continue  # disconnected — merging cannot raise Q
                dq = 2 * (e_ij - a_of(c1) * a_of(c2))
                if dq > best_gain:
                    best_gain, best_pair = dq, (c1, c2)
        if best_pair is not None:
            c1, c2 = best_pair
            members[c1] |= members.pop(c2)
            improved = True

    communities = sorted(sorted(s) for s in members.values())
    return communities, _modularity(graph, communities, deg, m2)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_cohesion.py -k modularity -q`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cgis/query/cohesion.py tests/unit/test_cohesion.py
git commit -m "feat(cohesion): greedy CNM modularity, deterministic (#242)"
```

---

## Task 3: `partition_divergence` (1−NMI) + `layout_direction`

**Files:**
- Modify: `src/cgis/query/cohesion.py`
- Test: `tests/unit/test_cohesion.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_cohesion.py
from cgis.query.cohesion import layout_direction, partition_divergence


def test_divergence_flat_layout_vs_multi_community_is_one() -> None:
    # P_comm: two groups; P_dir: everything in one "<root>" group (flat package).
    p_comm = {"a": 0, "b": 0, "c": 1, "d": 1}
    p_dir = {"a": "<root>", "b": "<root>", "c": "<root>", "d": "<root>"}
    assert partition_divergence(p_comm, p_dir) == pytest.approx(1.0)


def test_divergence_aligned_layout_is_zero() -> None:
    p_comm = {"a": 0, "b": 0, "c": 1, "d": 1}
    p_dir = {"a": "x", "b": "x", "c": "y", "d": "y"}  # dirs match communities
    assert partition_divergence(p_comm, p_dir) == pytest.approx(0.0)


def test_divergence_is_symmetric() -> None:
    p_comm = {"a": 0, "b": 1, "c": 1}
    p_dir = {"a": "x", "b": "y", "c": "y"}
    assert partition_divergence(p_comm, p_dir) == pytest.approx(
        partition_divergence(p_dir, p_comm)
    )


def test_divergence_both_trivial_is_zero() -> None:
    p_comm = {"a": 0, "b": 0}
    p_dir = {"a": "<root>", "b": "<root>"}
    assert partition_divergence(p_comm, p_dir) == pytest.approx(0.0)


def test_layout_direction() -> None:
    # |P_dir|=1 < |P_comm|=2 → under_split (the flat-package / cgis.query case)
    assert layout_direction({"a": 0, "b": 1}, {"a": "<root>", "b": "<root>"}) == "under_split"
    # |P_dir|=3 > |P_comm|=1 → over_split
    assert layout_direction({"a": 0, "b": 0, "c": 0}, {"a": "x", "b": "y", "c": "z"}) == "over_split"
    # equal counts → matched
    assert layout_direction({"a": 0, "b": 1}, {"a": "x", "b": "y"}) == "matched"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_cohesion.py -k "divergence or direction" -q`
Expected: FAIL — `ImportError: cannot import name 'partition_divergence'`

- [ ] **Step 3: Implement NMI + direction**

```python
# append to src/cgis/query/cohesion.py
from typing import Literal

Direction = Literal["under_split", "over_split", "matched"]


def _entropy(labels: Mapping[str, Hashable]) -> float:
    """Shannon entropy (nats) of a partition's label distribution."""
    n = len(labels)
    counts = Counter(labels.values())
    return -sum((c / n) * math.log(c / n) for c in counts.values())


def _mutual_information(a: Mapping[str, Hashable], b: Mapping[str, Hashable]) -> float:
    """Mutual information (nats) between two partitions over the same key set."""
    n = len(a)
    ca, cb = Counter(a.values()), Counter(b.values())
    joint = Counter((a[k], b[k]) for k in a)
    mi = 0.0
    for (x, y), nxy in joint.items():
        p_xy = nxy / n
        mi += p_xy * math.log(p_xy / ((ca[x] / n) * (cb[y] / n)))
    return mi


def partition_divergence(
    p_comm: Mapping[str, Hashable], p_dir: Mapping[str, Hashable]
) -> float:
    """1 − NMI between two partitions of the same file set, in [0, 1].

    NMI = I(X;Y) / mean(H(X), H(Y)); defined as 1.0 when both partitions are
    trivial (single cluster on each side → D = 0). A non-trivial partition
    against a trivial one has MI 0 → NMI 0 → D = 1 (the flat-package case).
    """
    h_a, h_b = _entropy(p_comm), _entropy(p_dir)
    if h_a == 0.0 and h_b == 0.0:
        return 0.0
    nmi = _mutual_information(p_comm, p_dir) / ((h_a + h_b) / 2)
    return 1.0 - nmi


def layout_direction(
    p_comm: Mapping[str, Hashable], p_dir: Mapping[str, Hashable]
) -> Direction:
    """Direction of the layout/community mismatch by distinct-group count.

    ``under_split`` when the directory layout is flatter than the communities
    (fewer dir groups than communities — the split case); ``over_split`` when
    finer; ``matched`` when equal.
    """
    n_dir, n_comm = len(set(p_dir.values())), len(set(p_comm.values()))
    if n_dir < n_comm:
        return "under_split"
    if n_dir > n_comm:
        return "over_split"
    return "matched"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_cohesion.py -k "divergence or direction" -q`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cgis/query/cohesion.py tests/unit/test_cohesion.py
git commit -m "feat(cohesion): 1-NMI divergence + layout direction (#242)"
```

---

## Task 4: `classify_verdict` — Q∧D∧direction gate

**Files:**
- Modify: `src/cgis/query/cohesion.py`
- Test: `tests/unit/test_cohesion.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_cohesion.py
from cgis.query.cohesion import THRESHOLDS, classify_verdict


def test_verdict_table() -> None:
    t = THRESHOLDS
    # leave: no real structure
    assert classify_verdict(q=0.10, d=1.0, direction="under_split", thresholds=t) == "leave"
    # aligned: structure exists, dirs express it
    assert classify_verdict(q=0.50, d=0.05, direction="matched", thresholds=t) == "aligned"
    # split: strong Q, divergent, flatter layout
    assert classify_verdict(q=0.43, d=1.0, direction="under_split", thresholds=t) == "split"
    # consolidate: strong Q, divergent, finer layout
    assert classify_verdict(q=0.43, d=0.8, direction="over_split", thresholds=t) == "consolidate"
    # matched + strong Q + divergent → re-group → borderline
    assert classify_verdict(q=0.43, d=0.8, direction="matched", thresholds=t) == "borderline"
    # weak Q band, divergent → borderline
    assert classify_verdict(q=0.30, d=1.0, direction="under_split", thresholds=t) == "borderline"


def test_verdict_min_q_override() -> None:
    t = THRESHOLDS
    # raising the split threshold demotes a would-be split to borderline
    strict = {**t, "split": 0.50}
    assert classify_verdict(q=0.43, d=1.0, direction="under_split", thresholds=strict) == "borderline"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_cohesion.py -k verdict -q`
Expected: FAIL — `ImportError: cannot import name 'classify_verdict'`

- [ ] **Step 3: Implement the verdict classifier**

```python
# append to src/cgis/query/cohesion.py

Verdict = Literal["split", "consolidate", "aligned", "leave", "borderline", "no_signal"]

#: Default thresholds (cross-validated for Q; divergence is provisional, #242 spec).
THRESHOLDS: dict[str, float] = {"split": 0.35, "leave": 0.25, "divergence": 0.2}


def classify_verdict(
    *, q: float, d: float, direction: Direction, thresholds: Mapping[str, float]
) -> Verdict:
    """Verdict from modularity Q, divergence D, and mismatch direction.

    ``no_signal`` is decided upstream (no files / no edges); this maps a scored
    package. Gated on BOTH Q (structure is real) and D (layout disagrees), with
    direction disambiguating split (flatter) from consolidate (finer).
    """
    if q < thresholds["leave"]:
        return "leave"
    if d < thresholds["divergence"]:
        return "aligned"
    # Q ≥ leave and D ≥ divergence: a change is warranted.
    if q >= thresholds["split"]:
        if direction == "under_split":
            return "split"
        if direction == "over_split":
            return "consolidate"
    return "borderline"  # weak-Q band, or matched/over at strong Q without a clean split
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_cohesion.py -q`
Expected: PASS (all cohesion tests)

- [ ] **Step 5: Commit**

```bash
git add src/cgis/query/cohesion.py tests/unit/test_cohesion.py
git commit -m "feat(cohesion): Q∧D∧direction verdict classifier (#242)"
```

---

## Task 5: `suggest_service.py` — orchestration + `SuggestReport`

**Files:**
- Create: `src/cgis/query/suggest_service.py`
- Test: `tests/unit/test_suggest_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_suggest_service.py
from pathlib import Path

import pytest

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.query.suggest_service import suggest_packages
from cgis.storage.sqlite_store import SQLiteStore


def _file(fqn: str) -> Node:
    return Node(id=fqn, type=NodeType.FILE, name=fqn.rsplit(".", 1)[-1],
                file_path=fqn.replace(".", "/") + ".py", start_line=0, end_line=0)


def _imp(src: str, tgt: str) -> Edge:
    return Edge(id=f"{src}:IMPORTS:{tgt}", source=src, target=tgt,
                type=EdgeType.IMPORTS, weight=1.0, confidence=1.0)


def _store_with(tmp_path: Path, nodes: list[Node], edges: list[Edge]) -> str:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = str(tmp_path / "g.db")
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)  # confirmed API: save_graph(nodes, edges, overwrite=False)
    return db


def _two_clusters() -> tuple[list[Node], list[Edge]]:
    # Flat package "p": two import-cliques {a,b,c} and {x,y,z}, no cross edges.
    files = [_file(f"p.{n}") for n in ("a", "b", "c", "x", "y", "z")]
    edges = []
    for grp in (("a", "b", "c"), ("x", "y", "z")):
        for s in grp:
            for t in grp:
                if s != t:
                    edges.append(_imp(f"p.{s}", f"p.{t}"))
    return files, edges


def test_suggest_flat_two_clusters_is_split(tmp_path: Path) -> None:
    db = _store_with(tmp_path, *_two_clusters())
    report = suggest_packages(db, prefix="p", with_calls=False)
    assert report.verdict == "split"
    assert report.direction == "under_split"
    assert report.divergence == pytest.approx(1.0)
    assert len(report.communities) == 2
    assert report.file_count == 6


def test_suggest_missing_db_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        suggest_packages(str(tmp_path / "nope.db"), prefix="p")


def test_suggest_no_files_is_no_signal(tmp_path: Path) -> None:
    db = _store_with(tmp_path, [_file("other.a")], [])
    report = suggest_packages(db, prefix="p")
    assert report.verdict == "no_signal"
    assert report.note and "matched 0" in report.note


def test_suggest_mis_rooted_emits_diagnostic(tmp_path: Path) -> None:
    # Files under p.*, but every import target written as cgis.p.* AND the
    # suffix cannot reconcile because no internal file matches → diagnostic.
    nodes = [_file("p.a"), _file("p.b")]
    edges = [_imp("p.a", "wholly.external.thing")]
    db = _store_with(tmp_path, nodes, edges)
    report = suggest_packages(db, prefix="p")
    assert report.verdict == "no_signal"
    assert report.note and "mis-rooted" in report.note.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_suggest_service.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cgis.query.suggest_service'`

- [ ] **Step 3: Implement `suggest_service.py`**

> CONFIRMED APIs (read from the codebase during planning): `SQLiteStore.save_graph(nodes, edges, overwrite=False)` writes; `get_all_nodes()` / `get_all_edges()` read; `store.db_path` is the on-disk path; the store is a context manager. No change needed at execution time.

```python
# src/cgis/query/suggest_service.py
"""Package-cohesion orchestration shared by the CLI and the MCP server (#242)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from cgis.core.models import Edge, Node
from cgis.query.cohesion import (
    THRESHOLDS,
    build_file_graph,
    classify_verdict,
    greedy_modularity,
    layout_direction,
    partition_divergence,
)
from cgis.storage.sqlite_store import SQLiteStore

_ROOT_GROUP = "<root>"


@dataclass(frozen=True)
class Community:
    """One detected community: an id and its member files (last FQN segment)."""

    id: int
    files: list[str]


@dataclass(frozen=True)
class Bridge:
    """A cross-community edge — the cost of splitting (file names, last segment)."""

    source: str
    target: str
    weight: float


@dataclass(frozen=True)
class SuggestReport:
    """Full suggest-packages result; serialized verbatim to CLI-json and MCP."""

    package: str
    layer: str
    file_count: int
    edge_count: int
    modularity_q: float
    divergence: float
    direction: str
    verdict: str
    communities: list[Community]
    bridges: list[Bridge]
    thresholds: dict[str, float]
    note: str | None = None


def _leaf(fqn: str) -> str:
    """Last FQN segment (the module name) for readable output."""
    return fqn.rsplit(".", 1)[-1]


def _dir_group(fqn: str, prefix: str) -> str:
    """The file's directory group relative to the package root.

    A single remaining segment after the prefix → the shared ``"<root>"`` group;
    two or more → the first remaining segment (a real sub-directory). See spec.
    """
    remainder = fqn[len(prefix) + 1 :] if fqn.startswith(prefix + ".") else fqn
    parts = remainder.split(".")
    return _ROOT_GROUP if len(parts) <= 1 else parts[0]


def _empty_report(package: str, layer: str, note: str) -> SuggestReport:
    """A no_signal report carrying a diagnostic note (never a silent green)."""
    return SuggestReport(
        package=package, layer=layer, file_count=0, edge_count=0, modularity_q=0.0,
        divergence=0.0, direction="matched", verdict="no_signal", communities=[],
        bridges=[], thresholds=dict(THRESHOLDS), note=note,
    )


def suggest_packages(
    db_path: str, prefix: str, with_calls: bool = False, min_q: float = 0.35
) -> SuggestReport:
    """Detect a package's communities and score layout divergence (#242).

    Raises:
        FileNotFoundError: if ``db_path`` is not an existing file (run ingest first).
    """
    if not Path(db_path).is_file():
        msg = f"Graph database not found: {db_path}"
        raise FileNotFoundError(msg)

    layer = "imports+calls" if with_calls else "imports"
    with SQLiteStore(db_path) as store:
        nodes: list[Node] = store.get_all_nodes()
        edges: list[Edge] = store.get_all_edges()

    graph = build_file_graph(nodes, edges, prefix, with_calls)
    if not graph.files:
        return _empty_report(prefix, layer, f"fqn_prefix '{prefix}' matched 0 nodes")

    internal_edges = sum(len(v) for v in graph.adj.values()) // 2
    if internal_edges == 0:
        # Distinguish a real flat leaf-bag from a mis-rooted ingest (#242 🔴).
        had_import_attempts = any(
            e.source.startswith(prefix + ".") or e.source == prefix for e in edges
            if e.type.value == "IMPORTS"
        )
        note = (
            f"{prefix}: files found but no import resolves inside the package — the "
            "graph looks mis-rooted or imports are unresolved; try ingesting the "
            "package's parent directory"
            if had_import_attempts
            else f"{prefix}: no intra-package imports (a flat leaf bag)"
        )
        return _empty_report(prefix, layer, note)

    communities, q = greedy_modularity(graph)
    comm_of = {f: i for i, c in enumerate(communities) for f in c}
    dir_of = {f: _dir_group(f, prefix) for f in graph.files}
    divergence = partition_divergence(comm_of, dir_of)
    direction = layout_direction(comm_of, dir_of)
    thresholds = {**THRESHOLDS, "split": min_q}
    verdict = classify_verdict(q=q, d=divergence, direction=direction, thresholds=thresholds)

    bridges = sorted(
        (
            Bridge(source=_leaf(a), target=_leaf(b), weight=w)
            for a in graph.adj
            for b, w in graph.adj[a].items()
            if a < b and comm_of[a] != comm_of[b]
        ),
        key=lambda br: (-br.weight, br.source, br.target),
    )
    return SuggestReport(
        package=prefix, layer=layer, file_count=len(graph.files), edge_count=internal_edges,
        modularity_q=round(q, 4), divergence=round(divergence, 4), direction=direction,
        verdict=verdict,
        communities=[Community(id=i, files=[_leaf(f) for f in c]) for i, c in enumerate(communities)],
        bridges=bridges, thresholds=thresholds,
    )


def report_to_dict(report: SuggestReport) -> dict[str, object]:
    """Plain-dict view for JSON (CLI --format json and MCP share this)."""
    return asdict(report)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_suggest_service.py -q`
Expected: PASS (5 tests). If a write-method name mismatch surfaces, fix the test helper per the NOTE, not the service.

- [ ] **Step 5: Commit**

```bash
git add src/cgis/query/suggest_service.py tests/unit/test_suggest_service.py
git commit -m "feat(suggest): service orchestration + SuggestReport (#242)"
```

---

## Task 6: Root-agnostic regression test (the 🔴 guard)

**Files:**
- Test: `tests/unit/test_suggest_service.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_suggest_service.py
def test_two_ingest_roots_yield_same_verdict(tmp_path: Path) -> None:
    """src/-style (cgis.p.*) and src/cgis/-style (p.*) ingests agree (#242 🔴)."""
    # Root A: files cgis.p.*, targets cgis.p.* (resolve internally).
    files_a = [_file(f"cgis.p.{n}") for n in ("a", "b", "c", "x", "y", "z")]
    edges_a = []
    for grp in (("a", "b", "c"), ("x", "y", "z")):
        for s in grp:
            for t in grp:
                if s != t:
                    edges_a.append(_imp(f"cgis.p.{s}", f"cgis.p.{t}"))
    db_a = _store_with(tmp_path / "a", files_a, edges_a)

    # Root B: files p.*, but targets written cgis.p.* (the mis-rooted shape).
    files_b = [_file(f"p.{n}") for n in ("a", "b", "c", "x", "y", "z")]
    edges_b = []
    for grp in (("a", "b", "c"), ("x", "y", "z")):
        for s in grp:
            for t in grp:
                if s != t:
                    edges_b.append(_imp(f"p.{s}", f"cgis.p.{t}"))
    db_b = _store_with(tmp_path / "b", files_b, edges_b)

    ra = suggest_packages(db_a, prefix="cgis.p")
    rb = suggest_packages(db_b, prefix="p")
    assert ra.verdict == rb.verdict == "split"
    assert ra.modularity_q == pytest.approx(rb.modularity_q)
    assert ra.file_count == rb.file_count == 6
```

> NOTE: `_store_with` now `mkdir`s its directory (fixed in Task 5), so distinct sub-dirs `tmp_path/"a"` and `tmp_path/"b"` give two independent dbs.

- [ ] **Step 2: Run test to verify it fails (then passes once helper dirs exist)**

Run: `uv run pytest tests/unit/test_suggest_service.py::test_two_ingest_roots_yield_same_verdict -q`
Expected: PASS — both roots reconcile to the same 6-file, 2-community split. (If it errors on missing dir, fix the helper per the NOTE.)

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_suggest_service.py
git commit -m "test(suggest): root-agnostic regression — two ingest roots agree (#242)"
```

---

## Task 7: CLI command `suggest-packages`

**Files:**
- Modify: `src/cgis/cli.py` (add `SuggestOutputFormat` near `DriftOutputFormat` ~line 74; add command + `_render_suggest`)
- Test: `tests/unit/test_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_cli.py  (uses the existing `runner` / CliRunner + a built db fixture)
import json as _json

from cgis.cli import app


def test_suggest_packages_json(tmp_path, runner) -> None:
    # Build a flat two-cluster db via ingest of a tiny package, OR reuse an
    # existing fixture that produces a db path. Here: assume `built_db` fixture
    # exists in this module returning a db with cgis.* nodes (mirror drift tests).
    result = runner.invoke(app, ["suggest-packages", "cgis.query", "--db", BUILT_DB, "--format", "json"])
    assert result.exit_code == 0
    payload = _json.loads(result.stdout)
    assert payload["package"] == "cgis.query"
    assert payload["verdict"] in {"split", "borderline", "aligned", "leave", "consolidate", "no_signal"}
    assert "direction" in payload and "modularity_q" in payload


def test_suggest_packages_missing_db(runner) -> None:
    result = runner.invoke(app, ["suggest-packages", "cgis.query", "--db", "/nope/x.db"])
    assert result.exit_code == 1
    assert "not found" in result.stdout.lower()
```

> NOTE: match the existing `test_cli.py` conventions for building a db (there is a fixture/helper used by the `drift`/`structure` CLI tests — reuse it; set `BUILT_DB` accordingly). Keep the test shape, adapt the db source.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_cli.py -k suggest -q`
Expected: FAIL — no such command `suggest-packages`

- [ ] **Step 3: Add the enum, command, and renderer to `cli.py`**

```python
# near the other StrEnums (~line 74), after DriftOutputFormat:
class SuggestOutputFormat(StrEnum):
    """Supported output formats for the suggest-packages command."""

    TEXT = "text"
    JSON = "json"
```

```python
# add imports at top with the other query imports:
from cgis.query.suggest_service import SuggestReport, report_to_dict, suggest_packages

# add the command (place near `drift`):
_VERDICT_LABEL = {
    "split": "✂️  SPLIT",
    "consolidate": "🔗 CONSOLIDATE",
    "aligned": "✅ ALIGNED",
    "leave": "· LEAVE",
    "borderline": "🟡 BORDERLINE",
    "no_signal": "◌ no signal",
}


def _render_suggest(report: SuggestReport) -> None:
    """Rich rendering of a SuggestReport (verdict, metrics, communities, bridges)."""
    console.print(
        f"[bold]{_VERDICT_LABEL.get(report.verdict, report.verdict)}[/bold]  "
        f"{escape(report.package)}  "
        f"[dim]Q={report.modularity_q:.3f}  divergence={report.divergence:.3f}  "
        f"direction={report.direction}  ({report.layer})[/dim]"
    )
    if report.note:
        console.print(f"  [dim]{escape(report.note)}[/dim]")
        return
    comm_table = Table(title="Communities")
    comm_table.add_column("#", justify="right", style="cyan")
    comm_table.add_column("Files", style="white")
    for c in report.communities:
        comm_table.add_row(str(c.id), escape(", ".join(c.files)))
    console.print(comm_table)
    if report.bridges:
        bridge_table = Table(title="Bridge edges (cost of splitting)")
        bridge_table.add_column("Source", style="yellow")
        bridge_table.add_column("Target", style="yellow")
        bridge_table.add_column("Weight", justify="right", style="magenta")
        for b in report.bridges:
            bridge_table.add_row(escape(b.source), escape(b.target), f"{b.weight:.0f}")
        console.print(bridge_table)


@app.command(name="suggest-packages")
def suggest_packages_cmd(
    prefix: str = typer.Argument(..., help="FQN prefix of the package to analyze (e.g. cgis.query)."),
    db: str = typer.Option(_DEFAULT_DB, "--db", "-d", help=_DEFAULT_DB_HELP),
    with_calls: bool = typer.Option(
        False, "--with-calls", help="Include CALLS edges (combined graph), not just IMPORTS."
    ),
    output_format: SuggestOutputFormat = typer.Option(
        SuggestOutputFormat.TEXT, "--format", "-f", help=_TEXT_JSON_FORMAT_HELP
    ),
    min_q: float = typer.Option(
        0.35, "--min-q", min=0.0, max=1.0,
        help="Modularity threshold above which a divergent package is flagged 'split'.",
    ),
) -> None:
    """Suggest sub-package boundaries from a package's dependency communities.

    Detects communities (greedy modularity Q) over the intra-package import
    graph, measures how far the directory layout diverges (1−NMI), and reports a
    verdict (split / consolidate / aligned / leave / borderline). Advisory —
    always exits 0. Run `ingest` first.
    """
    if not Path(db).is_file():
        console.print(f"[bold red]❌ Database not found:[/bold red] {db}. Run `ingest` first.")
        raise typer.Exit(code=1)
    try:
        report = suggest_packages(db, prefix, with_calls=with_calls, min_q=min_q)
    except Exception as e:
        console.print(f"[bold red]❌ Error during suggest-packages:[/bold red] {e}")
        raise typer.Exit(code=1) from e

    if output_format == SuggestOutputFormat.JSON:
        typer.echo(_json.dumps(report_to_dict(report), indent=2))
        return
    _render_suggest(report)
```

> NOTE: confirm the helper names already imported in `cli.py` (`console`, `escape`, `Table`, `_DEFAULT_DB`, `_DEFAULT_DB_HELP`, `_TEXT_JSON_FORMAT_HELP`, `_json`) — all are used by existing commands (`drift`, `structure`). Reuse them; do not redefine.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_cli.py -k suggest -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cgis/cli.py tests/unit/test_cli.py
git commit -m "feat(cli): suggest-packages command (text + json) (#242)"
```

---

## Task 8: MCP tool `cgis_suggest_packages`

**Files:**
- Modify: `src/cgis/api/mcp_server.py` (add tool near `cgis_drift`)
- Test: `tests/unit/test_mcp_server.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_mcp_server.py
import json

from cgis.api.mcp_server import cgis_ingest, cgis_suggest_packages


def test_cgis_suggest_packages_returns_json(repo_with_calls) -> None:
    repo, db = repo_with_calls
    cgis_ingest(str(repo), str(db))
    payload = json.loads(cgis_suggest_packages(str(db), prefix="cgis.query"))
    assert "verdict" in payload
    assert "modularity_q" in payload and "direction" in payload
    assert isinstance(payload["communities"], list)


def test_cgis_suggest_packages_missing_db(tmp_path) -> None:
    result = cgis_suggest_packages(str(tmp_path / "no.db"), prefix="cgis.query")
    assert "not found" in result.lower()
```

> NOTE: reuse whatever ingest fixture the existing `test_cgis_drift_*` tests use (e.g. `repo_with_calls`); the prefix should match a package that exists in that fixture's graph. If the fixture's package isn't `cgis.query`, pick one it has and assert the shape, not a specific verdict.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_mcp_server.py -k suggest -q`
Expected: FAIL — `ImportError: cannot import name 'cgis_suggest_packages'`

- [ ] **Step 3: Add the MCP tool**

```python
# in src/cgis/api/mcp_server.py, near cgis_drift; reuse the module's mcp decorator + _DEFAULT_DB.
from cgis.query.suggest_service import report_to_dict, suggest_packages

@mcp.tool()  # match the decorator the other tools in this file use
def cgis_suggest_packages(
    db_path: str = _DEFAULT_DB, prefix: str = "", with_calls: bool = False, min_q: float = 0.35
) -> str:
    """Suggest sub-package boundaries for a package from its dependency communities.

    Returns JSON: modularity_q, divergence, direction (under/over/matched),
    verdict (split/consolidate/aligned/leave/borderline/no_signal), the detected
    communities (id + member files), the cross-community bridge edges (cost of
    splitting), and the thresholds used. Default layer is IMPORTS; set
    ``with_calls`` for the combined import+call graph. Run ``cgis_ingest`` first.

    A mis-rooted graph (import targets resolve to no internal file) returns
    ``no_signal`` with a diagnostic note rather than a silent clean verdict.
    """
    if not Path(db_path).exists():
        return f"❌ Database not found at: {db_path}. Run cgis_ingest first."
    try:
        report = suggest_packages(db_path, prefix, with_calls=with_calls, min_q=min_q)
    except Exception as e:  # noqa: BLE001 — surface any failure as a string, like cgis_drift
        return f"❌ Error during suggest-packages: {e}"
    return json.dumps(report_to_dict(report), indent=2)
```

> CONFIRMED (read during planning): this file uses `@mcp.tool()`, `import json` (plain `json`, not `_json`), and `_DEFAULT_DB = "graph.db"`. The code above already matches.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_mcp_server.py -k suggest -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cgis/api/mcp_server.py tests/unit/test_mcp_server.py
git commit -m "feat(mcp): cgis_suggest_packages tool (#242)"
```

---

## Task 9: Self-parsing dogfood + nested-package D calibration

**Files:**
- Create: `tests/self_parsing/test_suggest.py`

- [ ] **Step 1: Write the test**

```python
# tests/self_parsing/test_suggest.py
"""Self-parsing validation for suggest-packages: the tool must catch the smell
it was built for (cgis.query) and read an already-nested package as aligned."""

import pytest

from cgis.core.models import Edge, Node
from cgis.query.suggest_service import suggest_packages
from cgis.storage.sqlite_store import SQLiteStore


def _db_path(store: SQLiteStore) -> str:
    """The on-disk path backing a self-parsing store fixture."""
    return store.db_path  # CONFIRMED: SQLiteStore.db_path (set in __init__).


def test_cgis_query_is_flagged_split(
    root_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """cgis.query (19 flat files, Q≈0.43) must read as 'split' — the canonical
    dogfood validation (the self-drift analogue, #242)."""
    store, _, _ = root_graph_data
    report = suggest_packages(_db_path(store), prefix="cgis.query", with_calls=False)
    assert report.verdict == "split"
    assert report.direction == "under_split"
    assert 0.38 <= report.modularity_q <= 0.48
    assert report.divergence == pytest.approx(1.0)  # flat package


def test_cgis_guardian_nested_is_not_split(
    root_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """cgis.guardian has a real providers/ subpackage — a non-flat layout, so
    divergence is below 1.0 and the verdict is NOT 'split'. Calibrates that the
    0.2 divergence threshold does work on a nested package (spec: provisional)."""
    store, _, _ = root_graph_data
    report = suggest_packages(_db_path(store), prefix="cgis.guardian", with_calls=False)
    assert report.verdict in {"aligned", "borderline", "leave", "consolidate"}
    assert report.verdict != "split"
```

> NOTE: `root_graph_data` ingests `src/` so FQNs carry the `cgis.` prefix (see `tests/self_parsing/conftest.py`); CONFIRMED it is a session fixture yielding `(store, nodes, edges)`. The guardian assertion is intentionally a band, not an exact verdict, because its measured divergence/Q is what calibrates the threshold — record the observed numbers in the PR description.

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/self_parsing/test_suggest.py -q -s`
Expected: PASS. If `test_cgis_guardian_nested_is_not_split` fails, **record the actual Q/divergence** — that is the calibration data the spec asked for. If guardian's divergence ≥ 0.2 with high Q (reads `split` despite the providers/ split), the `0.2` threshold needs adjustment; surface this to the human before changing it (do not silently retune).

- [ ] **Step 3: Commit**

```bash
git add tests/self_parsing/test_suggest.py
git commit -m "test(self): suggest-packages dogfood (cgis.query split, guardian nested) (#242)"
```

---

## Task 10: Self-drift check + conscious re-baseline + full verification

**Files:**
- Possibly modify: `docs/ontology/patterns.yaml` and `src/cgis/query/ontology_init.py` (only if the new files push `cgis.query` over tolerance)

- [ ] **Step 1: Run the self-drift guardrail**

Run: `uv run pytest tests/self_parsing/test_drift.py -q`
Expected: PASS. The two new files (`cohesion.py`, `suggest_service.py`) join the `cgis.query` census domain (already at drift ~0.18, tolerance 0.18). If this test now fails on `cgis.query`, that is the **expected** RFC-risk-#2 outcome — proceed to Step 2. If it passes, skip to Step 3.

- [ ] **Step 2: Conscious re-baseline (only if Step 1 failed on cgis.query)**

Measure the new score and raise the `cgis.query` tolerance in BOTH places (they are kept parse-identical by a staleness pin test):
- `docs/ontology/patterns.yaml` — the `cgis.query` `project_domain` `drift_tolerance`.
- `src/cgis/query/ontology_init.py` — the mirrored `_DEFAULT_ONTOLOGY_HEADER`.

Run to get the number:
```bash
uv run python -c "
from cgis.extractors.python_extractor import PythonExtractor
from cgis.pipeline import IngestionPipeline
from cgis.storage.sqlite_store import SQLiteStore
from cgis.query.fingerprint import FingerprintExtractor
from cgis.query.drift import DriftScorer
import tempfile, logging; logging.disable(logging.CRITICAL)
db = tempfile.mktemp(suffix='.db')
with SQLiteStore(db) as s:
    IngestionPipeline({'.py': PythonExtractor()}).run('src', store=s)
    fp = FingerprintExtractor(s).extract('cgis.query')
    sc = DriftScorer('docs/ontology/patterns.yaml')
    d = next(d for d in sc.load_project_domains() if d.fqn_prefix=='cgis.query')
    print('cgis.query drift =', round(sc.score(fp, d).drift_score, 4))
"
```
Set the new tolerance to the measured value rounded up to 2 dp (precedent #174/#161/#199), and add a one-line comment in `patterns.yaml` noting the re-baseline is for the #242 suggest-packages files. Re-run Step 1 → PASS.

> This is the precedent path, not a metric fudge: a deliberate feature added files to a domain; the ratchet is re-negotiated with a comment, exactly as in [[drift-rebaseline-on-refactor]].

- [ ] **Step 3: Full verification**

Run: `make format && make lint && make type-check && make pytest && make doc-coverage`
Expected: all green. mypy strict must pass (all new functions are fully annotated); interrogate ≥ 90% (every new public function has a docstring).

- [ ] **Step 4: Commit any re-baseline / lint fixups**

```bash
git add -A
git commit -m "chore(suggest): self-drift re-baseline + lint/type/doc green (#242)"
```

---

## Self-Review (completed during planning)

- **Spec coverage:** architecture (Tasks 1, 5, 7, 8) · Q (Task 2) · divergence 1−NMI + flat-degeneracy (Task 3) · direction + consolidate (Tasks 3, 4) · verdict table incl. matched→borderline (Task 4) · root-agnostic suffix reconciliation + mis-rooted diagnostic (Tasks 1, 5, 6) · isolated files (Tasks 1, 2) · graph layer imports/`--with-calls` (Task 1, CLI Task 7) · CLI + MCP + JSON contract incl. `direction` (Tasks 7, 8) · error handling missing-db/no-files/mis-rooted (Task 5) · self-parsing dogfood + nested D calibration (Task 9) · risk-#2 re-baseline (Task 10). All sections mapped.
- **Placeholder scan:** no TBD/TODO; every code step shows full code. The three `> NOTE`s flag real codebase facts to confirm (store write/read method names, existing CLI/MCP fixtures, the MCP decorator) — these are verification instructions, not placeholders, because the exact local names must be read from the code at execution time rather than guessed.
- **Type consistency:** `FileGraph(files, adj)`, `greedy_modularity → (list[list[str]], float)`, `partition_divergence(p_comm, p_dir)`, `layout_direction → Direction`, `classify_verdict(*, q, d, direction, thresholds) → Verdict`, `SuggestReport`/`Community`/`Bridge`, `suggest_packages(db_path, prefix, with_calls, min_q)`, `report_to_dict` — names and signatures are consistent across Tasks 1–9. `THRESHOLDS` keys (`split`/`leave`/`divergence`) match the JSON contract and `--min-q` override.

## Execution Handoff — see end of session message.
