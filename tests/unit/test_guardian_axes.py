"""Unit tests for per-axis review fan-out (#331)."""

import json
from pathlib import Path

import pytest
from guardian_stubs import BoomProvider, StubProvider
from pydantic import BaseModel

from cgis.guardian.axes import run_axis_review
from cgis.guardian.collector import ContextCollector
from cgis.guardian.prompts import (
    AXIS_GROUPS,
    FOCUS_AREAS,
    PromptBuilder,
    render_focus_areas,
    render_group,
)


def _finding(file: str, line: int, category: str = "logic", confidence: int = 80) -> dict:
    return {
        "file": file,
        "line": line,
        "severity": "major",
        "category": category,
        "title": "t",
        "evidence": "e",
        "problem": "p",
        "fix": "f",
        "confidence": confidence,
    }


def _response(findings: list[dict], summary: str = "s") -> str:
    return json.dumps({"findings": findings, "summary": summary})


@pytest.fixture
def collector(tmp_path: Path) -> ContextCollector:
    """A collector over an empty repo — the diff content is irrelevant here."""
    return ContextCollector(project_root=tmp_path, db_path=None, base_ref="main")


# ---------------------------------------------------------------------------
# The prompt split must not change the single-prompt behaviour
# ---------------------------------------------------------------------------


def test_rendering_every_axis_reproduces_one_blob() -> None:
    """Joining the mapping is what the inline block used to be."""
    joined = render_focus_areas()

    for text in FOCUS_AREAS.values():
        assert text in joined


def test_a_single_axis_excludes_the_others() -> None:
    """The whole point: an axis prompt must not mention its competitors."""
    only_logic = render_focus_areas("logic")

    assert FOCUS_AREAS["logic"] in only_logic
    assert FOCUS_AREAS["float_equality"] not in only_logic


def test_focus_group_context_key_narrows_the_prompt() -> None:
    """`focus_group` travels through the context dict, like every other toggle."""
    ctx = {"diff": "D"}
    full = PromptBuilder().build_user_prompt(ctx)
    narrowed = PromptBuilder().build_user_prompt({**ctx, "focus_group": "ontology"})

    assert FOCUS_AREAS["ontology"] in narrowed
    assert FOCUS_AREAS["logic"] not in narrowed
    assert FOCUS_AREAS["logic"] in full


# ---------------------------------------------------------------------------
# Fan-out behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_finder_call_per_axis(collector: ContextCollector) -> None:
    """Each axis gets its own call, and each call carries only its own axis."""
    provider = StubProvider([_response([]) for _ in FOCUS_AREAS])

    await run_axis_review(provider=provider, collector=collector, skeptic_provider=None)

    assert len(provider.prompts) == len(FOCUS_AREAS)
    # Every axis text appears exactly once across the whole fan-out.
    for text in FOCUS_AREAS.values():
        assert sum(text in prompt for prompt in provider.prompts) == 1


@pytest.mark.asyncio
async def test_findings_from_different_axes_are_merged(collector: ContextCollector) -> None:
    """The union is what the reviewer returns."""
    responses = [_response([_finding("a.py", i + 1)]) for i in range(len(FOCUS_AREAS))]
    provider = StubProvider(responses)

    result = await run_axis_review(provider=provider, collector=collector, skeptic_provider=None)

    assert len(result.findings) == len(FOCUS_AREAS)


@pytest.mark.asyncio
async def test_same_line_from_two_axes_is_deduped(collector: ContextCollector) -> None:
    """Axes see the same code, so collisions are expected rather than exceptional.

    The higher-confidence duplicate wins, matching dedup_findings' contract.
    """
    responses = (
        [_response([_finding("a.py", 7, confidence=60)])]
        + [_response([_finding("a.py", 7, confidence=95)])]
        + [_response([]) for _ in range(len(FOCUS_AREAS) - 2)]
    )
    provider = StubProvider(responses)

    result = await run_axis_review(provider=provider, collector=collector, skeptic_provider=None)

    assert len(result.findings) == 1
    assert result.findings[0].confidence == 95


@pytest.mark.asyncio
async def test_one_failing_axis_costs_only_that_axis(collector: ContextCollector) -> None:
    """A flaky call must not take the review down with it."""

    class OneBadAxis(StubProvider):
        async def generate_structured(
            self, system_prompt: str, user_prompt: str, schema: type[BaseModel]
        ) -> str:
            if FOCUS_AREAS["ontology"] in user_prompt:
                msg = "transport blew up"
                raise RuntimeError(msg)
            return await super().generate_structured(system_prompt, user_prompt, schema)

    provider = OneBadAxis([_response([_finding("a.py", 1)]) for _ in FOCUS_AREAS])

    result = await run_axis_review(provider=provider, collector=collector, skeptic_provider=None)

    assert not result.parse_failed
    assert len(result.findings) == 1  # deduped to the one distinct (file, line, category)


@pytest.mark.asyncio
async def test_every_axis_failing_is_reported_as_a_failure(collector: ContextCollector) -> None:
    """Total failure must not read as a clean review."""
    result = await run_axis_review(
        provider=BoomProvider(), collector=collector, skeptic_provider=None
    )

    assert result.parse_failed
    assert result.findings == []


@pytest.mark.asyncio
async def test_skeptic_runs_once_over_the_union(collector: ContextCollector) -> None:
    """Not once per axis: judging is the larger half of the bill already."""
    finder = StubProvider([_response([_finding("a.py", i + 1)]) for i in range(len(FOCUS_AREAS))])
    verdicts = json.dumps({"verdict": "confirmed", "note": "n", "impact_score": 5})
    skeptic = StubProvider([verdicts] * len(FOCUS_AREAS))

    result = await run_axis_review(provider=finder, collector=collector, skeptic_provider=skeptic)

    assert result.skeptic_total == len(FOCUS_AREAS)
    assert len(skeptic.prompts) == len(FOCUS_AREAS)


# ---------------------------------------------------------------------------
# Kinship grouping (#331 follow-up)
# ---------------------------------------------------------------------------


def test_groups_partition_every_axis_exactly_once() -> None:
    """A missing axis would silently stop being reviewed; a duplicated one doubles noise."""
    covered = [axis for axes in AXIS_GROUPS.values() for axis in axes]

    assert sorted(covered) == sorted(FOCUS_AREAS)


def test_a_group_renders_only_its_own_axes() -> None:
    """Grouping is only worth anything if the batches really are separate."""
    correctness = render_group(AXIS_GROUPS["correctness"])

    assert FOCUS_AREAS["logic"] in correctness
    assert FOCUS_AREAS["ontology"] not in correctness


@pytest.mark.asyncio
async def test_paired_flag_makes_two_calls_not_seven(tmp_path: Path) -> None:
    """The whole point of the follow-up: noise scaled with call count, so cut calls."""
    collector = ContextCollector(
        project_root=tmp_path, db_path=None, base_ref="main", features=frozenset({"axes_paired"})
    )
    provider = StubProvider([_response([]) for _ in AXIS_GROUPS])

    await run_axis_review(provider=provider, collector=collector, skeptic_provider=None)

    assert len(provider.prompts) == len(AXIS_GROUPS) == 2
