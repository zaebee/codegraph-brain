# Language Packs — Step 4: Resolution Rules — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every language branch of the resolver becomes a rule that a pack contributes. After this step `cgis.resolver` names no language, every stored graph stays byte-identical, and the `engine-packs` allowlist is empty.

**Architecture:**
- `cgis.core.resolution` defines the contract:
  - `ResolutionView`: what a rule may read. `SymbolIndex` satisfies it structurally, so packs never import the resolver.
  - `ResolutionRules`: type normalizers, positioned steps, a fallback, and an import reconciler.
- `LanguagePack.resolution()` returns a pack's rules. The registry answers `rules_for(source_file)` and `type_normalizers()`.
- The engine keeps the generic chain and calls the source file's rules at fixed points:
  - local type, with the normalizers applied;
  - import map;
  - `after:import_map` steps;
  - global symbols;
  - fallback.
- **TypeScript contributes:**
  - `reconcile_import` (tsconfig aliases, workspace packages, `.d` declarations);
  - the `after:import_map` step `_package_call`;
  - the JS-builtins fallback at confidence 0.8.

  Its resolution facts (`workspace_packages`, `path_aliases`, `file_bare_bindings`, `file_shadowed_globals`) move into `TypeScriptIndexFacts`.
- **Python contributes:**
  - `reconcile_import` (layout-prefix stripping);
  - the `Annotated`-alias type normalizer.

  Its star-import expansion moves into `PythonIndexFacts.reexports`.
- `cross_file_metadata` moves into the manifests. The pipeline unions them.

**Tech Stack:** Python 3.12, tree-sitter, SQLite, pytest, ruff, mypy strict, interrogate.

**Spec:** `docs/specs/2026-10-14-language-packs-design.md` (rev 3), sections "Resolution", "Incremental ingest" and Migration row 4. Outline: `docs/plans/2026-10-14-language-packs.md` "Step 4". Steps 1–3: #597, #599, #600 and the spec's implementation notes.

## Global Constraints

- `make format && make lint && make type-check && make pytest && make doc-coverage` passes before every commit. Gate commands with `&&`, never with `;`, a bare newline after a heredoc, or a pipe on the gating command.
- mypy strict on `src/`. Docstring coverage ≥ 90%. Sonar cognitive complexity ≤ 15 per function.
- **Behaviour unchanged:**
  - `tests/fixtures/graph_identity/snapshot.json` is not modified.
  - The main-vs-branch `graph_identity diff` reads `identical` on all three trees: cgis `src` @715cd9a, owner-api @8de2245, and `ui/`.
- The resolver's behaviour tests keep every assertion. They may change only where they reach into a moved private (`_through_annotated_alias`, `js_builtin_target`, `SymbolIndex.workspace_packages`, …). They construct `ResolverEngine(..., workspace_packages=..., tsconfig_paths=..., dependency_roots=..., npm_dependencies=...)`, and that signature does not change.
- **One language branch per commit** (spec, step 4). After each, the snapshot test and the full suite pass.
- **Ingest time:** the owner-api median of three, branch vs main, must stay within +5% (spec acceptance). Over budget: **stop and report**.
- **Drift:** `resolution` stands at 0.386 against a lock of 0.40, and unresolved at 0.442 against 0.45. The `symbols.py` DISPATCHES comment records that a module split once pushed it past tolerance. Measure after Tasks 4, 5 and 6. Over either bound: **stop and report**. Never raise a tolerance.
- `SymbolResolver` is at the god-object threshold, so new helpers are module-level, never methods on it.
- Test-count floor: raise `.github/test-count-baseline` (now 3095) if the suite passes 3245.
- **Disk:** the root disk is ~97% full. Keep scratch databases under the scratchpad (`/tmp`, tmpfs) and delete them after each measurement.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. The PR body ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- Worktree `/home/zaebee/projects/ownima/cgb-step4`, branch `feat/language-packs-step4`.

## Rulings (where this plan departs from the spec's or outline's text)

- **D1 — `layout_prefixes`, `reexports` and `suffix_map` stay on `SymbolIndex`, filled from pack facts.**
  - Step 3's C3 said these resolution facts would leave `SymbolIndex`, but `map_to_node_fqn`/`resolve_layout`/`follow_reexport` read them for every language today, and step 3's C4 kept those generic.
  - What moves to the packs is the *computation*: Python's star expansion becomes `PythonIndexFacts.reexports`.
  - The TS-only facts do leave, because only TS rules read them: `workspace_packages`, `path_aliases`, `file_bare_bindings` and `file_shadowed_globals`.
- **D2 — the engine applies every pack's type normalizers, in manifest order, not only the source pack's.**
  - `receiver_type_fqn` has no source file.
  - The only normalizer, Python's `Annotated` alias, is a no-op for a type that is not a Python DI alias.
  - *Cost if wrong:* a language whose normalizer rewrites another language's type. That is not possible with today's one normalizer.
- **D3 — positions are validated.** `ResolutionRules.steps` accepts only the positions the engine runs (`after:import_map`). An unknown position raises at pack load, so a typo cannot silently drop a rule.
- **D4 — a rule receives a `ResolutionView` (a core protocol), never `SymbolIndex`.** This keeps `cgis.languages` → `cgis.resolver` edges at zero; the `engine-packs` gate already forbids the reverse direction.

## Review Focus

- **A TS file importing through a tsconfig alias, a workspace package and a `.d.ts`.** These resolve exactly as before. Covered by the existing `test_resolver_ts_workspaces.py` / `test_resolver_ts_declarations.py` / `test_tsconfig_paths.py` and the `ui/` identity diff.
- **A Python reference with no source file (`receiver_type_fqn`).** It still gets the `Annotated` normalizer (Task 6, `test_a_normalizer_applies_without_a_source`).
- **A call in a TS file to a global the file shadows (`const confirm = …`).** It stays unresolved, never `js_builtins.confirm` (existing `test_resolver_js_builtins.py`).
- **An unknown step position in a pack's rules** fails at load (Task 2, `test_an_unknown_step_position_is_refused`).
- **Incremental ingest.** The cross-file signature is identical to before for every node type (Task 1, `test_cross_file_metadata_is_the_old_table`).

---

### Task 1: `cross_file_metadata` per manifest

**Files:**
- Modify: `src/cgis/languages/__init__.py` (`Manifest.cross_file_metadata` and `cross_file_metadata()`)
- Modify: `src/cgis/pipeline.py:32-37` (replace `_CROSS_FILE_METADATA`)
- Test: `tests/unit/test_languages.py` (append)

**Interfaces:**
- Produces:
  - `Manifest.cross_file_metadata: Mapping[NodeType, tuple[str, ...]]`
  - `cgis.languages.cross_file_metadata() -> dict[NodeType, tuple[str, ...]]`

- [ ] **Step 1: Write the failing test.** Append to `tests/unit/test_languages.py`:

```python
from cgis.languages import cross_file_metadata


def test_cross_file_metadata_is_the_old_table() -> None:
    """The pipeline's incremental signature reads exactly what it read before step 4."""
    assert cross_file_metadata() == {
        NodeType.FILE: ("import_map", "reexports", "star_imports"),
        NodeType.CLASS: ("self_types",),
        NodeType.VARIABLE: ("annotated_type",),
    }


def test_cross_file_metadata_imports_no_pack() -> None:
    """Spec: declared in the manifest, so collecting it imports nothing."""
    script = (
        "import sys\n"
        "from cgis.languages import cross_file_metadata\n"
        "cross_file_metadata()\n"
        "print(sorted(m for m in sys.modules if m.startswith('cgis.languages.')))\n"
    )
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"
```

Move the import to the import block.

- [ ] **Step 2: Run it and watch it fail.**
  Run: `uv run pytest tests/unit/test_languages.py -q -k cross_file`
  Expected: `ImportError: cannot import name 'cross_file_metadata'`.

- [ ] **Step 3: Implement.**
  1. In `src/cgis/languages/__init__.py`, import `NodeType` from `cgis.core.models`, and `field` from `dataclasses`.
  2. Add a field to `Manifest`:

```python
    #: Per node type, the metadata keys other files resolve against; a change to
    #: one forces a rebuild of an incremental run. Declared here so collecting the
    #: union imports no pack (spec, Incremental ingest).
    cross_file_metadata: Mapping[NodeType, tuple[str, ...]] = field(default_factory=dict)
```

  3. Give the Python manifest the three entries from `pipeline._CROSS_FILE_METADATA`, verbatim, comment included. The TypeScript manifest declares none.
  4. Add:

```python
def cross_file_metadata() -> dict[NodeType, tuple[str, ...]]:
    """The union over every manifest, keys in manifest order; imports no pack."""
    merged: dict[NodeType, tuple[str, ...]] = {}
    for manifest in MANIFESTS:
        for node_type, keys in manifest.cross_file_metadata.items():
            merged[node_type] = merged.get(node_type, ()) + tuple(
                k for k in keys if k not in merged.get(node_type, ())
            )
    return merged
```

  5. In `src/cgis/pipeline.py`, delete `_CROSS_FILE_METADATA`. At its single use, write `cross_file_metadata().get(node.type, ())`. Bind `_CROSS_FILE_KEYS = cross_file_metadata()` once at module level, next to the old constant's place, and use `_CROSS_FILE_KEYS.get(...)`. Import from `cgis.languages`.

- [ ] **Step 4: Run.**
  Run: `uv run pytest tests/unit/test_languages.py tests/unit/test_pipeline.py tests/unit/test_pipeline_incremental_symbols.py -q`
  Expected: all pass.

- [ ] **Step 5: Gate and commit.** `refactor(languages): cross_file_metadata is declared per manifest`.

---

### Task 2: The resolution contract

**Files:**
- Create: `src/cgis/core/resolution.py`, `tests/unit/test_resolution_rules.py`
- Modify:
  - `src/cgis/core/extractor.py` (`LanguagePack.resolution`)
  - both pack `__init__.py` (return empty rules for now)
  - `src/cgis/languages/__init__.py` (`rules_for`, `type_normalizers`)
  - `src/cgis/resolver/indices.py` (rename `_strip_layout_prefixes` → `strip_layout_prefixes`, so `SymbolIndex` satisfies `ResolutionView`)

**Interfaces:**
- Produces:
  - **`cgis.core.resolution`:**
    - `ResolutionView` (Protocol): `nodes`, `facts`, `map_to_node_fqn(fqn) -> str | None`, `strip_layout_prefixes(fqn) -> str | None`, `is_variable_node(fqn) -> bool`
    - `TypeNormalizer = Callable[[str, ResolutionView], str]`
    - `CallStep = Callable[[str, str, ResolutionView], str | None]`, taking `(name, file_path, view)`
    - `Fallback = Callable[[str, str, ResolutionView], tuple[str, float] | None]`, taking `(name, file_path, view)`
    - `ImportReconciler = Callable[[str, str | None, ResolutionView], str | None]`, taking `(fqn, source_file, view)`
    - `AFTER_IMPORT_MAP = "after:import_map"`
    - `ResolutionRules` (frozen dataclass): `type_normalizers: tuple[TypeNormalizer, ...] = ()`, `steps: tuple[tuple[str, CallStep], ...] = ()`, `fallback: Fallback | None = None`, `reconcile_import: ImportReconciler | None = None`. It validates `steps` in `__post_init__`.
  - **`cgis.languages`:**
    - `rules_for(source_file: str | None) -> ResolutionRules | None`, meaning `classifying_pack(source_file)`'s rules, or None
    - `type_normalizers() -> tuple[TypeNormalizer, ...]`, every pack's, in manifest order

- [ ] **Step 1: Write the failing tests.** Create `tests/unit/test_resolution_rules.py`:

```python
"""The resolution contract between the engine and the language packs (step 4)."""

import pytest

from cgis.core.resolution import AFTER_IMPORT_MAP, ResolutionRules, ResolutionView
from cgis.languages import rules_for, type_normalizers
from cgis.resolver.indices import IndexBuilder


def _step(name: str, file_path: str, view: ResolutionView) -> str | None:
    return None


def test_an_unknown_step_position_is_refused() -> None:
    """A typo would otherwise drop the rule silently."""
    with pytest.raises(ValueError, match="after:imports"):
        ResolutionRules(steps=(("after:imports", _step),))


def test_the_known_position_is_accepted() -> None:
    assert ResolutionRules(steps=((AFTER_IMPORT_MAP, _step),)).steps[0][0] == AFTER_IMPORT_MAP


def test_the_index_is_a_resolution_view() -> None:
    """Rules read the index through the core protocol, never by importing the resolver."""
    view: ResolutionView = IndexBuilder().build([])
    assert view.strip_layout_prefixes("x.y") is None


def test_rules_follow_the_classifying_pack() -> None:
    """No source is Python's (the preserved quirk); a source no pack reads has none."""
    assert rules_for(None) is not None
    assert rules_for("a.ts") is not None
    assert rules_for("a.js") is None


def test_normalizers_come_in_manifest_order() -> None:
    assert isinstance(type_normalizers(), tuple)
```

- [ ] **Step 2: Run them and watch them fail.**
  Run: `uv run pytest tests/unit/test_resolution_rules.py -q`
  Expected: `ModuleNotFoundError: cgis.core.resolution`.

- [ ] **Step 3: The contract.** Create `src/cgis/core/resolution.py`:

```python
"""What a language pack contributes to resolution (spec: Resolution).

The engine runs a fixed chain — local type (with type normalizers), import map,
steps positioned `after:import_map`, global symbols, fallback — and asks the
source file's pack at each of those points. A rule reads the index through
`ResolutionView`, never the resolver itself, so `cgis.languages` stays clear of
`cgis.resolver` (language-packs step 4).
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from cgis.core.index import IndexFacts
from cgis.core.models import Node

#: The only position the engine runs pack steps at today: after the import map,
#: before the global symbol index (TS `_package_call`).
AFTER_IMPORT_MAP = "after:import_map"
_POSITIONS = frozenset({AFTER_IMPORT_MAP})


class ResolutionView(Protocol):
    """What a resolution rule may read; `SymbolIndex` satisfies it."""

    @property
    def nodes(self) -> Mapping[str, Node]:
        """Every node, by id."""
        ...

    @property
    def facts(self) -> Mapping[str, IndexFacts]:
        """Each pack's index facts, by pack name."""
        ...

    def map_to_node_fqn(self, imported_fqn: str) -> str | None:
        """An imported FQN reconciled to a node id, or None."""
        ...

    def strip_layout_prefixes(self, fqn: str) -> str | None:
        """The node `fqn` names once corroborated layout prefixes are dropped, or None."""
        ...

    def is_variable_node(self, fqn: str) -> bool:
        """Whether `fqn` is a VARIABLE node."""
        ...


TypeNormalizer = Callable[[str, ResolutionView], str]
CallStep = Callable[[str, str, ResolutionView], str | None]
Fallback = Callable[[str, str, ResolutionView], tuple[str, float] | None]
ImportReconciler = Callable[[str, str | None, ResolutionView], str | None]


@dataclass(frozen=True)
class ResolutionRules:
    """One pack's contribution to the resolver's chain."""

    #: Applied to a declared type before its methods are looked up (e.g. Annotated alias → T).
    type_normalizers: tuple[TypeNormalizer, ...] = ()
    #: (position, step): a call step run at that point of the chain.
    steps: tuple[tuple[str, CallStep], ...] = ()
    #: When nothing resolved a call: (target, confidence), or None.
    fallback: Fallback | None = None
    #: An import target with no node of that name: the node it means, or None.
    reconcile_import: ImportReconciler | None = None

    def __post_init__(self) -> None:
        """Refuse a step at a position the engine never runs."""
        unknown = sorted({position for position, _ in self.steps} - _POSITIONS)
        if unknown:
            msg = f"unknown resolution step position(s): {unknown}"
            raise ValueError(msg)
```

  - In `cgis.core.extractor.LanguagePack`, add `def resolution(self) -> ResolutionRules: ...`, with the docstring `"""This pack's resolution rules (spec: Resolution)."""`.
  - Implement it in both packs with `@override`, returning `ResolutionRules()` for now. Tasks 3–6 fill them in.

- [ ] **Step 4: The registry and the view.**
  1. In `src/cgis/resolver/indices.py`, rename `_strip_layout_prefixes` to `strip_layout_prefixes` (definition plus its two call sites) and drop "private" from its docstring if it says so.
  2. In `src/cgis/languages/__init__.py`:

```python
@cache
def _rules(name: str) -> ResolutionRules:
    return _load(name).resolution()


def rules_for(source_file: str | None) -> ResolutionRules | None:
    """The resolution rules of the pack that classifies `source_file`, or None."""
    pack = classifying_pack(source_file)
    return None if pack is None else _rules(pack.name)


def type_normalizers() -> tuple[TypeNormalizer, ...]:
    """Every pack's type normalizers, in manifest order (step 4, ruling D2)."""
    return tuple(n for m in MANIFESTS for n in _rules(m.name).type_normalizers)
```

- [ ] **Step 5: Run.**
  Run: `uv run pytest tests/unit/test_resolution_rules.py tests/unit/test_resolver_indices.py -q && uv run mypy src`
  Expected: all pass, and mypy accepts `IndexBuilder().build([])` as a `ResolutionView`.

- [ ] **Step 6: Gate and commit.** `feat(languages): the resolution contract (ResolutionView, ResolutionRules)`.

---

### Task 3: TypeScript import reconciliation (and Python's)

**Files:**
- Modify:
  - `src/cgis/languages/typescript/index.py` (facts gain `workspace_packages`, `path_aliases`)
  - new `src/cgis/languages/typescript/resolution.py`
  - TS pack `__init__.py`
  - new `src/cgis/languages/python/resolution.py`
  - Python pack `__init__.py`
  - `src/cgis/resolver/indices.py` (`resolve_import_target`; delete `PathAlias`, `_resolve_typescript_import`, `_workspace_candidates`, `_alias_candidates`, `_ordered_aliases`, `_DECLARATION_SUFFIX`, `_ALIAS_WILDCARD`, `_PYTHON_SUFFIXES`, `_TYPESCRIPT_SUFFIXES` and the `workspace_packages`/`path_aliases` fields; the builder's `state` gains `workspace_packages` and `tsconfig_paths`)
- Test: `tests/unit/test_typescript_index.py`, `tests/unit/test_resolution_rules.py` (append)

- [ ] **Step 1: Write the failing tests.** Append to `tests/unit/test_resolution_rules.py`:

```python
from cgis.core.models import Node, NodeType


def _module(fqn: str, path: str) -> Node:
    return Node(id=fqn, type=NodeType.FILE, name=path.rsplit("/", 1)[-1], file_path=path,
                start_line=1, end_line=1, language="typescript")  # fmt: skip


def test_typescript_reconciles_through_its_own_pack() -> None:
    """Aliases and workspace packages are the TS pack's facts; the index no longer carries them."""
    nodes = [_module("packages.lib.util", "packages/lib/util.ts")]
    index = IndexBuilder().build(nodes, workspace_packages={"@acme.lib": "packages.lib"})
    assert not hasattr(index, "workspace_packages")
    assert index.resolve_import_target("@acme.lib.util", "apps/web/a.ts") == "packages.lib.util"
    assert index.resolve_import_target("@acme.lib.util", "apps/web/a.py") is None


def test_python_reconciles_by_layout_prefixes() -> None:
    rules = rules_for("app/x.py")
    assert rules is not None and rules.reconcile_import is not None
```

  Also append to `tests/unit/test_typescript_index.py` a test that `index_facts(..., {"workspace_packages": {"a": "x", "a.b": "y"}, "npm_dependencies": []}).workspace_packages` lists `"a.b"` before `"a"` (longest first), and that `path_aliases` holds `_ordered_aliases` output for a `{"apps/web": {"@/*": ["apps.web.src.*"]}}` input.

- [ ] **Step 2: Run them and watch them fail.**
  Run: `uv run pytest tests/unit/test_resolution_rules.py tests/unit/test_typescript_index.py -q`
  Expected: `test_typescript_reconciles_through_its_own_pack` fails on `hasattr`; the facts tests fail on a missing attribute.

- [ ] **Step 3: Move the TS half.**
  1. Into `languages/typescript/index.py`, move verbatim, with docstrings, `PathAlias`, `_ordered_aliases` and `_ALIAS_WILDCARD`.
  2. Add to `TypeScriptIndexFacts`:
     - `workspace_packages: Mapping[str, str]` (longest name first, `MappingProxyType` of the sorted dict, exactly as the builder built it);
     - `path_aliases: Mapping[str, tuple[PathAlias, ...]]`.
  3. In `index_facts`, read `state.get("workspace_packages", {})` and `state.get("tsconfig_paths", {})`, building them exactly as `IndexBuilder.build` did.
  4. Create `languages/typescript/resolution.py`:
     - move `_resolve_typescript_import`, `_workspace_candidates`, `_alias_candidates` and `_DECLARATION_SUFFIX` verbatim;
     - add:

```python
def reconcile_import(fqn: str, source_file: str | None, view: ResolutionView) -> str | None:
    """A TS import's node through tsconfig aliases, then workspace packages (#504, #507, #508)."""
    facts = cast("TypeScriptIndexFacts", view.facts["typescript"])
    aliased = _alias_candidates(fqn, source_file or "", facts.path_aliases)
    return _resolve_typescript_import(fqn, view.nodes, facts.workspace_packages, aliased)


RULES = ResolutionRules(reconcile_import=reconcile_import)
```

     Check `_alias_candidates`' signature at `indices.py:368` before writing the call. Keep the argument order it has.
  5. In the TS pack, `resolution()` returns `resolution.RULES`.
  6. Create `languages/python/resolution.py` with

```python
def reconcile_import(fqn: str, source_file: str | None, view: ResolutionView) -> str | None:
    """A Python import's node once corroborated layout prefixes are stripped (#319, #494)."""
    return view.strip_layout_prefixes(fqn)


RULES = ResolutionRules(reconcile_import=reconcile_import)
```

     The Python pack returns it.

- [ ] **Step 4: The engine side.** In `SymbolIndex.resolve_import_target`, keep the docstring and replace the body after `if fqn in self.nodes: return fqn` with:

```python
        rules = rules_for(source_file)
        if rules is None or rules.reconcile_import is None:
            return None
        return rules.reconcile_import(fqn, source_file, self)
```

  - This preserves today's three branches: a TS suffix goes to the TS rule; a non-`.py` suffix goes to `None`; `.py` or no source goes to stripping (`rules_for(None)` is Python's).
  - In `IndexBuilder.build`, add `"workspace_packages": dict(workspace_packages or {})` and `"tsconfig_paths": dict(tsconfig_paths or {})` to `state`, and delete the two fields and the deleted helpers listed under **Files**.

- [ ] **Step 5: Run.**
  Run: `uv run pytest tests/unit/test_resolution_rules.py tests/unit/test_typescript_index.py tests/unit/test_resolver_ts_workspaces.py tests/unit/test_resolver_ts_declarations.py tests/unit/test_tsconfig_paths.py tests/unit/test_configs_above_root.py tests/unit/test_resolver_indices.py tests/unit/test_graph_identity.py -q`
  Expected: all pass.

- [ ] **Step 6: Gate and commit.** `refactor(languages): import reconciliation is each pack's rule`.

---

### Task 4: TypeScript `_package_call` becomes the TS pack's positioned step

**Files:**
- Modify:
  - `src/cgis/languages/typescript/index.py` (facts gain `file_bare_bindings`)
  - `src/cgis/languages/typescript/resolution.py` (the step)
  - `src/cgis/resolver/symbols.py` (run positioned steps; delete `_package_call` and the TS import)
  - `src/cgis/resolver/indices.py` (delete the `file_bare_bindings` field and its accumulation in `_index_metadata`)
  - `tests/self_parsing/forbidden_edges_allowlist.json` (`engine-packs` → `{}`)

- [ ] **Step 1: Write the failing test.** Append to `tests/unit/test_resolution_rules.py`:

```python
from cgis.core.resolution import AFTER_IMPORT_MAP


def test_package_call_is_the_typescript_packs_step() -> None:
    rules = rules_for("app/x.ts")
    assert rules is not None
    assert [position for position, _ in rules.steps] == [AFTER_IMPORT_MAP]
```

- [ ] **Step 2: Run it and watch it fail.**
  Run: `uv run pytest tests/unit/test_resolution_rules.py -q -k package_call`
  Expected: FAIL. The rules have no steps yet.

- [ ] **Step 3: Move the rule.**
  1. Add `file_bare_bindings: Mapping[str, dict[str, str]]` to `TypeScriptIndexFacts`. Build it in `index_facts` under the same condition as `file_bare_imports`, keyed by normalized path: `dict(n.metadata.get("bare_bindings") or {})` when `bare_imports` is truthy.
  2. In `languages/typescript/resolution.py`, move `_package_call`'s body from `symbols.py`, with its docstring:

```python
def package_call(name: str, file_path: str, view: ResolutionView) -> str | None:
    """<symbols._package_call's docstring, moved verbatim>"""
    facts = cast("TypeScriptIndexFacts", view.facts["typescript"])
    bindings = facts.file_bare_bindings.get(file_path)
    # ... the rest of the old body, with `index.` reads replaced by `facts.` ...
    if package_namespace(candidate, file_path, facts) is None:
        return None
    return candidate
```

     Then `RULES = ResolutionRules(steps=((AFTER_IMPORT_MAP, package_call),), reconcile_import=reconcile_import)`.
  3. In `symbols.py`, add a module-level helper (not a method: `SymbolResolver` is at the god-object threshold):

```python
def _positioned(index: SymbolIndex, position: str, name: str, file_path: str) -> str | None:
    """The first hit among the source pack's steps at `position` (spec: Resolution)."""
    rules = rules_for(file_path)
    if rules is None:
        return None
    for step_position, step in rules.steps:
        if step_position == position:
            found = step(name, file_path, index)
            if found:
                return found
    return None
```

  4. In `resolve_global_call`, replace `or _package_call(self.index, name, file_path)` with `or _positioned(self.index, AFTER_IMPORT_MAP, name, file_path)`.
  5. Delete `_package_call` and the `cgis.languages.typescript.index` import. Import `rules_for` from `cgis.languages` (the exempt registry) and `AFTER_IMPORT_MAP` from `cgis.core.resolution`.
  6. Set `"engine-packs": {}` in the allowlist.

- [ ] **Step 4: Run.**
  Run: `uv run pytest tests/unit/test_resolution_rules.py tests/unit/test_resolver.py tests/unit/test_resolver_language_scope.py tests/self_parsing/test_architecture.py tests/unit/test_graph_identity.py -q`
  Expected: all pass, including `test_forbidden_edges_match_the_allowlist_exactly` with `engine-packs` empty.

- [ ] **Step 5: Measure `resolution` drift** with the commands from step 3's plan, Task 4 Step 1. Stop if it exceeds the bounds. Record the result in the ledger.

- [ ] **Step 6: Gate and commit.** `refactor(languages): _package_call is the TypeScript pack's after:import_map step`.

---

### Task 5: The JS-builtins fallback becomes the TS pack's

**Files:**
- Move: `src/cgis/resolver/js_builtins.py` → `src/cgis/languages/typescript/builtins.py` (with `git mv`; drop `is_js_source`, since the pack dispatch replaces it)
- Modify:
  - `src/cgis/languages/typescript/index.py` (facts gain `file_shadowed_globals`)
  - `src/cgis/languages/typescript/resolution.py` (fallback)
  - `src/cgis/resolver/engine.py` (call `rules_for(file_path).fallback`)
  - `src/cgis/resolver/indices.py` (delete the `file_shadowed_globals` field and its build)
  - importers of `js_builtins` in tests

- [ ] **Step 1: Write the failing test.** Append:

```python
def test_js_builtins_are_the_typescript_packs_fallback() -> None:
    """Spec: the fallback carries its confidence — 0.8 for a JS runtime global, not 1.0."""
    ts, py = rules_for("app/x.ts"), rules_for("app/x.py")
    assert ts is not None and ts.fallback is not None
    assert py is not None and py.fallback is None
    view = IndexBuilder().build([])
    assert ts.fallback("Math.max", "app/x.ts", view) == ("js_builtins.Math.max", 0.8)
```

- [ ] **Step 2: Run it and watch it fail.**
  Run: `uv run pytest tests/unit/test_resolution_rules.py -q -k fallback`
  Expected: FAIL, because `fallback is None`.

- [ ] **Step 3: Move it.**
  1. Run `git mv` on the module. Keep `js_builtin_target`, with the `is_js_source` guard removed because only the TS pack calls it now. Keep its docstring and add `(Only the TypeScript pack calls this, so the source is already JS.)`.
  2. Add `file_shadowed_globals: Mapping[str, frozenset[str]]` to the TS facts. Build it exactly as `IndexBuilder.build` did: FILE nodes whose `metadata["shadowed_globals"]` is truthy, keyed by normalized path.
  3. In `resolution.py`:

```python
_FALLBACK_CONFIDENCE = 0.8


def fallback(name: str, file_path: str, view: ResolutionView) -> tuple[str, float] | None:
    """A call on a JS runtime global the file does not shadow: `js_builtins.<name>` at 0.8 (#111)."""
    facts = cast("TypeScriptIndexFacts", view.facts["typescript"])
    target = js_builtin_target(name, file_path, facts.file_shadowed_globals.get(file_path, frozenset()))
    return None if target is None else (target, _FALLBACK_CONFIDENCE)
```

     Add `fallback=fallback` to `RULES`.
  4. In `engine._resolved_call_edge`, replace the shadowed or `js_builtin_target` block with:

```python
            file_path = self._index.normalized_file_path(edge.source, edge.file_path)
            rules = rules_for(file_path) if file_path else None
            hit = rules.fallback(raw_name, file_path, self._index) if rules and rules.fallback else None
            if hit is not None:
                return edge.model_copy(update={"target": hit[0], "confidence": hit[1]})
```

     Today a `None` file path rewrote nothing (`is_js_source(None)` is False), and the `if file_path` guard keeps that.
  5. Re-point test imports with `git grep -n "resolver.js_builtins\|is_js_source" -- tests src`.
  6. `JS_BUILTINS_ROOT` and `JS_GLOBALS` stay in `cgis.core.js_globals`.

- [ ] **Step 4: Run.**
  Run: `uv run pytest tests/unit/test_resolver_js_builtins.py tests/self_parsing/test_self_parse_ts.py tests/unit/test_resolution_rules.py tests/unit/test_graph_identity.py -q`
  Expected: all pass.

- [ ] **Step 5: Measure drift** as in Task 4 Step 5.

- [ ] **Step 6: Gate and commit.** `refactor(languages): the JS-builtins fallback is the TypeScript pack's`.

---

### Task 6: Python's type normalizer and star-import expansion

**Files:**
- Modify:
  - `src/cgis/languages/python/resolution.py` (normalizer)
  - `src/cgis/languages/python/index.py` (facts gain `reexports`; move `_public_names_under`, `_close_over_sources` and `_expand_star_imports` from `IndexBuilder`)
  - `src/cgis/core/index.py` (`IndexFacts.reexports`)
  - `src/cgis/languages/typescript/index.py` (empty `reexports`)
  - `src/cgis/resolver/symbols.py` (`_through_annotated_alias` → registry normalizers)
  - `src/cgis/resolver/indices.py` (builder merges facts' `reexports`; delete the star code and the `reexports`/`star_imports` accumulation in `_index_metadata`)

- [ ] **Step 1: Write the failing tests.** Append:

```python
def test_the_annotated_alias_is_pythons_normalizer() -> None:
    rules = rules_for("app/x.py")
    assert rules is not None
    assert len(rules.type_normalizers) == 1
    assert type_normalizers() == rules.type_normalizers


def test_a_normalizer_applies_without_a_source() -> None:
    """receiver_type_fqn has no source file: ServiceDep still reads as Service (#550, D2)."""
    alias = Node(id="app.deps.ServiceDep", type=NodeType.VARIABLE, name="ServiceDep",
                 file_path="app/deps.py", start_line=1, end_line=1,
                 metadata={"annotated_type": "app.svc.Service"})  # fmt: skip
    service = Node(id="app.svc.Service", type=NodeType.CLASS, name="Service",
                   file_path="app/svc.py", start_line=1, end_line=1)  # fmt: skip
    index = IndexBuilder().build([alias, service])
    (normalize,) = type_normalizers()
    assert normalize("app.deps.ServiceDep", index) == "app.svc.Service"
```

  Add to `tests/unit/test_python_index.py` a test that `index_facts` turns a FILE with `metadata={"star_imports": ["pkg.mod"]}` over nodes `pkg.mod.A` and `pkg.mod._b` into `reexports == {<file id>: {"A": "pkg.mod.A"}}`. That is the `_expand_star_imports` contract.

- [ ] **Step 2: Run them and watch them fail.**
  Run: `uv run pytest tests/unit/test_resolution_rules.py tests/unit/test_python_index.py -q`
  Expected: FAIL. There are no normalizers, and the facts have no `reexports`.

- [ ] **Step 3: Move the normalizer.**
  1. Move `_through_annotated_alias` from `symbols.py` to `languages/python/resolution.py` as `annotated_alias(type_fqn, view)`, docstring included. Replace `index.` with `view.`.
  2. Set `RULES = ResolutionRules(type_normalizers=(annotated_alias,), reconcile_import=reconcile_import)`.
  3. In `symbols.py`, add a module-level helper:

```python
def _normalized(index: SymbolIndex, type_fqn: str) -> str:
    """`type_fqn` through every pack's type normalizers, in manifest order (D2)."""
    for normalize in type_normalizers():
        type_fqn = normalize(type_fqn, index)
    return type_fqn
```

  4. Replace both `_through_annotated_alias(self.index, x)` / `(index, x)` call sites with `_normalized(index, x)`.

- [ ] **Step 4: Move the star expansion.**
  1. Add `reexports: Mapping[str, Mapping[str, str]]` as a property to `IndexFacts`.
  2. TS facts get `reexports=MappingProxyType({})`.
  3. In `languages/python/index.py`, move `_public_names_under`, `_close_over_sources` and `_expand_star_imports` from `IndexBuilder` as module functions. In `index_facts`, collect `reexports`/`star_imports` from FILE metadata exactly as `_index_metadata` did, run the expansion, and store the result.
  4. In `IndexBuilder.build`, set `reexports` to the merge of every pack's `reexports` (a key two packs both produce is a `ValueError`, as in step 2's `from_facts`), and delete the moved code.

- [ ] **Step 5: Run.**
  Run: `uv run pytest tests/unit/test_resolution_rules.py tests/unit/test_python_index.py tests/unit/test_resolver.py tests/unit/test_resolver_indices.py tests/self_parsing tests/unit/test_graph_identity.py -q`
  Expected: all pass.

- [ ] **Step 6: Measure drift** as in Task 4 Step 5.

- [ ] **Step 7: Gate and commit.** `refactor(languages): Python's Annotated normalizer and star expansion are the Python pack's`.

---

### Task 7: Measure, record, document

- [ ] **Step 1: Ingest timing.** Run owner-api, median of three, main vs branch, using the Python `perf_counter` wrapper from step 3, Task 4 (there is no `/usr/bin/time` on this machine). Expected: within +5%. Delete the databases afterwards.
- [ ] **Step 2: Inventory.** Regenerate it. Expected: nothing gone, and the new rule modules added.
- [ ] **Step 3: Grep for leftovers.**
  Run: `git grep -nE "\.ts\b|\.tsx\b|\.py\b|typescript|python|js_builtins" -- src/cgis/resolver`
  Expected: no language rule remains. Docstrings that name a language as history are fine. A suffix check or pack name in code is a finding: fix it or ledger it.
- [ ] **Step 4: Docs.**
  - Spec "Resolution": add an `> **Implementation note (step 4):**` covering:
    - D1 (generic `layout_prefixes`/`reexports`/`suffix_map` stay, filled by packs);
    - D2 (normalizers from every pack);
    - D3 (validated positions);
    - D4 (`ResolutionView`);
    - the empty `engine-packs` allowlist;
    - the timing.
  - CLAUDE.md "Adding a Language Pack": add `resolution()` (rules: normalizers, positioned steps, fallback with confidence, `reconcile_import`) and `cross_file_metadata` in the manifest.
- [ ] **Step 5: Gate and commit.** `docs: step 4 implementation notes; inventory`, with the measurements in the body.

---

### Task 8: Identity report, review, PR

- [ ] **Step 1: Identity.** Same as step 3's plan, Task 5 Step 1, with `step4` paths, `BR=/home/zaebee/projects/ownima/cgb-step4`, and scratch under the scratchpad.
  - Expected: `identical` three times.
  - Then delete the identity databases.
- [ ] **Step 2: Independent review.** Dispatch a reviewer with execution tools on the most capable model. Give it the branch diff, this plan, the spec, the ledger rulings and the Review Focus. Ask for an old-vs-new probe of `resolve()` output over the same captured nodes and edges, as in step 3's review, and warn it about the disk. Fix Critical and Important findings test-first, then rerun the gate and identity.
- [ ] **Step 3: PR.** Push, then open `refactor(languages): step 4 — resolution rules per pack`. The body carries:
  - the module map;
  - rulings D1–D4;
  - three identity results;
  - timing and drift;
  - the `engine-packs` allowlist emptied;
  - deferred minors.

  Then check the Gemini and Guardian comments and Sonar, and reply in every thread.
