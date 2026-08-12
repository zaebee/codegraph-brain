"""The Martian Code Review Bench corpus, as this project consumes it (#342 Phase 2).

Loading, filtering and slicing only — no network, no LLM, no git. Everything
here is pure over the vendored JSON in `benchmarks/martian/`, so the population
a gate is computed on can be asserted in a unit test rather than trusted.

That separation is deliberate. Phase 1's first write-up was falsified because a
silent population filter decided both gate verdicts (retraction R1), so in Phase
2 the population is a value with a name, built by tested code, before anything
is spent.
"""

import json
import re
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from cgis.extractors.registry import is_supported

#: Severity vocabulary of the upstream corpus. Four values, not three — the
#: spec's §"Ground truth format" omits `Critical` (12 of the 173 comments).
Severity = Literal["Critical", "High", "Medium", "Low"]

#: The three scoring profiles, as a type rather than a bare string, so a typo
#: is a type error instead of a KeyError at report time.
Profile = Literal["strict", "core", "all"]

#: Owner, repo and number out of a PR URL. A regex rather than splitting on "/"
#: and indexing from the end: the corpus's URLs are all plain today, but
#: `.../pull/123/files` or `?w=1` would silently shift the fields under the
#: index form, and `number` would fail with an `int()` ValueError naming
#: nothing useful.
_PR_URL = re.compile(r"github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)")

#: Verbatim from `offline/analysis/score_profiles.py::PROFILE_CATEGORIES` (MIT).
#:
#: The spec says "Severity drives the three profiles". **It does not** — the
#: profiles are *category* filters, and severity plays no part. The arithmetic
#: refuses the other reading: Critical+High+Medium is 127 comments, and `core`
#: is 158. Filtering by severity would have produced Guardian numbers computed
#: over a different 158 comments than the leaderboard's, silently incomparable
#: — the precise category error this whole spec exists to prevent. Caught by
#: `test_profile_totals_match_upstreams_published_ones`, then confirmed against
#: upstream source.
PROFILE_CATEGORIES: dict[Profile, frozenset[str]] = {
    "strict": frozenset({"bug", "security", "concurrency", "data", "api"}),
    "core": frozenset(
        {"bug", "security", "concurrency", "data", "api", "perf", "test_gap", "doc_defect"}
    ),
    "all": frozenset(
        {
            "bug",
            "security",
            "concurrency",
            "data",
            "api",
            "perf",
            "test_gap",
            "doc_defect",
            "style",
            "speculative",
        }
    ),
}

#: The dashboard default, and the basis for every leaderboard number in the
#: spec — a Guardian figure placed beside theirs has to be on the same profile.
DEFAULT_PROFILE: Profile = "core"


class GoldenComment(BaseModel, frozen=True):
    """One human-verified defect the corpus expects a reviewer to find."""

    comment: str
    severity: Severity
    category: str


class BenchPr(BaseModel, frozen=True):
    """One benchmark PR and the golden comments written against it."""

    project: str
    pr_title: str
    url: str
    comments: list[GoldenComment]
    #: Upstream's own note about the entry. Non-null means the corpus is telling
    #: us the reviewed state cannot be reconstructed — see `is_reproducible`.
    az_comment: str | None = None
    #: Paths the PR touches, filled in by the planner from `gh pr diff
    #: --name-only`. Empty until then; `slice_of` needs it.
    changed_files: tuple[str, ...] = Field(default_factory=tuple)

    @property
    def is_reproducible(self) -> bool:
        """Whether the reviewed state can be reconstructed at all.

        Five of the fifty carry an `az_comment`: four say "reviewed commit is
        not in the repo" and one says "there is no such PR, it is a mix of many
        PRs". `gh pr diff` still returns *a* diff for them, which is exactly the
        trap — it is a different state from the one the golden comments were
        written against, so scoring it would charge Guardian for defects that
        are not in the code it was shown, and credit it for ones nobody
        reviewed. They are excluded and reported as excluded.
        """
        return self.az_comment is None

    def _parsed_url(self) -> re.Match[str]:
        """The URL's owner/repo/number, or a failure that names the URL."""
        match = _PR_URL.search(self.url)
        if match is None:
            _msg = f"Not a GitHub PR URL: {self.url!r}"
            raise ValueError(_msg)
        return match

    @property
    def repo(self) -> str:
        """`owner/name` from the PR URL.

        Not always the upstream project: 15 of the 50 live in the benchmark's
        own fork org `ai-code-review-evaluation`, discourse entirely.
        """
        parsed = self._parsed_url()
        return f"{parsed['owner']}/{parsed['repo']}"

    @property
    def number(self) -> int:
        """The PR number from the URL."""
        return int(self._parsed_url()["number"])


def golden_texts(pr: BenchPr, profile: Profile = DEFAULT_PROFILE) -> list[str]:
    """The golden comments of one PR under a severity profile, as plain text.

    Text only, because the corpus carries no file and no line — matching is
    semantic, and `cgis.guardian.calibrate` renders our side anchor-free to
    match.

    Filtered by `category`, not by severity: see `PROFILE_CATEGORIES`.
    """
    allowed = PROFILE_CATEGORIES[profile]
    return [c.comment for c in pr.comments if c.category in allowed]


def load_corpus(directory: Path) -> list[BenchPr]:
    """Every PR in the vendored corpus, project-then-URL ordered.

    Deterministic order so a run interrupted halfway resumes over the same
    sequence, and so a report's row order is not an artefact of the filesystem.
    """
    prs: list[BenchPr] = [
        BenchPr(project=path.stem, **raw)
        for path in sorted(directory.glob("*.json"))
        for raw in json.loads(path.read_text(encoding="utf-8"))
    ]
    return sorted(prs, key=lambda p: (p.project, p.url))


def slice_of(pr: BenchPr) -> Literal["graph", "diff-only"]:
    """Which G5 slice a PR belongs to.

    Decided by the files the PR actually touches, not by the repository's
    headline language. That distinction is the whole reason the spec's original
    stratification table was wrong: grafana is filed as a Go project and three
    of its PRs are front-end TypeScript, two of them purely so.

    A PR with no recorded `changed_files` is diff-only, which is also what it
    would be in practice — the collector finds nothing to look up.
    """
    return "graph" if any(is_supported(f) for f in pr.changed_files) else "diff-only"


def evaluated(prs: Iterable[BenchPr]) -> list[BenchPr]:
    """The PRs a gate is computed on: reproducible ones, in corpus order."""
    return [pr for pr in prs if pr.is_reproducible]


class SliceCounts(BaseModel, frozen=True):
    """How many PRs and golden comments a slice holds."""

    prs: int
    comments: int


def slice_counts(
    prs: Sequence[BenchPr], profile: Profile = DEFAULT_PROFILE
) -> dict[str, SliceCounts]:
    """PR and comment totals per G5 slice, for the population handed in.

    Returns both slices always, zeroed when empty, so a report cannot silently
    omit a slice that turned out to have nothing in it — an absent row and a
    zero row mean very different things when a gate compares two slices.
    """
    totals = {name: [0, 0] for name in ("graph", "diff-only")}
    for pr in prs:
        entry = totals[slice_of(pr)]
        entry[0] += 1
        entry[1] += len(golden_texts(pr, profile))
    return {name: SliceCounts(prs=n, comments=c) for name, (n, c) in totals.items()}


#: What a PR's slice is when its file list could not be fetched. Deliberately
#: not "diff-only": that is a real classification and a failure is not, and the
#: two must never be summed together into a gate population.
UNKNOWN_SLICE = "unknown"


class PrPlan(BaseModel, frozen=True):
    """One PR as the runner will treat it, decided before anything is spent."""

    project: str
    url: str
    repo: str
    number: int
    reproducible: bool
    pr_slice: Literal["graph", "diff-only", "unknown"]
    changed_files: tuple[str, ...]
    golden_comments: int
    #: Non-null when the file list could not be fetched. Such a row is
    #: `unknown`, never `diff-only` — see UNKNOWN_SLICE.
    fetch_error: str | None = None


def build_plan(
    prs: Sequence[BenchPr],
    fetch: Callable[[BenchPr], tuple[str, ...]],
    profile: Profile = DEFAULT_PROFILE,
) -> list[PrPlan]:
    """Resolve every PR's slice, calling `fetch` for the files it touches.

    `fetch` is injected so the plan can be tested without the network, and so a
    cached run and a live run are the same code path.

    An exception from `fetch` is captured rather than raised: one unreachable PR
    should not lose the other forty-nine, and losing rows silently is exactly
    how the reconnaissance for this work first mis-counted the corpus at 49.
    The row is recorded as `unknown` and the caller decides what to do about it.
    """
    plans: list[PrPlan] = []
    for pr in prs:
        error: str | None = None
        files: tuple[str, ...] = ()
        try:
            files = tuple(fetch(pr))
        except Exception as exc:  # recorded on the row, never swallowed
            error = f"{type(exc).__name__}: {exc}"[:200]
        resolved = pr.model_copy(update={"changed_files": files})
        plans.append(
            PrPlan(
                project=pr.project,
                url=pr.url,
                repo=pr.repo,
                number=pr.number,
                reproducible=pr.is_reproducible,
                pr_slice=UNKNOWN_SLICE if error else slice_of(resolved),
                changed_files=files,
                golden_comments=len(golden_texts(pr, profile)),
                fetch_error=error,
            )
        )
    return plans


def plan_population(plans: Sequence[PrPlan]) -> dict[str, SliceCounts]:
    """Gate populations from a plan: the evaluated PRs, by slice.

    Only reproducible rows count — the five the corpus flags are excluded, not
    scored as misses. `unknown` rows are returned under their own key so a
    fetch failure shows up as a number instead of quietly inflating one slice.
    """
    totals: dict[str, list[int]] = {name: [0, 0] for name in ("graph", "diff-only", UNKNOWN_SLICE)}
    for plan in plans:
        if not plan.reproducible:
            continue
        entry = totals[plan.pr_slice]
        entry[0] += 1
        entry[1] += plan.golden_comments
    return {name: SliceCounts(prs=n, comments=c) for name, (n, c) in totals.items()}
