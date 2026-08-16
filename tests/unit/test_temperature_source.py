"""A recorded temperature has to say whether it was chosen or inherited (#393).

`temperature: float | None` cannot carry that on its own: an absent key and an
explicit null both load as `None` and re-serialise identically, so "ran on the
provider's default" and "nothing was recorded" are the same row. 70 of the 115
committed review rows are in exactly that state, which is why a downstream
consumer could not use temperature as a covariate at all.
"""

import ast
import json
import sys
from pathlib import Path

import pytest
from conftest import IDENTITY_FIELDS
from pydantic import ValidationError

from cgis.guardian.martian import ReviewRecord

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_BASE = {
    "url": "https://github.com/o/r/pull/1",
    "project": "p",
    "pr_slice": "graph",
    "base_sha": "b",
    "head_sha": "h",
    "had_graph": True,
    "finder_model": "m",
    "skeptic_model": None,
    "findings": [],
    "prompt_tokens": 1,
    "completion_tokens": 1,
    "duration_s": 1.0,
    "parse_failed": False,
    "guardian_sha": "sha",
    "reviewed_at": "2026-08-12T00:00:00+00:00",
    **IDENTITY_FIELDS,
}


def _record(**overrides: object) -> ReviewRecord:
    return ReviewRecord(**(_BASE | overrides))  # type: ignore[arg-type]


class TestTheFloatAloneCannotCarryTheDistinction:
    """The premise of #393, asserted rather than assumed."""

    def test_an_absent_key_and_an_explicit_null_are_indistinguishable(self) -> None:
        """This is why a companion field is required, not merely preferable.

        If these two differed, a convention on the float would have sufficed and
        the whole field below would be unnecessary. They do not differ — and
        that is a property of the model, so it is checked here rather than
        described in a comment.
        """
        absent = ReviewRecord.model_validate(_BASE)
        explicit_null = ReviewRecord.model_validate(_BASE | {"temperature": None})
        assert absent.temperature is explicit_null.temperature is None
        assert absent.model_dump_json() == explicit_null.model_dump_json()


class TestTheSourceIsRefusedWhenItContradictsTheValue:
    """Refuse, never repair: there is no way to tell which half is right."""

    def test_explicit_without_a_value_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="not explicit"):
            _record(temperature=None, temperature_source="explicit")

    def test_provider_default_with_a_value_is_refused(self) -> None:
        """A value here can only have come from us, which makes it explicit."""
        with pytest.raises(ValidationError, match="not observable"):
            _record(temperature=0.7, temperature_source="provider_default")

    def test_explicit_zero_is_accepted(self) -> None:
        """0.0 is falsy and real; a truthiness check in the validator would refuse it.

        And `temperature = 0` is the one setting a phase would register
        deliberately — the temp-0 arm this field exists to make comparable — so
        refusing it would break the case that motivated the work.
        """
        assert _record(temperature=0.0, temperature_source="explicit").temperature == 0.0

    def test_the_legacy_shape_still_loads(self) -> None:
        """70 committed rows carry neither field; they must stay readable."""
        record = _record()
        assert record.temperature is None
        assert record.temperature_source is None

    def test_a_bare_value_without_a_source_still_loads(self) -> None:
        """45 committed rows carry `temperature: 0.7` and no source. Also legacy."""
        assert _record(temperature=0.7).temperature_source is None


def _review_record_call_sites() -> list[tuple[str, int, set[str]]]:
    """Every `ReviewRecord(...)` construction under src/ and scripts/, with its kwargs."""
    sites: list[tuple[str, int, set[str]]] = []
    for root in ("src", "scripts"):
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name != "ReviewRecord":
                    continue
                kwargs = {kw.arg for kw in node.keywords if kw.arg}
                sites.append((str(path.relative_to(REPO_ROOT)), node.lineno, kwargs))
    return sites


def test_a_new_record_always_states_its_temperature_source() -> None:
    """Every production `ReviewRecord(...)` must pass `temperature_source`.

    Named in the field's own docstring, which claims new records never leave it
    unset. Without this the claim would be an intention: the field is defaulted
    (it has to be — 70 committed rows predate it), so omitting it at a call site
    is silent, and the row would rejoin the population that cannot say whether
    its `None` was chosen or inherited.

    Checked over construction sites rather than by running the writer, because
    the writer needs a worktree, an ingest and two paid model passes. The floor
    is a ratchet: a call site added tomorrow is covered without editing this
    test, and an empty result cannot pass for compliance.
    """
    sites = _review_record_call_sites()
    assert len(sites) >= 1, (
        "found no ReviewRecord(...) construction under src/ or scripts/ — that is a "
        "broken scan, not a clean repository, and an empty result must not read as "
        "'every site states its source'."
    )
    missing = [
        f"{path}:{line}" for path, line, kwargs in sites if "temperature_source" not in kwargs
    ]
    assert not missing, (
        f"these ReviewRecord constructions do not state temperature_source: {missing}. "
        "A defaulted None there is indistinguishable from a legacy row."
    )


def test_the_committed_corpus_is_all_legacy_and_stays_that_way() -> None:
    """No committed row claims a source, and none contradicts itself.

    The corpora are not rewritten by #393 — a row's `None` is honest about what
    was known when it was written, and re-labelling it would invent a fact. This
    pins that: if a future backfill ever does label them, it will have to change
    this test deliberately rather than pass unnoticed.
    """
    rows = []
    for path in sorted((REPO_ROOT / "benchmarks").rglob("*.jsonl")):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if "findings" in row and "url" in row:
                rows.append((path.name, row))
    assert len(rows) >= 115, (
        f"found only {len(rows)} review-shaped rows, expected at least 115 — an empty "
        "result must not read as 'no row claims a source'."
    )
    labelled = [name for name, row in rows if row.get("temperature_source") is not None]
    assert not labelled, f"committed rows now claim a temperature_source: {sorted(set(labelled))}"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__]))
