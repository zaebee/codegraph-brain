"""TypeScript imports of a workspace package must reach the package's own modules (#504).

A monorepo imports its packages by name: `@calcom/lib/hooks/useLocale` means
`packages/lib/hooks/useLocale.ts`. The extractor writes that target dotted,
`@calcom.lib.hooks.useLocale`, and nothing mapped it to the node. On cal.com that
left 86% of IMPORTS edges on names no node bears, and the impact graph of a module
imported in 256 places was the module alone.

The mapping is not a guess. It comes from the `package.json` files the pipeline
walked, and a target is rewritten only when the module it names exists — anything
else stays visibly unresolved, the same rule `resolve_import_target` applies to
Python layout prefixes (#494).
"""

from cgis.core.models import Edge, EdgeType, Node, NodeNamespace, NodeType
from cgis.resolver.engine import ResolverEngine

PACKAGES = {"@calcom.lib": "packages.lib", "@calcom.ui": "packages.ui"}
PAGE = "apps.web.page"


def _file(node_id: str, file_path: str) -> Node:
    return Node(
        id=node_id,
        type=NodeType.FILE,
        name=node_id.rsplit(".", 1)[-1],
        file_path=file_path,
        start_line=1,
        end_line=1,
        namespace=NodeNamespace.INTERNAL,
    )


def _import(source: str, target: str) -> Edge:
    return Edge(
        id=f"{source}->import:{target}", source=source, target=target, type=EdgeType.IMPORTS
    )


def _resolved_target(
    nodes: list[Node],
    target: str,
    packages: dict[str, str] | None = PACKAGES,
    source: str = PAGE,
) -> str:
    page = _file(source, "apps/web/page.tsx") if source == PAGE else None
    graph = [*nodes, page] if page is not None else nodes
    engine = (
        ResolverEngine(graph, [_import(source, target)])
        if packages is None
        else ResolverEngine(graph, [_import(source, target)], workspace_packages=packages)
    )
    resolved, _ = engine.resolve()
    (edge,) = (e for e in resolved if e.type == EdgeType.IMPORTS)
    return edge.target


def test_a_workspace_package_import_reaches_the_module() -> None:
    nodes = [_file("packages.lib.hooks.useLocale", "packages/lib/hooks/useLocale.ts")]
    assert _resolved_target(nodes, "@calcom.lib.hooks.useLocale") == "packages.lib.hooks.useLocale"


def test_the_package_name_alone_reaches_its_index() -> None:
    nodes = [_file("packages.lib", "packages/lib/index.ts")]
    assert _resolved_target(nodes, "@calcom.lib") == "packages.lib"


def test_a_src_layout_is_tried_after_the_package_root() -> None:
    nodes = [_file("packages.ui.src.Button", "packages/ui/src/Button.tsx")]
    assert _resolved_target(nodes, "@calcom.ui.Button") == "packages.ui.src.Button"


def test_a_package_name_matches_only_on_a_segment_boundary() -> None:
    # `@x/a` must not claim `@x/a-b/util`: without the boundary it would rewrite the
    # target to `packages.a` followed by `-b.util`, a name no node bears.
    packages = {"@x.a": "packages.a", "@x.a-b": "packages.ab"}
    nodes = [_file("packages.ab.util", "packages/ab/util.ts")]
    assert _resolved_target(nodes, "@x.a-b.util", packages) == "packages.ab.util"


def test_the_longest_package_name_wins() -> None:
    # npm names may contain dots (`socket.io`), so once `/` is dotted, `a.b` is both
    # "package a, subpath b" and "package a.b". The more specific package is meant.
    packages = {"a": "packages.a", "a.b": "packages.ab"}
    nodes = [
        _file("packages.a.b.util", "packages/a/b/util.ts"),
        _file("packages.ab.util", "packages/ab/util.ts"),
    ]
    assert _resolved_target(nodes, "a.b.util", packages) == "packages.ab.util"


def test_a_module_the_package_does_not_have_stays_unresolved() -> None:
    nodes = [_file("packages.lib.hooks.useLocale", "packages/lib/hooks/useLocale.ts")]
    assert _resolved_target(nodes, "@calcom.lib.hooks.gone") == "@calcom.lib.hooks.gone"


def test_an_external_package_is_left_alone() -> None:
    nodes = [_file("packages.lib.hooks.useLocale", "packages/lib/hooks/useLocale.ts")]
    assert _resolved_target(nodes, "@prisma.client") == "@prisma.client"


def test_a_python_source_is_not_rewritten() -> None:
    # The mapping is learned from package.json and scoped to the language that
    # declares it, as the Python layout prefixes are scoped the other way (#454).
    nodes = [
        _file("packages.lib.hooks.useLocale", "packages/lib/hooks/useLocale.ts"),
        _file("tools.seed", "tools/seed.py"),
    ]
    target = "@calcom.lib.hooks.useLocale"
    assert _resolved_target(nodes, target, source="tools.seed") == target


def test_without_workspace_packages_nothing_changes() -> None:
    nodes = [_file("packages.lib.hooks.useLocale", "packages/lib/hooks/useLocale.ts")]
    target = "@calcom.lib.hooks.useLocale"
    assert _resolved_target(nodes, target, packages=None) == target
