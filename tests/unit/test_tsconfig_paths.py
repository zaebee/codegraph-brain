"""TypeScript imports through tsconfig `paths` aliases reach the module they name (#508).

`import { Button } from "@components/ui/Button"` in `apps/web` means
`apps/web/components/ui/Button.tsx` when `apps/web/tsconfig.json` maps
`@components/*` to `components/*`. On cal.com after #506 such imports were the
largest remaining unresolved group: `@lib.*` 91, `@components.*` 90, `@server.*` 23.

An alias belongs to a project, not to the repository: two apps can give `@lib/*`
different meanings, so a file uses the nearest tsconfig above it. As with the
workspace packages, a target is rewritten only onto a node that exists, and a
change to the aliases rebuilds the graph.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from cgis.core.models import EdgeType
from cgis.extractors.typescript_extractor import TypeScriptExtractor
from cgis.pipeline import IngestionPipeline
from cgis.storage.sqlite_store import SQLiteStore
from cgis.tsconfig_paths import TsconfigPaths, load_jsonc

BUTTON = "export function Button() {\n  return 1;\n}\n"


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _tsconfig(compiler: dict[str, object] | None = None, **top: object) -> str:
    return json.dumps({**top, **({"compilerOptions": compiler} if compiler is not None else {})})


def _importer(spec: str) -> str:
    return (
        f'import {{ Button }} from "{spec}";\nexport function Page() {{\n  return Button();\n}}\n'
    )


def _pipeline() -> IngestionPipeline:
    return IngestionPipeline({".ts": TypeScriptExtractor(), ".tsx": TypeScriptExtractor(tsx=True)})


def _import_target(root: Path, importer: str) -> str:
    _, _, resolved = _pipeline().run(str(root))
    (edge,) = (e for e in resolved if e.type == EdgeType.IMPORTS and e.source == importer)
    return edge.target


def _web_app(root: Path, paths: dict[str, list[str]], base_url: str | None = ".") -> None:
    compiler: dict[str, object] = {"paths": paths}
    if base_url is not None:
        compiler["baseUrl"] = base_url
    _write(
        root,
        {
            "apps/web/tsconfig.json": _tsconfig(compiler),
            "apps/web/components/ui/Button.tsx": BUTTON,
            "apps/web/pages/index.tsx": _importer("@components/ui/Button"),
        },
    )


# --- resolution -----------------------------------------------------------------


def test_a_wildcard_alias_reaches_the_module(tmp_path: Path) -> None:
    _web_app(tmp_path, {"@components/*": ["components/*"]})
    assert _import_target(tmp_path, "apps.web.pages") == "apps.web.components.ui.Button"


def test_targets_are_relative_to_the_config_without_a_base_url(tmp_path: Path) -> None:
    # TypeScript 4.1+: with no baseUrl, `paths` resolve against the declaring config.
    _web_app(tmp_path, {"@components/*": ["./components/*"]}, base_url=None)
    assert _import_target(tmp_path, "apps.web.pages") == "apps.web.components.ui.Button"


def test_targets_are_relative_to_the_base_url_when_set(tmp_path: Path) -> None:
    _web_app(tmp_path, {"@components/*": ["*"]}, base_url="./components")
    assert _import_target(tmp_path, "apps.web.pages") == "apps.web.components.ui.Button"


def test_targets_are_tried_in_order(tmp_path: Path) -> None:
    _web_app(tmp_path, {"@components/*": ["missing/*", "components/*"]})
    assert _import_target(tmp_path, "apps.web.pages") == "apps.web.components.ui.Button"


def test_an_exact_alias_reaches_its_file(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "apps/web/tsconfig.json": _tsconfig(
                {"paths": {"@button": ["./components/ui/Button.tsx"]}}
            ),
            "apps/web/components/ui/Button.tsx": BUTTON,
            "apps/web/pages/index.tsx": _importer("@button"),
        },
    )
    assert _import_target(tmp_path, "apps.web.pages") == "apps.web.components.ui.Button"


def test_the_longest_matching_pattern_decides_without_fallback(tmp_path: Path) -> None:
    # `@components/ui/*` is the better match; its target misses, and TypeScript
    # does not then try `@components/*`, so neither does the resolver.
    _web_app(tmp_path, {"@components/*": ["components/*"], "@components/ui/*": ["nowhere/*"]})
    assert _import_target(tmp_path, "apps.web.pages") == "@components.ui.Button"


def test_an_alias_onto_no_module_stays_visibly_unresolved(tmp_path: Path) -> None:
    _web_app(tmp_path, {"@components/*": ["elsewhere/*"]})
    assert _import_target(tmp_path, "apps.web.pages") == "@components.ui.Button"


def test_a_target_outside_the_repository_is_dropped(tmp_path: Path) -> None:
    _web_app(tmp_path, {"@components/*": ["../../../outside/*"]})
    assert TsconfigPaths(tmp_path, {".ts": TypeScriptExtractor()}).aliases() == {}
    assert _import_target(tmp_path, "apps.web.pages") == "@components.ui.Button"


def test_a_declaration_file_is_reached_through_an_alias(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "apps/web/tsconfig.json": _tsconfig(
                {"baseUrl": ".", "paths": {"@types/*": ["types/*"]}}
            ),
            "apps/web/types/Calendar.d.ts": "export interface Calendar {}\n",
            "apps/web/pages/index.tsx": _importer("@types/Calendar"),
        },
    )
    assert _import_target(tmp_path, "apps.web.pages") == "apps.web.types.Calendar.d"


# --- scope: per project ------------------------------------------------------------


def test_each_project_reads_its_own_aliases(tmp_path: Path) -> None:
    # The same `@lib/*` names a different directory in each app.
    _write(
        tmp_path,
        {
            "apps/web/tsconfig.json": _tsconfig({"baseUrl": ".", "paths": {"@lib/*": ["lib/*"]}}),
            "apps/web/lib/util.ts": BUTTON,
            "apps/web/page.ts": _importer("@lib/util"),
            "apps/api/tsconfig.json": _tsconfig(
                {"baseUrl": ".", "paths": {"@lib/*": ["src/lib/*"]}}
            ),
            "apps/api/src/lib/util.ts": BUTTON,
            "apps/api/page.ts": _importer("@lib/util"),
        },
    )
    _, _, resolved = _pipeline().run(str(tmp_path))
    targets = {e.source: e.target for e in resolved if e.type == EdgeType.IMPORTS}
    assert targets["apps.web.page"] == "apps.web.lib.util"
    assert targets["apps.api.page"] == "apps.api.src.lib.util"


def test_a_nearer_project_without_aliases_shadows_a_farther_one(tmp_path: Path) -> None:
    _web_app(tmp_path, {"@components/*": ["components/*"]})
    _write(
        tmp_path,
        {
            "apps/web/pages/tsconfig.json": _tsconfig({"strict": True}),
        },
    )
    assert _import_target(tmp_path, "apps.web.pages") == "@components.ui.Button"


def test_a_file_outside_every_project_has_no_aliases(tmp_path: Path) -> None:
    _web_app(tmp_path, {"@components/*": ["components/*"]})
    _write(tmp_path, {"scripts/run.ts": _importer("@components/ui/Button")})
    assert _import_target(tmp_path, "scripts.run") == "@components.ui.Button"


# --- extends ------------------------------------------------------------------------


def test_aliases_are_inherited_relative_to_the_declaring_config(tmp_path: Path) -> None:
    # The base declares `paths` without a baseUrl, so its targets are relative to
    # the base's own directory, not to the extending app.
    _write(
        tmp_path,
        {
            "tsconfig.base.json": _tsconfig({"paths": {"@ui/*": ["packages/ui/*"]}}),
            "packages/ui/Button.tsx": BUTTON,
            "apps/web/tsconfig.json": _tsconfig(extends="../../tsconfig.base"),
            "apps/web/page.tsx": _importer("@ui/Button"),
        },
    )
    assert _import_target(tmp_path, "apps.web.page") == "packages.ui.Button"


def test_an_inherited_alias_follows_the_extending_base_url(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "configs/base.json": _tsconfig({"paths": {"@ui/*": ["ui/*"]}}),
            "apps/web/tsconfig.json": _tsconfig(
                {"baseUrl": "./src"}, extends="../../configs/base.json"
            ),
            "apps/web/src/ui/Button.tsx": BUTTON,
            "apps/web/page.tsx": _importer("@ui/Button"),
        },
    )
    assert _import_target(tmp_path, "apps.web.page") == "apps.web.src.ui.Button"


def test_extends_finds_a_shared_config_in_a_workspace_package(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "packages/tsconfig/package.json": json.dumps({"name": "@x/tsconfig"}),
            "packages/tsconfig/nextjs.json": _tsconfig(
                {"baseUrl": "../../apps/web", "paths": {"~/*": ["src/*"]}}
            ),
            "apps/web/tsconfig.json": _tsconfig(extends="@x/tsconfig/nextjs.json"),
            "apps/web/src/ui/Button.tsx": BUTTON,
            "apps/web/page.tsx": _importer("~/ui/Button"),
        },
    )
    assert _import_target(tmp_path, "apps.web.page") == "apps.web.src.ui.Button"


def test_an_extends_cycle_does_not_hang(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "a/tsconfig.json": _tsconfig({"paths": {"@a/*": ["*"]}}, extends="../b/tsconfig.json"),
            "b/tsconfig.json": _tsconfig(extends="../a/tsconfig.json"),
        },
    )
    collector = TsconfigPaths(tmp_path, {".ts": TypeScriptExtractor()})
    for config in ("a/tsconfig.json", "b/tsconfig.json"):
        collector.note(tmp_path / config)
    assert collector.aliases()["a"] == {"@a.*": ["a.*"]}


# --- robustness ------------------------------------------------------------------------


def test_jsonc_comments_and_trailing_commas_are_read() -> None:
    text = """{
      // a line comment
      "compilerOptions": {
        /* a block comment */
        "paths": { "@/*": ["./src/*"], "a//b": ["c"], },
      },
    }"""
    assert load_jsonc(text) == {"compilerOptions": {"paths": {"@/*": ["./src/*"], "a//b": ["c"]}}}


def test_an_unreadable_tsconfig_is_skipped_not_fatal(tmp_path: Path) -> None:
    _web_app(tmp_path, {"@components/*": ["components/*"]})
    _write(tmp_path, {"apps/other/tsconfig.json": "{ not json"})
    assert _import_target(tmp_path, "apps.web.pages") == "apps.web.components.ui.Button"


@pytest.mark.parametrize("pattern", ["@a/*/b", "*a*"])
def test_a_pattern_the_resolver_cannot_express_is_ignored(tmp_path: Path, pattern: str) -> None:
    _write(
        tmp_path, {"tsconfig.json": _tsconfig({"paths": {pattern: ["src/*"], "@ok/*": ["src/*"]}})}
    )
    collector = TsconfigPaths(tmp_path, {".ts": TypeScriptExtractor()})
    collector.note(tmp_path / "tsconfig.json")
    assert collector.aliases() == {"": {"@ok.*": ["src.*"]}}


def test_a_target_with_text_after_the_wildcard_is_dropped(tmp_path: Path) -> None:
    _write(tmp_path, {"tsconfig.json": _tsconfig({"paths": {"@a/*": ["src/*.ts", "lib/*"]}})})
    collector = TsconfigPaths(tmp_path, {".ts": TypeScriptExtractor()})
    collector.note(tmp_path / "tsconfig.json")
    assert collector.aliases() == {"": {"@a.*": ["lib.*"]}}


def test_without_a_typescript_extractor_no_config_is_read(tmp_path: Path) -> None:
    _web_app(tmp_path, {"@components/*": ["components/*"]})
    collector = TsconfigPaths(tmp_path, {})
    collector.note(tmp_path / "apps/web/tsconfig.json")
    assert collector.aliases() == {}


# --- incremental ----------------------------------------------------------------------


def _stored_import_targets(db: str, importer: str) -> list[str]:
    return [
        row[0]
        for row in sqlite3.connect(db).execute(
            "select target from edges where source = ? and type = ?",
            (importer, EdgeType.IMPORTS.value),
        )
    ]


def test_editing_an_alias_rebuilds_so_an_unchanged_importer_follows(tmp_path: Path) -> None:
    work = tmp_path / "repo"
    _web_app(work, {"@components/*": ["components/*"]})
    db = str(tmp_path / "graph.db")
    with SQLiteStore(db) as store:
        _pipeline().run(str(work), store=store, rebuild=True)
        assert store.get_tsconfig_paths() == {
            "apps/web": {"@components.*": ["apps.web.components.*"]}
        }
    assert _stored_import_targets(db, "apps.web.pages") == ["apps.web.components.ui.Button"]

    # Only the tsconfig changes; pages/index.tsx is untouched, so an incremental run
    # would otherwise keep its import on the module the old alias named.
    _write(
        work,
        {
            "apps/web/tsconfig.json": _tsconfig(
                {"baseUrl": ".", "paths": {"@components/*": ["gone/*"]}}
            )
        },
    )
    with SQLiteStore(db) as store:
        _pipeline().run(str(work), store=store)
    assert _stored_import_targets(db, "apps.web.pages") == ["@components.ui.Button"]


def test_an_unchanged_alias_map_keeps_the_incremental_no_op(tmp_path: Path) -> None:
    work = tmp_path / "repo"
    _web_app(work, {"@components/*": ["components/*"]})
    db = str(tmp_path / "graph.db")
    with SQLiteStore(db) as store:
        _pipeline().run(str(work), store=store, rebuild=True)
    with SQLiteStore(db) as store:
        _, _, resolved = _pipeline().run(str(work), store=store)
    assert resolved == []
