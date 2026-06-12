"""Unit tests for the init-ontology proposer (#174)."""

from cgis.core.models import VIRTUAL_FILE_PATH, Node, NodeType
from cgis.query.ontology_init import discover_domains


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
