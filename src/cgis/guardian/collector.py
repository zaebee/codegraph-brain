"""Gathers git diff and project files needed for Guardian review context."""

import subprocess
from pathlib import Path

import structlog

from cgis.extractors.python_extractor import file_path_to_module_fqn
from cgis.query.engine import QueryEngine
from cgis.query.mermaid import MermaidCompiler
from cgis.storage.sqlite_store import SQLiteStore

log = structlog.getLogger(__name__)

VALID_FEATURES = frozenset({"full_files", "flow", "drift"})


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
        self.graph_stats: dict[str, int] = {"total": 0, "with_graph": 0}

    def _diff_range(self) -> str:
        """Return the git range argument for diff commands."""
        base = self.base_ref or f"origin/{self.base_branch}"
        return f"{base}...HEAD"

    def get_git_diff(self) -> str:
        """Returns diff between HEAD and the base branch on origin."""
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
            return result.stdout

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
        """Reads a file from the project root."""
        file_path = self.project_root / relative_path
        if not file_path.exists():
            return f"Error: File {relative_path} not found."
        return file_path.read_text()

    def collect_graph_context(self) -> str:
        """Query graph.db for impact graphs of changed files; return Mermaid blocks."""
        if self.db_path is None or not self.db_path.exists():
            return ""

        changed_files = self.get_changed_py_files()
        if not changed_files:
            return ""

        compiler = MermaidCompiler()
        sections: list[str] = []
        total_changed = len(changed_files)

        with SQLiteStore(str(self.db_path)) as store:
            engine = QueryEngine(store)
            for rel_path in changed_files:
                module_fqn = file_path_to_module_fqn(rel_path, self.source_root)
                nodes, edges = engine.get_impact_graph(module_fqn, max_depth=2)
                if not nodes:
                    log.debug("No impact graph for module", fqn=module_fqn)
                    continue
                mermaid = compiler.compile(nodes, edges)
                sections.append(
                    f"#### Impact graph for `{module_fqn}`:\n```mermaid\n{mermaid}\n```"
                )

        self.graph_stats = {"total": total_changed, "with_graph": len(sections)}
        if total_changed > 0 and len(sections) == 0:
            log.warning(
                "No graph context found for any changed file.",
                changed_files=total_changed,
                project_root=str(self.project_root),
            )
        return "\n\n".join(sections)

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
        return context
