"""Recovering the valid prefix of a truncated finder response (#248).

The recall-lean finder emits 15 findings per PR on real diffs, and a longer JSON
body is likelier to be cut off mid-object. When that happened in the Phase 3 run
the whole review was discarded: eighteen findings were produced, fifteen of them
parsed cleanly, and all eighteen were thrown away because the sixteenth was
missing a field and the seventeenth was a bare string fragment.

Discarding is the wrong default in production, where fifteen real findings beat
none. It stays the right default for the benchmark, and that split is deliberate:
salvaging is a rescue, not a measurement, so a salvaged review is still flagged
`parse_failed` and the bench's exclusion rule still drops it.
"""

import json
from typing import ClassVar

import pytest
from pydantic import BaseModel

from cgis.guardian.core import GuardianReviewer, finder_pass
from cgis.guardian.findings import salvage_findings
from cgis.guardian.providers.base import BaseProvider
from cgis.guardian.skeptic import visible_findings

_GOOD = {
    "file": "a.py",
    "line": 3,
    "severity": "major",
    "category": "logic",
    "title": "t",
    "evidence": "e",
    "problem": "p",
    "fix": "f",
    "confidence": 70,
}


def _body(*findings: object, closed: bool = True) -> str:
    """A finder payload, optionally truncated mid-array like a cut-off response."""
    inner = ", ".join(json.dumps(f) for f in findings)
    text = '{"summary": "s", "findings": [' + inner
    return text + "]}" if closed else text


class TestSalvageFindings:
    """What survives when the strict parse does not."""

    def test_a_complete_body_yields_every_finding(self) -> None:
        assert len(salvage_findings(_body(_GOOD, _GOOD))) == 2

    def test_a_body_cut_off_mid_object_keeps_the_prefix(self) -> None:
        """The real failure: the array is severed part-way through an object."""
        truncated = _body(_GOOD, _GOOD, closed=False) + ', {"file": "b.py", "sev'

        assert len(salvage_findings(truncated)) == 2

    def test_an_invalid_object_is_skipped_and_the_rest_survive(self) -> None:
        """`findings.15.confidence` missing was the observed case, mid-array."""
        missing_confidence = {k: v for k, v in _GOOD.items() if k != "confidence"}

        recovered = salvage_findings(_body(_GOOD, missing_confidence, _GOOD))

        assert len(recovered) == 2

    def test_a_string_fragment_among_the_objects_is_skipped(self) -> None:
        """The observed tail: `findings.16` decoded as a bare string."""
        recovered = salvage_findings(_body(_GOOD, "button` to the button an", _GOOD))

        assert len(recovered) == 2

    def test_a_missing_findings_key_salvages_nothing(self) -> None:
        assert salvage_findings('{"summary": "s"}') == []

    def test_prose_that_is_not_json_salvages_nothing(self) -> None:
        """A refusal or an apology must not be mined for fragments."""
        assert salvage_findings("I could not review this diff.") == []

    def test_a_fenced_body_is_unwrapped_first(self) -> None:
        """Providers wrap JSON in markdown fences; `extract_json` handles it."""
        fenced = "```json\n" + _body(_GOOD) + "\n```"

        assert len(salvage_findings(fenced)) == 1

    def test_the_truncation_point_does_not_produce_a_partial_finding(self) -> None:
        """A half-decoded object must not become a `Finding` with defaults.

        Silently filling `confidence` or `fix` would turn a transport failure
        into a plausible-looking review comment.
        """
        half = _body(_GOOD, closed=False) + ', {"file": "b.py", "severity": "maj'

        recovered = salvage_findings(half)

        assert [f.file for f in recovered] == ["a.py"]

    @pytest.mark.parametrize("payload", ["", "[]", "{}", "null"])
    def test_degenerate_payloads_are_empty_rather_than_raising(self, payload: str) -> None:
        """Salvage runs on the failure path and must never add a failure of its own."""
        assert salvage_findings(payload) == []


class TestFinderPassSalvage:
    """The finder's second failure recovers what it can instead of discarding it."""

    @pytest.mark.asyncio
    async def test_a_twice_failing_parse_still_returns_the_valid_prefix(self) -> None:
        """Fifteen of eighteen were lost this way on one PR of the Phase 3 run."""
        truncated = _body(_GOOD, _GOOD, closed=False) + ', {"file": "b.py", "sev'
        provider = _AlwaysReturns(truncated)

        result = await finder_pass(provider, {"diff": "d"})

        assert len(result.findings) == 2
        assert result.parse_failed is True

    @pytest.mark.asyncio
    async def test_parse_failed_stays_set_so_the_bench_still_excludes_it(self) -> None:
        """A rescue is not a measurement: the recovered set is smaller than reality."""
        provider = _AlwaysReturns(_body(_GOOD, closed=False) + ", {")

        result = await finder_pass(provider, {"diff": "d"})

        assert result.parse_failed is True

    @pytest.mark.asyncio
    async def test_unsalvageable_output_still_degrades_to_empty(self) -> None:
        """Prose, a refusal, an error page — nothing to mine, and no crash."""
        provider = _AlwaysReturns("I cannot review this.")

        result = await finder_pass(provider, {"diff": "d"})

        assert result.findings == []
        assert result.parse_failed is True

    @pytest.mark.asyncio
    async def test_a_valid_response_never_reaches_the_salvage_path(self) -> None:
        provider = _AlwaysReturns(_body(_GOOD))

        result = await finder_pass(provider, {"diff": "d"})

        assert len(result.findings) == 1
        assert result.parse_failed is False


class _AlwaysReturns(BaseProvider):
    """A provider that answers every call with the same canned text."""

    name: ClassVar[str] = "gemini"

    def __init__(self, text: str) -> None:
        """Store the text this provider will return for any prompt."""
        super().__init__()
        self._text = text
        self.calls = 0

    async def generate_content(self, system_prompt: str, user_prompt: str) -> str:
        """Return the canned text."""
        del system_prompt, user_prompt
        self.calls += 1
        return self._text

    async def generate_structured(
        self, system_prompt: str, user_prompt: str, schema: type[BaseModel]
    ) -> str:
        """Return the canned text, ignoring the schema."""
        del system_prompt, user_prompt, schema
        self.calls += 1
        return self._text


class TestSalvagedFindingsAreStillJudged:
    """Salvaged findings go through the skeptic like any others.

    The skip on `parse_failed` was free when it implied an empty list. Now that a
    failed parse can carry findings, skipping would ship them unfiltered — trading
    the old loss of findings for a new gain in noise.
    """

    @pytest.mark.asyncio
    async def test_the_skeptic_runs_over_a_salvaged_review(self) -> None:
        truncated = _body(_GOOD, _GOOD, closed=False) + ', {"file": "b.py", "sev'
        skeptic = _AlwaysReturns('{"verdict": "refuted", "impact_score": 1, "rationale": "no"}')
        reviewer = GuardianReviewer(
            provider=_AlwaysReturns(truncated),
            context_collector=_StubCollector(),
            skeptic_provider=skeptic,
        )

        result = await reviewer.run_review()

        assert result.parse_failed is True
        assert skeptic.calls > 0
        assert result.skeptic_total == 2

    @pytest.mark.asyncio
    async def test_an_unsalvageable_review_still_skips_the_skeptic(self) -> None:
        """No findings, nothing to judge, and no call worth paying for."""
        skeptic = _AlwaysReturns("{}")
        reviewer = GuardianReviewer(
            provider=_AlwaysReturns("I cannot review this."),
            context_collector=_StubCollector(),
            skeptic_provider=skeptic,
        )

        result = await reviewer.run_review()

        assert result.findings == []
        assert skeptic.calls == 0


class _StubCollector:
    """Minimal stand-in for ContextCollector."""

    def collect_all(self) -> dict[str, str]:
        """Return a context with just a diff."""
        return {"diff": "d"}


class TestSalvagedFindingsAreSanitized:
    """Salvaged findings must have the skeptic's fields reset, like parsed ones.

    `ReviewResult` doubles as the finder's output schema, so the model sees
    `verdict`, `skeptic_note` and `impact_score` and sometimes fills them in.
    `visible_findings` hides anything with `verdict == "refuted"` — so a
    hallucinated verdict silently deletes a real finding from the report.

    The strict path has always run `_sanitize_finder_result` for exactly this
    reason. The salvage path did not, and a truncated response is if anything a
    likelier place for the model to have been filling fields loosely.
    """

    @pytest.mark.asyncio
    async def test_a_hallucinated_verdict_cannot_silence_a_salvaged_finding(self) -> None:
        refuted = _GOOD | {"verdict": "refuted"}
        provider = _AlwaysReturns(_body(refuted, closed=False) + ', {"file": "b')

        result = await finder_pass(provider, {"diff": "d"})

        assert len(result.findings) == 1
        assert result.findings[0].verdict is None
        assert visible_findings(result.findings) == result.findings

    @pytest.mark.asyncio
    async def test_a_hallucinated_impact_score_is_cleared(self) -> None:
        """Otherwise the finding hides behind an impact threshold it never earned."""
        scored = _GOOD | {"impact_score": 0}
        provider = _AlwaysReturns(_body(scored, closed=False) + ", {")

        result = await finder_pass(provider, {"diff": "d"})

        assert result.findings[0].impact_score is None

    @pytest.mark.asyncio
    async def test_the_skeptic_counters_are_not_taken_from_the_model(self) -> None:
        provider = _AlwaysReturns(_body(_GOOD, closed=False) + ", {")

        result = await finder_pass(provider, {"diff": "d"})

        assert (result.skeptic_status, result.skeptic_judged, result.skeptic_total) == ("off", 0, 0)

    @pytest.mark.asyncio
    async def test_sanitising_does_not_clear_the_parse_failure(self) -> None:
        """`_sanitize_finder_result` copies the result, and the flag must survive."""
        provider = _AlwaysReturns(_body(_GOOD, closed=False) + ", {")

        assert (await finder_pass(provider, {"diff": "d"})).parse_failed is True


class TestSalvageNeverRaises:
    """The failure path must not manufacture a worse failure.

    `json.raw_decode` recurses once per nested opener and raises `RecursionError`
    — a `RuntimeError`, not a `ValueError` — when the input nests too deeply. The
    loop caught only `ValueError`, so a degenerate run of unmatched brackets
    escaped `salvage_findings` entirely.

    That mattered because the default review path is unguarded:
    `chunked._single_pass` calls `run_review` with no `try`, and
    `scripts/guardian_review.py` calls `run_guardian` with no `try`, so the
    exception would reach `asyncio.run` and end the run. A truncated response —
    exactly what this function exists to survive — would have crashed the CLI
    instead of degrading to `parse_failed`, which is worse than the discard-all
    behaviour it replaced.
    """

    def test_a_deeply_nested_run_of_openers_returns_empty(self) -> None:
        """Bracket repetition is a known degenerate LLM output under sampling."""
        assert salvage_findings('{"findings": [' + "[" * 60_000) == []

    def test_deep_nesting_after_a_valid_finding_keeps_the_prefix(self) -> None:
        """The recursion limit is a truncation point like any other."""
        payload = _body(_GOOD, closed=False) + ", " + "[" * 60_000

        assert len(salvage_findings(payload)) == 1

    def test_deeply_nested_objects_are_survived_too(self) -> None:
        """Objects recurse the same way arrays do."""
        assert salvage_findings('{"findings": [' + '{"a":' * 60_000) == []
