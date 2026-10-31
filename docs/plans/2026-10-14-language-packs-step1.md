# Language Packs — Step 1: Skeleton and Registry — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the Python and TypeScript extractors into `cgis/languages/<pack>/`, behind a lazy registry that replaces `extractors/registry.py`, without changing any stored graph.

**Architecture:** `cgis/languages/__init__.py` is the registry. It holds one `Manifest` per pack (name, module path, extensions, the test files the walk never reads) and imports a pack module only when a file of that language turns up. Each pack module exposes `PACK`, an object satisfying the `LanguagePack` protocol: `code_fence`, `is_test`, `scope_of`, `extractor`. Surfaces (CLI, MCP server, guardian, `mermaid.py`, scripts) go through the registry, never a pack. The extractor contract (`BaseExtractor`, `ModuleNamer`) moves to `cgis.core.extractor`.

**Tech Stack:** Python 3.12, tree-sitter, SQLite, pytest, ruff, mypy strict, interrogate.

**Spec:** `docs/specs/2026-10-14-language-packs-design.md` (rev 3) — sections *Registry*, *The `LanguagePack` contract → Files and namespaces*, *Migration* row 1. Outline: `docs/plans/2026-10-14-language-packs.md` "Step 1".

## Global Constraints

- `make format && make lint && make type-check && make pytest && make doc-coverage` passes before every commit. Gate commands with `&&`, never with `;`, a bare newline after a heredoc, or a pipe on the gating command.
- mypy strict on `src/`; docstring coverage ≥ 90%; Sonar cognitive complexity ≤ 15 per function.
- **Behaviour unchanged:** `tests/fixtures/graph_identity/snapshot.json` is not modified (`git diff --exit-code main -- tests/fixtures/graph_identity/snapshot.json`), and `test_graph_identity.py` passes under both hash seeds.
- Moved modules are imported from their new paths. No compatibility shims, no re-export modules left at the old paths.
- Drift tolerances only go down (`docs/ontology/tolerances.lock`). The god-object baseline gets a renamed entry, never a new one. If a self-drift or god-object gate fails for any reason other than a renamed FQN, **stop and report**. Do not raise anything.
- Test-count floor: raise `.github/test-count-baseline` (now 3095) in the same PR if the suite passes 3245.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. The PR body ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- Work in the worktree `/home/zaebee/projects/ownima/cgb-step1` on branch `feat/language-packs-step1`.

## Rulings (where this plan departs from the spec's text, and why)

> Superseded in places by the code that shipped (#597): `LanguagePack` lives in
> `cgis.core.extractor`, manifests carry `load` functions instead of module paths,
> packs subclass the protocol with `@override`, and (step 2) R7 gave way to light
> pack modules that import their grammar inside `extractor()`. The spec's
> implementation notes record each.

- **R1 — Two "is test" rules, not one.** The spec says a pack's `is_test` replaces both `core.paths.is_test_path` and the pipeline's `_TEST_FILE_PATTERN`. They are different rules:
  - `_TEST_FILE_PATTERN` (`foo.test.ts`, `foo.spec.py`) makes the walk **skip** a file;
  - `is_test_path` (`tests/`, `test_*.py`) **flags** an ingested node.

  Merging them would drop every `tests/` directory from the graph. So:
  - the skip becomes `Manifest.never_ingested` plus the registry's `skips(filename)`;
  - `LanguagePack.is_test` is the flag, and both packs delegate it to `is_test_path`;
  - the `Node` validator keeps calling `is_test_path`, because core cannot import packs.

  The pack method gets its first consumer with Go (step 7): that extractor passes `is_test` explicitly, and the validator already honours an explicit value. *Cost if wrong:* one method rename in step 7.
- **R2 — TypeScript has two grammars.** `.ts` and `.tsx` need different parsers and different fences. So the pack answers per extension:
  - `extractor(extension, source_roots)`, where the spec has `extractor(source_roots)`;
  - `code_fence(path)`, where the spec has a `code_fence` attribute.

  *Cost if wrong:* a signature change when Go lands. Go ignores the argument.
- **R3 — `extractor_for(path, roots)` becomes `extractors(roots)`, a lazy `Mapping[str, BaseExtractor]`.** `IngestionPipeline` keeps its `Mapping` parameter. 34 tests inject exactly one language through it, and production callers pass the lazy mapping. Laziness comes from looking up by key: the mapping builds an extractor on first `__getitem__`, and `__contains__`/`__iter__` never build one.
- **R4 — The contract lives in `cgis.core.extractor`, not `cgis/languages/`.** The forbidden-edge gate's `_pack()` reads every `cgis.languages.<x>` module as a pack. A contract module next to the packs would trip `cross-pack` from every pack. `base.py` imports only `cgis.core.models`, so `core-isolation` holds.
- **R5 — The CLI's Python fallback moves into the registry's `scope_of(path, root)`.** A path with no pack (e.g. extensionless `src/cgis/pipeline`) gets the Python rule, a slash-to-dot rewrite. This is the same kind of quirk the spec keeps in the registry for `classify` with no source.
- **R6 — `_python_ast.py` becomes `_syntax.py`, not `_ast.py`.** A module named `_ast` shadows the stdlib C module whenever its directory lands on `sys.path`.
- **R7 — Each pack module imports its extractor at top level.** The registry is what stays lazy: it imports a pack only when it meets one of that pack's files. Nothing imports inside a function.

## Review Focus

- **`Foo.TS` (uppercase extension).** Registry and pipeline must both say "not ours". Covered by the ported `TestCaseSensitivity` (Task 2).
- **A `.tsx` file must get the TSX grammar.** `"const a = <div />;"` parses cleanly with the `.tsx` extractor and with errors under `.ts` (Task 2, `test_tsx_gets_the_tsx_grammar`).
- **A Python-only repository must never load `tree_sitter_typescript`.** This includes the CLI path and the TS-only config readers, which must not touch the TS extractor when the repo has no package.json or tsconfig. Task 3's subprocess test has a positive control.
- **Every extracted node's `language` must name the pack that reads its file.** `mermaid.py` switches from the path suffix to `pack_for_node`, so this must hold (Task 2, `test_every_node_names_the_pack_that_reads_its_file`). A language no pack has (`"rust"`) maps to `None`, never to a default.
- **The skip rule.** `skips()` must equal the old `_TEST_FILE_PATTERN` on every name, including `a.TEST.tsx`, `a.spec.jsx` and `a.test.pyi` (Task 3, parametrised against the old regex copied into the test).

---

### Task 1: Move the modules (no behaviour change)

**Files:**
- Move: `src/cgis/extractors/base.py` → `src/cgis/core/extractor.py`
- Move: `src/cgis/extractors/python_extractor.py` → `src/cgis/languages/python/extractor.py`
- Move: `src/cgis/extractors/_python_{ast,annotations,classes,functions,imports,types}.py` → `src/cgis/languages/python/_{syntax,annotations,classes,functions,imports,types}.py`
- Move: `src/cgis/extractors/typescript_extractor.py` → `src/cgis/languages/typescript/extractor.py`
- Move: `src/cgis/extractors/_typescript_bindings.py` → `src/cgis/languages/typescript/_bindings.py`
- Modify (imports only): every importer in `src/`, `scripts/`, `tests/`
- Modify: `tests/self_parsing/test_architecture.py`, `tests/self_parsing/forbidden_edges_allowlist.json`, `tests/self_parsing/test_receiver_resolution.py:210-212`, `docs/ontology/patterns.yaml:117-132`, `docs/ontology/domains.yaml:5-12`, `docs/ontology/tolerances.lock` (only if the measurement allows lowering)

`src/cgis/extractors/registry.py` stays where it is until Task 2. Its imports are rewritten here.

**Interfaces:**
- Produces:
  - `cgis.core.extractor.BaseExtractor` and `cgis.core.extractor.ModuleNamer`, unchanged;
  - `cgis.languages.python.extractor.PythonExtractor` and `.file_path_to_module_fqn`;
  - `cgis.languages.python._syntax.PYTHON_LANG`;
  - `cgis.languages.typescript.extractor.TypeScriptExtractor`, `.file_path_to_module_fqn` and `.TYPESCRIPT_LANG`.

- [ ] **Step 1: Record the before-numbers on main.** Later steps compare against these.

```bash
S=/tmp/claude-1001/-home-zaebee-projects-ownima-codegraph-brain/97c475b5-6bc7-4231-a643-ff81f3f651ec/scratchpad/step1 && mkdir -p $S && uv run --project /home/zaebee/projects/ownima/codegraph-brain cgis ingest /home/zaebee/projects/ownima/codegraph-brain/src --output $S/self-main.db && uv run --project /home/zaebee/projects/ownima/codegraph-brain cgis drift --db $S/self-main.db --profile python > $S/drift-main.txt
grep -n "extraction" $S/drift-main.txt
```

Expected: an `extraction` line with a drift score at or below 0.40, plus its unresolved ratio. Write both numbers down; the Step 6 commit message carries them.

- [ ] **Step 2: Make the self-parse guard able to fail.** Invariant 2 (`test_extractors_are_database_blind`) matches on `_clean(n.id) == "extractors"`. After the move nothing matches, and the test would pass vacuously. Add the guard first, while the constant still reads `"extractors"`. Edit `tests/self_parsing/test_architecture.py`.

Add `_EXTRACTION = "extractors"` next to `_CGIS`, and at the start of `test_extractors_are_database_blind` add:

```python
    assert any(_clean(n.id) == _EXTRACTION for n in nodes), (
        f"no '{_EXTRACTION}' node in the self-parsed graph — the invariant would examine nothing"
    )
```

Then replace the two `"extractors"` literals in that test (the `_is_imports(...)` call and the `_clean(n.id) == "extractors"` check) with `_EXTRACTION`.

Run: `uv run pytest tests/self_parsing/test_architecture.py -q -k database_blind`
Expected: PASS. The guard holds on today's tree.

- [ ] **Step 3: Move the files with git.**

```bash
cd /home/zaebee/projects/ownima/cgb-step1 && mkdir -p src/cgis/languages/python src/cgis/languages/typescript && git mv src/cgis/extractors/base.py src/cgis/core/extractor.py && git mv src/cgis/extractors/python_extractor.py src/cgis/languages/python/extractor.py && git mv src/cgis/extractors/_python_ast.py src/cgis/languages/python/_syntax.py && git mv src/cgis/extractors/_python_annotations.py src/cgis/languages/python/_annotations.py && git mv src/cgis/extractors/_python_classes.py src/cgis/languages/python/_classes.py && git mv src/cgis/extractors/_python_functions.py src/cgis/languages/python/_functions.py && git mv src/cgis/extractors/_python_imports.py src/cgis/languages/python/_imports.py && git mv src/cgis/extractors/_python_types.py src/cgis/languages/python/_types.py && git mv src/cgis/extractors/typescript_extractor.py src/cgis/languages/typescript/extractor.py && git mv src/cgis/extractors/_typescript_bindings.py src/cgis/languages/typescript/_bindings.py
```

- [ ] **Step 4: Rewrite the imports.** Only `from`/`import` lines get rewritten. A bare string can be synthetic: `tests/unit/test_python_extractor.py:226` expects `"src.cgis.extractors.base"`, which is what parsing a made-up path yields, and it must stay. Then the two files whose strings are real FQNs get rewritten in full.

```bash
cd /home/zaebee/projects/ownima/cgb-step1 && uv run python - <<'PY'
import re
import subprocess
from pathlib import Path

MOVES = [
    (r"cgis\.extractors\.base\b", "cgis.core.extractor"),
    (r"cgis\.extractors\._python_ast\b", "cgis.languages.python._syntax"),
    (r"cgis\.extractors\._python_(annotations|classes|functions|imports|types)\b", r"cgis.languages.python._\1"),
    (r"cgis\.extractors\.python_extractor\b", "cgis.languages.python.extractor"),
    (r"cgis\.extractors\._typescript_bindings\b", "cgis.languages.typescript._bindings"),
    (r"cgis\.extractors\.typescript_extractor\b", "cgis.languages.typescript.extractor"),
]
IMPORT = re.compile(r"^\s*(from|import) cgis\.extractors\.")
FULL = {"tests/self_parsing/forbidden_edges_allowlist.json", "tests/self_parsing/test_receiver_resolution.py"}


def move(line: str) -> str:
    for pattern, new in MOVES:
        line = re.sub(pattern, new, line)
    return line


files = subprocess.run(["git", "ls-files", "src", "scripts", "tests"], capture_output=True, text=True, check=True).stdout.split()
changed = 0
for name in files:
    if not name.endswith((".py", ".json")) or name.startswith("tests/fixtures/"):
        continue
    path = Path(name)
    old = path.read_text(encoding="utf-8")
    lines = old.splitlines(keepends=True)
    new = "".join(move(l) if (name in FULL or IMPORT.match(l)) else l for l in lines)
    if new != old:
        path.write_text(new, encoding="utf-8")
        changed += 1
print("files changed:", changed)
PY
git grep -nE "^\s*(from|import) cgis\.extractors\.(base|python_extractor|typescript_extractor|_python_|_typescript_bindings)" -- src scripts tests
```

Expected:
- `files changed:` prints about 50;
- the `git grep` prints nothing (exit 1 is expected here);
- `git diff tests/self_parsing/forbidden_edges_allowlist.json tests/self_parsing/test_receiver_resolution.py` shows the keys and FQNs as `cgis.languages.python.extractor...` and `cgis.languages.typescript.extractor...`;
- `tests/unit/test_python_extractor.py:226` is untouched.

Then list what is left:

```bash
git grep -n "cgis\.extractors\|cgis/extractors" -- src scripts tests
```

Expected leftovers, all deliberate:
- docstrings and comments in `src/` that name an old path. Fix each one that describes where code lives now; leave the ones that record history (`#344`, `#506`);
- `src/cgis/extractors/registry.py`'s own module docstring;
- the importers of `cgis.extractors.registry` (Task 2 replaces them);
- `tests/self_parsing/test_architecture.py` `RULES` (Task 2);
- synthetic strings in `tests/unit/test_drift*.py`, `test_cli.py`, `test_gen_ideal_graph.py`, `test_python_extractor.py:23,226,230`, `test_review_fingerprint_closure.py:221` and `agent_ab_stubs.py:109`. These are made-up FQNs and module paths fed to parsers or scorers; no real module stands behind them. Leave them as they are.

Anything else is a missed import: fix it.

- [ ] **Step 5: Re-point the self-graph gates.**

In `tests/self_parsing/test_architecture.py`:
- `_EXTRACTION = "languages"`.
- In `_FORBIDDEN_FROM_CORE`, replace `"extractors"` with `"languages"`.
- The module docstring says "Six invariants" and lists six. Make it "Seven invariants", add `7. Forbidden edges — exact allowlists per rule (language-packs spec).`, and change item 2 to `Extractor Boundary — cgis.languages.* never imports storage, query, api or pipeline.`
- In `_KNOWN_GOD_OBJECTS`, replace `"extractors.typescript_extractor.TypeScriptExtractor"` with `"languages.typescript.extractor.TypeScriptExtractor"`. Keep the comment.

In `docs/ontology/patterns.yaml`, `extraction` domain: set `fqn_prefix: "cgis.languages"`. Add one comment line above it: `# Re-pointed in language-packs step 1: the extractors moved into cgis.languages.<pack>; the contract (BaseExtractor) moved to cgis.core.`

In `docs/ontology/domains.yaml`, `ParserLayer`:
- `file_path_patterns: ["*languages/*"]`;
- `fqn_patterns: ["*.Extractor*", "*languages.*"]`.

- [ ] **Step 6: Measure the re-pointed domain.**

```bash
S=/tmp/claude-1001/-home-zaebee-projects-ownima-codegraph-brain/97c475b5-6bc7-4231-a643-ff81f3f651ec/scratchpad/step1 && uv run cgis ingest /home/zaebee/projects/ownima/cgb-step1/src --output $S/self-branch.db && uv run cgis drift --db $S/self-branch.db --profile python > $S/drift-branch.txt
grep -n "extraction" $S/drift-main.txt $S/drift-branch.txt
```

Run both commands from the worktree.

Expected: an `extraction` line in both files.
- If the branch drift is ≤ 0.40 and the unresolved ratio is ≤ 0.37, keep both lock values.
- If drift fell by more than 0.05, lower `drift_tolerance` to `measured + 0.03`, rounded up to two places, in both `patterns.yaml` and `tolerances.lock`.
- **If drift is > 0.40 or the unresolved ratio is > 0.37, stop and report the numbers.** The ratchet forbids raising either.

- [ ] **Step 7: Run the full gate and the identity check.**

```bash
cd /home/zaebee/projects/ownima/cgb-step1 && S=/tmp/claude-1001/-home-zaebee-projects-ownima-codegraph-brain/97c475b5-6bc7-4231-a643-ff81f3f651ec/scratchpad/step1 && make format > /dev/null && make lint > $S/lint.log 2>&1 && make type-check > $S/mypy.log 2>&1 && make pytest > $S/pytest.log 2>&1 && make doc-coverage > $S/doc.log 2>&1 && git diff --exit-code main -- tests/fixtures/graph_identity/snapshot.json && echo GATE-OK
tail -3 $S/pytest.log
```

Expected: `GATE-OK`, with the same passed count as main plus the one Step 2 guard.

If `test_forbidden_edges_match_the_allowlist_exactly` fails, compare its "Actual" output with the allowlist:
- only renamed keys, same counts: correct the JSON;
- any count change: stop and investigate.

- [ ] **Step 8: Commit.**

```bash
cd /home/zaebee/projects/ownima/cgb-step1 && git add -A && git commit -q -F - <<'MSG' && git log --oneline -1
refactor(languages): move the extractors into cgis.languages.<pack>, the contract into core

Pure move (git mv) plus import rewrites; the identity snapshot is unchanged.
BaseExtractor/ModuleNamer -> cgis.core.extractor; _python_ast -> _syntax (an `_ast`
module shadows the stdlib one). Self-graph gates re-pointed: Invariant 2 now has a
guard that fails when it examines no node; extraction domain -> cgis.languages
(drift <before> -> <after>, unresolved <before> -> <after>).

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
MSG
```

Fill the `<before>`/`<after>` placeholders in the message from Step 6's output before running this.

---

### Task 2: The lazy registry and the two packs

**Files:**
- Create: `src/cgis/languages/__init__.py` (registry)
- Create: `src/cgis/languages/python/__init__.py`, `src/cgis/languages/typescript/__init__.py` (packs)
- Create: `tests/unit/test_languages.py` (ports `tests/unit/test_extractor_registry.py` and adds the new tests)
- Delete: `src/cgis/extractors/registry.py`, `tests/unit/test_extractor_registry.py`, and the now-empty `src/cgis/extractors/`
- Modify:
  - `src/cgis/cli.py:39-40,205-208,838-848`
  - `src/cgis/api/mcp_server.py:24-25,60-64,472`
  - `src/cgis/api/auto_refresh.py:26,81`
  - `src/cgis/guardian/collector.py:16,129,175-176,214-219,365`
  - `src/cgis/guardian/martian.py:23`
  - `src/cgis/query/render/mermaid.py:6,77-79`
  - `scripts/graph_identity.py:21,123`
  - `scripts/generate_health.py:18,24,80`
  - `scripts/guardian_martian.py:38,301-304`
  - the five tests that call `build_extractors`
  - `tests/self_parsing/test_architecture.py` (`RULES`)
  - `tests/self_parsing/forbidden_edges_allowlist.json`

**Interfaces:**
- Consumes (Task 1): `cgis.core.extractor.BaseExtractor`, `PythonExtractor`, `TypeScriptExtractor`, both `file_path_to_module_fqn`, `PYTHON_LANG`, `TYPESCRIPT_LANG`, `cgis.core.paths.is_test_path`.
- Produces, in `cgis.languages`:
  - `LanguagePack` (Protocol)
  - `Manifest`
  - `MANIFESTS: tuple[Manifest, ...]`
  - `is_supported(path: str) -> bool`
  - `pack_for_path(path: str) -> LanguagePack | None`
  - `pack_named(name: str) -> LanguagePack | None`
  - `pack_for_node(node: Node) -> LanguagePack | None`
  - `scope_of(path: str, source_root: str | None) -> str`
  - `skips(filename: str) -> bool`
  - `extractors(source_roots: list[str]) -> Mapping[str, BaseExtractor]`

  Each pack module also exposes `PACK`.

- [ ] **Step 1: Write the failing tests.** Create `tests/unit/test_languages.py`:

```python
"""Tests for the language registry (#344, language-packs step 1).

The registry exists because several sites had to agree about what a file
extension means and did not. The tests that matter hold those sites together —
above all `TestModuleFqnRoundTrip`, which ingests real source and asserts the
registry derives the ids the extractors actually emitted.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from cgis.core.models import Node, NodeType
from cgis.core.paths import is_test_path
from cgis.languages import (
    MANIFESTS,
    Manifest,
    extractors,
    is_supported,
    pack_for_node,
    pack_for_path,
    pack_named,
    scope_of,
)
from cgis.pipeline import IngestionPipeline

TS_SOURCE = """
export function helper(value: number): number {
  return value * 2;
}
"""

TSX_SOURCE = """
export const Widget = () => {
  return null;
};
"""

PY_SOURCE = """
def helper(value: int) -> int:
    return value * 2
"""


def _ingest(tmp_path: Path, files: dict[str, str]) -> list[Node]:
    for relative, source in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    nodes, _, _ = IngestionPipeline(extractors([])).run(str(tmp_path))
    return nodes


class TestPackForPath:
    """The lookup every caller must branch on rather than defaulting."""

    @pytest.mark.parametrize(
        ("path", "name"),
        [
            ("src/cgis/pipeline.py", "python"),
            ("src/app/handler.ts", "typescript"),
            ("src/app/Widget.tsx", "typescript"),
            ("src/app/types.d.ts", "typescript"),
            ("src\\app\\handler.ts", "typescript"),
        ],
    )
    def test_recognises_supported_extensions(self, path: str, name: str) -> None:
        pack = pack_for_path(path)
        assert pack is not None
        assert pack.name == name
        assert is_supported(path)

    @pytest.mark.parametrize("path", ["README.md", "src/app/old.js", "src/app/old.jsx", "Makefile"])
    def test_returns_none_for_anything_without_an_extractor(self, path: str) -> None:
        """.js and .jsx especially: the TS FQN helper strips them, but nothing parses them."""
        assert pack_for_path(path) is None
        assert not is_supported(path)


class TestCaseSensitivity:
    """The registry must agree with the ingest pipeline, including where both say no."""

    def test_an_uppercase_extension_is_unsupported_because_ingest_skips_it(
        self, tmp_path: Path
    ) -> None:
        """`Foo.TS` is never parsed; calling it supported would recreate #344's silent miss."""
        nodes = _ingest(tmp_path, {"Foo.TS": TS_SOURCE})
        assert [n for n in nodes if n.type is NodeType.FILE] == []
        assert pack_for_path("Foo.TS") is None


class TestPacks:
    """Each pack is what its manifest declares, and answers the step-1 contract."""

    @pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda m: m.name)
    def test_a_pack_is_what_its_manifest_says(self, manifest: Manifest) -> None:
        pack = pack_named(manifest.name)
        assert pack is not None
        assert (pack.name, pack.extensions) == (manifest.name, manifest.extensions)

    def test_an_unknown_language_has_no_pack(self) -> None:
        assert pack_named("rust") is None
        node = Node(
            id="m.f", type=NodeType.FUNCTION, name="f", file_path="m.rs",
            start_line=1, end_line=1, language="rust",
        )  # fmt: skip
        assert pack_for_node(node) is None

    @pytest.mark.parametrize(
        ("path", "fence"), [("a.py", "python"), ("a.ts", "ts"), ("a.tsx", "tsx")]
    )
    def test_fence_matches_the_file(self, path: str, fence: str) -> None:
        pack = pack_for_path(path)
        assert pack is not None
        assert pack.code_fence(path) == fence

    @pytest.mark.parametrize(
        "path",
        ["tests/a.py", "src/test_a.py", "src/a.py", "tests/a.ts", "src/a.test.ts", "contest/a.ts"],
    )
    def test_is_test_is_the_core_rule_for_both_packs(self, path: str) -> None:
        """Ruling R1: the flag stays `is_test_path`; the Node validator still calls it."""
        for manifest in MANIFESTS:
            pack = pack_named(manifest.name)
            assert pack is not None
            assert pack.is_test(path) is is_test_path(path)

    def test_every_node_names_the_pack_that_reads_its_file(self, tmp_path: Path) -> None:
        """`mermaid.py` finds the pack by `Node.language`; that must agree with the path."""
        nodes = _ingest(
            tmp_path, {"pkg/mod.py": PY_SOURCE, "app/h.ts": TS_SOURCE, "app/W.tsx": TSX_SOURCE}
        )
        extracted = [n for n in nodes if n.file_path in {"pkg/mod.py", "app/h.ts", "app/W.tsx"}]
        assert {n.file_path for n in extracted} == {"pkg/mod.py", "app/h.ts", "app/W.tsx"}
        for node in extracted:
            assert pack_for_node(node) is pack_for_path(node.file_path), node.id


class TestExtractors:
    """The pipeline's mapping comes from the registry, so it cannot drift from it."""

    def test_covers_every_registered_extension(self) -> None:
        assert set(extractors([])) == {ext for m in MANIFESTS for ext in m.extensions}

    def test_source_roots_reach_the_extractors(self) -> None:
        """A root that does not reach the extractor silently changes every FQN."""
        nodes, _ = extractors(["src"])[".ts"].parse(TS_SOURCE, "src/app/handler.ts")
        assert nodes[0].id == "app.handler"

    def test_tsx_gets_the_tsx_grammar(self) -> None:
        """Ruling R2: one pack, two grammars, chosen by extension."""
        jsx = b"const a = <div />;\n"
        found = extractors([])
        assert not found[".tsx"]._parser.parse(jsx).root_node.has_error  # noqa: SLF001
        assert found[".ts"]._parser.parse(jsx).root_node.has_error  # noqa: SLF001

    def test_membership_and_iteration_build_nothing(self) -> None:
        """Run in a fresh interpreter: this process has long since loaded both grammars."""
        script = (
            "import sys\n"
            "from cgis.languages import extractors\n"
            "m = extractors([])\n"
            "assert '.ts' in m and '.tsx' in m and len(list(m)) == 3\n"
            "print(sorted(x for x in sys.modules if x.startswith('tree_sitter_')))\n"
        )
        out = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, check=False
        )
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "[]"


class TestModuleFqnRoundTrip:
    """The acceptance test of #344: derive the id the extractor really emitted."""

    @staticmethod
    def _files(tmp_path: Path, files: dict[str, str]) -> dict[str, str]:
        nodes = _ingest(tmp_path, files)
        return {n.file_path: n.id for n in nodes if n.type is NodeType.FILE}

    def test_typescript(self, tmp_path: Path) -> None:
        ingested = self._files(
            tmp_path,
            {"app/handler.ts": TS_SOURCE, "app/Widget.tsx": TSX_SOURCE, "components/index.tsx": TSX_SOURCE},
        )
        assert ingested, "ingest produced no FILE nodes — the fixture is wrong, not the code"
        for relative, node_id in ingested.items():
            assert scope_of(relative, None) == node_id

    def test_barrel_file_collapses_like_the_extractor_does(self, tmp_path: Path) -> None:
        ingested = self._files(tmp_path, {"components/index.tsx": TSX_SOURCE})
        assert ingested["components/index.tsx"] == "components"

    def test_python(self, tmp_path: Path) -> None:
        ingested = self._files(tmp_path, {"pkg/mod.py": PY_SOURCE, "pkg/__init__.py": ""})
        assert ingested
        for relative, node_id in ingested.items():
            assert scope_of(relative, None) == node_id

    def test_the_python_rule_would_have_failed_this(self, tmp_path: Path) -> None:
        """Pins the #344 defect: the Python rule leaves `.ts` on."""
        ingested = self._files(tmp_path, {"app/handler.ts": TS_SOURCE})
        python = pack_named("python")
        assert python is not None
        assert python.scope_of("app/handler.ts", None) == "app.handler.ts"
        assert python.scope_of("app/handler.ts", None) != ingested["app/handler.ts"]

    def test_a_path_no_pack_reads_gets_the_python_rule(self) -> None:
        """Ruling R5: the CLI's extensionless fallback, kept in one place."""
        assert scope_of("src/cgis/pipeline", None) == "src.cgis.pipeline"
```

- [ ] **Step 2: Run them and watch them fail.**

Run: `uv run pytest tests/unit/test_languages.py -q`
Expected: collection error, `ImportError: cannot import name 'MANIFESTS' from 'cgis.languages'` (`cgis.languages` is still a namespace package).

- [ ] **Step 3: Write the registry.** Create `src/cgis/languages/__init__.py`:

```python
"""The language registry: which files cgis reads, and which pack reads them (#344, #146).

One manifest per pack declares everything needed before a file is met — name,
module, extensions, the test files the walk never reads — so nothing imports a
pack until a file of its language turns up. Surfaces and `query` import this
module, never `cgis.languages.<pack>` (the `surface-packs` gate).

Adding a language is a manifest here plus a package beside this file that
exposes `PACK`.

Case-sensitive on purpose: `IngestionPipeline._get_extractor` matches
`filename.endswith(ext)` against these lowercase extensions, so `Foo.TS` is
never parsed. Calling it supported here would derive an FQN that matches no
node — the silent miss #344 removed. `TestCaseSensitivity` pins the agreement.
"""

import importlib
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import PurePosixPath
from typing import Protocol

from cgis.core.extractor import BaseExtractor
from cgis.core.models import Node


class LanguagePack(Protocol):
    """What one language contributes (spec: The `LanguagePack` contract; step 1 of it)."""

    name: str
    extensions: tuple[str, ...]

    def code_fence(self, path: str) -> str:
        """Markdown fence label for this file's full-text context."""
        ...

    def is_test(self, path: str) -> bool:
        """Whether this file is test code (the `Node.is_test` flag, not the walk's skip)."""
        ...

    def scope_of(self, path: str, source_root: str | None) -> str:
        """The namespace unit this file's definitions live in — its module FQN for py/ts."""
        ...

    def extractor(self, extension: str, source_roots: list[str]) -> BaseExtractor:
        """A new extractor for files of `extension`."""
        ...


@dataclass(frozen=True)
class Manifest:
    """What the registry knows about a pack without importing it."""

    name: str  #: equals `Node.language` for everything the pack emits
    module: str  #: imported on first use; must define `PACK`
    extensions: tuple[str, ...]  #: matched against the path suffix, dot included
    never_ingested: re.Pattern[str]  #: this language's test files the walk skips outright


MANIFESTS: tuple[Manifest, ...] = (
    Manifest(
        name="python",
        module="cgis.languages.python",
        extensions=(".py",),
        never_ingested=re.compile(r"\.(test|spec)\.py$", re.IGNORECASE),
    ),
    Manifest(
        name="typescript",
        module="cgis.languages.typescript",
        extensions=(".ts", ".tsx"),
        # .js/.jsx have no pack, but the walk always skipped their test files;
        # kept so `skips` stays exactly the pattern it replaced.
        never_ingested=re.compile(r"\.(test|spec)\.(ts|tsx|js|jsx)$", re.IGNORECASE),
    ),
)

_BY_EXTENSION: dict[str, Manifest] = {ext: m for m in MANIFESTS for ext in m.extensions}
_BY_NAME: dict[str, Manifest] = {m.name: m for m in MANIFESTS}


def _suffix(path: str) -> str:
    return PurePosixPath(path.replace("\\", "/")).suffix


@cache
def _load(name: str) -> LanguagePack:
    """Import a pack's module and return its `PACK` — once per process."""
    pack: LanguagePack = importlib.import_module(_BY_NAME[name].module).PACK
    return pack


def is_supported(path: str) -> bool:
    """Whether some pack reads this path. Imports no pack."""
    return _suffix(path) in _BY_EXTENSION


def pack_for_path(path: str) -> LanguagePack | None:
    """The pack for a path's extension, or None — callers branch, never default."""
    manifest = _BY_EXTENSION.get(_suffix(path))
    return None if manifest is None else _load(manifest.name)


def pack_named(name: str) -> LanguagePack | None:
    """The pack whose `Node.language` is `name`, or None."""
    return _load(name) if name in _BY_NAME else None


def pack_for_node(node: Node) -> LanguagePack | None:
    """The pack that emitted `node`, by `Node.language` — never by re-sniffing the path."""
    return pack_named(node.language)


def scope_of(path: str, source_root: str | None) -> str:
    """A path's module FQN by its own pack.

    A path no pack reads (an extensionless `src/cgis/pipeline`) gets the Python
    rule, which is then a plain slash-to-dot rewrite — the fallback the CLI has
    always used, kept here so it lives in one place.
    """
    pack = pack_for_path(path) or _load("python")
    return pack.scope_of(path, source_root)


def skips(filename: str) -> bool:
    """Whether the walk never reads this file (`foo.test.ts`, `foo.spec.py`)."""
    return any(m.never_ingested.search(filename) for m in MANIFESTS)


class _LazyExtractors(Mapping[str, BaseExtractor]):
    """Extension → extractor, each built (and its grammar loaded) on first lookup."""

    def __init__(self, source_roots: list[str]) -> None:
        self._roots = list(source_roots)
        self._built: dict[str, BaseExtractor] = {}

    def __getitem__(self, extension: str) -> BaseExtractor:
        if extension not in self._built:
            manifest = _BY_EXTENSION[extension]  # KeyError for an unknown one, as a dict
            self._built[extension] = _load(manifest.name).extractor(extension, self._roots)
        return self._built[extension]

    def __contains__(self, extension: object) -> bool:
        return extension in _BY_EXTENSION  # Mapping's default would build it

    def __iter__(self) -> Iterator[str]:
        return iter(_BY_EXTENSION)

    def __len__(self) -> int:
        return len(_BY_EXTENSION)


def extractors(source_roots: list[str]) -> Mapping[str, BaseExtractor]:
    """The extension → extractor mapping `IngestionPipeline` takes, built lazily."""
    return _LazyExtractors(source_roots)
```

- [ ] **Step 4: Write the two packs.** Create `src/cgis/languages/python/__init__.py`:

```python
"""The Python language pack: tree-sitter-python, module FQNs from file paths."""

from cgis.core.extractor import BaseExtractor
from cgis.core.paths import is_test_path
from cgis.languages.python._syntax import PYTHON_LANG
from cgis.languages.python.extractor import PythonExtractor, file_path_to_module_fqn


class PythonPack:
    """`LanguagePack` for `.py`."""

    name: str = PYTHON_LANG
    extensions: tuple[str, ...] = (".py",)

    def code_fence(self, path: str) -> str:
        """Every Python file fences as `python`."""
        return "python"

    def is_test(self, path: str) -> bool:
        """The core rule (`tests/`, `test_*.py`, `conftest.py`, …) — ruling R1."""
        return is_test_path(path)

    def scope_of(self, path: str, source_root: str | None) -> str:
        """The module FQN the extractor gives this file's FILE node."""
        return file_path_to_module_fqn(path, source_root)

    def extractor(self, extension: str, source_roots: list[str]) -> BaseExtractor:
        """One grammar for the one extension."""
        return PythonExtractor(source_roots=source_roots)


PACK = PythonPack()
```

Create `src/cgis/languages/typescript/__init__.py`:

```python
"""The TypeScript language pack: `.ts` and `.tsx`, one grammar each."""

from cgis.core.extractor import BaseExtractor
from cgis.core.paths import is_test_path
from cgis.languages.typescript.extractor import (
    TYPESCRIPT_LANG,
    TypeScriptExtractor,
    file_path_to_module_fqn,
)


class TypeScriptPack:
    """`LanguagePack` for `.ts`/`.tsx` — the extension picks the grammar (ruling R2)."""

    name: str = TYPESCRIPT_LANG
    extensions: tuple[str, ...] = (".ts", ".tsx")

    def code_fence(self, path: str) -> str:
        """`tsx` for `.tsx`, `ts` for the rest."""
        return "tsx" if path.endswith(".tsx") else "ts"

    def is_test(self, path: str) -> bool:
        """The core rule — `tests/` segments; `foo.test.ts` never reaches a node (R1)."""
        return is_test_path(path)

    def scope_of(self, path: str, source_root: str | None) -> str:
        """The module FQN the extractor gives this file's FILE node (`/index` collapses)."""
        return file_path_to_module_fqn(path, source_root)

    def extractor(self, extension: str, source_roots: list[str]) -> BaseExtractor:
        """The TSX grammar for `.tsx`, the TypeScript one otherwise."""
        return TypeScriptExtractor(tsx=extension == ".tsx", source_roots=source_roots)


PACK = TypeScriptPack()
```

If `PYTHON_LANG` or `TYPESCRIPT_LANG` is not importable from those modules, check with `git grep -n "PYTHON_LANG =\|TYPESCRIPT_LANG ="`. Import it from where it is defined, and ledger the path.

- [ ] **Step 5: Run the new tests.**

Run: `uv run pytest tests/unit/test_languages.py -q`
Expected: all pass. If `test_an_unknown_language_has_no_pack` fails while building the `Node`, read `Node`'s required fields in `src/cgis/core/models.py` and adjust the constructor arguments only.

- [ ] **Step 6: Switch every consumer to the registry.**
  - **`src/cgis/cli.py`:**
    - Replace lines 39-40 with `from cgis.languages import extractors, is_supported, scope_of`.
    - In `ingest`, replace `extractors = build_extractors(roots)` and `IngestionPipeline(extractors, ...)` with `pipeline = IngestionPipeline(extractors(roots), domains_config=domains)`.
    - At about line 842, keep the `if` and replace the `language`/`to_fqn` lines with `target = scope_of(normalized, None)`. The comment becomes: `# Per-language, because the rules disagree (#344); a path no pack reads gets the Python slash-to-dot rewrite (registry scope_of).`
    - Confirm `git grep -n "file_path_to_module_fqn\|language_for\|build_extractors" src/cgis/cli.py` prints nothing.
  - **`src/cgis/api/mcp_server.py`:**
    - Delete lines 24-25 and the `_EXTRACTORS` dict at 60-64.
    - Add `from cgis.languages import extractors`.
    - At line 472, use `pipeline = IngestionPipeline(extractors([]))`.
    - Confirm that `git grep -n "_EXTRACTORS" src/cgis/api/mcp_server.py` prints nothing.
  - **`src/cgis/api/auto_refresh.py`:** `from cgis.languages import extractors`; `IngestionPipeline(extractors(source_roots), domains_config=domains)`.
  - **`src/cgis/guardian/collector.py`:**
    - `from cgis.languages import is_supported, pack_for_path`.
    - At 175-176: `pack = pack_for_path(rel_path)` then `fence = pack.code_fence(rel_path) if pack else ""`.
    - At 214-219: `pack = pack_for_path(rel_path)`; keep the existing None branch; then `module_fqn = pack.scope_of(rel_path, self.source_root)`.
  - **`src/cgis/guardian/martian.py`:** `from cgis.languages import is_supported`.
  - **`src/cgis/query/render/mermaid.py`:**
    - `from cgis.languages import pack_for_node`.
    - At 75-79: `pack = pack_for_node(node)`, `if pack is not None:`, `path_fqn = pack.scope_of(node.file_path, None)`.
    - Comment: `# The path FQN by the node's own pack (#504): the Python rule keeps .ts on.`
  - **`scripts/graph_identity.py`:** `from cgis.languages import extractors`; `IngestionPipeline(extractors([]))`.
  - **`scripts/generate_health.py`:**
    - Delete the `PythonExtractor` import and `_EXTRACTORS`.
    - Add `from cgis.languages import extractors`; `IngestionPipeline(extractors([]))`.
    - It ingests `src/cgis`, which holds no `.ts` files: `git ls-files src | grep -cE "\.tsx?$"` prints 0. So all languages ingest the same graph as Python alone did.
  - **`scripts/guardian_martian.py`:**
    - `from cgis.languages import is_supported, pack_for_path`.
    - At 301-304: `pack = pack_for_path(path)`; keep the None branch; `store.get_node(pack.scope_of(path, SOURCE_ROOT))`.
  - **Tests:**
    - Run `git grep -ln "build_extractors" -- tests`.
    - In each listed file, import `from cgis.languages import extractors` and replace `build_extractors(` with `extractors(`. Rename any local variable named `extractors` that would now shadow the import.
    - Run `git grep -n "language_for\|LANGUAGES\b\|cgis.extractors.registry" -- tests src scripts`. Expected: only `tests/unit/agent_ab_stubs.py:109`, which is fake tool output; leave it.
  - **Delete:** `git rm -q src/cgis/extractors/registry.py tests/unit/test_extractor_registry.py`, then `/bin/rm -rf src/cgis/extractors` (only `__pycache__` remains).

- [ ] **Step 7: Close the gate's `cgis.extractors` entries.** In `tests/self_parsing/test_architecture.py` `RULES`:
  - `pack-isolation` sources become `("cgis.languages",)`;
  - `surface-packs` targets become `("cgis.languages",)` and its `exempt_modules` becomes `frozenset({"cgis.languages"})`.

  In `forbidden_edges_allowlist.json`, `"surface-packs"` becomes `{}`.

Run: `uv run pytest tests/self_parsing/test_architecture.py -q`
Expected: PASS. The surface-to-pack edges are gone because every surface now imports `cgis.languages`. If the test reports any `surface-packs` or `cross-pack` entry, a consumer still imports a pack module: fix the import, not the allowlist.

- [ ] **Step 8: Full gate, identity check, commit.**

```bash
cd /home/zaebee/projects/ownima/cgb-step1 && S=/tmp/claude-1001/-home-zaebee-projects-ownima-codegraph-brain/97c475b5-6bc7-4231-a643-ff81f3f651ec/scratchpad/step1 && make format > /dev/null && make lint > $S/lint.log 2>&1 && make type-check > $S/mypy.log 2>&1 && make pytest > $S/pytest.log 2>&1 && make doc-coverage > $S/doc.log 2>&1 && git diff --exit-code main -- tests/fixtures/graph_identity/snapshot.json && git add -A && git commit -q -F - <<'MSG' && git log --oneline -1
feat(languages): lazy registry and python/typescript packs replace extractors/registry

cgis.languages holds one manifest per pack and imports a pack only when a file of
its language is met; extractors(roots) is a Mapping that builds each extractor on
first lookup. CLI, MCP server, auto-refresh, guardian, mermaid and the scripts go
through the registry; the surface-packs allowlist is empty. Rulings R1-R5 of the
step-1 plan: a pack's is_test is the node flag (the walk's skip is the manifest's
never_ingested), TS answers per extension, the contract lives in core.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
MSG
```

Expected: the commit lands, so every gate passed. If anything failed, read the log, fix it, and rerun the whole line.

---

### Task 3: The pipeline asks the registry, and Python-only ingests stay TypeScript-free

**Files:**
- Modify: `src/cgis/pipeline.py:447-456` (`_TEST_FILE_PATTERN`, `_get_extractor`)
- Modify: `src/cgis/workspaces.py:111-121,132,179`, `src/cgis/tsconfig_paths.py:81-91,113`
- Test: `tests/unit/test_languages.py` (append)

**Interfaces:**
- Consumes (Task 2): `cgis.languages.skips`, `cgis.languages.extractors`.

- [ ] **Step 1: Write the failing tests.** Append to `tests/unit/test_languages.py`:

```python
import re

from cgis.languages import skips

# The pipeline's pattern before step 1, copied so `skips` is held to it exactly.
_OLD_SKIP = re.compile(r"\.(test|spec)\.(py|ts|tsx|js|jsx)$", re.IGNORECASE)


@pytest.mark.parametrize(
    "name",
    [
        "a.test.py", "a.spec.ts", "a.TEST.tsx", "a.spec.jsx", "a.test.js", "a.test.TS",
        "a_test.py", "test_a.py", "a.py", "a.ts", "contest.ts", "a.test.pyi", "a.spec.d.ts",
    ],
)  # fmt: skip
def test_skips_is_the_pipeline_pattern_it_replaced(name: str) -> None:
    assert skips(name) is bool(_OLD_SKIP.search(name))


def _cli_ingest_loads(tmp_path: Path, files: dict[str, str]) -> list[str]:
    """Grammar modules a fresh `cgis ingest` of `files` imported, in a clean interpreter."""
    repo = tmp_path / "repo"
    for relative, source in files.items():
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        (repo / relative).write_text(source, encoding="utf-8")
    script = (
        "import sys\n"
        "from typer.testing import CliRunner\n"
        "from cgis.cli import app\n"
        f"r = CliRunner().invoke(app, ['ingest', {str(repo)!r}, '--output', {str(tmp_path / 'g.db')!r}])\n"
        "assert r.exit_code == 0, r.output\n"
        "import json\n"
        "print(json.dumps(sorted(m for m in sys.modules if m.startswith('tree_sitter_'))))\n"
    )
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    loaded: list[str] = json.loads(out.stdout.strip().splitlines()[-1])  # last line: ours
    return loaded


def test_a_python_only_ingest_never_loads_the_typescript_grammar(tmp_path: Path) -> None:
    """Spec, Registry: loading every grammar eagerly is what the registry exists to avoid."""
    assert _cli_ingest_loads(tmp_path, {"pkg/a.py": PY_SOURCE}) == ["tree_sitter_python"]


def test_a_typescript_file_does_load_it(tmp_path: Path) -> None:
    """The control: without it, the test above would pass on a probe that sees nothing."""
    loaded = _cli_ingest_loads(tmp_path, {"pkg/a.py": PY_SOURCE, "app/h.ts": TS_SOURCE})
    assert "tree_sitter_typescript" in loaded
```

Move the `re` and `json` imports and `skips` to the file's import block when you paste this. ruff will flag them otherwise. The pipeline logs through structlog, which may write to the real stdout around `CliRunner`. That is why the probe reads only its own last line.

- [ ] **Step 2: Run them and watch the right ones fail.**

Run: `uv run pytest tests/unit/test_languages.py -q -k "skips or grammar or does_load"`
Expected:
- `test_skips_*` PASS. The registry already has `skips`. This test pins equality before the pipeline starts relying on it.
- `test_a_typescript_file_does_load_it` PASS.
- `test_a_python_only_ingest_never_loads_the_typescript_grammar` FAILS, with `['tree_sitter_python', 'tree_sitter_typescript']`. Two causes: `_get_extractor` iterates `.items()`, and `WorkspacePackages`/`TsconfigPaths` call `extractors.get(".ts")` in `__init__`.

If it fails with any other module list, find the importer with `uv run python -X importtime -c "from cgis.cli import app" 2>&1 | grep tree_sitter_typescript` and add that site to Step 3.

- [ ] **Step 3: Make the pipeline and the TS config readers lazy.**

In `src/cgis/pipeline.py`:
- delete `_TEST_FILE_PATTERN`;
- add `from cgis.languages import skips` to the imports;
- delete `import re` if nothing else in the file uses it (`grep -n "re\." src/cgis/pipeline.py`);
- replace `_get_extractor` with:

```python
    def _get_extractor(self, filename: str) -> BaseExtractor | None:
        """Return the registered extractor for the given filename, or None.

        Matches on keys and fetches only the hit: the registry's mapping builds an
        extractor, grammar and all, on first lookup, so walking its values would load
        every language for a repository that has one.
        """
        if skips(filename):
            return None
        for ext in self._extractors:
            if filename.endswith(ext):
                return self._extractors[ext]
        return None
```

In `src/cgis/workspaces.py` `WorkspacePackages`:
- `__init__` stores `self._extractors = extractors` and no longer computes `self._extractor`. Keep the docstring and add one sentence: `The extractor is looked up on first use, so a repository with no package.json never builds it.`
- Add `from functools import cached_property`, and a property:

```python
    @cached_property
    def _extractor(self) -> ModuleNamer | None:
        """The `.ts` extractor if it can name modules — fetched only when a manifest needs it."""
        extractor = self._extractors.get(".ts") or self._extractors.get(".tsx")
        return extractor if isinstance(extractor, ModuleNamer) else None
```

- In the method at :179 that returns the package map, change the guard to `if not self._claims or self._extractor is None:`. With no claims the loop already returns `{}`, so the output is unchanged.
- `note()` keeps `if self._extractor is None: return`. It only runs for a `package.json`.

In `src/cgis/tsconfig_paths.py` `TsconfigPaths`, make the same change:
- store `self._extractors`;
- a `cached_property` `_namer` with the same body;
- `aliases()` guard: `if not self._configs or self._namer is None: return {}`.

- [ ] **Step 4: Run the tests.**

Run: `uv run pytest tests/unit/test_languages.py tests/unit/test_pipeline.py tests/unit/test_workspaces.py tests/unit/test_tsconfig_paths.py tests/unit/test_configs_above_root.py -q`
Expected: all pass. If one of those files does not exist, `ls tests/unit | grep -i "workspace\|tsconfig\|configs_above"` gives the right names.

Mutation check: put `.items()` back in `_get_extractor` for one run. Expected: `test_a_python_only_ingest_never_loads_the_typescript_grammar` fails. Restore it, and confirm `git diff --stat` shows only the intended files.

- [ ] **Step 5: Full gate, identity check, commit.**

```bash
cd /home/zaebee/projects/ownima/cgb-step1 && S=/tmp/claude-1001/-home-zaebee-projects-ownima-codegraph-brain/97c475b5-6bc7-4231-a643-ff81f3f651ec/scratchpad/step1 && make format > /dev/null && make lint > $S/lint.log 2>&1 && make type-check > $S/mypy.log 2>&1 && make pytest > $S/pytest.log 2>&1 && make doc-coverage > $S/doc.log 2>&1 && git diff --exit-code main -- tests/fixtures/graph_identity/snapshot.json && git add -A && git commit -q -F - <<'MSG' && git log --oneline -1
perf(pipeline): a Python-only ingest no longer loads the TypeScript grammar

The walk's test-file skip comes from the registry manifests (skips), the
pipeline fetches only the extractor a file needs, and the workspace/tsconfig
readers ask for the TS extractor only when a package.json or tsconfig exists.
Pinned by a fresh-interpreter CLI ingest, with a TS control.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
MSG
```

---

### Task 4: Documentation and the small debts

**Files:**
- Modify: `CLAUDE.md` (Architecture block, the FQN-format paragraph, "Adding a New Language Extractor")
- Modify: `docs/specs/2026-10-14-language-packs-design.md:313` (the CLI line flagged in the #596 review)
- Modify: `.github/workflows/ci.yml` (drop the no-op `-Dsonar.exclusions=tests/fixtures/**`)

- [ ] **Step 1: CLAUDE.md.**
  - In the Architecture block, replace `├── BaseExtractor subclasses (extractors/)   ← language-specific AST → Nodes+Edges` and `│     └── PythonExtractor                    ← only language implemented so far` with:

```
  ├── cgis.languages (registry)                ← lazy: a pack is imported when its first file is met
  │     ├── languages/python/                  ← PythonExtractor + PACK
  │     └── languages/typescript/              ← TypeScriptExtractor (.ts/.tsx) + PACK
```

  - In the FQN-format paragraph, change the helper reference to `cgis.languages.scope_of(path, source_root)` (or a pack's `scope_of`). Change the example path `extractors/python_extractor.py` to `languages/python/extractor.py`.
  - Replace the "Adding a New Language Extractor" section with:

```markdown
## Adding a Language Pack

1. Create `src/cgis/languages/<name>/` with an extractor subclassing `cgis.core.extractor.BaseExtractor`
   (`parse(code, file_path) -> tuple[list[Node], list[Edge]]`; emit `CALLS` edges with
   `target="raw_call:<name>"`, set `Node.language = "<name>"`).
2. In its `__init__.py`, define `PACK`, satisfying `cgis.languages.LanguagePack`
   (`name`, `extensions`, `code_fence`, `is_test`, `scope_of`, `extractor`).
3. Add one `Manifest` to `MANIFESTS` in `src/cgis/languages/__init__.py`. Nothing else names the pack:
   CLI, MCP server, guardian and scripts all go through the registry, and the `surface-packs`
   forbidden-edge gate fails if a surface imports a pack directly.
4. A pack never imports another pack or `cgis.storage` (`cross-pack`, `pack-isolation` gates).
```

- [ ] **Step 2: The spec line.** In `docs/specs/2026-10-14-language-packs-design.md:313`, change `` `scripts/graph_identity.py <corpus> <db-before> <db-after>` diffs two ingests `` to `` `scripts/graph_identity.py diff <db-a> <root-a> <db-b> <root-b>` diffs two ingests (`snapshot <corpus>` records one) ``.

- [ ] **Step 3: ci.yml.**
  - Delete the line `-Dsonar.exclusions=tests/fixtures/**`. `tests/fixtures` sits under `sonar.tests`, so only `sonar.test.exclusions` applies to it (#596 review).
  - In the comment above, change "is excluded outright" to "is excluded from test analysis (`sonar.test.exclusions`; it sits under `sonar.tests`)".
  - Run `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"`.

- [ ] **Step 4: Gate and commit.**

```bash
cd /home/zaebee/projects/ownima/cgb-step1 && S=/tmp/claude-1001/-home-zaebee-projects-ownima-codegraph-brain/97c475b5-6bc7-4231-a643-ff81f3f651ec/scratchpad/step1 && make format > /dev/null && make lint > $S/lint.log 2>&1 && make type-check > $S/mypy.log 2>&1 && make pytest > $S/pytest.log 2>&1 && make doc-coverage > $S/doc.log 2>&1 && git add -A && git commit -q -F - <<'MSG' && git log --oneline -1
docs: CLAUDE.md describes language packs; spec CLI line; drop a no-op Sonar flag

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
MSG
```

---

### Task 5: Identity report, review, PR

- [ ] **Step 1: Ingest the same inputs with main's cgis and the branch's cgis**, the two-extractors-one-tree comparison the spec requires.

```bash
S=/tmp/claude-1001/-home-zaebee-projects-ownima-codegraph-brain/97c475b5-6bc7-4231-a643-ff81f3f651ec/scratchpad/step1/identity && MAIN=/home/zaebee/projects/ownima/codegraph-brain && BR=/home/zaebee/projects/ownima/cgb-step1 && /bin/rm -rf $S && mkdir -p $S/cgis $S/oa && git -C $MAIN rev-parse --short HEAD && git -C $MAIN archive 715cd9a src | tar -x -C $S/cgis && git -C /home/zaebee/projects/ownima/owner-api archive 8de2245 ownima-backend | tar -x -C $S/oa && uv run --project $MAIN cgis ingest $S/cgis/src --output $S/cgis-main.db > /dev/null && uv run --project $BR cgis ingest $S/cgis/src --output $S/cgis-branch.db > /dev/null && uv run --project $MAIN cgis ingest $S/oa/ownima-backend --output $S/oa-main.db > /dev/null && uv run --project $BR cgis ingest $S/oa/ownima-backend --output $S/oa-branch.db > /dev/null && echo INGESTED
```

Expected:
- the first line is main's HEAD and equals `git rev-parse --short main`; if it doesn't, pull main first;
- then `INGESTED`.

Each `--project` runs that checkout's editable cgis, so the two databases come from two different extractors reading one tree.

- [ ] **Step 2: Diff them.**

```bash
S=/tmp/claude-1001/-home-zaebee-projects-ownima-codegraph-brain/97c475b5-6bc7-4231-a643-ff81f3f651ec/scratchpad/step1/identity && cd /home/zaebee/projects/ownima/cgb-step1 && uv run python scripts/graph_identity.py diff $S/cgis-main.db $S/cgis/src $S/cgis-branch.db $S/cgis/src && uv run python scripts/graph_identity.py diff $S/oa-main.db $S/oa/ownima-backend $S/oa-branch.db $S/oa/ownima-backend
```

Expected: `identical` twice, exit 0. Any diff line is a behaviour change: stop and investigate before the PR.

- [ ] **Step 3: Independent review.** Dispatch a reviewer with execution tools on the most capable model. Give it the branch diff against `main`, this plan, the spec, the Rulings section and the Review Focus list. Fix Critical/Important findings test-first, then rerun the full gate and Step 2.

- [ ] **Step 4: Open the PR.** Push `feat/language-packs-step1`. Title: `refactor(languages): step 1 — lazy registry, python/typescript packs`.

The body carries:
- what moved, with the module map from Task 1;
- the Rulings R1–R7, one line each;
- the identity report from Step 2, both repos, with main's and the branch's SHAs;
- the extraction-domain numbers before and after (Task 1 Step 6);
- the `surface-packs` allowlist going from 3 entries to 0;
- the Python-only acceptance test and its control.

End the body with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. Then check the Gemini and Guardian comments and the Sonar result, and reply in every thread.

<!-- START_CGIS_RELATED -->
**Related:** [language-packs-step6](2026-10-14-language-packs-step6.md) · [language-packs](2026-10-14-language-packs.md) · [suggest-packages](../specs/plans/2026-06-13-suggest-packages.md) · [language-packs](../specs/2026-10-14-language-packs-design.md) · [SELF_PORTRAIT](../architecture/SELF_PORTRAIT.md)

**Code:** [`src/cgis/api/`](../../src/cgis/api/) · [`src/cgis/core/`](../../src/cgis/core/) · [`src/cgis/guardian/`](../../src/cgis/guardian/) · [`src/cgis/languages/`](../../src/cgis/languages/) · [`src/cgis/query/`](../../src/cgis/query/)

<!-- END_CGIS_RELATED -->
