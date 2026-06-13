"""Gathers git diff and project files needed for Guardian review context."""

import subprocess
from pathlib import Path

import structlog

from cgis.extractors.python_extractor import file_path_to_module_fqn
from cgis.guardian.chunker import Chunk
from cgis.query.drift import DriftScorer
from cgis.query.engine import QueryEngine
from cgis.query.fingerprint import FingerprintExtractor
from cgis.query.mermaid import MermaidCompiler
from cgis.query.quotient import build_quotient
from cgis.storage.sqlite_store import SQLiteStore

log = structlog.getLogger(__name__)

VALID_FEATURES = frozenset({"full_files", "flow", "drift", "chunked"})

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


class ContextCollector:
    """Gathers all necessary context for the review."""

    def __init__(
        self,
        project_root: Path,
        base_branch: str = "main",
        db_path: Path | None = None,
        base_ref: str | None = None,
        source_root: str = "src",
        features: frozenset[str] = frozenset(),
    ) -> None:
        """Set project root, diff base (branch or explicit ref), and optional graph DB.

        base_ref, when given, is used verbatim (e.g. a SHA for benchmark
        replays); otherwise the diff base is origin/<base_branch>.

        source_root must match the ingest root used to build the graph DB
        (CI runs `cgis ingest ./src`): node FQNs are relative to that root,
        so changed-file paths are stripped of it before lookup.

        features gates the optional context sections (spec §4): "full_files", "flow", "drift".
        """
        self.project_root = project_root
        self.base_branch = base_branch
        self.db_path = db_path
        self.base_ref = base_ref
        self.source_root = source_root
        self.features = features
        self.graph_stats: dict[str, int] = {"total": 0, "with_graph": 0, "flow_fallback": 0}
        self._diff_cache: str | None = None

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

    def get_changed_py_files(self) -> list[str]:
        """Returns relative paths of .py files changed vs the base branch."""
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
        return [p for p in result.stdout.splitlines() if p.endswith(".py")]

    def read_file(self, relative_path: str) -> str:
        """Reads a file from the project root; returns "" when it does not exist.

        An empty string (not an error marker) lets the prompt builder omit the
        corresponding section entirely — a repo without CONTRIBUTING.md or
        docs/ontology/ should review cleanly, not get "Error: File ..." injected
        as if it were the standards/ontology text.
        """
        file_path = self.project_root / relative_path
        if not file_path.exists():
            return ""
        return file_path.read_text(encoding="utf-8")

    def collect_full_files(self, files: list[str] | None = None) -> str:
        """Full HEAD text of given (default: changed) .py files, smallest-first under budgets.

        Per-file cap ~1200 lines and a global ~120K-char budget; omitted files get
        an explicit note so the model never reads absence-of-file as absence-of-code.
        In chunked mode the budget applies per chunk (spec §4.2).
        """
        changed = files if files is not None else self.get_changed_py_files()
        sized: list[tuple[int, str, str]] = []
        omitted: list[str] = []
        for rel_path in changed:
            path = self.project_root / rel_path
            if not path.exists():  # deleted in this PR — nothing to show at HEAD
                continue
            text = path.read_text(encoding="utf-8")
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
            sections.append(f"#### `{rel_path}`\n```python\n{text}\n```")
        return "\n\n".join(sections + omitted)

    def _graph_sections(
        self, changed_files: list[str], *, flow: bool
    ) -> tuple[list[str], dict[str, int]]:
        """Impact-graph Mermaid sections + local stats for the given files.

        Pure with respect to self.graph_stats — callers decide whether to
        overwrite (global path) or accumulate (per-chunk path).
        """
        stats = {"total": 0, "with_graph": 0, "flow_fallback": 0}
        if self.db_path is None or not self.db_path.exists() or not changed_files:
            return [], stats
        stats["total"] = len(changed_files)
        compiler = MermaidCompiler()
        sections: list[str] = []
        with SQLiteStore(str(self.db_path)) as store:
            engine = QueryEngine(store)
            for rel_path in changed_files:
                module_fqn = file_path_to_module_fqn(rel_path, self.source_root)
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

    def collect_graph_context(self) -> str:
        """Query graph.db for impact graphs of changed files; return Mermaid blocks."""
        if self.db_path is None or not self.db_path.exists():
            return ""
        changed_files = self.get_changed_py_files()
        if not changed_files:
            return ""
        sections, stats = self._graph_sections(changed_files, flow="flow" in self.features)
        self.graph_stats = stats
        if stats["total"] > 0 and stats["with_graph"] == 0:
            log.warning(
                "No graph context found for any changed file.",
                changed_files=stats["total"],
                project_root=str(self.project_root),
            )
        return "\n\n".join(sections)

    def collect_drift(self) -> str:
        """Compact per-domain drift table + quotient k=1 lines (spec §4.3).

        First real consumer of drift v2 outside tests — the soft enforcement
        channel deferred in #146/#151. Any failure degrades to an empty section.
        """
        if self.db_path is None or not self.db_path.exists():
            return ""
        patterns = self.project_root / "docs" / "ontology" / "patterns.yaml"
        if not patterns.exists():
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
                    qnodes, qedges = build_quotient(
                        store.get_all_nodes(), store.get_all_edges(), domains
                    )
                    q_extractor = FingerprintExtractor.from_graph(qnodes, qedges)
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

    def collect_all(self) -> dict[str, str]:
        """Collects all relevant files, git diff, and optional graph context."""
        context: dict[str, str] = {
            "diff": self.get_git_diff(),
            "contributing": self.read_file("CONTRIBUTING.md"),
            "ontology": self.read_file("docs/ontology/core.yaml"),
        }
        graph_context = self.collect_graph_context()
        if graph_context:
            context["graph_context"] = graph_context
        if "full_files" in self.features:
            full_files = self.collect_full_files()
            if full_files:
                context["full_files"] = full_files
        if "drift" in self.features:
            drift = self.collect_drift()
            if drift:
                context["drift"] = drift
        return context

    def collect_for_chunk(self, chunk: Chunk) -> dict[str, str]:
        """Per-chunk context: the chunk's diff, full files, and impact graphs (spec §4.2).

        chunked implies per-chunk full_files, graph context, AND the flow
        fallback — each chunk gets a small, complete world. graph_stats
        ACCUMULATE across chunks so the footer coverage stays truthful.
        """
        py_files = [f for f in chunk.files if f.endswith(".py")]
        context: dict[str, str] = {
            "diff": chunk.diff,
            "contributing": self.read_file("CONTRIBUTING.md"),
            "ontology": self.read_file("docs/ontology/core.yaml"),
        }
        sections, stats = self._graph_sections(py_files, flow=True)
        for key, value in stats.items():
            self.graph_stats[key] = self.graph_stats.get(key, 0) + value
        if sections:
            context["graph_context"] = "\n\n".join(sections)
        full_files = self.collect_full_files(py_files)
        if full_files:
            context["full_files"] = full_files
        return context
