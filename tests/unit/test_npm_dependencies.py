"""A TypeScript import of a declared npm package is EXTERNAL, not unresolved (#556).

On owner-admin (React 19 + Vite, ingested at the repo root) `cgis validate` read
`external 0 · unresolved 51%`, with `react`, `lucide-react` and `useState` at the top
of `top_unresolved`. A React app that imports React was unhealthy by construction,
and the ratio could no longer say whether the resolver had lost a caller.

Two gaps made that. The TypeScript extractor recorded no binding for an import, so
`useState()` stayed a bare name; and nothing read the `dependencies` of the
`package.json` the walk passed, so `react` classified UNKNOWN. The manifest is what
licenses EXTERNAL, as it is for Python (#495) — and only for a name the calling file
actually imports, so a local value that shares a package's name stays unresolved
(the lesson of #454).
"""

import json
from pathlib import Path

from cgis.core.models import Edge, EdgeType, Node, NodeNamespace
from cgis.extractors.typescript_extractor import TypeScriptExtractor
from cgis.npm_dependencies import NpmDependencies
from cgis.pipeline import IngestionPipeline
from cgis.storage.sqlite_store import SQLiteStore

APP = """\
import React, { useState, useEffect as onEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import * as Icons from "lucide-react";
import { readFileSync } from "node:fs";
import path from "path";
import "react-toastify/dist/ReactToastify.css";
import { helper } from "./util";

export function App() {
  const [x] = useState(0);
  onEffect(() => {});
  useQuery({});
  React.createElement("div");
  Icons.Home();
  readFileSync("a");
  path.join("a", "b");
  helper();
  return x;
}
"""

MANIFEST = {
    "name": "app",
    "dependencies": {"react": "^19", "@tanstack/react-query": "^5", "react-toastify": "^10"},
    "devDependencies": {"lucide-react": "^1"},
}


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def _app(root: Path, manifest: dict[str, object] | None = None, app: str = APP) -> None:
    _write(
        root,
        {
            "package.json": json.dumps(MANIFEST if manifest is None else manifest),
            "src/App.tsx": app,
            "src/util.ts": "export function helper() {\n  return 1;\n}\n",
        },
    )


def _pipeline() -> IngestionPipeline:
    return IngestionPipeline({".ts": TypeScriptExtractor(), ".tsx": TypeScriptExtractor(tsx=True)})


def _graph(root: Path) -> tuple[dict[str, Node], list[Edge]]:
    nodes, _, resolved = _pipeline().run(str(root))
    return {n.id: n for n in nodes}, resolved


def _namespace(nodes: dict[str, Node], fqn: str) -> NodeNamespace:
    return nodes[fqn].namespace


def _call_targets(edges: list[Edge], source: str = "src.App.App") -> set[str]:
    return {e.target for e in edges if e.type == EdgeType.CALLS and e.source == source}


# --- what the extractor records ------------------------------------------------


def test_the_extractor_records_bare_imports_and_their_bindings() -> None:
    nodes, _ = TypeScriptExtractor(tsx=True).parse(APP, "src/App.tsx")
    meta = next(n for n in nodes if n.id == "src.App").metadata
    assert meta["bare_imports"] == [
        "@tanstack/react-query",
        "lucide-react",
        "node:fs",
        "path",
        "react",
        "react-toastify/dist/ReactToastify.css",
    ]
    assert meta["bare_bindings"] == {
        "Icons": "lucide-react",
        "React": "react",
        "onEffect": "react.useEffect",
        "path": "path",
        "readFileSync": "node:fs.readFileSync",
        "useQuery": "@tanstack.react-query.useQuery",
        "useState": "react.useState",
    }


def test_a_relative_import_is_not_a_bare_import() -> None:
    nodes, _ = TypeScriptExtractor().parse('import { a } from "./a";\n', "src/b.ts")
    meta = nodes[0].metadata
    assert "bare_imports" not in meta
    assert "bare_bindings" not in meta


# --- what the manifests declare --------------------------------------------------


def test_every_dependency_kind_is_read_as_written(tmp_path: Path) -> None:
    manifest = tmp_path / "package.json"
    manifest.write_text(
        json.dumps(
            {
                "dependencies": {"react": "^19", "@tanstack/react-query": "^5"},
                "devDependencies": {"vitest": "^1"},
                "peerDependencies": {"react-dom": "^19"},
                "optionalDependencies": {"chart.js": "^4"},
            }
        ),
        encoding="utf-8",
    )
    deps = NpmDependencies()
    deps.note(manifest)
    assert deps.declared() == ["@tanstack/react-query", "chart.js", "react", "react-dom", "vitest"]


def test_a_local_protocol_dependency_is_not_a_package(tmp_path: Path) -> None:
    # `"@x/lib": "workspace:*"` is a package of this repository: an import of it
    # that fails to resolve is a lost edge of ours, not a call into a library.
    manifest = tmp_path / "package.json"
    manifest.write_text(
        json.dumps(
            {
                "dependencies": {
                    "@x/lib": "workspace:*",
                    "@x/ui": "file:../ui",
                    "@x/cfg": "link:../cfg",
                    "react": "^19",
                }
            }
        ),
        encoding="utf-8",
    )
    deps = NpmDependencies()
    deps.note(manifest)
    assert deps.declared() == ["react"]


def test_an_unreadable_manifest_is_skipped(tmp_path: Path) -> None:
    manifest = tmp_path / "package.json"
    manifest.write_text("{ not json", encoding="utf-8")
    deps = NpmDependencies()
    deps.note(manifest)
    assert deps.declared() == []


# --- what the resolver makes of them ----------------------------------------------


def test_imports_of_declared_packages_are_external(tmp_path: Path) -> None:
    _app(tmp_path)
    nodes, _ = _graph(tmp_path)
    for fqn in ("react", "@tanstack.react-query", "lucide-react"):
        assert _namespace(nodes, fqn) is NodeNamespace.EXTERNAL, fqn
    # A subpath of a declared package belongs to it.
    assert _namespace(nodes, "react-toastify.dist.ReactToastify.css") is NodeNamespace.EXTERNAL


def test_calls_through_package_bindings_resolve_into_the_package(tmp_path: Path) -> None:
    _app(tmp_path)
    nodes, edges = _graph(tmp_path)
    targets = _call_targets(edges)
    expected = {
        "react.useState",
        "react.useEffect",
        "@tanstack.react-query.useQuery",
        "react.createElement",
        "lucide-react.Home",
    }
    assert expected <= targets
    for fqn in expected:
        assert _namespace(nodes, fqn) is NodeNamespace.EXTERNAL, fqn


def test_node_builtins_are_stdlib(tmp_path: Path) -> None:
    _app(tmp_path)
    nodes, edges = _graph(tmp_path)
    assert {"node:fs.readFileSync", "path.join"} <= _call_targets(edges)
    for fqn in ("node:fs", "node:fs.readFileSync", "path", "path.join"):
        assert _namespace(nodes, fqn) is NodeNamespace.STDLIB, fqn


def test_an_internal_call_is_untouched(tmp_path: Path) -> None:
    _app(tmp_path)
    _, edges = _graph(tmp_path)
    assert "src.util.helper" in _call_targets(edges)


def test_an_undeclared_package_stays_unresolved(tmp_path: Path) -> None:
    # No manifest names it, so nothing licenses EXTERNAL: it may be a broken alias
    # or a file the walk missed, and that is what `top_unresolved` is for.
    _app(tmp_path, manifest={"name": "app", "dependencies": {"react": "^19"}})
    nodes, edges = _graph(tmp_path)
    assert _namespace(nodes, "@tanstack.react-query") is NodeNamespace.UNKNOWN
    # The call is not rewritten into a package nothing declares: it stays as written.
    assert "useQuery" in _call_targets(edges)
    assert _namespace(nodes, "useQuery") is NodeNamespace.UNKNOWN
    assert _namespace(nodes, "react") is NodeNamespace.EXTERNAL


def test_a_local_value_named_like_a_package_stays_unresolved(tmp_path: Path) -> None:
    # `react` is declared, but this file never imports it: `react.render` here is a
    # local receiver, and calling it EXTERNAL would hide an unresolved call (#454).
    app = (
        'import { helper } from "./util";\n'
        "export function App(react: any) {\n  react.render();\n  helper();\n}\n"
    )
    _app(tmp_path, app=app)
    nodes, edges = _graph(tmp_path)
    assert "react.render" in _call_targets(edges)
    assert _namespace(nodes, "react.render") is NodeNamespace.UNKNOWN


def test_a_workspace_package_declared_as_a_dependency_is_not_external(tmp_path: Path) -> None:
    # Monorepos list their own packages under `dependencies` as well. One that the
    # resolver could not reach is a lost edge of ours, never a library.
    _write(
        tmp_path,
        {
            "packages/lib/package.json": json.dumps({"name": "@x/lib"}),
            "packages/lib/util.ts": "export function helper() {\n  return 1;\n}\n",
            "apps/web/package.json": json.dumps(
                {"name": "web", "dependencies": {"@x/lib": "^1.0.0"}}
            ),
            "apps/web/page.ts": (
                'import { gone } from "@x/lib/missing";\n'
                "export function Page() {\n  return gone();\n}\n"
            ),
        },
    )
    nodes, _ = _graph(tmp_path)
    assert _namespace(nodes, "@x.lib.missing") is NodeNamespace.UNKNOWN


def test_validate_counts_the_app_as_resolved(tmp_path: Path) -> None:
    _app(tmp_path)
    db = tmp_path / "graph.db"
    with SQLiteStore(str(db)) as store:
        _pipeline().run(str(tmp_path), store=store)
        stats = store.get_edge_stats()
    assert stats.external > 0
    assert stats.unresolved == 0


def test_a_changed_dependency_rebuilds_the_graph(tmp_path: Path) -> None:
    # An incremental run re-resolves only changed files; adding a dependency changes
    # what an unchanged file's import means, so it must rebuild like #495 does.
    _app(tmp_path, manifest={"name": "app", "dependencies": {"react": "^19"}})
    db = tmp_path / "graph.db"
    with SQLiteStore(str(db)) as store:
        _pipeline().run(str(tmp_path), store=store)
    _write(tmp_path, {"package.json": json.dumps(MANIFEST)})
    with SQLiteStore(str(db)) as store:
        _pipeline().run(str(tmp_path), store=store)
        node = store.get_node("@tanstack.react-query")
    assert node is not None
    assert node.namespace is NodeNamespace.EXTERNAL


def test_an_ambiguous_workspace_name_is_still_ours(tmp_path: Path) -> None:
    # Two manifests claim `@acme/ui`, so it maps to no directory — but it is a
    # package of this repository, and a plain `"*"` range (npm/yarn-classic style,
    # no `workspace:` protocol) must not turn it into a library (#557 review).
    _write(
        tmp_path,
        {
            "packages/ui/package.json": json.dumps({"name": "@acme/ui"}),
            "packages/ui/src/Button.ts": "export function Button() {\n  return 1;\n}\n",
            "examples/ui-copy/package.json": json.dumps({"name": "@acme/ui"}),
            "apps/web/package.json": json.dumps(
                {"name": "web", "dependencies": {"@acme/ui": "*", "react": "^19"}}
            ),
            "apps/web/page.ts": (
                'import { Button } from "@acme/ui";\n'
                "export function Page() {\n  return Button();\n}\n"
            ),
        },
    )
    nodes, edges = _graph(tmp_path)
    assert "packages.ui.src.Button.Button" in _call_targets(edges, "apps.web.page.Page")
    assert _namespace(nodes, "@acme.ui") is NodeNamespace.UNKNOWN


def test_a_dotted_package_is_not_a_subpath_of_a_declared_one(tmp_path: Path) -> None:
    # `lodash.debounce` is its own npm package. Dotted, it reads exactly like the
    # `debounce` subpath of `lodash`; only the written specifier tells them apart.
    app = (
        'import debounce from "lodash.debounce";\n'
        'import { map } from "lodash";\n'
        "export function App() {\n  debounce();\n  map();\n}\n"
    )
    _app(tmp_path, manifest={"name": "app", "dependencies": {"lodash": "^4"}}, app=app)
    nodes, edges = _graph(tmp_path)
    assert _namespace(nodes, "lodash.debounce") is NodeNamespace.UNKNOWN
    assert {"debounce", "lodash.map"} <= _call_targets(edges)
    assert _namespace(nodes, "lodash.map") is NodeNamespace.EXTERNAL
