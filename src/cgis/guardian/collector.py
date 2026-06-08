import subprocess
from pathlib import Path


class ContextCollector:
    """Gathers all necessary context for the review."""

    def __init__(self, project_root: Path, base_branch: str = "main") -> None:
        self.project_root = project_root
        self.base_branch = base_branch

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

    def read_file(self, relative_path: str) -> str:
        """Reads a file from the project root."""
        file_path = self.project_root / relative_path
        if not file_path.exists():
            return f"Error: File {relative_path} not found."
        return file_path.read_text()

    def collect_all(self) -> dict[str, str]:
        """Collects all relevant files and the git diff."""
        return {
            "diff": self.get_git_diff(),
            "contributing": self.read_file("CONTRIBUTING.md"),
            "ontology": self.read_file("docs/ontology/core.yaml"),
        }
