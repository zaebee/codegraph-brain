"""The bench records the reviewer that produced each scored row (#390).

`scripts/guardian_bench.py` writes `benchmarks/guardian/results.jsonl`, which
`scripts/guardian_calibrate.py` then grades. Without an identity on the results
row there is nothing for the calibration record to carry, so this is the source
end of the only measured recall the repository has.
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import ClassVar

import pytest
from guardian_stubs import StubProvider

from cgis.guardian.bench import GroundTruth
from cgis.guardian.findings import ReviewResult
from cgis.guardian.review_fingerprint import (
    compute_fingerprint,
    disk_reader,
    resolve_active_providers,
)

# This repository sets no pytest `pythonpath`, so every test that imports a
# script does this explicitly (test_guardian_calibrate_script.py:27 and five
# others). Required, not optional.
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

import guardian_bench as gb

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_TRUTH = GroundTruth.model_validate(
    {"pr": 900, "base": "aaa", "head": "bbb", "findings": [], "ambiguous": []}
)


class MistralStub(StubProvider):
    """A provider on the other vendor, so the active-provider set actually widens."""

    name: ClassVar[str] = "mistral"


class MisleadingStub(StubProvider):
    """A provider whose `name` disagrees with its model's entry in the model table.

    `backfill_review_fingerprint.MODEL_PROVIDERS` maps `gemini-2.5-flash` to
    `gemini`; this runs that model on a provider that calls itself `ollama`,
    which is exactly the case a local Ollama serving a gemini-named model
    produces. Without it, every stub's `name` coincides with what the model
    table would return, and a regression from `provider.name` back to a model
    lookup — the thing `finder_provider` was added in #375 to make unnecessary —
    is invisible.
    """

    name: ClassVar[str] = "ollama"


async def _record(
    results: Path,
    skeptic: tuple[StubProvider, str] | None,
    provider: StubProvider | None = None,
) -> dict[str, object]:
    """Run one `_score_and_record` and return the row it appended."""
    await gb._score_and_record(  # noqa: SLF001
        _TRUTH,
        0,
        results,
        ReviewResult(findings=[], summary=""),
        "gemini-2.5-flash",
        provider or StubProvider([]),
        skeptic,
        chunks=None,
    )
    # Read via asyncio.to_thread: ruff ASYNC240 bans blocking pathlib inside an
    # async function, and the rule is right — this is the same reason
    # `_score_and_record` appends through `asyncio.to_thread`.
    text = await asyncio.to_thread(results.read_text)
    return json.loads(text.splitlines()[-1])


@pytest.mark.asyncio
async def test_a_scored_row_names_its_reviewer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Measured, not reconstructed, and taken over `_REPO_ROOT` specifically.

    The value is compared against a digest computed here over `REPO_ROOT`
    rather than merely checked for length — a 12-character string is what a
    fingerprint over *any* tree looks like.

    The `chdir` is what gives that comparison teeth. pytest runs from the
    repository root, so `Path.cwd()` and `_REPO_ROOT` are the same directory and
    an implementation that hashed the current directory passed this test
    unchanged. Moving the process elsewhere first separates them.
    """
    monkeypatch.chdir(tmp_path)
    row = await _record(tmp_path / "results.jsonl", skeptic=None)
    expected = compute_fingerprint(disk_reader(REPO_ROOT), resolve_active_providers("gemini", None))
    assert row["review_fingerprint"] == expected
    assert row["review_fingerprint_source"] == "measured"
    assert row["finder_provider"] == "gemini"
    assert row["skeptic_provider"] is None


@pytest.mark.asyncio
async def test_the_provider_names_itself_rather_than_being_looked_up(tmp_path: Path) -> None:
    """`finder_provider` comes from `BaseProvider.name`, not from the model name.

    `MisleadingStub` runs the model `gemini-2.5-flash` — which
    `MODEL_PROVIDERS` maps to `gemini` — on a provider that calls itself
    `ollama`. Every other stub in this file has a `name` that coincides with
    what the model table would return, so without this case a regression from
    `provider.name` to a model lookup is invisible, and #375 added
    `finder_provider` precisely because a model name does not always carry its
    vendor.

    The digest must move too: `ollama` and `gemini` are different modules in the
    closure, so this is a different reviewer, not just a different label.
    """
    labelled = await _record(tmp_path / "a.jsonl", skeptic=None)
    misleading = await _record(tmp_path / "b.jsonl", skeptic=None, provider=MisleadingStub([]))
    assert misleading["finder_provider"] == "ollama"
    assert misleading["model"] == "gemini-2.5-flash"
    assert misleading["review_fingerprint"] != labelled["review_fingerprint"]


@pytest.mark.asyncio
async def test_a_cross_provider_skeptic_is_a_different_reviewer(tmp_path: Path) -> None:
    """A skeptic on the other vendor widens the closure, so the digest must move.

    Asserted as a difference against the no-skeptic row rather than as a fixed
    value: a hard-coded digest would have to be updated on every guardian
    change and would then be pinning the corpus, not the behaviour.
    """
    alone = await _record(tmp_path / "a.jsonl", skeptic=None)
    paired = await _record(tmp_path / "b.jsonl", skeptic=(MistralStub([]), "mistral-medium-latest"))
    assert paired["skeptic_provider"] == "mistral"
    assert paired["skeptic_model"] == "mistral-medium-latest"
    assert paired["review_fingerprint"] != alone["review_fingerprint"]


@pytest.mark.asyncio
async def test_a_same_provider_skeptic_keeps_the_finders_identity(tmp_path: Path) -> None:
    """Same vendor adds nothing to the active set, so the digest must not move.

    The mirror of the test above, and the invariant that explains why two
    identities in the committed corpus each span two provider pairs (#375 spec
    §3.3). Without this half, a change that made every skeptic widen the set
    would pass the difference test above while silently splitting every
    existing identity in two.
    """
    alone = await _record(tmp_path / "a.jsonl", skeptic=None)
    paired = await _record(tmp_path / "b.jsonl", skeptic=(StubProvider([]), "gemini-3.5-flash"))
    assert paired["skeptic_provider"] == "gemini"
    assert paired["review_fingerprint"] == alone["review_fingerprint"]


class UnknownVendorStub(StubProvider):
    """A provider whose name matches no module in the closure.

    This is what the first provider added after today looks like to
    `resolve_active_providers`, which raises `UnknownProviderError` on a name it
    cannot place — deliberately, since silently pruning the provider layer would
    return a confident wrong digest (#375).
    """

    name: ClassVar[str] = "cohere"


@pytest.mark.asyncio
async def test_a_row_survives_a_fingerprint_that_cannot_be_taken(tmp_path: Path) -> None:
    """Failing to name the reviewer must not destroy the review.

    By the time the fingerprint is computed the row has cost a worktree
    checkout, a `cgis ingest`, a paid finder pass and a paid skeptic pass.
    Computing it inside the row literal let `UnknownProviderError` propagate to
    `main`'s per-PR `except`, which discarded the review and wrote an
    `{"error": ...}` row instead — indistinguishable from a rate-limit failure,
    and therefore dropped from the recall corpus permanently and silently.

    The assertions below are what separate "survived" from "survived as
    garbage": the scored measurement must still be there, and the identity must
    be null rather than invented.
    """
    row = await _record(tmp_path / "results.jsonl", skeptic=None, provider=UnknownVendorStub([]))
    assert row["review_fingerprint"] is None
    assert row["review_fingerprint_source"] is None
    assert row["finder_provider"] == "cohere"
    # The measurement itself is intact — this is the row the recall corpus needs.
    assert "matched" in row
    assert "precision" in row
    assert "error" not in row


# --- budget (#246 B) -------------------------------------------------------

from run_budget import Budget, Price, make_budget  # noqa: E402

from cgis.guardian.providers.base import ProviderUsage  # noqa: E402

_FLAT = Price(1.0, 10.0)
_TRUTH = GroundTruth(pr=1, base="a", head="b", findings=[])


def _spending_run(calls_per_run: int, *, fail: bool = False):  # noqa: ANN202
    """A `run_one` stand-in that records `calls_per_run` calls of 1000 in / 100 out."""
    ran: list[int] = []

    async def run_one(
        _truth: GroundTruth, run_idx: int, spent: list[tuple[str, list[ProviderUsage]]]
    ) -> None:
        provider = StubProvider([])
        spent.append((provider.name, provider.call_usages))
        for _ in range(calls_per_run):
            provider._record_usage(ProviderUsage(prompt_tokens=1000, completion_tokens=100))  # noqa: SLF001
        ran.append(run_idx)
        if fail:
            _msg = "ingest failed"
            raise RuntimeError(_msg)

    return run_one, ran


async def test_every_run_is_charged_and_the_next_one_waits_for_the_cap(tmp_path: Path) -> None:
    """Each stubbed run costs $0.002; a $0.003 cap allows two of five, then stops."""
    run_one, ran = _spending_run(1)
    budget = Budget(cap_usd=0.003, prices={"gemini": _FLAT})
    code = await gb.run_all([_TRUTH], 5, run_one, tmp_path / "r.jsonl", budget)
    assert code == 3
    assert ran == [0, 1]
    assert budget.stopped
    assert budget.spent_usd == pytest.approx(0.004)


async def test_a_failed_run_is_still_charged(tmp_path: Path) -> None:
    """The money was spent before the exception; the cap must see it."""
    run_one, ran = _spending_run(2, fail=True)
    budget = Budget(cap_usd=1.0, prices={"gemini": _FLAT})
    code = await gb.run_all([_TRUTH], 2, run_one, tmp_path / "r.jsonl", budget)
    assert code == 0
    assert ran == [0, 1]
    assert budget.spent_usd == pytest.approx(0.008)
    rows = [json.loads(line) for line in (tmp_path / "r.jsonl").read_text().splitlines()]
    assert [r["error"] for r in rows] == ["ingest failed", "ingest failed"]


async def test_reaching_the_cap_on_the_last_run_is_a_complete_run(tmp_path: Path) -> None:
    run_one, ran = _spending_run(1)
    budget = Budget(cap_usd=0.004, prices={"gemini": _FLAT})
    assert await gb.run_all([_TRUTH], 2, run_one, tmp_path / "r.jsonl", budget) == 0
    assert ran == [0, 1]
    assert not budget.stopped


async def test_no_budget_runs_everything(tmp_path: Path) -> None:
    run_one, ran = _spending_run(1)
    assert await gb.run_all([_TRUTH], 3, run_one, tmp_path / "r.jsonl", None) == 0
    assert ran == [0, 1, 2]


@pytest.mark.parametrize(
    ("cap", "items", "names", "message"),
    [
        (1.0, [], ["anthropic"], "missing: ['anthropic']"),
        (None, ["gemini=0.3:2.5"], ["gemini"], "only means something with --budget-usd"),
        (1.0, ["gemini=0.3"], ["gemini"], "expected ARM=IN:OUT"),
    ],
)
def test_an_unpriceable_cap_is_refused(
    cap: float | None, items: list[str], names: list[str], message: str
) -> None:
    with pytest.raises(ValueError, match=message.replace("[", r"\[").replace("]", r"\]")):
        make_budget(cap, items, names)


def test_the_providers_a_run_will_charge_are_named_from_the_environment() -> None:
    env = {"GUARDIAN_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "k", "GUARDIAN_SKEPTIC": "off"}
    assert gb.priced_names(env, record_finder=False) == ["anthropic"]
    env = {**env, "GUARDIAN_SKEPTIC": "gemini", "GEMINI_API_KEY": "g"}
    assert gb.priced_names(env, record_finder=False) == ["anthropic", "gemini"]
    # A recorded finder pass skips the skeptic, so it is never charged.
    assert gb.priced_names(env, record_finder=True) == ["anthropic"]
