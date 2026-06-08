import subprocess
from pathlib import Path


class ContextCollector:
    """Gathers all necessary context for the review."""

    def __init__(self, project_root: Path):
        self.project_root = project_root

    def get_git_diff(self) -> str:
        """Gets the current git diff (staged or between branches)."""
        try:
            # Default to diff between current branch and main, or just current changes
            result = subprocess.run(
                ["git", "diff", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
                cwd=self.project_root,
            )
            return result.stdout
        except subprocess.CalledProcessError as e:
            return f"Error getting git diff: {e.stderr}"

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
            "ontology": self.read_file("docs/ontology/core.yaml"),  # Simplified for now
        }
