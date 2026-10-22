"""Gathers git diff and project files needed for Guardian review context.

Three classes, split along their dependencies (#215): `SourceCollector` reads git
and the working tree, `GraphContextCollector` reads graph.db and the ontology, and
`ContextCollector` composes the two into a review's context sections. The first
two know nothing of features or of each other.
"""

import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import structlog

from cgis.guardian.chunker import Chunk
from cgis.languages import is_supported, pack_for_path
from cgis.query.diffcheck import DiffError, DiffReport, check_database
from cgis.query.drift.drift import DriftScorer
from cgis.query.drift.fingerprint import FingerprintExtractor
from cgis.query.drift.quotient import build_quotient, quotient_call_tally
from cgis.query.engine import QueryEngine
from cgis.query.render.mermaid import MermaidCompiler
from cgis.storage.sqlite_store import SQLiteStore

log = structlog.getLogger(__name__)

VALID_FEATURES = frozenset(
    {
        "full_files",
        "flow",
        "drift",
        "chunked",
        "axes",
        "axes_paired",
        "context_requests",
        "diff_check",
    }
)

#: Leads shown to the finder by the `diff_check` feature (#733), ranked by diff-check.
DEPENDANTS_CAP = 15
#: The section's size bound: a key path can be long, and fifteen of them are not free.
_DEPENDANTS_MAX_CHARS = 6_000

_MAX_FILE_LINES = 1200
_MAX_TOTAL_CHARS = 120_000


def parse_features(raw: str) -> frozenset[str]:
    """Parse a GUARDIAN_FEATURES value ('full_files,flow,drift') into a validated set.

    Raises ValueError on unknown names: a typo silently disabling an ablation
    arm would corrupt the benchmark comparison.
    """
    items = {item.strip() for item in raw.split(",") if item.strip()}
    unknown = items - VALID_FEATURES
    if unknown:
        _msg = f"Unknown GUARDIAN_FEATURES: {sorted(unknown)}; valid: {sorted(VALID_FEATURES)}"
        raise ValueError(_msg)
    return frozenset(items)


#: How a review's feature set came to be (#505). "env" — GUARDIAN_FEATURES was
#: set, possibly to nothing; "default" — it was unset or blank, so the review ran
#: with no optional section. Recorded beside the set rather than inferred from
#: it: an empty set is what both an unset variable and `GUARDIAN_FEATURES=,`
#: produce, and only one of them was a choice.
FeaturesSource = Literal["env", "default"]


def features_setting(env: Mapping[str, str]) -> tuple[frozenset[str], FeaturesSource]:
    """The context sections a review gets, and how that came to be, from one read (#505).

    One call for both halves, like `temperature_setting` (#393): reading the
    variable twice is how a record comes to describe a prompt it did not have.
    An unknown name still raises, through `parse_features`.
    """
    raw = (env.get("GUARDIAN_FEATURES") or "").strip()
    if not raw:
        return frozenset(), "default"
    return parse_features(raw), "env"


def contained(root: Path, relative_path: str) -> Path | None:
    """`root / relative_path`, resolved, when it lies inside `root`; else None.

    The reviewed checkout is a PR, and a PR is data its author controls (#730).
    A symlink or a `..` is data that reads somewhere else: a CONTRIBUTING.md
    linked to /proc/self/environ would put the job's provider keys in the
    prompt, and from there in a public review comment. `cgis ingest` refuses
    such links the same way. A link that stays inside the checkout is allowed.
    """
    try:
        resolved = (root / relative_path).resolve()
        if resolved.is_relative_to(root.resolve()):
            return resolved
    except (OSError, RuntimeError, ValueError):  # a loop of links, or a null byte
        return None
    return None


class SourceCollector:
    """What the PR changed and what its files say: git and the working tree."""

    def __init__(
        self, project_root: Path, base_branch: str = "main", base_ref: str | None = None
    ) -> None:
        """Set project root and diff base (an explicit ref, else origin/<base_branch>).

        base_ref, when given, is used verbatim (e.g. a SHA for benchmark replays).
        """
        self.project_root = project_root
        self.base_branch = base_branch
        self.base_ref = base_ref
        self._diff_cache: str | None = None

    def merge_base(self) -> str | None:
        """The commit the PR branched from, or None when git cannot name it.

        diff-check reads `base..head`; against a moved base branch that would also
        list what the base gained since, so the range starts where the PR did —
        the same commits `git diff base...HEAD` shows the finder.
        """
        base = self.base_ref or f"origin/{self.base_branch}"
        try:
            result = subprocess.run(
                ["git", "merge-base", "--end-of-options", base, "HEAD"],
                capture_output=True,
                text=True,
                check=True,
                cwd=self.project_root,
            )
        except (subprocess.CalledProcessError, OSError):  # OSError: no git at all
            return None
        return result.stdout.strip() or None

    def _diff_range(self) -> str:
        """Return the git range argument for diff commands."""
        base = self.base_ref or f"origin/{self.base_branch}"
        return f"{base}...HEAD"

    def get_git_diff(self) -> str:
        """Returns diff between HEAD and the base branch on origin.

        The diff is cached after the first successful call: within one review
        run it is needed twice (LLM context and inline-comment line index),
        and the working tree does not change in between.
        """
        if self._diff_cache is not None:
            return self._diff_cache
        try:
            result = subprocess.run(
                ["git", "diff", self._diff_range()],
                capture_output=True,
                text=True,
                check=True,
                cwd=self.project_root,
            )
        except subprocess.CalledProcessError as e:
            return f"Error getting git diff: {e.stderr}"
        else:
            self._diff_cache = result.stdout
            return self._diff_cache

    def get_changed_source_files(self) -> list[str]:
        """Relative paths of changed files cgis has an extractor for (#344).

        Was `.py` only, which meant that on a TypeScript PR the finder
        received a bare diff — no full-file context and no impact graph —
        even though `graph.db` was full of TS nodes. Guardian's
        differentiator was simply absent, and nothing said so.
        """
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", self._diff_range()],
                capture_output=True,
                text=True,
                check=True,
                cwd=self.project_root,
            )
        except subprocess.CalledProcessError:
            return []
        return [p for p in result.stdout.splitlines() if is_supported(p)]

    def read_file(self, relative_path: str) -> str:
        """Reads a file from the project root; "" when it is missing or resolves outside it.

        An empty string (not an error marker) lets the prompt builder omit the
        corresponding section entirely — a repo without CONTRIBUTING.md or
        docs/ontology/ should review cleanly, not get "Error: File ..." injected
        as if it were the standards/ontology text. `is_file()` (not `exists()`)
        so a path that resolves to a directory degrades to "" instead of raising
        IsADirectoryError on read. Undecodable bytes are replaced: the file is the
        PR's, and one bad byte must not end the review.
        """
        file_path = contained(self.project_root, relative_path)
        if file_path is None or not file_path.is_file():
            return ""
        return file_path.read_text(encoding="utf-8", errors="replace")

    def collect_full_files(self, files: list[str] | None = None) -> str:
        """Full HEAD text of given (default: changed) source files, smallest-first.

        Per-file cap ~1200 lines and a global ~120K-char budget; omitted files get
        an explicit note so the model never reads absence-of-file as absence-of-code.
        In chunked mode the budget applies per chunk (spec §4.2).
        """
        changed = files if files is not None else self.get_changed_source_files()
        sized: list[tuple[int, str, str]] = []
        omitted: list[str] = []
        for rel_path in changed:
            path = contained(self.project_root, rel_path)
            if path is None:
                omitted.append(f"file omitted: links outside the checkout ({rel_path})")
                continue
            if not path.is_file():  # deleted in this PR, or not a file — nothing to show
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if len(text.splitlines()) > _MAX_FILE_LINES:
                omitted.append(f"file omitted: too large ({rel_path})")
                continue
            sized.append((len(text), rel_path, text))

        sections: list[str] = []
        used = 0
        for size, rel_path, text in sorted(sized):
            if used + size > _MAX_TOTAL_CHARS:
                omitted.append(f"file omitted: budget exhausted ({rel_path})")
                continue
            used += size
            # Per-language fence: the label was hardcoded "python", so a TS
            # file reached the model announced as Python (#344).
            pack = pack_for_path(rel_path)
            fence = pack.code_fence(rel_path) if pack else ""
            sections.append(f"#### `{rel_path}`\n```{fence}\n{text}\n```")
        return "\n\n".join(sections + omitted)


class GraphContextCollector:
    """What graph.db says about the changed files: impact graphs and drift."""

    def __init__(self, project_root: Path, db_path: Path | None, source_root: str = "src") -> None:
        """Set the graph DB, the ingest root its FQNs are relative to, and the project root.

        source_root must match the ingest root used to build the graph DB (CI
        runs `cgis ingest pr/src` with project_root `pr`): changed-file paths
        are stripped of it before lookup. project_root locates
        docs/ontology/patterns.yaml for drift.
        """
        self.project_root = project_root
        self.db_path = db_path
        self.source_root = source_root

    def has_db(self) -> bool:
        """Whether a graph DB was given and exists on disk."""
        return self.db_path is not None and self.db_path.exists()

    def sections(self, changed_files: list[str], *, flow: bool) -> tuple[list[str], dict[str, int]]:
        """Impact-graph Mermaid sections + their stats for the given files.

        Holds no stats of its own — the caller decides whether to overwrite
        (global path) or accumulate (per-chunk path).
        """
        stats = {"total": 0, "with_graph": 0, "flow_fallback": 0}
        if not self.has_db() or not changed_files:
            return [], stats
        stats["total"] = len(changed_files)
        compiler = MermaidCompiler()
        sections: list[str] = []
        with SQLiteStore(str(self.db_path)) as store:
            engine = QueryEngine(store)
            for rel_path in changed_files:
                pack = pack_for_path(rel_path)
                if pack is None:
                    # Callers filter, so this means one stopped doing so.
                    log.warning("No extractor for changed file.", path=rel_path)
                    continue
                module_fqn = pack.file_node_id(rel_path, self.source_root)
                nodes, edges = engine.get_impact_graph(module_fqn, max_depth=2)
                title = "Impact graph"
                if not nodes and flow:
                    # New file: nothing references it yet (#94) — show what it calls.
                    nodes, edges = engine.get_flow_graph(module_fqn, max_depth=2)
                    title = "Dependency graph (outbound)"
                    if nodes:
                        stats["flow_fallback"] += 1
                if not nodes:
                    log.debug("No impact graph for module", fqn=module_fqn)
                    continue
                mermaid = compiler.compile(nodes, edges)
                sections.append(f"#### {title} for `{module_fqn}`:\n```mermaid\n{mermaid}\n```")
        stats["with_graph"] = len(sections)
        return sections, stats

    def collect_dependants(self, repo: Path, base: str | None) -> str:
        """diff-check's list of code outside the diff that depends on it (#733).

        Leads, not findings: callers, subclasses and the other readers and
        writers of changed data keys, capped at DEPENDANTS_CAP. Any failure —
        no graph, no merge base, a range git cannot read — costs the section,
        never the review. Not carried by chunked mode (`collect_for_chunk`).
        """
        if not self.has_db() or base is None:
            return ""
        try:
            report = check_database(
                str(self.db_path), repo, base, "HEAD", DEPENDANTS_CAP, prefix=self.source_root
            )
        except DiffError:
            log.warning("Dependants section skipped: git could not read the range.")
            return ""
        except Exception:
            log.warning("Dependants section skipped.", exc_info=True)
            return ""
        return self.render_dependants(report)

    def render_dependants(self, report: DiffReport) -> str:
        """The leads as the finder reads them: repo paths, as in the diff; no operator hints.

        diff-check's own text names MCP tools and re-ingest commands for an agent
        at a terminal; here they would be instructions the finder cannot follow.
        """
        if not report.items:
            return ""
        root = self.source_root.strip("/")
        lines = [
            f"{len(report.changed_symbols)} symbol(s) and {len(report.changed_keys)} data key(s) "
            "changed; what else depends on them:"
        ]
        for item in report.items:
            path = f"{root}/{item.file}" if root else item.file
            where = f"{path}:{item.line}" if item.line else path
            lines.append(f"- [{item.kind}] {item.site} ({where}): {item.reason}")
        if report.hidden:
            lines.append(f"- ... and {report.hidden} more, not shown")
        text = "\n".join(lines)
        if len(text) > _DEPENDANTS_MAX_CHARS:
            text = text[:_DEPENDANTS_MAX_CHARS].rsplit("\n", 1)[0] + "\n- ... (cut for length)"
        return text

    def collect_drift(self) -> str:
        """Compact per-domain drift table + quotient k=1 lines (spec §4.3).

        First real consumer of drift v2 outside tests — the soft enforcement
        channel deferred in #146/#151. Any failure degrades to an empty section.
        """
        if not self.has_db():
            return ""
        patterns = contained(self.project_root, "docs/ontology/patterns.yaml")
        if patterns is None or not patterns.exists():
            return ""
        try:
            scorer = DriftScorer(str(patterns))
            domains = scorer.load_project_domains()
            quotient_lines: list[str] = []
            with SQLiteStore(str(self.db_path)) as store:
                extractor = FingerprintExtractor(store)
                # default_tolerance=0.50 fallback is deliberate here: collector has no
                # max_drift to thread, and production domains declare their own tolerance.
                reports = [scorer.score(extractor.extract(d.fqn_prefix), d) for d in domains]
                level = scorer.load_project_level()
                if level:
                    all_nodes, all_edges = store.get_all_nodes(), store.get_all_edges()
                    qnodes, qedges = build_quotient(all_nodes, all_edges, domains)
                    q_extractor = FingerprintExtractor.from_graph(
                        qnodes, qedges, quotient_call_tally(all_nodes, all_edges, domains)
                    )
                    quotient_lines = [
                        f"Quotient k=1 [{b.name}] vs {qr.expected_pattern}: "
                        f"drift={qr.drift_score:.2f} (observe-only)"
                        for b in level
                        # default_tolerance=0.50 fallback is deliberate here (see comment above).
                        for qr in [scorer.score(q_extractor.extract(b.fqn_prefix), b)]
                    ]
        except Exception:
            log.warning("Drift section skipped.", exc_info=True)
            return ""

        if not reports and not quotient_lines:  # no domains declared — skip the empty table
            return ""

        rows = [
            f"| {r.fqn_prefix} | {r.expected_pattern or '(hygiene)'} "
            f"| {r.drift_score:.2f} | {r.tolerance:.2f} "
            f"| {'⚠' if r.drift_score > r.tolerance else ''} |"
            for r in reports
        ]
        table = "| domain | expected | drift | tolerance | over |\n|---|---|---|---|---|\n"
        return (
            table + "\n".join(rows) + ("\n" + "\n".join(quotient_lines) if quotient_lines else "")
        )


class ContextCollector:
    """Composes a review's context sections from its source and graph collaborators."""

    def __init__(
        self,
        project_root: Path,
        base_branch: str = "main",
        db_path: Path | None = None,
        base_ref: str | None = None,
        source_root: str = "src",
        features: frozenset[str] = frozenset(),
        include_graph: bool = True,
    ) -> None:
        """Build the source and graph collaborators and set what the review asks for.

        project_root, base_branch and base_ref go to `source`; db_path and
        source_root to `graph` (see each for their meaning).

        features gates the optional context sections (spec §4): "full_files", "flow", "drift".

        include_graph=False drops the impact-graph Mermaid section — a "diff-only"
        prompt that fits a smaller context window (e.g. a local Ollama model whose
        window is exceeded once the graph is appended).
        """
        self.source = SourceCollector(project_root, base_branch, base_ref)
        self.graph = GraphContextCollector(project_root, db_path, source_root)
        self.features = features
        self.include_graph = include_graph
        self.graph_stats: dict[str, int] = {"total": 0, "with_graph": 0, "flow_fallback": 0}

    def collect_graph_context(self) -> str:
        """Impact graphs of the changed files as Mermaid blocks; records graph_stats."""
        if not self.include_graph:
            return ""  # diff-only prompt (fits a smaller context window)
        if not self.graph.has_db():
            return ""
        changed_files = self.source.get_changed_source_files()
        if not changed_files:
            return ""
        sections, stats = self.graph.sections(changed_files, flow="flow" in self.features)
        self.graph_stats = stats
        if stats["total"] > 0 and stats["with_graph"] == 0:
            log.warning(
                "No graph context found for any changed file.",
                changed_files=stats["total"],
                project_root=str(self.source.project_root),
            )
        return "\n\n".join(sections)

    def collect_all(self) -> dict[str, str]:
        """Collects all relevant files, git diff, and optional graph context."""
        context: dict[str, str] = {
            "diff": self.source.get_git_diff(),
            "contributing": self.source.read_file("CONTRIBUTING.md"),
            "ontology": self.source.read_file("docs/ontology/core.yaml"),
        }
        graph_context = self.collect_graph_context()
        if graph_context:
            context["graph_context"] = graph_context
        if "full_files" in self.features:
            full_files = self.source.collect_full_files()
            if full_files:
                context["full_files"] = full_files
        if "drift" in self.features:
            drift = self.graph.collect_drift()
            if drift:
                context["drift"] = drift
        if "diff_check" in self.features:
            dependants = self.graph.collect_dependants(
                self.source.project_root, self.source.merge_base()
            )
            if dependants:
                context["dependants"] = dependants
        return context

    def collect_for_chunk(self, chunk: Chunk) -> dict[str, str]:
        """Per-chunk context: the chunk's diff, full files, and impact graphs (spec §4.2).

        chunked implies per-chunk full_files, graph context, AND the flow
        fallback — each chunk gets a small, complete world. graph_stats
        ACCUMULATE across chunks so the footer coverage stays truthful.
        """
        source_files = [f for f in chunk.files if is_supported(f)]
        context: dict[str, str] = {
            "diff": chunk.diff,
            "contributing": self.source.read_file("CONTRIBUTING.md"),
            "ontology": self.source.read_file("docs/ontology/core.yaml"),
        }
        sections, stats = self.graph.sections(source_files, flow=True)
        for key, value in stats.items():
            self.graph_stats[key] = self.graph_stats.get(key, 0) + value
        if sections:
            context["graph_context"] = "\n\n".join(sections)
        full_files = self.source.collect_full_files(source_files)
        if full_files:
            context["full_files"] = full_files
        return context
