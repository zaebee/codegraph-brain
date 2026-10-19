"""Conformance cases for the core clauses (docs/specs/core/ontology.yml), run on every pack.

A case is a directory under tests/fixtures/conformance/<language>/<case>/: source files,
plus `expected.yml` naming the clause it checks and the edges that must and must not
exist after a full ingest (each names a `type`, CALLS when omitted). `status` says what
the case is expected to do today: `pass` must pass; `todo` and `xfail` are strict
expected failures (a case the pack does not implement yet, or a language outside a
pilot), so a case that starts passing has to be promoted rather than pass unnoticed.
`options` sets ingest options the case needs (`data_keys: true` for the opt-in DK clauses).
"""

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from cgis.core.models import Edge, EdgeType
from cgis.languages import extractors
from cgis.pipeline import IngestionPipeline

_ROOT = Path(__file__).resolve().parents[2]
_CASES = _ROOT / "tests" / "fixtures" / "conformance"
_ONTOLOGY = yaml.safe_load((_ROOT / "docs" / "specs" / "core" / "ontology.yml").read_text())
_STATUSES = {"pass", "todo", "xfail"}


def _cases() -> list[Any]:
    params = []
    for expected in sorted(_CASES.glob("*/*/expected.yml")):
        spec = yaml.safe_load(expected.read_text())
        case = expected.parent
        marks = []
        if spec["status"] != "pass":
            reason = spec.get("reason", f"{spec['clause']} not implemented for this pack yet")
            marks.append(pytest.mark.xfail(reason=reason, strict=True))
        params.append(pytest.param(case, spec, id=f"{case.parent.name}/{case.name}", marks=marks))
    return params


def _edges(case: Path, tmp_path: Path, options: dict[str, Any]) -> list[Edge]:
    tree = tmp_path / "tree"
    shutil.copytree(case, tree, ignore=shutil.ignore_patterns("expected.yml"))
    keys = bool(options.get("data_keys", False))
    pipeline = IngestionPipeline(extractors([], data_keys=keys), data_keys=keys)
    _nodes, _raw, edges = pipeline.run(str(tree))
    return list(edges)


def _matches(edge: Edge, want: dict[str, str]) -> bool:
    """Whether `edge` is the edge `want` names; `type` defaults to CALLS."""
    return (
        edge.type is EdgeType(want.get("type", "CALLS"))
        and edge.source == want["source"]
        and edge.target == want["target"]
        and want.get("context", "") in (edge.context or "")
    )


@pytest.mark.parametrize(("case", "spec"), _cases())
def test_conformance_case(case: Path, spec: dict[str, Any], tmp_path: Path) -> None:
    calls = _edges(case, tmp_path, spec.get("options", {}))
    for want in spec.get("must_have", []):
        hits = [e for e in calls if _matches(e, want)]
        assert hits, (
            f"{spec['clause']}: missing edge {want}; have {[(e.source, e.target) for e in calls]}"
        )
        assert all(e.confidence == pytest.approx(1.0) for e in hits), (
            f"{spec['clause']}: {want} scored {[e.confidence for e in hits]}, not 1.0"
        )
    for unwanted in spec.get("must_not_have", []):
        assert not [e for e in calls if _matches(e, unwanted)], (
            f"{spec['clause']}: unexpected edge {unwanted}"
        )


@pytest.mark.parametrize(
    "expected", sorted(_CASES.glob("*/*/expected.yml")), ids=lambda p: p.parent.name
)
def test_every_case_cites_a_clause_and_asserts_something(expected: Path) -> None:
    spec = yaml.safe_load(expected.read_text())
    assert spec["clause"] in _ONTOLOGY["clauses"], f"{expected}: unknown clause {spec['clause']}"
    assert spec["status"] in _STATUSES
    assert spec.get("must_have") or spec.get("must_not_have"), f"{expected}: asserts nothing"
    if spec["status"] == "xfail":
        assert spec.get("reason"), f"{expected}: an xfail case says why"
    # The data-key layer is opt-in: a DK case run without it would test nothing.
    if spec["clause"].startswith("DK-"):
        assert spec.get("options", {}).get("data_keys") is True, f"{expected}: needs data_keys"


def test_the_cases_cover_every_clause_in_every_pack_language() -> None:
    covered = {
        (p.parent.parent.name, yaml.safe_load(p.read_text())["clause"])
        for p in _CASES.glob("*/*/expected.yml")
    }
    for language in ("go", "python", "typescript"):
        for clause in _ONTOLOGY["clauses"]:
            assert (language, clause) in covered, f"no {language} case for {clause}"
