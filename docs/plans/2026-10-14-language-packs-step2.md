# Language Packs — Step 2: Project Readers — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each language pack reads its own project configs: Python its declared dependencies; TypeScript its npm dependencies, workspace packages and tsconfig aliases. `import_names.py` becomes a language-free collector. No stored graph and no `ingest_state` value changes.

**Architecture:** `cgis.core.project` defines two protocols, `ProjectReader` and `ProjectFacts`. `LanguagePack` gains `project_reader(root, extractors)`. The four config modules move into their packs:
- `python_dependencies`
- `npm_dependencies`
- `workspaces`
- `tsconfig_paths`

The collector asks the registry for every pack's reader, routes walk files and the directories above the root to them, and assembles `ImportNames` from the readers' facts. The store keys and the `ImportNames` API stay as they are, so the pipeline and resolver are untouched. Pack modules stop importing their grammar at import time, so readers can exist from the first file without loading tree-sitter grammars.

**Tech Stack:** Python 3.12, tree-sitter, SQLite, pytest, ruff, mypy strict, interrogate.

**Spec:** `docs/specs/2026-10-14-language-packs-design.md` (rev 3): "Project facts", Migration row 2. Outline: `docs/plans/2026-10-14-language-packs.md` "Step 2". Previous step: PR #597 (`docs/plans/2026-10-14-language-packs-step1.md`, its Rulings).

## Global Constraints

- `make format && make lint && make type-check && make pytest && make doc-coverage` passes before every commit. Chain commands with `&&`: never `;`, never a newline after a heredoc, never a pipe on the command whose exit status gates.
- mypy strict on `src/`. Docstring coverage ≥ 90%. Sonar cognitive complexity ≤ 15 per function.
- **Behaviour unchanged:**
  - `tests/fixtures/graph_identity/snapshot.json` is not modified, `ingest_state` included (`git diff --exit-code main -- tests/fixtures/graph_identity/snapshot.json`).
  - `test_graph_identity.py` passes under both hash seeds.
- `tests/unit/test_configs_above_root.py` passes, with no change to its fixtures or assertions. Only its `import` lines may change, because modules move. This is how step 1 treated moved imports.
- Moved modules are imported from their new paths. No shims.
- Drift tolerances only go down (`docs/ontology/tolerances.lock`).
  - If `extraction` drift exceeds 0.40 or its unresolved ratio exceeds 0.37 after the move, **stop and report**.
  - If any other self-drift or god-object gate fails, **stop and report**.
- Test-count floor: raise `.github/test-count-baseline` (currently 3095) if the suite passes 3245.
- Every PR gets an independent reviewer with execution tools before merge.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. The PR body ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- Worktree `/home/zaebee/projects/ownima/cgb-step2`, branch `feat/language-packs-step2`.

## Rulings (where this plan departs from the spec's text)

- **S1 — `ProjectFacts` is data, not a writer.**
  - **Conflict:** the spec gives `ProjectFacts.record(store)` and `differs_from(store)`. It also says "a pack never touches storage", and the `pack-isolation` gate enforces that: a pack calling `SQLiteStore.record_*` is a `cgis.languages → cgis.storage` edge.
  - **Ruling:** `ProjectFacts.state() -> Mapping[str, object]`, keyed by the `ingest_state` key each fact is stored under. The collector's `ImportNames` records and compares facts through the store's existing typed methods, so the store keys stay as they are.
  - *Cost if wrong:* moving two methods.
- **S2 — Pack modules are imported early; grammars are not.**
  - **Why:** the spec says nothing imports a pack until one of its files turns up. A project reader, though, must exist before the walk, to see configs above the root and `package.json` files met ahead of any `.ts` file.
  - **Ruling:** every pack's `__init__` stays light. The extractor, and with it the tree-sitter grammar, is imported inside `extractor()` (a static import, `noqa: PLC0415`). The review-fingerprint walk sees static imports.
  - This supersedes step 1's R7. *Cost if wrong:* none found; laziness moves from the module to the grammar.
- **S3 — `note_above` is the TS reader's search; Python keeps its own.**
  - `declared_import_roots` looks for the nearest manifest **including the root itself** and stops at `.git`. `_enclosing_directories` starts above the root.
  - The Python reader therefore ignores `note_above` and keeps the exact search it has today.
  - *Cost if wrong:* the two searches stay separate until someone needs them unified.
- **S4 — Readers keep the walk's injection seam.** `project_reader(root, extractors)` takes the pipeline's extractor mapping. A test pipeline built with only `{".py": PythonExtractor()}` keeps today's TS facts: no namer, so empty workspace packages and aliases, while npm names are still read.

## Review Focus

- **TS-only repo:** must not load `tree_sitter_python`. All readers are created up front now (Task 3, `test_a_typescript_only_ingest_never_loads_the_python_grammar`).
- **Python-only repo with a tooling `package.json` at its root** (prettier, commitlint): must not load `tree_sitter_typescript`. Step-1 deferred minor (Task 4).
- **Two packs claiming one `ingest_state` key:** must fail loudly, not let the last writer win (Task 3, `test_two_packs_claiming_one_key_fail`).
- **A graph stored before a fact was recorded:** must read as "had none" and not force a rebuild loop. `ImportNames.differ_from` is unchanged; Task 3's round-trip test records and re-reads all four keys.
- **Configs above the root** (#555, pnpm #562) and an unbounded search with no `.git`: unchanged. `test_configs_above_root.py` runs with only its import lines edited.

---

### Task 1: Light packs — no grammar at import

**Files:**
- Create: `src/cgis/languages/typescript/naming.py` (holds `TYPESCRIPT_LANG` and `file_path_to_module_fqn`, moved out of `extractor.py`)
- Modify:
  - `src/cgis/languages/typescript/extractor.py`
  - `src/cgis/languages/typescript/__init__.py`
  - `src/cgis/languages/python/__init__.py`
  - `tests/self_parsing/test_self_parse_ts.py:13`
  - `tests/unit/test_typescript_extractor.py:6`
- Test: `tests/unit/test_languages.py` (append)

**Interfaces:**
- Produces: `cgis.languages.typescript.naming.TYPESCRIPT_LANG` and `.file_path_to_module_fqn(file_path: str, source_root: str | None = None) -> str`. The body is unchanged.

- [ ] **Step 1: Write the failing test.** Append to `tests/unit/test_languages.py`:

```python
def test_importing_the_packs_loads_no_grammar() -> None:
    """Ruling S2: readers need every pack from the first file; grammars wait for a parse."""
    script = (
        "import json, sys\n"
        "from cgis.languages import pack_named\n"
        "for name in ('python', 'typescript'):\n"
        "    pack = pack_named(name)\n"
        "    pack.scope_of('src/app/x.tsx', 'src')\n"
        "    pack.code_fence('a.py')\n"
        "print(json.dumps(sorted({m.split('.')[0] for m in sys.modules if m.startswith('tree_sitter_')})))\n"
    )
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip().splitlines()[-1]) == []
```

- [ ] **Step 2: Run it and watch it fail.**
  - Run: `uv run pytest tests/unit/test_languages.py -q -k loads_no_grammar`
  - Expected: FAIL, with both `tree_sitter_python` and `tree_sitter_typescript` loaded, because each pack's `__init__` imports its extractor.

- [ ] **Step 3: Move the TS naming out, and import the extractors lazily.**
  - **`src/cgis/languages/typescript/naming.py`:** module docstring `"""TypeScript module FQNs from file paths — no tree-sitter, so a pack can name files without its grammar."""`. Move `TYPESCRIPT_LANG`, with its `#:` comment, and the whole `file_path_to_module_fqn` function verbatim out of `extractor.py`.
  - **`extractor.py`:** add `from cgis.languages.typescript.naming import TYPESCRIPT_LANG, file_path_to_module_fqn`. Delete the moved definitions.
  - **The two test files:** change their `file_path_to_module_fqn` import to `cgis.languages.typescript.naming`. In `test_typescript_extractor.py`, keep `TypeScriptExtractor` imported from `.extractor` on its own line.
  - **`typescript/__init__.py`:** import `TYPESCRIPT_LANG` and `file_path_to_module_fqn` from `.naming`, drop the top-level extractor import, and make `extractor` read:

```python
    @override
    def extractor(self, extension: str, source_roots: list[str]) -> BaseExtractor:
        """The TSX grammar for `.tsx`, the TypeScript one otherwise — loaded here, on first use."""
        from cgis.languages.typescript.extractor import TypeScriptExtractor  # noqa: PLC0415

        return TypeScriptExtractor(tsx=extension == ".tsx", source_roots=source_roots)
```

  - **`python/__init__.py`:** import `PYTHON_LANG` and `file_path_to_module_fqn` from `cgis.languages.python._syntax`. The extractor module's `file_path_to_module_fqn` only delegates to that one; check with `sed -n 25,45p src/cgis/languages/python/extractor.py`. Drop the top-level `PythonExtractor` import, and make `extractor` import it inside the method with `# noqa: PLC0415`, the same way.
  - Update the module docstring of `src/cgis/languages/__init__.py`. It currently says each manifest's load function imports "the pack". Add: `A pack's own module imports no grammar; its extractor() does (step 2, ruling S2).`

- [ ] **Step 4: Run the tests.**
  - Run: `uv run pytest tests/unit/test_languages.py tests/unit/test_typescript_extractor.py tests/self_parsing/test_self_parse_ts.py tests/unit/test_review_fingerprint_contract.py -q`
  - Expected: all pass. If the review-path inventory test reports a module that left the closure, it is `naming.py` showing up as new; that is growth and passes. A *missing* module means the walk lost one: stop and look.

- [ ] **Step 5: Gate and commit.**

```bash
cd /home/zaebee/projects/ownima/cgb-step2 && S=/tmp/claude-1001/-home-zaebee-projects-ownima-codegraph-brain/97c475b5-6bc7-4231-a643-ff81f3f651ec/scratchpad/step2 && mkdir -p $S && make format > /dev/null && make lint > $S/lint.log 2>&1 && make type-check > $S/mypy.log 2>&1 && make pytest > $S/pytest.log 2>&1 && make doc-coverage > $S/doc.log 2>&1 && git diff --exit-code main -- tests/fixtures/graph_identity/snapshot.json && git add -A && git commit -q -F - <<'MSG'
refactor(languages): a pack's module loads no grammar; extractor() does

Readers (step 2) need every pack before the first file; the grammar can wait
for the first parse. TS module naming moves to a tree-sitter-free naming.py.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
MSG
```

---

### Task 2: Move the config readers into their packs (no behaviour change)

**Files:**
- Move: `src/cgis/python_dependencies.py` → `src/cgis/languages/python/dependencies.py`
- Move: `src/cgis/npm_dependencies.py` → `src/cgis/languages/typescript/npm.py`
- Move: `src/cgis/workspaces.py` → `src/cgis/languages/typescript/workspaces.py`
- Move: `src/cgis/tsconfig_paths.py` → `src/cgis/languages/typescript/tsconfig.py`
- Modify: importers in `src/`, `scripts/` and `tests/`, which are:
  - `import_names.py`
  - `tests/unit/test_python_dependencies.py`, `test_npm_dependencies.py`, `test_tsconfig_paths.py`
  - the import line of `test_configs_above_root.py`
- Modify (self-graph):
  - `docs/ontology/patterns.yaml` (the extraction comment)
  - `tests/unit/review_path_inventory.txt` (regenerate)
  - anything `tests/self_parsing` names by the old FQNs

- [ ] **Step 1: Move with git, then rewrite the imports.** Rewrite only `from`/`import` lines, the same way step 1 did.

```bash
cd /home/zaebee/projects/ownima/cgb-step2 && git mv src/cgis/python_dependencies.py src/cgis/languages/python/dependencies.py && git mv src/cgis/npm_dependencies.py src/cgis/languages/typescript/npm.py && git mv src/cgis/workspaces.py src/cgis/languages/typescript/workspaces.py && git mv src/cgis/tsconfig_paths.py src/cgis/languages/typescript/tsconfig.py && uv run python - <<'PY'
import re
import subprocess
from pathlib import Path

MOVES = [
    (r"cgis\.python_dependencies\b", "cgis.languages.python.dependencies"),
    (r"cgis\.npm_dependencies\b", "cgis.languages.typescript.npm"),
    (r"cgis\.workspaces\b", "cgis.languages.typescript.workspaces"),
    (r"cgis\.tsconfig_paths\b", "cgis.languages.typescript.tsconfig"),
]
IMPORT = re.compile(r"^\s*(from|import) cgis\.(python_dependencies|npm_dependencies|workspaces|tsconfig_paths)\b")


def move(line: str) -> str:
    for pattern, new in MOVES:
        line = re.sub(pattern, new, line)
    return line

files = subprocess.run(["git", "ls-files", "src", "scripts", "tests"], capture_output=True, text=True, check=True).stdout.split()
changed = 0
for name in files:
    path = Path(name)
    if not name.endswith(".py") or name.startswith("tests/fixtures/") or not path.exists():
        continue
    old = path.read_text(encoding="utf-8")
    new = "".join(move(l) if IMPORT.match(l) else l for l in old.splitlines(keepends=True))
    if new != old:
        path.write_text(new, encoding="utf-8")
        changed += 1
print("files changed:", changed)
PY
git grep -nE "cgis\.(python_dependencies|npm_dependencies|workspaces|tsconfig_paths)\b" -- src scripts tests docs/ontology
```

Expected:
- `files changed:` prints about 7.
- The final grep prints only non-import mentions. Fix those that describe where code lives now, such as `patch("cgis.workspaces...")` strings or `self_parsing` FQNs. Leave historical issue references alone.
- `git diff tests/unit/test_configs_above_root.py` shows import lines only.

- [ ] **Step 2: Measure the extraction domain.** The four modules now sit under `cgis.languages`.

```bash
cd /home/zaebee/projects/ownima/cgb-step2 && S=/tmp/claude-1001/-home-zaebee-projects-ownima-codegraph-brain/97c475b5-6bc7-4231-a643-ff81f3f651ec/scratchpad/step2 && uv run cgis ingest src --output $S/self.db > /dev/null 2>&1 && uv run cgis drift --db $S/self.db --profile python --format json > $S/drift.json 2>/dev/null
python3 -c "import json; d=json.load(open('$S/drift.json')); rs=d if isinstance(d,list) else d.get('reports') or d.get('domains'); [print(r['domain'], round(r['drift_score'],4), round(r['actual']['unresolved_ratio'],4), r['actual']['node_count']) for r in rs if r['domain']=='extraction']"
```

Expected: `extraction` drift at or below 0.40 and unresolved at or below 0.37. Main after #597 measured drift 0.315 and unresolved 0.346. Above either bound: **stop and report**.

- [ ] **Step 3: Regenerate the review-path inventory.** First check that nothing left the closure except the moved names.

```bash
cd /home/zaebee/projects/ownima/cgb-step2 && uv run python - <<'PY'
import sys; sys.path.insert(0, "tests/unit")
from test_review_fingerprint_contract import current_closure, INVENTORY
pinned = set(INVENTORY.read_text().split()); now = set(current_closure())
print("gone:", sorted(pinned - now)); print("new:", sorted(now - pinned))
INVENTORY.write_text("\n".join(sorted(now)) + "\n")
PY
```

Expected:
- `gone` lists only the four old paths that were in the closure.
- `new` lists their new paths, plus `naming.py` from Task 1.

Anything else gone means a module dropped off the review path: stop.

- [ ] **Step 4: Gate and commit.** Use the same gate line as Task 1 Step 5, with this message:

```
refactor(languages): config readers move into their packs

python_dependencies -> languages/python/dependencies; npm_dependencies,
workspaces, tsconfig_paths -> languages/typescript/{npm,workspaces,tsconfig}.
Pure move plus imports; snapshot and ingest_state unchanged. Extraction domain
<drift>/<unresolved> (lock 0.40/0.37).

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

Replace the placeholders with the numbers measured in Step 2.

---

### Task 3: `ProjectReader` / `ProjectFacts` per pack; a language-free collector

**Files:**
- Create:
  - `src/cgis/core/project.py`
  - `src/cgis/languages/python/project.py`
  - `src/cgis/languages/typescript/project.py`
  - `tests/unit/test_project_facts.py`
- Modify:
  - `src/cgis/core/extractor.py` (`LanguagePack.project_reader`)
  - `src/cgis/languages/__init__.py` (`project_readers`)
  - both pack `__init__.py`
  - `src/cgis/import_names.py`
- Test: `tests/unit/test_languages.py` (append the TS-only acceptance test)

**Interfaces:**
- **`cgis.core.project`:**
  - `ProjectFacts` (Protocol): `state() -> Mapping[str, object]`.
  - `ProjectReader` (Protocol): `note(path: Path) -> bool`, `note_above(directories: list[Path], bounded: bool) -> None`, `facts() -> ProjectFacts`.
- **`LanguagePack.project_reader(root: Path, extractors: Mapping[str, BaseExtractor]) -> ProjectReader`.**
- **`cgis.languages.project_readers(root: Path, extractors: Mapping[str, BaseExtractor]) -> list[ProjectReader]`**, one reader per manifest, in manifest order.
- **Python pack:** `PythonProjectReader(root)` and `PythonFacts(dependencies: list[str])`; `state()` is `{"python_dependencies": dependencies}`.
- **TS pack:** `TypeScriptProjectReader(root, extractors)` and `TypeScriptFacts(workspace_packages, path_aliases, npm_dependencies)`; `state()` keys are `workspace_packages`, `tsconfig_paths` and `npm_dependencies`.
- **`ImportNames.from_facts(facts: Iterable[ProjectFacts]) -> ImportNames`.** The fields, `differ_from` and `record` are unchanged.

- [ ] **Step 1: Write the failing tests.** Create `tests/unit/test_project_facts.py`:

```python
"""Per-pack project readers and the collector that merges their facts (language-packs step 2)."""

import json
from pathlib import Path

import pytest

from cgis.import_names import ImportNameCollector, ImportNames
from cgis.languages import extractors, project_readers
from cgis.languages.python.dependencies import declared_import_roots
from cgis.languages.python.project import PythonFacts, PythonProjectReader
from cgis.languages.typescript.project import TypeScriptFacts, TypeScriptProjectReader
from cgis.storage.sqlite_store import SQLiteStore

_STORE_KEYS = {"workspace_packages", "tsconfig_paths", "python_dependencies", "npm_dependencies"}


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["PyYAML>=6", "requests"]\n', encoding="utf-8"
    )
    (root / "package.json").write_text(
        json.dumps({"name": "mono", "dependencies": {"react": "18"}}), encoding="utf-8"
    )
    (root / "packages" / "ui").mkdir(parents=True)
    (root / "packages" / "ui" / "package.json").write_text(
        json.dumps({"name": "@acme/ui"}), encoding="utf-8"
    )
    return root


def test_every_pack_contributes_a_reader_in_manifest_order(tmp_path: Path) -> None:
    readers = project_readers(tmp_path, extractors([]))
    assert [type(r) for r in readers] == [PythonProjectReader, TypeScriptProjectReader]


def test_the_packs_own_exactly_the_store_keys() -> None:
    """Ruling S1: facts are keyed by the ingest_state key the store has always used."""
    python = set(PythonFacts([]).state())
    typescript = set(TypeScriptFacts({}, {}, []).state())
    assert python | typescript == _STORE_KEYS
    assert not python & typescript


def test_python_facts_are_the_declared_roots(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    reader = PythonProjectReader(root)
    assert reader.note(root / "pyproject.toml") is False  # read at the root, not from the walk
    assert reader.facts().state() == {"python_dependencies": sorted(declared_import_roots(root))}
    assert reader.facts().state()["python_dependencies"] == ["requests", "yaml"]


def test_the_typescript_reader_takes_only_its_files(tmp_path: Path) -> None:
    reader = TypeScriptProjectReader(tmp_path, extractors([]))
    assert reader.note(tmp_path / "package.json") is True
    assert reader.note(tmp_path / "tsconfig.json") is True
    assert reader.note(tmp_path / "pyproject.toml") is False


def test_two_packs_claiming_one_key_fail() -> None:
    """Last-writer-wins would silently drop one pack's facts."""
    with pytest.raises(ValueError, match="python_dependencies"):
        ImportNames.from_facts([PythonFacts(["a"]), PythonFacts(["b"])])


def test_a_missing_pack_reads_as_no_facts() -> None:
    names = ImportNames.from_facts([PythonFacts(["yaml"])])
    assert names == ImportNames(
        workspace_packages={}, path_aliases={}, python_dependencies=["yaml"], npm_dependencies=[]
    )


def test_collected_facts_round_trip_through_the_store(tmp_path: Path) -> None:
    """Recorded under the old keys, and an unchanged project asks for no rebuild."""
    root = _repo(tmp_path)
    collector = ImportNameCollector(root, extractors([]))
    for path in sorted(root.rglob("package.json")):
        collector.note(path)
    names = collector.collect()
    assert names.python_dependencies == ["requests", "yaml"]
    assert names.npm_dependencies == ["react"]
    assert names.workspace_packages == {"@acme.ui": "packages.ui"}
    with SQLiteStore(str(tmp_path / "g.db")) as store:
        names.record(store)
        assert names.differ_from(store) is False
        keys = {row[0] for row in store._conn.execute("SELECT key FROM ingest_state")}  # noqa: SLF001
    assert keys >= _STORE_KEYS
```

Append to `tests/unit/test_languages.py`:

```python
def test_a_typescript_only_ingest_never_loads_the_python_grammar(tmp_path: Path) -> None:
    """Every pack's reader exists from the first file now; the Python grammar must still wait."""
    files = {"app/h.ts": TS_SOURCE, "README.md": "# demo\n"}
    assert _cli_ingest_loads(tmp_path, files) == ["tree_sitter_typescript"]
```

- [ ] **Step 2: Run them and watch them fail.**
  - Run: `uv run pytest tests/unit/test_project_facts.py tests/unit/test_languages.py -q -k "facts or reader or pack or keys or typescript_only"`
  - Expected: `test_project_facts.py` fails to collect with `ModuleNotFoundError: cgis.languages.python.project`. The TS-only test passes already: today Python dependencies are read without importing the Python pack. It is a guard for the step that follows, and Step 6 mutation-checks it.
  - If `workspace_packages` comes out as something other than `{"@acme.ui": "packages.ui"}` once the code exists, check what `WorkspacePackages.unambiguous()` does today with the same fixture before you change the test. The test must describe today's behaviour.

- [ ] **Step 3: The contract.** Create `src/cgis/core/project.py`:

```python
"""What a language pack reads from a project's configs (spec: Project facts).

Data, not a writer: a pack never touches storage (the `pack-isolation` gate), so
facts say *what* to store under which `ingest_state` key and the build plane's
collector (`cgis.import_names`) records and compares them (step 2, ruling S1).
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol


class ProjectFacts(Protocol):
    """Frozen, JSON-serialisable facts one pack read from a project."""

    def state(self) -> Mapping[str, object]:
        """Each fact under the `ingest_state` key it is stored as."""
        ...


class ProjectReader(Protocol):
    """Collects one pack's configs during a walk, and from the directories above it."""

    def note(self, path: Path) -> bool:
        """Take a file the walk passed, if it is one of this pack's configs."""
        ...

    def note_above(self, directories: list[Path], bounded: bool) -> None:
        """Read configs above the ingest root, nearest first (#555).

        `bounded` says a repository boundary ends the list; without one the list
        runs to `/`, and only the nearest find should be trusted.
        """
        ...

    def facts(self) -> ProjectFacts:
        """What the walk's configs say, once it is done."""
        ...
```

In `src/cgis/core/extractor.py`:
- add `from collections.abc import Mapping`, `from pathlib import Path` and `from cgis.core.project import ProjectReader`;
- add to `LanguagePack`:

```python
    def project_reader(self, root: Path, extractors: Mapping[str, BaseExtractor]) -> ProjectReader:
        """A reader for this pack's configs around `root` (`extractors`: the walk's own)."""
        ...
```

- [ ] **Step 4: The two readers.**

Create `src/cgis/languages/python/project.py`:

```python
"""The Python pack's project reader: the dependencies a project declares (#495)."""

from dataclasses import dataclass
from pathlib import Path
from typing import override

from cgis.core.project import ProjectFacts, ProjectReader
from cgis.languages.python.dependencies import declared_import_roots


@dataclass(frozen=True)
class PythonFacts(ProjectFacts):
    """Import roots of the declared dependencies, sorted."""

    dependencies: list[str]

    @override
    def state(self) -> dict[str, object]:
        """Stored as `python_dependencies`."""
        return {"python_dependencies": self.dependencies}


class PythonProjectReader(ProjectReader):
    """Reads the manifests at or above the root; the walk itself brings nothing."""

    def __init__(self, root: Path) -> None:
        """Read for the project at `root`."""
        self._root = root

    @override
    def note(self, path: Path) -> bool:
        """Never: the nearest manifest at or above the root is found in `facts` (S3)."""
        return False

    @override
    def note_above(self, directories: list[Path], bounded: bool) -> None:
        """Ignored: `declared_import_roots` searches from the root itself, bounded by `.git` (S3)."""

    @override
    def facts(self) -> PythonFacts:
        """The declared dependencies' import roots."""
        return PythonFacts(sorted(declared_import_roots(self._root)))
```

Create `src/cgis/languages/typescript/project.py`. It holds the TS half of today's `ImportNameCollector`, moved: `_note_configs_above`, `note` and the three TS lines of `collect`.

```python
"""The TypeScript pack's project reader: workspace packages, npm dependencies, tsconfig aliases.

Four sources read the same way (#504, #556, #508, #562): `package.json` and
`tsconfig.json` files the walk passes or that sit above the ingest root (#555).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import override

import structlog

from cgis.core.extractor import BaseExtractor
from cgis.core.project import ProjectFacts, ProjectReader
from cgis.languages.typescript.npm import NpmDependencies
from cgis.languages.typescript.tsconfig import TSCONFIG, TsconfigPaths
from cgis.languages.typescript.workspaces import (
    PACKAGE_MANIFEST,
    PNPM_WORKSPACE,
    WorkspacePackages,
    pnpm_member_names,
    workspace_member_names,
)

logger = structlog.getLogger(__name__)


@dataclass(frozen=True)
class TypeScriptFacts(ProjectFacts):
    """What one walk's configs say TypeScript import names mean."""

    #: Dotted package name -> the directory FQN it lives at.
    workspace_packages: dict[str, str]
    #: Project directory -> dotted alias pattern -> dotted target FQNs.
    path_aliases: dict[str, dict[str, list[str]]]
    #: Declared npm package names as written, less our workspace packages, sorted.
    npm_dependencies: list[str]

    @override
    def state(self) -> dict[str, object]:
        """Stored as `workspace_packages`, `tsconfig_paths` and `npm_dependencies`."""
        return {
            "workspace_packages": self.workspace_packages,
            "tsconfig_paths": self.path_aliases,
            "npm_dependencies": self.npm_dependencies,
        }


class TypeScriptProjectReader(ProjectReader):
    """Takes the `package.json` and `tsconfig.json` files a walk passes, and those above it."""

    def __init__(self, root: Path, extractors: Mapping[str, BaseExtractor]) -> None:
        """Read for `root`, naming modules with the walk's own `.ts` extractor (S4)."""
        self._root = root
        self._packages = WorkspacePackages(root, extractors)
        self._tsconfigs = TsconfigPaths(root, extractors)
        self._npm = NpmDependencies()
        # Workspace packages named by a manifest above the root, outside the walk.
        self._siblings: set[str] = set()

    @override
    def note(self, path: Path) -> bool:
        """`package.json` feeds packages and npm names; `tsconfig.json` feeds aliases."""
        if path.name == PACKAGE_MANIFEST:
            self._packages.note(path)
            self._npm.note(path)
            return True
        if path.name == TSCONFIG:
            self._tsconfigs.note(path)
            return True
        return False

    @override
    def note_above(self, directories: list[Path], bounded: bool) -> None:
        """Read the configs above the ingest root that its files still belong to (#555).

        `cgis ingest app/src` never walks past `app/tsconfig.json` or
        `app/package.json`, yet the compiler gives each file the nearest tsconfig
        above it and Node finds packages in every `node_modules` up the tree. So
        the nearest tsconfig above counts — unless the root has its own, which is
        nearer — and every package.json above declares dependencies, less the
        workspace packages a monorepo manifest there names.
        """
        manifests = [d / PACKAGE_MANIFEST for d in directories if (d / PACKAGE_MANIFEST).is_file()]
        # Without a repository the walk up reaches `$HOME` or `/`: a stray manifest
        # there would add packages, so only the nearest is trusted (#560 review).
        for manifest in manifests if bounded else manifests[:1]:
            self._npm.note(manifest)
            self._siblings |= workspace_member_names(manifest)
        # pnpm names its workspace in its own file, beside the root manifest (#562).
        pnpm = [d / PNPM_WORKSPACE for d in directories if (d / PNPM_WORKSPACE).is_file()]
        for workspace_file in pnpm if bounded else pnpm[:1]:
            self._siblings |= pnpm_member_names(workspace_file)
        if (self._root / TSCONFIG).is_file():
            return
        config = next((d / TSCONFIG for d in directories if (d / TSCONFIG).is_file()), None)
        if config is not None:
            logger.info("Reading tsconfig.json above the ingest root", path=str(config))
            self._tsconfigs.note(config)

    @override
    def facts(self) -> TypeScriptFacts:
        """Aliases last: a tsconfig can extend a shared config in a workspace package."""
        return TypeScriptFacts(
            workspace_packages=self._packages.unambiguous(),
            path_aliases=self._tsconfigs.aliases(self._packages.directories()),
            npm_dependencies=self._npm.declared(excluding=self._packages.names() | self._siblings),
        )
```

Add `project_reader` to both packs. Each needs `@override` and the imports `Mapping` and `Path`.
- Python: `return PythonProjectReader(root)`. `extractors` is unused, and `@override` covers that.
- TS: `return TypeScriptProjectReader(root, extractors)`.

Import the reader classes at the top of each pack's `__init__`. Neither module imports a grammar.

In `src/cgis/languages/__init__.py`, add `from pathlib import Path` and `from cgis.core.project import ProjectReader`, then:

```python
def project_readers(root: Path, extractors: Mapping[str, BaseExtractor]) -> list[ProjectReader]:
    """Every pack's project reader, in manifest order — imports each pack, loads no grammar."""
    return [_load(m.name).project_reader(root, extractors) for m in MANIFESTS]
```

- [ ] **Step 5: The language-free collector.** In `src/cgis/import_names.py`:
  - Replace the module docstring's second sentence with: `Each language pack reads its own (cgis.core.project); this module only routes the walk's files and the directories above the root to every pack's reader, and merges what they read.`
  - Delete the imports of `NpmDependencies`, `declared_import_roots`, `TSCONFIG`, `TsconfigPaths` and the workspace names.
  - Add `from collections.abc import Iterable, Mapping`, `from typing import TYPE_CHECKING, cast`, `from cgis.core.project import ProjectFacts` and `from cgis.languages import project_readers`.
  - Keep `ImportNames` with its fields, `differ_from` and `record` unchanged, and add:

```python
    @classmethod
    def from_facts(cls, facts: Iterable[ProjectFacts]) -> "ImportNames":
        """Merge every pack's facts; a pack that read nothing contributes empty values.

        A key two packs both claim is an error, not a last-writer-wins: one pack's
        facts would vanish from resolution without a trace.
        """
        state: dict[str, object] = {}
        for fact in facts:
            contribution = fact.state()
            shared = state.keys() & contribution.keys()
            if shared:
                msg = f"two packs record the same ingest_state key(s): {sorted(shared)}"
                raise ValueError(msg)
            state.update(contribution)
        return cls(
            workspace_packages=cast("dict[str, str]", state.get("workspace_packages", {})),
            path_aliases=cast("dict[str, dict[str, list[str]]]", state.get("tsconfig_paths", {})),
            python_dependencies=cast("list[str]", state.get("python_dependencies", [])),
            npm_dependencies=cast("list[str]", state.get("npm_dependencies", [])),
        )
```

  - Replace `ImportNameCollector` with:

```python
class ImportNameCollector:
    """Routes the configs a walk passes to every language pack's reader."""

    def __init__(self, workspace_root: Path, extractors: Mapping[str, BaseExtractor]) -> None:
        """Collect for `workspace_root`; `extractors` reach the readers that name modules (S4).

        The root is resolved first: the search above it walks `parents`, which a
        relative path does not have past `.` (#560 review). The pipeline already
        passes a resolved root, so for it nothing changes.
        """
        self._root = workspace_root.resolve()
        self._readers = project_readers(self._root, extractors)
        directories, bounded = _enclosing_directories(self._root)
        for reader in self._readers:
            reader.note_above(directories, bounded)

    def note(self, path: Path) -> None:
        """Hand a file the walk passed to every reader; each takes only its own."""
        for reader in self._readers:
            reader.note(path)

    def collect(self) -> ImportNames:
        """The names, once the walk is done."""
        return ImportNames.from_facts(reader.facts() for reader in self._readers)
```

  - `logger` and `structlog` may now be unused here. Remove them if ruff says so.

- [ ] **Step 6: Run the tests, then mutation-check the TS-only guard.**
  - Run: `uv run pytest tests/unit/test_project_facts.py tests/unit/test_languages.py tests/unit/test_configs_above_root.py tests/unit/test_pipeline_ts_workspaces.py tests/unit/test_tsconfig_paths.py tests/unit/test_python_dependencies.py tests/unit/test_npm_dependencies.py -q`
  - Expected: all pass.
  - Then put `from cgis.languages.python.extractor import PythonExtractor` back at the top of `languages/python/__init__.py` for one run. `test_a_typescript_only_ingest_never_loads_the_python_grammar` must fail. Restore it, then check that `git diff --stat` lists only intended files.

- [ ] **Step 7: Gate and commit.** Use Task 1's gate line, with this message:

```
feat(languages): per-pack project readers; import_names collects without knowing languages

ProjectReader/ProjectFacts (cgis.core.project) per pack: Python reads declared
dependencies, TypeScript npm names, workspace packages and tsconfig aliases.
Facts are data keyed by their ingest_state key; the collector records and
compares them through the store, since a pack never touches storage (ruling S1).
Store keys, ImportNames and the snapshot (ingest_state included) unchanged.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

---

### Task 4: A root `package.json` stops loading the TypeScript grammar (step-1 deferred minor)

**Files:**
- Modify: `src/cgis/languages/typescript/workspaces.py`, `WorkspacePackages.note`
- Test: `tests/unit/test_languages.py` (append)

- [ ] **Step 1: Write the failing test.**

```python
def test_a_root_package_json_does_not_load_the_typescript_grammar(tmp_path: Path) -> None:
    """A tooling manifest at a Python repo's root (prettier, commitlint) names no package."""
    files = {
        "pkg/a.py": PY_SOURCE,
        "package.json": '{"name": "tooling", "devDependencies": {"prettier": "3"}}',
    }
    assert _cli_ingest_loads(tmp_path, files) == ["tree_sitter_python"]
```

- [ ] **Step 2: Run it and watch it fail.**
  - Run: `uv run pytest tests/unit/test_languages.py -q -k root_package_json`
  - Expected: FAIL, with `['tree_sitter_python', 'tree_sitter_typescript']`. `note()` evaluates `self._extractor` before it learns the manifest is the root's.

- [ ] **Step 3: Check the extractor last.** In `WorkspacePackages.note`:
  - move `if self._extractor is None: return` to just before `json.loads`, after the root and outside-root checks;
  - add to the docstring: `The extractor is asked for last: a root manifest, which is skipped anyway, must not load a grammar.`

- [ ] **Step 4: Run it, plus the workspace tests.**
  - Run: `uv run pytest tests/unit/test_languages.py tests/unit/test_pipeline_ts_workspaces.py tests/unit/test_configs_above_root.py -q`
  - Expected: all pass.

- [ ] **Step 5: Gate and commit.** Use Task 1's gate line, with message `perf(workspaces): a root package.json no longer loads the TypeScript grammar`, plus a body line and the Co-Authored-By trailer.

---

### Task 5: Documentation

**Files:**
- `CLAUDE.md`, section "Adding a Language Pack"
- `docs/specs/2026-10-14-language-packs-design.md`
- `docs/plans/2026-10-14-language-packs-step1.md`, a superseded-by note

- [ ] **Step 1: CLAUDE.md.**
  - In step 2 of "Adding a Language Pack", after `extractor`, add `project_reader`.
  - Add a step: `A pack's __init__ imports no grammar: import the extractor inside extractor(), and put config reading in a ProjectReader (cgis.core.project) whose facts are data keyed by ingest_state key — the collector, not the pack, writes them.`

- [ ] **Step 2: Spec notes.** Change no decision. Add a short `> **Implementation note (step N):**` block under each of these:
  - **Architecture → Core:** the contract (`BaseExtractor`, `ModuleNamer`, `LanguagePack`, `ProjectReader`, `ProjectFacts`) lives in `cgis.core`. A protocol inside the registry would form a cycle with the packs that subclass it (#597).
  - **Registry:** pack modules are imported up front, for their readers; grammars load in `extractor()` (S2). Manifests load packs through a function with a static import, never `importlib`, which the review-fingerprint walk cannot follow (#597).
  - **Project facts:** `ProjectFacts` is `state()` data; the collector records and compares it (S1, with the conflict stated).
  - **Files and namespaces:** step 1 split "is test" into the walk's skip (`Manifest.never_ingested`) and the node flag (`is_test`) (#597, R1).

- [ ] **Step 3: Step-1 plan.** Under its Rulings heading, add one line: `> Superseded in places by the code that shipped (#597): LanguagePack lives in cgis.core.extractor, manifests carry load functions instead of module paths, and packs subclass the protocol with @override. The spec's implementation notes record each.`

- [ ] **Step 4: Gate and commit.** Use Task 1's gate line, with message `docs: implementation notes for language-packs steps 1–2`.

---

### Task 6: Identity report, review, PR

- [ ] **Step 1: Ingest the same inputs with main's cgis and the branch's cgis.** The inputs are cgis `src` @715cd9a, owner-api @8de2245, and this repo's `ui/`, the tree with package.json and tsconfig that exercises the TS facts.

```bash
S=/tmp/claude-1001/-home-zaebee-projects-ownima-codegraph-brain/97c475b5-6bc7-4231-a643-ff81f3f651ec/scratchpad/step2/identity && MAIN=/home/zaebee/projects/ownima/codegraph-brain && BR=/home/zaebee/projects/ownima/cgb-step2 && /bin/rm -rf $S && mkdir -p $S/cgis $S/oa $S/ui && echo "main $(git -C $MAIN rev-parse --short HEAD) branch $(git -C $BR rev-parse --short HEAD)" && git -C $MAIN archive 715cd9a src | tar -x -C $S/cgis && git -C /home/zaebee/projects/ownima/owner-api archive 8de2245 ownima-backend | tar -x -C $S/oa && git -C $MAIN archive HEAD ui | tar -x -C $S/ui && for t in cgis/src oa/ownima-backend ui/ui; do n=$(echo $t | cut -d/ -f1); uv run --project $MAIN cgis ingest $S/$t --output $S/$n-main.db > /dev/null 2>&1 && uv run --project $BR cgis ingest $S/$t --output $S/$n-branch.db > /dev/null 2>&1 || exit 1; done && echo INGESTED
```

Expected:
- The main line shows a HEAD equal to `main`.
- Then `INGESTED`.
- If `git diff --stat <base> main -- src` is non-empty, main has moved under the branch: rebase first.

- [ ] **Step 2: Diff them.**

```bash
S=/tmp/claude-1001/-home-zaebee-projects-ownima-codegraph-brain/97c475b5-6bc7-4231-a643-ff81f3f651ec/scratchpad/step2/identity && cd /home/zaebee/projects/ownima/cgb-step2 && uv run python scripts/graph_identity.py diff $S/cgis-main.db $S/cgis/src $S/cgis-branch.db $S/cgis/src && uv run python scripts/graph_identity.py diff $S/oa-main.db $S/oa/ownima-backend $S/oa-branch.db $S/oa/ownima-backend && uv run python scripts/graph_identity.py diff $S/ui-main.db $S/ui/ui $S/ui-branch.db $S/ui/ui
```

Expected: `identical` three times. The dump includes `ingest_state`, which is exactly what this step relocates.

- [ ] **Step 3: Independent review.** Dispatch a reviewer with execution tools on the most capable model. Give it the branch diff, this plan, the spec, the Rulings and the Review Focus. Fix Critical and Important findings test-first, then rerun the gate and Step 2.

- [ ] **Step 4: Open the PR.** Push the branch. Title: `refactor(languages): step 2 — per-pack project readers`. The body carries:
  - the module map;
  - rulings S1–S4;
  - the three identity results;
  - the extraction-domain numbers;
  - the two new acceptance tests (TS-only repo, root package.json) and their mutation checks;
  - the reviewer's deferred minors.

  Then check the Gemini comments, Guardian comments and Sonar results, and reply in every thread.

<!-- START_CGIS_RELATED -->
**Related:** [language-packs-step1](2026-10-14-language-packs-step1.md)

**Code:** [`src/cgis/core/`](../../src/cgis/core/) · [`src/cgis/languages/`](../../src/cgis/languages/) · [`src/cgis/storage/`](../../src/cgis/storage/) · [`tests/fixtures/graph_identity/snapshot.json`](../../tests/fixtures/graph_identity/snapshot.json) · [`tests/unit/test_configs_above_root.py`](../../tests/unit/test_configs_above_root.py)

<!-- END_CGIS_RELATED -->
