"""A TypeScript import may name a declaration file, and must reach it (#507).

`packages/types/Calendar.d.ts` is extracted as the module `packages.types.Calendar.d`
— the extractor strips the last extension only — while code imports it as
`@calcom/types/Calendar` or `./Calendar`, with no `.d`. After #506 mapped
`@calcom/types` to `packages.types`, the candidate `packages.types.Calendar` still
matched nothing, and 249 of cal.com's workspace imports stopped there.

Node ids are left as they are. Stripping `.d` from them would rename every
declaration node in existing graphs and give `foo.ts` and `foo.d.ts` one id. The
resolver instead tries each candidate as written and then as a declaration, the
order TypeScript itself resolves in: an implementation beside its declaration wins.
"""

from pathlib import Path

from cgis.core.models import Edge, EdgeType, Node, NodeNamespace, NodeType
from cgis.extractors.typescript_extractor import TypeScriptExtractor
from cgis.pipeline import IngestionPipeline
from cgis.resolver.engine import ResolverEngine

PACKAGES = {"@calcom.types": "packages.types"}


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


def _resolved(nodes: list[Node], source: Node, target: str) -> str:
    edge = Edge(
        id=f"{source.id}->import:{target}", source=source.id, target=target, type=EdgeType.IMPORTS
    )
    resolved, _ = ResolverEngine([*nodes, source], [edge], workspace_packages=PACKAGES).resolve()
    (out,) = (e for e in resolved if e.type == EdgeType.IMPORTS)
    return out.target


PAGE = _file("apps.web.page", "apps/web/page.tsx")


def test_a_workspace_import_reaches_a_declaration_module() -> None:
    nodes = [_file("packages.types.Calendar.d", "packages/types/Calendar.d.ts")]
    assert _resolved(nodes, PAGE, "@calcom.types.Calendar") == "packages.types.Calendar.d"


def test_a_relative_import_reaches_a_declaration_module() -> None:
    # The extractor has already turned `./types` into `apps.web.types`.
    nodes = [_file("apps.web.types.d", "apps/web/types.d.ts")]
    assert _resolved(nodes, PAGE, "apps.web.types") == "apps.web.types.d"


def test_an_implementation_beside_its_declaration_wins() -> None:
    nodes = [
        _file("packages.types.Calendar", "packages/types/Calendar.ts"),
        _file("packages.types.Calendar.d", "packages/types/Calendar.d.ts"),
    ]
    assert _resolved(nodes, PAGE, "@calcom.types.Calendar") == "packages.types.Calendar"


def test_a_python_import_is_not_given_a_declaration_suffix() -> None:
    seed = _file("tools.seed", "tools/seed.py")
    nodes = [_file("tools.config.d", "tools/config.d.ts")]
    assert _resolved(nodes, seed, "tools.config") == "tools.config"


def test_the_pipeline_resolves_an_import_of_a_real_declaration_file(tmp_path: Path) -> None:
    """End to end, so the `.d` in the node id comes from the extractor and not from this test."""
    files = {
        "packages/types/package.json": '{"name": "@calcom/types"}',
        "packages/types/Calendar.d.ts": "export interface Calendar {\n  id: string;\n}\n",
        "apps/web/page.ts": (
            'import type { Calendar } from "@calcom/types/Calendar";\n'
            "export function f(c: Calendar) {\n  return c.id;\n}\n"
        ),
    }
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _, _, resolved = IngestionPipeline({".ts": TypeScriptExtractor()}).run(str(tmp_path))
    (edge,) = (e for e in resolved if e.type == EdgeType.IMPORTS and e.source == "apps.web.page")
    assert edge.target == "packages.types.Calendar.d"
