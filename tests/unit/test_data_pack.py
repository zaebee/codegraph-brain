"""JSON and YAML files declare data keys (DK-3): the `data` language pack.

A declaring file contributes a FILE node and DECLARES_KEY edges only — no symbols,
calls or impact. An OpenSearch mapping declares its properties and their `fields`
subfields, which is what decides whether `event_type.keyword` exists.
"""

import json
import re
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from cgis.api.server_instructions import SERVER_INSTRUCTIONS
from cgis.core.models import RAW_KEY_PREFIX, Edge, EdgeType, Node, NodeType
from cgis.languages import MANIFESTS, extractors, pack_for_path
from cgis.pipeline import IngestionPipeline

REPO = Path(__file__).resolve().parent.parent.parent


def _parse(code: str, path: str) -> tuple[list[Node], list[Edge]]:
    extractor = extractors([])[Path(path).suffix]
    return extractor.parse(code, path)


def _declared(edges: list[Edge]) -> dict[str, int]:
    """`<namespace>|<path>` -> line, for every DECLARES_KEY edge."""
    assert all(e.type is EdgeType.DECLARES_KEY for e in edges)
    assert all(e.target.startswith(RAW_KEY_PREFIX) for e in edges)
    return {e.target.removeprefix(RAW_KEY_PREFIX): e.line_number or 0 for e in edges}


_MAPPING = """\
{
  "settings": {"number_of_shards": 1},
  "mappings": {
    "properties": {
      "event_type": {"type": "keyword"},
      "entity_id": {
        "type": "text",
        "fields": {
          "keyword": {"type": "keyword", "ignore_above": 256}
        }
      },
      "details": {
        "type": "object",
        "properties": {
          "currency": {"type": "keyword"}
        }
      }
    }
  }
}
"""


def test_mapping_declares_property_paths_and_subfields() -> None:
    nodes, edges = _parse(_MAPPING, "search/events.json")
    declared = _declared(edges)
    assert declared == {
        "|event_type": 5,
        "|entity_id": 6,
        "|entity_id.keyword": 9,
        "|details": 12,
        "|details.currency": 15,
    }
    # `{type: keyword}` is not a `.keyword` subfield: only `fields` makes one.
    assert "|event_type.keyword" not in declared
    # The mapping's own wrapping is not data: no `settings...` or `mappings...` paths.
    assert not [k for k in declared if "settings" in k or "mappings" in k]
    [file_node] = nodes
    assert file_node.type is NodeType.FILE
    assert {e.source for e in edges} == {file_node.id}


def test_a_mapping_declaration_carries_the_property_type() -> None:
    _, edges = _parse(_MAPPING, "search/events.json")
    types = {e.target.removeprefix(RAW_KEY_PREFIX): e.context for e in edges}
    assert types == {
        "|event_type": "keyword",
        "|entity_id": "text",
        "|entity_id.keyword": "keyword",
        "|details": "object",
        "|details.currency": "keyword",
    }
    yaml_mapping = "properties:\n  meta:\n    type: nested\n  tag:\n    type: keyword\n"
    _, edges = _parse(yaml_mapping, "search/tags.yaml")
    assert {e.target: e.context for e in edges} == {
        f"{RAW_KEY_PREFIX}|meta": "object",
        f"{RAW_KEY_PREFIX}|tag": "keyword",
    }
    # A plain document has no mapping types.
    _, edges = _parse('{"a": {"type": "keyword"}}', "conf/plain.json")
    assert {e.context for e in edges} == {None}


def test_a_top_level_properties_document_is_a_mapping_too() -> None:
    code = (
        "properties:\n  status:\n    type: text\n    fields:\n      raw:\n        type: keyword\n"
    )
    _, edges = _parse(code, "index.yaml")
    assert _declared(edges) == {"|status": 2, "|status.raw": 5}


def test_mapping_namespace_from_file_name() -> None:
    _, edges = _parse(_MAPPING, "ownima-backend/opensearch/mappings/events.json")
    assert set(_declared(edges)) == {
        "events|event_type",
        "events|entity_id",
        "events|entity_id.keyword",
        "events|details",
        "events|details.currency",
    }
    # The same document anywhere else is an unlinked mapping: keys declared, unscoped.
    _, elsewhere = _parse(_MAPPING, "ownima-backend/opensearch/templates/events.json")
    assert all(k.startswith("|") for k in _declared(elsewhere))
    # The rule is for mappings: a plain document under mappings/ stays unscoped.
    _, plain = _parse('{"a": 1}', "mappings/events.json")
    assert _declared(plain) == {"|a": 1}


def test_plain_yaml_declares_unscoped_paths() -> None:
    code = "service:\n  name: api\n  ports:\n    - port: 80\n  env:\n    DEBUG: 'true'\ntop: 1\n"
    nodes, edges = _parse(code, "deploy/config.yml")
    # Every key path, dotted, at every depth; nothing below a list.
    assert _declared(edges) == {
        "|service": 1,
        "|service.name": 2,
        "|service.ports": 3,
        "|service.env": 5,
        "|service.env.DEBUG": 6,
        "|top": 7,
    }
    [file_node] = nodes
    assert file_node.language == "data"
    assert file_node.end_line == 7


def test_plain_json_declares_unscoped_paths_with_lines() -> None:
    code = '{\n  "a": {"b": [1, {"c": 2}], "d": {"e": null}},\n  "f": "x"\n}\n'
    _, edges = _parse(code, "conf/settings.json")
    assert _declared(edges) == {"|a": 2, "|a.b": 2, "|a.d": 2, "|a.d.e": 2, "|f": 3}


def test_a_multi_document_yaml_declares_each_document() -> None:
    code = "a: 1\n---\nb:\n  c: 2\n"
    _, edges = _parse(code, "k8s/app.yaml")
    assert _declared(edges) == {"|a": 1, "|b": 3, "|b.c": 4}


def test_a_list_document_declares_nothing() -> None:
    nodes, edges = _parse('[{"a": 1}]', "data/cars.json")
    assert edges == []
    assert [n.type for n in nodes] == [NodeType.FILE]


@pytest.mark.parametrize(
    "path",
    [
        "package-lock.json",
        "web/pnpm-lock.yaml",
        "web/Cargo.LOCK.json",
        "dist2/app.min.json",
        "web/package.json",
        "web/tsconfig.json",
        "web/tsconfig.build.json",
        "web/node_modules/left-pad/conf.json",
        ".github/workflows/ci.yml",
        "app/.vscode/settings.json",
    ],
)
def test_large_and_lock_files_declare_nothing(path: str) -> None:
    nodes, edges = _parse('{"name": "x", "nested": {"key": 1}}', path)
    assert edges == []
    [file_node] = nodes
    assert file_node.type is NodeType.FILE


def test_a_file_over_256_kb_declares_nothing() -> None:
    padding = "x" * (256 * 1024)
    nodes, edges = _parse(json.dumps({"a": padding}), "data/big.json")
    assert edges == []
    assert [n.type for n in nodes] == [NodeType.FILE]
    # Just under the limit is read.
    _, small = _parse(json.dumps({"a": "x" * (256 * 1024 - 20)}), "data/ok.json")
    assert _declared(small) == {"|a": 1}


def test_a_file_over_2000_keys_declares_nothing() -> None:
    many = json.dumps({f"k{i}": i for i in range(2001)})
    nodes, edges = _parse(many, "data/codes.json")
    assert edges == []
    assert [n.type for n in nodes] == [NodeType.FILE]
    enough = json.dumps({f"k{i}": i for i in range(2000)})
    assert len(_parse(enough, "data/codes.json")[1]) == 2000


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("conf/bad.yaml", "a: [1, 2\nb: {\n"),
        ("conf/bad.json", '{"a": 1,, "b": 2}'),
        ("conf/truncated.json", '{"a": {"b": '),
        ("conf/trailing.json", '{"a": 1} {"b": 2}'),
    ],
)
def test_malformed_yaml_is_skipped_not_raised(path: str, code: str) -> None:
    with capture_logs() as logs:
        nodes, edges = _parse(code, path)
    assert edges == []
    assert [n.type for n in nodes] == [NodeType.FILE]
    warnings = [entry for entry in logs if entry["log_level"] == "warning"]
    assert len(warnings) == 1, logs
    assert warnings[0]["file_path"] == path


def test_a_data_file_node_id_is_its_own_and_dotted() -> None:
    pack = pack_for_path("opensearch/mappings/events.json")
    assert pack is not None
    assert pack.name == "data"
    file_id = pack.file_node_id("app/opensearch/mappings/events.json", "app")
    assert file_id == "opensearch.mappings.events_json"
    assert pack.scope_of("app/opensearch/mappings/events.json", "app") == file_id
    # Never a Python module's id: `config.py` and `config.yaml` side by side.
    assert pack.file_node_id("config.yaml", None) == "config_yaml"
    assert pack.code_fence("a.yml") == "yaml"
    assert pack.code_fence("a.json") == "json"
    assert pack.is_test("tests/fixtures/a.json")
    assert not pack.is_test("app/a.json")


def test_an_ingest_resolves_declarations_to_key_nodes(tmp_path: Path) -> None:
    (tmp_path / "opensearch" / "mappings").mkdir(parents=True)
    (tmp_path / "opensearch" / "mappings" / "events.json").write_text(_MAPPING)
    (tmp_path / "app.py").write_text("def f(q):\n    return q['entity_id.keyword']\n")
    nodes, _raw, edges = IngestionPipeline(extractors([])).run(str(tmp_path))
    declares = {e.target for e in edges if e.type is EdgeType.DECLARES_KEY}
    assert "key:events|entity_id.keyword" in declares
    assert "key:events|event_type.keyword" not in declares
    keys = {n.id for n in nodes if n.type is NodeType.DATA_KEY}
    assert declares <= keys


def test_coverage_claims_name_the_data_pack() -> None:
    assert "data" in {m.name for m in MANIFESTS}
    coverage = next(x for x in SERVER_INSTRUCTIONS.splitlines() if x.startswith("Coverage:"))
    assert "JSON and YAML (.json, .yaml, .yml)" in coverage
    # The instructions say what these files are for: declarations, not code.
    assert re.search(
        r"JSON and YAML files contribute data key declarations only", SERVER_INSTRUCTIONS
    )
    assert "Go is supported for structure and metrics only." in SERVER_INSTRUCTIONS
    for path in ("README.md", "plugin/skills/cgis/SKILL.md"):
        text = (REPO / path).read_text(encoding="utf-8")
        assert "JSON and YAML" in text, path
        assert "key declarations only" in text, path


def _boundary_namespaces(root: Path) -> dict[str, str]:
    nodes, _raw, _edges = IngestionPipeline(extractors([])).run(str(root))
    return {
        n.id: n.namespace.value
        for n in nodes
        if n.file_path == "EXTERNAL" and n.type is not NodeType.DATA_KEY
    }


def test_a_data_directory_changes_no_name_classification(tmp_path: Path) -> None:
    """`data/x.json` must not make `data` an internal root: `import data` stays a library's.

    The control is the same tree without the JSON file, which is what main reads.
    """
    code = (
        "import data\n\n"
        "def f(request):\n"
        "    payload = request.json()\n"
        "    return data.load() or payload.get('x') or config.get('y')\n"
    )
    for name in ("without", "with"):
        (tmp_path / name / "pkg").mkdir(parents=True)
        (tmp_path / name / "pkg" / "app.py").write_text(code)
    (tmp_path / "with" / "data").mkdir()
    (tmp_path / "with" / "data" / "x.json").write_text('{"a": 1}')
    (tmp_path / "with" / "config.yaml").write_text("y: 1\n")
    control = _boundary_namespaces(tmp_path / "without")
    assert control["data.load"] == "EXTERNAL"  # the case the data directory once flipped
    assert _boundary_namespaces(tmp_path / "with") == control
