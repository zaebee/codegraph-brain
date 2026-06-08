"""Validate that docs/ontology/core.yaml stays in sync with NodeType/EdgeType enums."""

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from cgis.core.models import EdgeType, NodeType

_ONTOLOGY_PATH = Path(__file__).parents[2] / "docs" / "ontology" / "core.yaml"


@lru_cache(maxsize=1)
def _load_ontology() -> dict[str, Any]:
    data = yaml.safe_load(_ONTOLOGY_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        msg = f"Expected a dictionary from {_ONTOLOGY_PATH}, got {type(data)}"
        raise TypeError(msg)
    return data


def _yaml_node_types(ontology: dict[str, Any]) -> set[str]:
    node_types: set[str] = set()
    for values in ontology["node_types"].values():
        node_types.update(values)
    return node_types


def _yaml_edge_types(ontology: dict[str, Any]) -> set[str]:
    edge_types: set[str] = set()
    for values in ontology["edge_types"].values():
        edge_types.update(values)
    return edge_types


def test_all_python_node_types_are_in_yaml() -> None:
    ontology = _load_ontology()
    yaml_nodes = _yaml_node_types(ontology)
    python_nodes = {e.value for e in NodeType}
    missing = python_nodes - yaml_nodes
    assert not missing, (
        f"NodeType values in models.py but missing from core.yaml: {sorted(missing)}\n"
        "Add them to docs/ontology/core.yaml under the correct node_types category."
    )


def test_all_yaml_node_types_are_valid_python_enums() -> None:
    ontology = _load_ontology()
    yaml_nodes = _yaml_node_types(ontology)
    python_nodes = {e.value for e in NodeType}
    unknown = yaml_nodes - python_nodes
    assert not unknown, (
        f"node_types in core.yaml not present in NodeType enum: {sorted(unknown)}\n"
        "Either add them to NodeType in models.py or remove from core.yaml."
    )


def test_all_python_edge_types_are_in_yaml() -> None:
    ontology = _load_ontology()
    yaml_edges = _yaml_edge_types(ontology)
    python_edges = {e.value for e in EdgeType}
    missing = python_edges - yaml_edges
    assert not missing, (
        f"EdgeType values in models.py but missing from core.yaml: {sorted(missing)}\n"
        "Add them to docs/ontology/core.yaml under the correct edge_types category."
    )


def test_all_yaml_edge_types_are_valid_python_enums() -> None:
    ontology = _load_ontology()
    yaml_edges = _yaml_edge_types(ontology)
    python_edges = {e.value for e in EdgeType}
    unknown = yaml_edges - python_edges
    assert not unknown, (
        f"edge_types in core.yaml not present in EdgeType enum: {sorted(unknown)}\n"
        "Either add them to EdgeType in models.py or remove from core.yaml."
    )


def test_confidence_values_are_in_range() -> None:
    ontology = _load_ontology()
    for key, value in ontology["confidence"].items():
        assert 0.0 <= float(value) <= 1.0, (
            f"confidence.{key} = {value} is outside [0.0, 1.0] in core.yaml"
        )


def test_conventions_unresolved_prefix_is_documented() -> None:
    ontology = _load_ontology()
    prefix = ontology["conventions"]["unresolved_call_prefix"]
    assert prefix == "raw_call:", (
        f"Expected unresolved_call_prefix='raw_call:' in core.yaml, got '{prefix}'"
    )


def test_fqn_format_is_documented() -> None:
    ontology = _load_ontology()
    fqn_format = ontology["conventions"]["fqn_format"]
    # FQNs are fully dot-separated — no colon, no slash (file path is NOT part of the FQN)
    assert "." in fqn_format, (
        "core.yaml conventions.fqn_format must use dot-separated segments, e.g. "
        "'<package>.<module>.[ClassName].<symbol_name>'"
    )
    assert ":" not in fqn_format, (
        "core.yaml conventions.fqn_format must not contain a colon — "
        "FQNs use dots only, not 'file_path:ClassName.method' format."
    )
    examples = ontology["conventions"]["fqn_examples"]
    assert examples, "core.yaml conventions.fqn_examples must not be empty."
    assert all("." in ex and ":" not in ex for ex in examples), (
        "All fqn_examples in core.yaml must be dot-separated and contain no colon."
    )
