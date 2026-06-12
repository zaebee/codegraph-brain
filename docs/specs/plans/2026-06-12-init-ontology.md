# cgis init-ontology (#174) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `cgis init-ontology` (CLI) and `cgis_init_ontology` (MCP, read-only) propose a ready-to-edit `patterns.yaml` from the measured graph — closing #174 (roadmap #179 P0 adoption blocker).

**Architecture:** Per `docs/specs/2026-06-12-init-ontology-design.md`. New module `src/cgis/query/ontology_init.py` (module-level functions, no class): `discover_domains` (auto-descent prefix discovery) + `propose_ontology` (fit each domain against the 5 bundled templates by scoring with the existing `DriftScorer` — zero new fitting math; honest no-fit > 0.5 → hygiene-only; tolerance = measured + margin for ALL entries). Round-trip guarantee: the proposed yaml passes `analyze_drift` green by construction.

**Tech Stack:** Python 3.12, dataclasses, tempfile, pytest, mypy strict, ruff, interrogate ≥90%.

**Branch:** `feat/issue-174-init-ontology` (worktree `.claude/worktrees/issue-174-init-ontology` — Lane A; `cli.py`/`mcp_server.py` changes are APPEND-ONLY blocks)

**Hard rules for every task:**
- Run the FULL unit suite (`uv run pytest tests/unit/ -q`) before committing.
- Read-only over the graph; `docs/ontology/patterns.yaml` is NEVER modified.
- Docstrings everywhere (interrogate ≥90%). `/bin/rm -f` instead of bare rm.
- Determinism: any iteration feeding output must be sorted or in yaml-declaration order; fitting ties break by template name.

---

### Task 1: `discover_domains` (TDD)

**Files:**
- Create: `src/cgis/query/ontology_init.py` (first slice: discovery only)
- Create: `tests/unit/test_ontology_init.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_ontology_init.py`:

```python
"""Unit tests for the init-ontology proposer (#174)."""

from pathlib import Path

import pytest

from cgis.core.models import VIRTUAL_FILE_PATH, Edge, EdgeType, Node, NodeType
from cgis.query.ontology_init import discover_domains, propose_ontology
from cgis.storage.sqlite_store import SQLiteStore


def _node(fqn: str, file_path: str = "mod.py", node_type: NodeType = NodeType.FUNCTION) -> Node:
    """Minimal node for discovery/proposal tests."""
    return Node(
        id=fqn,
        type=node_type,
        name=fqn.rsplit(".", maxsplit=1)[-1],
        file_path=file_path,
        start_line=1,
        end_line=2,
    )


# ---------------------------------------------------------------------------
# discover_domains
# ---------------------------------------------------------------------------


def test_discover_auto_descends_single_root() -> None:
    """src.click.{core,parser} → domains at the first level with >= 2 children."""
    nodes = [
        _node("src.click.core.f"),
        _node("src.click.core.g"),
        _node("src.click.parser.h"),
    ]
    assert discover_domains(nodes) == ["src.click.core", "src.click.parser"]


def test_discover_multi_root_uses_roots_level() -> None:
    """Two top-level roots → the roots themselves are the candidates."""
    nodes = [_node("app.f"), _node("lib.g")]
    assert discover_domains(nodes) == ["app", "lib"]


def test_discover_depth_override() -> None:
    """depth=3 takes 3-segment prefixes regardless of auto-descent."""
    nodes = [
        _node("src.click.core.sub.f"),
        _node("src.click.parser.other.g"),
    ]
    assert discover_domains(nodes, depth=3) == ["src.click.core", "src.click.parser"]


def test_discover_excludes_virtual_nodes() -> None:
    """Virtual boundary nodes (file_path == EXTERNAL) never form domains."""
    nodes = [
        _node("app.real.f"),
        _node("app.real.g"),
        _node("fastapi.Depends", file_path=VIRTUAL_FILE_PATH),
        _node("os.path.join", file_path=VIRTUAL_FILE_PATH),
    ]
    domains = discover_domains(nodes)
    assert all(not d.startswith(("fastapi", "os")) for d in domains)


def test_discover_sorted_and_deduplicated() -> None:
    """Output is sorted; many nodes per prefix yield one candidate."""
    nodes = [_node("z.b.f"), _node("z.a.g"), _node("z.a.h"), _node("z.b.i")]
    assert discover_domains(nodes) == ["z.a", "z.b"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_ontology_init.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cgis.query.ontology_init'` (the propose_ontology import also fails until Task 2 — add a temporary `propose_ontology` stub raising NotImplementedError in Step 3 so the import line works, OR split the import; simplest: include the stub).

- [ ] **Step 3: Implement discovery (+ propose stub)**

Create `src/cgis/query/ontology_init.py`:

```python
"""Auto-propose a starter patterns.yaml from the measured graph (#174).

Measure-then-label: discover candidate domains from FQN structure, fit each
against the bundled pattern templates by scoring with the existing
DriftScorer, and emit a ready-to-edit ontology whose tolerances are the
measured values plus a margin — green by construction on the same graph.
"""

from cgis.core.models import VIRTUAL_FILE_PATH, Node


def discover_domains(nodes: list[Node], depth: int | None = None) -> list[str]:
    """Candidate domain prefixes from node FQNs (spec §2.1).

    Auto-descent: walk down from the FQN roots while a level has a single
    child; the first level with >= 2 children yields the candidates. An
    explicit ``depth`` (segment count) overrides auto-descent. Virtual
    boundary nodes are excluded. Sorted, deduplicated.
    """
    real_ids = [n.id for n in nodes if n.file_path != VIRTUAL_FILE_PATH]
    if not real_ids:
        return []
    if depth is not None:
        return sorted({".".join(i.split(".")[:depth]) for i in real_ids if i.count(".") >= depth - 1})
    prefix = ""
    while True:
        level = {
            i[len(prefix) :].split(".")[0]
            for i in real_ids
            if i.startswith(prefix) and len(i) > len(prefix)
        }
        if len(level) != 1:
            break
        prefix = f"{prefix}{next(iter(level))}."
    return sorted({f"{prefix}{seg}" for seg in level})


def propose_ontology(
    db_path: str,
    margin: float = 0.03,
    min_nodes: int = 10,
    depth: int | None = None,
) -> str:
    """Return a ready-to-edit patterns.yaml as text (implemented in Task 2)."""
    raise NotImplementedError
```

NOTE the auto-descent contract precisely: at each level collect the distinct next segments of ids under the current prefix; descend only while exactly one segment exists; candidates are `prefix + segment` for the first multi-segment level. A node id EQUAL to the prefix level (no further segments) is simply not counted at that level.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/unit/test_ontology_init.py -q -k discover`
Expected: 5 passed.

- [ ] **Step 5: Full suite + gates**

Run: `uv run pytest tests/unit/ -q && make type-check && make lint`

- [ ] **Step 6: Commit**

```bash
git add src/cgis/query/ontology_init.py tests/unit/test_ontology_init.py
git commit -m "feat(ontology-init): domain discovery with auto-descent (#174 task 1)"
```

---

### Task 2: `propose_ontology` — fitting + yaml assembly (TDD)

**Files:**
- Modify: `src/cgis/query/ontology_init.py` (replace the stub; add `_DEFAULT_ONTOLOGY_HEADER` and helpers)
- Test: `tests/unit/test_ontology_init.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_ontology_init.py`:

```python
# ---------------------------------------------------------------------------
# propose_ontology
# ---------------------------------------------------------------------------

import yaml

from cgis.query.drift import DriftScorer
from cgis.query.drift_service import analyze_drift


def _chain_domain_nodes(prefix: str, count: int) -> tuple[list[Node], list[Edge]]:
    """`count` functions in one domain wired as a CALLS chain f0→f1→…→fn."""
    nodes = [_node(f"{prefix}.f{i}", file_path=f"{prefix.replace('.', '/')}.py") for i in range(count)]
    edges = [
        Edge(
            id=f"e{prefix}{i}",
            source=f"{prefix}.f{i}",
            target=f"{prefix}.f{i + 1}",
            type=EdgeType.CALLS,
        )
        for i in range(count - 1)
    ]
    return nodes, edges


@pytest.fixture
def two_domain_db(tmp_path: Path) -> str:
    """Graph with one big chain domain (>= min_nodes) and one tiny domain."""
    db = str(tmp_path / "g.db")
    big_nodes, big_edges = _chain_domain_nodes("app.pipeline", 12)
    tiny_nodes, tiny_edges = _chain_domain_nodes("app.tiny", 3)
    with SQLiteStore(db) as store:
        store.save_graph(big_nodes + tiny_nodes, big_edges + tiny_edges)
    return db


def test_propose_missing_db_raises(tmp_path: Path) -> None:
    """Nonexistent db must raise BEFORE SQLite silently creates a file (spec §2.1.1)."""
    missing = tmp_path / "nope.db"
    with pytest.raises(FileNotFoundError):
        propose_ontology(str(missing))
    assert not missing.exists()


def test_propose_emits_parseable_yaml_with_domains(two_domain_db: str) -> None:
    """Output parses as yaml and contains both discovered domains."""
    text = propose_ontology(two_domain_db, min_nodes=10)
    data = yaml.safe_load(text)
    names = {d["fqn_prefix"] for d in data["project_domains"]}
    assert names == {"app.pipeline", "app.tiny"}
    assert "patterns" in data and "profiles" in data and "hygiene" in data


def test_propose_labels_big_domain_and_hygienes_tiny(two_domain_db: str) -> None:
    """>= min_nodes chain gets an expected_pattern; tiny domain is hygiene-only."""
    text = propose_ontology(two_domain_db, min_nodes=10)
    data = yaml.safe_load(text)
    by_prefix = {d["fqn_prefix"]: d for d in data["project_domains"]}
    assert "expected_pattern" in by_prefix["app.pipeline"]
    assert "expected_pattern" not in by_prefix["app.tiny"]
    assert "# below min_nodes" in text


def test_propose_tolerance_is_measured_plus_margin(two_domain_db: str) -> None:
    """Round-trip: every proposed domain scores within its proposed tolerance."""
    text = propose_ontology(two_domain_db, min_nodes=10, margin=0.03)
    out = Path(two_domain_db).parent / "proposed.yaml"
    out.write_text(text)
    analysis = analyze_drift(two_domain_db, str(out))
    assert analysis.any_critical is False
    for r in analysis.reports:
        assert r.status != "empty"
        assert r.drift_score <= r.tolerance + 1e-9


def test_propose_no_fit_goes_hygiene_only(tmp_path: Path) -> None:
    """A census far from every ideal → no forced label, '# no template fits' comment."""
    # A dense bidirectional clique diverges from all five chain/star/dag ideals.
    db = str(tmp_path / "clique.db")
    names = [f"app.blob.n{i}" for i in range(12)]
    nodes = [_node(n, file_path="app/blob.py") for n in names]
    edges = [
        Edge(id=f"c{i}-{j}", source=a, target=b, type=EdgeType.CALLS)
        for i, a in enumerate(names)
        for j, b in enumerate(names)
        if i != j
    ]
    with SQLiteStore(db) as store:
        store.save_graph(nodes, edges)
    text = propose_ontology(db, min_nodes=10)
    data = yaml.safe_load(text)
    blob = next(d for d in data["project_domains"] if d["fqn_prefix"] == "app.blob")
    if "expected_pattern" not in blob:
        assert "# no template fits" in text
    # Either way the round-trip must hold:
    out = tmp_path / "p.yaml"
    out.write_text(text)
    assert analyze_drift(db, str(out)).any_critical is False


def test_propose_deterministic(two_domain_db: str) -> None:
    """Two runs over the same graph are byte-identical."""
    assert propose_ontology(two_domain_db) == propose_ontology(two_domain_db)


def test_header_templates_match_repo_ontology() -> None:
    """Staleness pin: bundled patterns block == docs/ontology/patterns.yaml's (parsed)."""
    from cgis.query.ontology_init import _DEFAULT_ONTOLOGY_HEADER

    bundled = yaml.safe_load(_DEFAULT_ONTOLOGY_HEADER)
    repo = yaml.safe_load(Path("docs/ontology/patterns.yaml").read_text())
    assert bundled["patterns"] == repo["patterns"]
    assert bundled["profiles"] == repo["profiles"]
    assert bundled["hygiene"] == repo["hygiene"]
```

NOTE on the no-fit test: a 12-node full clique has cycle_ratio 1.0 and a 030C/111-heavy census — if empirically the best fit lands ≤ 0.5, the test's `if` branch tolerates it (the round-trip assertion is the invariant); do NOT weaken the threshold to force the comment.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_ontology_init.py -q -k propose`
Expected: FAIL — NotImplementedError / missing `_DEFAULT_ONTOLOGY_HEADER`.

- [ ] **Step 3: Implement**

In `src/cgis/query/ontology_init.py` replace the stub. Structure:

a) `_DEFAULT_ONTOLOGY_HEADER: str` — module constant containing, VERBATIM, the `version:`, `profiles:`, `patterns:` and `hygiene:` blocks copied from `docs/ontology/patterns.yaml` at current main (READ that file and copy; STRIP the `project_domains:`/`project_level:` sections and any cgis-specific comments like the #178 NOTE). Prepend a generated-file banner comment:

```yaml
# Generated by `cgis init-ontology` — a measured baseline, not a verdict.
# Tolerances are measured + margin: ratchet them DOWN over time.
# Docs: docs/specs/2026-06-12-init-ontology-design.md
```

b) Constants and small helpers (module-level, all with docstrings):

```python
_NO_FIT_THRESHOLD = 0.5
_TS_EXTENSIONS = frozenset({".ts", ".tsx", ".js", ".jsx", ".vue"})


def _detect_profile(domain_nodes: list[Node]) -> tuple[str, bool]:
    """(profile, guessed): majority extension; ties/unknown → ("python", True)."""


def _fit_templates(
    fp: PatternFingerprint, profile: str, scorer: DriftScorer
) -> list[tuple[str, float]]:
    """[(template_name, fit_score)] sorted by (score, name) — DriftScorer is the fitter.

    Each fit = scorer.score(fp, DomainConfig(name=..., fqn_prefix=fp.domain,
    expected_pattern=t, profile=profile, drift_tolerance=1.0)).drift_score.
    Template names come from the bundled header's patterns block, in
    declaration order.
    """


def _ceil2(x: float) -> float:
    """Ceil to 2 decimals (tolerance rounding)."""
    return math.ceil(x * 100) / 100
```

c) `propose_ontology` flow (spec §2.1):

```python
def propose_ontology(
    db_path: str,
    margin: float = 0.03,
    min_nodes: int = 10,
    depth: int | None = None,
) -> str:
    """Return a ready-to-edit patterns.yaml proposed from the measured graph.

    Raises:
        FileNotFoundError: if ``db_path`` does not exist (SQLite would
            otherwise silently create an empty database).
    """
    if not Path(db_path).is_file():
        msg = f"Graph database not found: {db_path}. Run `cgis ingest` first."
        raise FileNotFoundError(msg)

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as tf:
        tf.write(_DEFAULT_ONTOLOGY_HEADER)
        header_path = tf.name
    try:
        scorer = DriftScorer(header_path)
        with SQLiteStore(db_path) as store:
            all_nodes = store.get_all_nodes()
            extractor = FingerprintExtractor(store)
            entries = [
                _domain_entry(prefix, all_nodes, extractor, scorer, margin, min_nodes)
                for prefix in discover_domains(all_nodes, depth=depth)
            ]
    finally:
        Path(header_path).unlink(missing_ok=True)

    return _assemble_yaml(entries)
```

d) `_domain_entry(prefix, all_nodes, extractor, scorer, margin, min_nodes) -> str` — returns the yaml lines for one domain (string assembly keeps comments trivial; do NOT round-trip through yaml.dump, which drops comments). Decision ladder per spec §2.1.5:

```python
def _domain_entry(...) -> str:
    """Yaml block for one domain: label, hygiene-only reason, tolerance, comments."""
    fp = extractor.extract(prefix)
    domain_nodes = [n for n in all_nodes if _in_prefix(n.id, prefix) and n.file_path != VIRTUAL_FILE_PATH]
    profile, guessed = _detect_profile(domain_nodes)
    fits = _fit_templates(fp, profile, scorer)
    best_name, best = fits[0]
    runner_name, runner = fits[1]
    lines = [f'  - name: "{prefix.rsplit(".", maxsplit=1)[-1]}"', f'    fqn_prefix: "{prefix}"']
    if fp.node_count < min_nodes:
        reason = f"# below min_nodes ({fp.node_count} nodes) — census too small to label"
    elif fp.edge_count == 0:
        reason = "# no intra-domain edges — nothing to fit"
    elif best > _NO_FIT_THRESHOLD:
        reason = f"# no template fits (best: {best_name} at {best:.2f})"
    else:
        reason = None
        lines.append(f"    expected_pattern: {best_name}")
    lines.append(f"    profile: {profile}" + ("  # profile guessed" if guessed else ""))
    tolerance = _ceil2(_hygiene_or_fit_score(...) + margin)
    comment = (
        f"# measured ≈ {best:.2f} via init-ontology (runner-up: {runner_name} at {runner:.2f}) — ratchet down over time"
        if reason is None
        else reason
    )
    lines.append(f"    drift_tolerance: {tolerance:.2f} {comment}")
    return "\n".join(lines)
```

PRECISION POINT — the tolerance source differs by branch (spec §2.1.5): for a LABELED domain it is the best-fit score; for a hygiene-only domain it is the domain's v1 hygiene score — compute it as `scorer.score(fp, DomainConfig(name=..., fqn_prefix=prefix, expected_pattern=None, profile=None, drift_tolerance=1.0)).drift_score`. Implement `_hygiene_or_fit_score` accordingly (or inline both calls). The duplicate names `name:` derived from the last segment may collide across prefixes — disambiguate by using the FULL prefix as `name` when the short name is not unique among entries.

e) `_assemble_yaml(entries)` — header + `\nproject_domains:\n` + entries + the commented-out `project_level` skeleton:

```yaml
# project_level:  # quotient binding is an architectural decision — uncomment and tune:
#   - name: "whole"
#     fqn_prefix: "<collapse-prefix>"
#     expected_pattern: pipeline_stage
#     profile: python
#     drift_tolerance: 0.50
#     enforce: false
```

f) Imports: `math`, `tempfile`, `Path`, `DomainConfig`, `DriftScorer`, `FingerprintExtractor`, `PatternFingerprint`, `SQLiteStore`, models. Mind import direction: `query/ontology_init.py` importing from `query/` siblings and `storage/` is fine (matches drift_service).

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/unit/test_ontology_init.py -q`
Expected: all pass (12 tests).

- [ ] **Step 5: Full suite + gates**

Run: `uv run pytest tests/unit/ -q && make type-check && make lint && make doc-coverage`

- [ ] **Step 6: Commit**

```bash
git add src/cgis/query/ontology_init.py tests/unit/test_ontology_init.py
git commit -m "feat(ontology-init): template fitting + yaml proposal via DriftScorer (#174 task 2)"
```

---

### Task 3: CLI command (append-only) (TDD)

**Files:**
- Modify: `src/cgis/cli.py` (APPEND a new command block only — Lane A contract)
- Test: `tests/unit/test_cli.py` (append; read its CliRunner idiom first)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_cli.py` (reuse its runner/fixture idiom; build a small db via SQLiteStore like neighboring drift tests):

```python
def test_init_ontology_writes_file_and_summary(tmp_path: Path) -> None:
    """Happy path: writes the yaml, prints a summary, exit 0."""
    db = _make_chain_db(tmp_path)  # mirror the file's existing db-building helper style
    out = tmp_path / "patterns.yaml"
    result = runner.invoke(app, ["init-ontology", "--db", db, "--out", str(out)])
    assert result.exit_code == 0
    assert out.exists()
    assert "project_domains" in out.read_text()


def test_init_ontology_refuses_overwrite_without_force(tmp_path: Path) -> None:
    """Existing --out without --force → exit 1, file untouched."""
    db = _make_chain_db(tmp_path)
    out = tmp_path / "patterns.yaml"
    out.write_text("hand-tuned: true\n")
    result = runner.invoke(app, ["init-ontology", "--db", db, "--out", str(out)])
    assert result.exit_code == 1
    assert out.read_text() == "hand-tuned: true\n"
    forced = runner.invoke(app, ["init-ontology", "--db", db, "--out", str(out), "--force"])
    assert forced.exit_code == 0
    assert "project_domains" in out.read_text()


def test_init_ontology_missing_db_exits_1(tmp_path: Path) -> None:
    """Missing db → red message, exit 1, no db file created."""
    missing = tmp_path / "none.db"
    result = runner.invoke(app, ["init-ontology", "--db", str(missing), "--out", str(tmp_path / "p.yaml")])
    assert result.exit_code == 1
    assert not missing.exists()
```

(`_make_chain_db`: if no suitable helper exists in the file, add one local helper building a 12-node CALLS chain via SQLiteStore — mirror the `_chain_domain_nodes` shape from test_ontology_init.py.)

- [ ] **Step 2: Implement.** APPEND to `src/cgis/cli.py` (import `propose_ontology` in the import block):

```python
@app.command(name="init-ontology")
def init_ontology(
    db: str = typer.Option(_DEFAULT_DB, "--db", "-d", help=_DEFAULT_DB_HELP),
    out: str = typer.Option(
        "patterns.yaml", "--out", "-o", help="Where to write the proposed ontology."
    ),
    margin: float = typer.Option(
        0.03, "--margin", min=0.0, max=0.5, help="Tolerance headroom above the measured score."
    ),
    min_nodes: int = typer.Option(
        10, "--min-nodes", min=1, help="Domains smaller than this stay hygiene-only."
    ),
    depth: int | None = typer.Option(
        None, "--depth", help="Fixed FQN segment depth for domain discovery (default: auto)."
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing --out file."),
) -> None:
    """Propose a starter patterns.yaml from the measured graph (measure-then-label)."""
    if Path(out).exists() and not force:
        console.print(f"[bold red]❌ {out} already exists[/bold red] — use --force to overwrite.")
        raise typer.Exit(code=1)
    try:
        text = propose_ontology(db, margin=margin, min_nodes=min_nodes, depth=depth)
    except FileNotFoundError as e:
        console.print(f"[bold red]❌ {e}[/bold red]")
        raise typer.Exit(code=1) from e
    Path(out).write_text(text)
    console.print(f"[bold green]✅ Proposed ontology written to {out}[/bold green]")
    console.print(f"Next: [cyan]cgis drift --db {db} --patterns {out}[/cyan]")
```

(Plus a compact Rich summary table — domain / nodes / proposal / tolerance — iterate the parsed yaml or return structured entries; keep it simple: parse `yaml.safe_load(text)["project_domains"]` and render name, fqn_prefix, expected_pattern or "(hygiene)", drift_tolerance. Mirror `_render_drift_table` style with a small private `_render_init_summary(text)` helper next to the command.)

- [ ] **Step 3: Run** `uv run pytest tests/unit/test_cli.py -q && uv run pytest tests/unit/ -q && make type-check && make lint`

- [ ] **Step 4: Commit**

```bash
git add src/cgis/cli.py tests/unit/test_cli.py
git commit -m "feat(cli): init-ontology command with overwrite protection (#174 task 3)"
```

---

### Task 4: MCP tool (append-only, read-only) (TDD)

**Files:**
- Modify: `src/cgis/api/mcp_server.py` (APPEND only)
- Test: `tests/unit/test_mcp_server.py` (append; mirror cgis_drift test idiom)

- [ ] **Step 1: Failing tests** (mirror the file's existing fixture style):

```python
def test_cgis_init_ontology_returns_yaml_text(tmp_path: Path) -> None:
    """Returns parseable yaml with project_domains; writes NO files."""
    db = _make_chain_db(tmp_path)  # mirror existing db helper in this file
    before = set(tmp_path.iterdir())
    result = cgis_init_ontology(db_path=db)
    assert "project_domains:" in result
    assert yaml.safe_load(result)["project_domains"]
    assert set(tmp_path.iterdir()) == before  # read-only surface


def test_cgis_init_ontology_missing_db_message(tmp_path: Path) -> None:
    """Missing db → the ❌ message string, no exception, no db created."""
    missing = tmp_path / "none.db"
    result = cgis_init_ontology(db_path=str(missing))
    assert result.startswith("❌ Database not found")
    assert not missing.exists()
```

- [ ] **Step 2: Implement.** APPEND to `mcp_server.py`:

```python
@mcp.tool()
def cgis_init_ontology(
    db_path: str = _DEFAULT_DB,
    margin: float = 0.03,
    min_nodes: int = 10,
    depth: int | None = None,
) -> str:
    """Propose a starter patterns.yaml from the measured graph (read-only).

    Returns the YAML text — save it yourself (e.g. to patterns.yaml), review
    the proposed labels, then run ``cgis_drift`` with it. Tolerances are the
    measured scores plus ``margin``: a baseline to ratchet down, not a verdict.
    """
    if not Path(db_path).exists():
        return f"❌ Database not found at: {db_path}. Run cgis_ingest first."
    try:
        return propose_ontology(db_path, margin=margin, min_nodes=min_nodes, depth=depth)
    except Exception as e:  # mirror cgis_drift's error-translation idiom
        return f"❌ Error proposing ontology: {e}"
```

(Match the decorator/registration idiom actually used in the file — read it first; `@mcp.tool()` shown as the expected shape.)

- [ ] **Step 3: Run** `uv run pytest tests/unit/test_mcp_server.py -q && uv run pytest tests/unit/ -q && make type-check && make lint`

- [ ] **Step 4: Commit**

```bash
git add src/cgis/api/mcp_server.py tests/unit/test_mcp_server.py
git commit -m "feat(mcp): cgis_init_ontology read-only proposal tool (#174 task 4)"
```

---

### Task 5: Self-graph round-trip + full gates

**Files:**
- Create: `tests/self_parsing/test_init_ontology_roundtrip.py`

- [ ] **Step 1: Self-parse round-trip test** (reuse the `graph_data`/store fixtures from `tests/self_parsing/conftest.py` — read it first for the canonical ingest fixture; if the fixture exposes a db path, use it, otherwise ingest into tmp_path the way conftest does):

```python
"""Round-trip acceptance: propose on the cgis self-graph, drift passes green (#174)."""


def test_self_graph_proposal_round_trips(...) -> None:
    """propose_ontology(self graph) → analyze_drift → green by construction."""
    text = propose_ontology(db_path)
    out = tmp_path / "proposed.yaml"
    out.write_text(text)
    analysis = analyze_drift(db_path, str(out))
    assert analysis.any_critical is False
    for r in analysis.reports:
        assert r.status != "empty"
        assert r.drift_score <= r.tolerance + 1e-9
    # sanity: the real packages were discovered
    prefixes = {r.fqn_prefix for r in analysis.reports}
    assert any("query" in p for p in prefixes)
    assert any("extractors" in p for p in prefixes)
```

- [ ] **Step 2: Run** `uv run pytest tests/self_parsing/ -q` — ALL pass incl. existing ratchets (we changed nothing they measure, but verify).

- [ ] **Step 3: Live smoke (capture for the PR description)**

```bash
uv run cgis ingest src --source-root src -o /tmp/cgis174.db
uv run cgis init-ontology --db /tmp/cgis174.db --out /tmp/proposed.yaml
uv run cgis drift --db /tmp/cgis174.db --patterns /tmp/proposed.yaml; echo "exit=$?"
```

Expected: summary table; then a drift table with NO empty rows and `exit=0`. Capture both outputs.

- [ ] **Step 4: Full gates**

```bash
make format && make lint && make type-check && make pytest && make doc-coverage
```

- [ ] **Step 5: Acceptance checks — report exact outputs**

```bash
git diff main -- docs/ontology/patterns.yaml      # MUST be empty
git diff main -- src/cgis/cli.py | grep -c "^-"   # expect ~1 (--- header only; append-only)
```

- [ ] **Step 6: Commit**

```bash
git add tests/self_parsing/test_init_ontology_roundtrip.py
git commit -m "test(self): init-ontology round-trip on the cgis self-graph (#174 task 5)"
```

---

## Final checklist (controller, before PR)

- [ ] `docs/ontology/patterns.yaml` untouched; cli.py/mcp_server.py diffs are pure appends (+ import lines)
- [ ] Round-trip green on fixtures AND self-graph; live smoke outputs captured
- [ ] Staleness pin test guards the bundled header
- [ ] PR body: `Closes #174`, spec link, live smoke outputs, note the read-only MCP decision
