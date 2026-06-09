"""Gathers git diff and project files needed for Guardian review context."""

import subprocess
from pathlib import Path

import structlog

from cgis.extractors.python_extractor import file_path_to_module_fqn
from cgis.query.engine import QueryEngine
from cgis.query.mermaid import MermaidCompiler
from cgis.storage.sqlite_store import SQLiteStore

log = structlog.getLogger(__name__)


class ContextCollector:
    """Gathers all necessary context for the review."""

    def __init__(
        self,
        project_root: Path,
        base_branch: str = "main",
        db_path: Path | None = None,
    ) -> None:
        """Set project root, the base branch used for git diff, and optional graph DB."""
        self.project_root = project_root
        self.base_branch = base_branch
        self.db_path = db_path
        self.graph_stats: dict[str, int] = {"total": 0, "with_graph": 0}

    def get_git_diff(self) -> str:
        """Returns diff between HEAD and the base branch on origin."""
        try:
            result = subprocess.run(
                ["git", "diff", f"origin/{self.base_branch}...HEAD"],
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
                ["git", "diff", "--name-only", f"origin/{self.base_branch}...HEAD"],
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
        total = len(changed_files)

        with SQLiteStore(str(self.db_path)) as store:
            engine = QueryEngine(store)
            for rel_path in changed_files:
                module_fqn = file_path_to_module_fqn(rel_path)
                nodes, edges = engine.get_impact_graph(module_fqn, max_depth=2)
                if not nodes:
                    log.debug("No impact graph for module", fqn=module_fqn)
                    continue
                mermaid = compiler.compile(nodes, edges)
                sections.append(
                    f"#### Impact graph for `{module_fqn}`:\n```mermaid\n{mermaid}\n```"
                )

        self.graph_stats = {"total": total, "with_graph": len(sections)}
        if total > 0 and len(sections) == 0:
            log.warning(
                "Graph context empty for all changed files — "
                "graph.db may be stale or built from wrong path.",
                changed_files=total,
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
