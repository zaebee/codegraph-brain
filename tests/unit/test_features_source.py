"""A review row has to say which GUARDIAN_FEATURES sections its prompt was built with (#505).

The features decide whether full changed files are included and whether a module
with no impact graph falls back to its flow graph — a large difference in what
the model reads. Neither `review_fingerprint` (a digest of code) nor any other
field recorded it, so two rows with equal fingerprints, models and temperature
could have had different prompts, and a graph-vs-ablated comparison built on the
corpus could not state what its graph arm added.
"""

import json

import pytest
from pydantic import ValidationError
from test_temperature_source import REPO_ROOT, _record, _review_record_call_sites

from cgis.guardian.collector import features_setting
from cgis.guardian.martian import ReviewRecord


class TestFeaturesSetting:
    """One read gives the set and how it came to be."""

    def test_unset_is_the_default_with_no_section(self) -> None:
        assert features_setting({}) == (frozenset(), "default")

    def test_blank_is_the_default_too(self) -> None:
        assert features_setting({"GUARDIAN_FEATURES": "  "}) == (frozenset(), "default")

    def test_a_set_variable_is_env(self) -> None:
        env = {"GUARDIAN_FEATURES": "flow, full_files"}
        assert features_setting(env) == (frozenset({"flow", "full_files"}), "env")

    def test_set_to_nothing_is_still_a_choice(self) -> None:
        """`GUARDIAN_FEATURES=,` enables no section, like unset — but someone set it."""
        assert features_setting({"GUARDIAN_FEATURES": ","}) == (frozenset(), "env")

    def test_an_unknown_name_still_raises(self) -> None:
        """A typo silently disabling an ablation arm would corrupt the comparison."""
        with pytest.raises(ValueError, match="Unknown GUARDIAN_FEATURES"):
            features_setting({"GUARDIAN_FEATURES": "flwo"})


class TestUnknownIsNotEmpty:
    """A legacy row reads as *unknown*, never as the empty set (#505)."""

    def test_the_legacy_shape_still_loads_as_unknown(self) -> None:
        record = _record()
        assert record.features is None
        assert record.features_source is None

    def test_unknown_and_no_features_serialise_differently(self) -> None:
        legacy = json.loads(_record().model_dump_json())
        empty = json.loads(_record(features=[], features_source="default").model_dump_json())
        assert legacy["features"] is None
        assert empty["features"] == []

    def test_a_recorded_set_round_trips(self) -> None:
        record = _record(features=["drift", "flow"], features_source="env")
        assert ReviewRecord.model_validate_json(record.model_dump_json()) == record


class TestTheSourceIsRefusedWhenItContradictsTheSet:
    """Refuse, never repair — the same policy as the temperature pair (#393)."""

    def test_a_set_without_a_source_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="together or not at all"):
            _record(features=["flow"])

    def test_a_source_without_a_set_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="together or not at all"):
            _record(features_source="default")

    def test_default_with_sections_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="enables no section"):
            _record(features=["flow"], features_source="default")

    @pytest.mark.parametrize("features", [["flow", "drift"], ["flow", "flow"]])
    def test_an_unsorted_or_repeated_list_is_refused(self, features: list[str]) -> None:
        """Equal configurations must be equal rows, or grouping splits one arm in two."""
        with pytest.raises(ValidationError, match="sorted and unique"):
            _record(features=features, features_source="env")


def test_a_new_record_always_states_its_features() -> None:
    """Every production `ReviewRecord(...)` passes both `features` and `features_source`.

    The fields are defaulted, because every committed row predates them, so an
    omission at a call site would be silent and the row would read as unknown.
    Checked over construction sites, as the temperature source is, because the
    writer needs a worktree, an ingest and paid model passes.
    """
    sites = _review_record_call_sites()
    assert sites, "found no ReviewRecord(...) construction — a broken scan, not compliance"
    missing = [
        f"{path}:{line}"
        for path, line, kwargs in sites
        if not {"features", "features_source"} <= kwargs
    ]
    assert not missing, f"these ReviewRecord constructions do not state features: {missing}"


def test_the_committed_corpus_stays_unknown() -> None:
    """No committed row is relabelled: what a row did not record is not known now either."""
    rows = [
        json.loads(line)
        for path in sorted((REPO_ROOT / "benchmarks").rglob("*.jsonl"))
        for line in path.read_text().splitlines()
        if line.strip()
    ]
    reviews = [row for row in rows if "findings" in row and "url" in row]
    assert len(reviews) >= 115, f"found only {len(reviews)} review-shaped rows"
    labelled = [row["url"] for row in reviews if row.get("features_source") is not None]
    assert not labelled, f"committed rows now claim a features_source: {labelled[:5]}"
