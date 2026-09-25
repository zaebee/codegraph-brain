"""The pipeline learns workspace packages from `package.json` and hands them to the resolver (#504).

The resolver rewrites `@x/lib/util` onto `packages/lib/util.ts` only when told that
a `package.json` named `@x/lib` lives in `packages/lib`. These tests cover where
that knowledge comes from: which manifests count, which are refused, and that a
change to it rebuilds the graph — an incremental run re-resolves only the files
that changed, so without a rebuild an unchanged importer would keep an edge onto a
package that no longer carries that name.
"""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from cgis.core.models import EdgeType
from cgis.extractors.typescript_extractor import TypeScriptExtractor
from cgis.pipeline import IngestionPipeline
from cgis.storage.sqlite_store import SQLiteStore

IMPORTER = "apps.web.page"


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _manifest(name: str) -> str:
    return json.dumps({"name": name, "version": "0.0.0"})


def _monorepo(root: Path, lib_name: str = "@x/lib") -> None:
    _write(
        root,
        {
            "packages/lib/package.json": _manifest(lib_name),
            "packages/lib/util.ts": "export function helper() {\n  return 1;\n}\n",
            "apps/web/package.json": _manifest("web"),
            "apps/web/page.ts": (
                'import { helper } from "@x/lib/util";\n'
                "export function Page() {\n  return helper();\n}\n"
            ),
        },
    )


def _pipeline() -> IngestionPipeline:
    return IngestionPipeline({".ts": TypeScriptExtractor()})


def _import_target(root: Path) -> str:
    _, _, resolved = _pipeline().run(str(root))
    (edge,) = (e for e in resolved if e.type == EdgeType.IMPORTS and e.source == IMPORTER)
    return edge.target


def test_a_workspace_import_reaches_the_package_module(tmp_path: Path) -> None:
    _monorepo(tmp_path)
    assert _import_target(tmp_path) == "packages.lib.util"


def test_the_repository_root_package_is_not_a_workspace_package(tmp_path: Path) -> None:
    # The root manifest names the monorepo itself. Mapping it would send any import
    # of that name to the empty directory FQN.
    _monorepo(tmp_path)
    _write(tmp_path, {"package.json": _manifest("@x/lib")})
    assert _import_target(tmp_path) == "packages.lib.util"


def test_a_name_claimed_by_two_packages_is_not_mapped(tmp_path: Path) -> None:
    _monorepo(tmp_path)
    _write(
        tmp_path,
        {
            "packages/lib-copy/package.json": _manifest("@x/lib"),
            "packages/lib-copy/util.ts": "export function helper() {\n  return 2;\n}\n",
        },
    )
    assert _import_target(tmp_path) == "@x.lib.util"


def test_a_package_linked_into_node_modules_is_not_a_second_claim(tmp_path: Path) -> None:
    # Package managers place workspace packages under node_modules too. The walk
    # never enters node_modules, so the copy there must not make the name ambiguous.
    _monorepo(tmp_path)
    _write(tmp_path, {"node_modules/@x/lib/package.json": _manifest("@x/lib")})
    assert _import_target(tmp_path) == "packages.lib.util"


def test_an_unreadable_manifest_is_skipped_not_fatal(tmp_path: Path) -> None:
    _monorepo(tmp_path)
    _write(tmp_path, {"packages/broken/package.json": "{ not json"})
    assert _import_target(tmp_path) == "packages.lib.util"


def test_renaming_a_package_rebuilds_so_an_unchanged_importer_follows(tmp_path: Path) -> None:
    _monorepo(tmp_path)
    db = str(tmp_path / "graph.db")
    work = tmp_path
    with SQLiteStore(db) as store:
        _pipeline().run(str(work), store=store, rebuild=True)

    # Only the manifest changes. page.ts is untouched, so an incremental run would
    # otherwise not re-resolve its import and would keep it on packages.lib.util.
    _write(work, {"packages/lib/package.json": _manifest("@y/lib")})
    with SQLiteStore(db) as store:
        _pipeline().run(str(work), store=store)

    targets = [
        row[0]
        for row in sqlite3.connect(db).execute(
            "select target from edges where source = ? and type = ?",
            (IMPORTER, EdgeType.IMPORTS.value),
        )
    ]
    assert targets == ["@x.lib.util"]


def test_importing_the_pipeline_does_not_load_a_language_grammar() -> None:
    """The pipeline is language-agnostic; workspace support must not change that (#506 review).

    Run in a fresh interpreter: in this one, other tests have already imported the
    TypeScript extractor, so `sys.modules` would say nothing about the pipeline.
    """
    probe = "import sys, cgis.pipeline; print('tree_sitter_typescript' in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False"
