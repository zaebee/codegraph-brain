"""Ingesting a sub-directory still reads the project configs above it (#555).

`cgis ingest owner-admin/src`, as the MCP docs' own example does, left two in three
edges unresolved: every import went through the `@/*` alias of a `tsconfig.json`
one level up, and the walk never passed it. The repository-root ingest of the same
code resolved fine, and nothing said why the two differed.

TypeScript itself gives each file the nearest `tsconfig.json` above it, wherever
the compiler was started, and Node looks for packages in every `node_modules` up
the tree. So the configs above the ingest root are read too — up to the repository
boundary, the same limit `python_dependencies` keeps (#495), so a checkout nested
in another never reads its parent's.
"""

import json
from pathlib import Path

import pytest

from cgis.core.models import EdgeType, NodeNamespace
from cgis.extractors.typescript_extractor import TypeScriptExtractor
from cgis.import_names import ImportNameCollector
from cgis.pipeline import IngestionPipeline
from cgis.workspaces import pnpm_member_names

APP = """\
import { cn } from "@/lib/utils";
import { useState } from "react";

export function App() {
  useState(0);
  return cn();
}
"""


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def _tsconfig(paths: dict[str, list[str]], base_url: str | None = None) -> str:
    options: dict[str, object] = {"paths": paths}
    if base_url is not None:
        options["baseUrl"] = base_url
    return json.dumps({"compilerOptions": options})


def _project(root: Path, tsconfig: str | None = None) -> Path:
    """A Vite-style app: configs at the repository root, sources under `src/`."""
    (root / ".git").mkdir()
    _write(
        root,
        {
            "package.json": json.dumps({"name": "app", "dependencies": {"react": "^19"}}),
            "tsconfig.json": tsconfig or _tsconfig({"@/*": ["./src/*"]}),
            "src/App.tsx": APP,
            "src/lib/utils.ts": "export function cn() {\n  return 1;\n}\n",
        },
    )
    return root / "src"


def _pipeline() -> IngestionPipeline:
    return IngestionPipeline({".ts": TypeScriptExtractor(), ".tsx": TypeScriptExtractor(tsx=True)})


def _imports(ingest_root: Path) -> set[str]:
    _, _, resolved = _pipeline().run(str(ingest_root))
    return {e.target for e in resolved if e.type == EdgeType.IMPORTS and e.source == "App"}


def test_an_alias_from_the_tsconfig_above_resolves(tmp_path: Path) -> None:
    assert "lib.utils" in _imports(_project(tmp_path))


def test_a_base_url_above_the_root_is_honoured(tmp_path: Path) -> None:
    src = _project(tmp_path, _tsconfig({"@/*": ["src/*"]}, base_url="."))
    assert "lib.utils" in _imports(src)


def test_the_package_json_above_declares_npm_dependencies(tmp_path: Path) -> None:
    # #556 reads `dependencies` from the manifests the walk passes; from `src/`
    # it passes none, and `react` fell back to unresolved.
    nodes, _, _ = _pipeline().run(str(_project(tmp_path)))
    by_id = {n.id: n for n in nodes}
    assert by_id["react"].namespace is NodeNamespace.EXTERNAL
    assert by_id["react.useState"].namespace is NodeNamespace.EXTERNAL


def test_a_tsconfig_at_the_root_shadows_the_one_above(tmp_path: Path) -> None:
    # The nearest config is the project, for the compiler as for cgis: one in
    # `src/` without the alias means `@/lib/utils` is not this project's alias.
    src = _project(tmp_path)
    _write(src, {"tsconfig.json": _tsconfig({})})
    assert "lib.utils" not in _imports(src)


def test_the_search_stops_at_the_repository_boundary(tmp_path: Path) -> None:
    # A checkout inside another (a submodule, a vendored repo) is its own project;
    # the outer repository's alias must not leak into it.
    outer = tmp_path
    _write(outer, {"tsconfig.json": _tsconfig({"@/*": ["./inner/src/*"]})})
    inner = outer / "inner"
    (inner / ".git").mkdir(parents=True)
    _write(
        inner,
        {
            "src/App.tsx": APP,
            "src/lib/utils.ts": "export function cn() {\n  return 1;\n}\n",
        },
    )
    assert "lib.utils" not in _imports(inner / "src")


def test_an_alias_target_outside_the_root_is_dropped(tmp_path: Path) -> None:
    # `@shared/*` points beside `src/`, outside what was ingested: there is no
    # node to land on, so the import stays as written rather than guessed.
    src = _project(tmp_path, _tsconfig({"@/*": ["./src/*"], "@shared/*": ["./shared/*"]}))
    # A decoy inside the root with the same tail: an alias that leaked past the
    # root boundary (`../shared/*` read as `shared.*`) would land on it.
    _write(tmp_path, {"shared/x.ts": "export const x = 1;\n"})
    _write(src, {"shared/x.ts": "export const x = 2;\n"})
    _write(src, {"App.tsx": 'import { x } from "@shared/x";\n' + APP})
    imports = _imports(src)
    assert "@shared.x" in imports
    assert "shared.x" not in imports
    assert "lib.utils" in imports


def test_a_workspace_sibling_above_the_root_is_not_external(tmp_path: Path) -> None:
    # Ingesting `apps/web/src` of a monorepo: the app depends on `@x/lib` with a
    # plain `"*"` range, and `@x/lib` is a workspace package outside the walk.
    # It is this repository's own code — unresolved here, never a library (#560 review).
    (tmp_path / ".git").mkdir()
    _write(
        tmp_path,
        {
            "package.json": json.dumps({"name": "mono", "workspaces": ["packages/*", "apps/*"]}),
            "packages/lib/package.json": json.dumps({"name": "@x/lib"}),
            "packages/lib/index.ts": "export function libFn() {\n  return 1;\n}\n",
            "apps/web/package.json": json.dumps(
                {"name": "web", "dependencies": {"@x/lib": "*", "react": "^19"}}
            ),
            "apps/web/src/main.ts": (
                'import { libFn } from "@x/lib";\nimport { useState } from "react";\n'
                "export function main() {\n  useState(0);\n  return libFn();\n}\n"
            ),
        },
    )
    nodes, _, _ = _pipeline().run(str(tmp_path / "apps/web/src"))
    by_id = {n.id: n for n in nodes}
    assert by_id["@x.lib"].namespace is NodeNamespace.UNKNOWN
    assert by_id["react"].namespace is NodeNamespace.EXTERNAL


def test_a_pnpm_workspace_sibling_above_the_root_is_not_external(tmp_path: Path) -> None:
    """pnpm names its workspace in pnpm-workspace.yaml, not package.json (#562)."""
    (tmp_path / ".git").mkdir()
    _write(
        tmp_path,
        {
            "package.json": json.dumps({"name": "mono"}),
            "pnpm-workspace.yaml": 'packages:\n  - "packages/*"\n  - "apps/*"\n  - "!**/test"\n',
            "packages/lib/package.json": json.dumps({"name": "@x/lib"}),
            "packages/lib/index.ts": "export function libFn() {\n  return 1;\n}\n",
            "apps/web/package.json": json.dumps(
                {"name": "web", "dependencies": {"@x/lib": "^1.0.0", "react": "^19"}}
            ),
            "apps/web/src/main.ts": (
                'import { libFn } from "@x/lib";\nimport { useState } from "react";\n'
                "export function main() {\n  useState(0);\n  return libFn();\n}\n"
            ),
        },
    )
    nodes, _, _ = _pipeline().run(str(tmp_path / "apps/web/src"))
    by_id = {n.id: n for n in nodes}
    assert by_id["@x.lib"].namespace is NodeNamespace.UNKNOWN
    assert by_id["react"].namespace is NodeNamespace.EXTERNAL


def test_a_malformed_pnpm_workspace_file_names_nothing(tmp_path: Path) -> None:
    (tmp_path / "pnpm-workspace.yaml").write_text("packages: [unclosed", encoding="utf-8")
    assert pnpm_member_names(tmp_path / "pnpm-workspace.yaml") == set()


def test_without_a_repository_only_the_nearest_manifest_counts(tmp_path: Path) -> None:
    # No `.git` anywhere — a ZIP download, a container's `/app`. The search would
    # reach `$HOME` or `/`, and a stray manifest there must not add packages.
    _write(
        tmp_path,
        {
            "package.json": json.dumps({"dependencies": {"stray-pkg": "1"}}),
            "proj/package.json": json.dumps({"dependencies": {"react": "^19"}}),
            "proj/src/main.ts": (
                'import { a } from "stray-pkg";\nimport { useState } from "react";\n'
                "export function main() {\n  useState(0);\n  return a();\n}\n"
            ),
        },
    )
    nodes, _, _ = _pipeline().run(str(tmp_path / "proj/src"))
    by_id = {n.id: n for n in nodes}
    assert by_id["react"].namespace is NodeNamespace.EXTERNAL
    assert by_id["stray-pkg"].namespace is NodeNamespace.UNKNOWN


def test_a_relative_root_still_reaches_the_configs_above(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The pipeline hands the collector a resolved root, but a direct caller may
    # not: `Path("src").parents` is just `.`, and would never reach the project.
    _project(tmp_path)
    monkeypatch.chdir(tmp_path / "src")
    names = ImportNameCollector(Path(), {".ts": TypeScriptExtractor()}).collect()
    assert names.npm_dependencies == ["react"]


def test_the_repository_root_ingest_is_unchanged(tmp_path: Path) -> None:
    # The configs are inside the walk here, so nothing above is read; the node ids
    # carry `src.` and the alias lands on them as before #555.
    _project(tmp_path)
    _, _, resolved = _pipeline().run(str(tmp_path))
    imports = {e.target for e in resolved if e.type == EdgeType.IMPORTS and e.source == "src.App"}
    assert "src.lib.utils" in imports
