"""What the repository's own checkers say about the changed files (#401).

The skeptic refutes a finding only on "concrete evidence that it is wrong". That
bar is deliberate — it protects the recall the finder is tuned for — but the
skeptic had no way to *obtain* such evidence, so "could not disprove" did the
work of "is true". A review of #399 produced six copies of the claim that `Any`
is prohibited under strict mypy, and the skeptic confirmed every one. One command
settles it; nothing could run one.

This module runs that command. It does not lower the bar; it makes the bar
reachable.

Scope, stated rather than implied: the verbs are Python-only. `guardian.yml` and
`guardian_bench` review this repository, where `uv` and the checkers are
installed; `guardian_martian` reviews five foreign repositories whose toolchains
are not ours, and there this returns None and the skeptic behaves exactly as it
does today. `collect_evidence` is the seam a TypeScript implementation would
extend, not replace.

Every failure is absence, never a verdict. A missing toolchain, a crashing
checker, a timeout and a path outside the repository all produce None. Reporting
a checker's stderr as "what the type checker said" would let an infrastructure
failure refute a real finding, which is worse than the noise this exists to cut.
"""

import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:  # pragma: no cover
    from cgis.guardian.collector import ContextCollector

#: Cap on the rendered evidence. A checker over a large diff can outrun the
#: context window, and a silently truncated verdict whose visible prefix happens
#: to be clean reads as "no errors".
MAX_EVIDENCE_CHARS = 4000

#: Wall-clock budget for all checkers together. They run once per review, not
#: once per finding, so this is bounded by the review rather than by its size.
EVIDENCE_TIMEOUT_S = 120

_PYTHON_SUFFIX = ".py"


class Evidence(BaseModel, frozen=True):
    """Commands that were run, and what they printed.

    Both halves travel together on purpose. Output alone is an assertion; output
    beside the command that produced it is something a reader can re-run. The
    skeptic is being asked to treat this as disproof, so it has to be able to
    say where the disproof came from.
    """

    commands: tuple[str, ...]
    output: str

    def render(self) -> str:
        """The prompt section, truncated with a notice rather than silently."""
        body = self.output.strip() or "(no output — the checkers reported nothing)"
        if len(body) > MAX_EVIDENCE_CHARS:
            body = body[:MAX_EVIDENCE_CHARS] + "\n… (truncated)"
        listed = "\n".join(f"$ {c}" for c in self.commands)
        return f"{listed}\n\n{body}"


def _python_files_inside(project_root: Path, changed_files: tuple[str, ...]) -> list[str]:
    """The changed `.py` paths that really live under `project_root`.

    The list comes from `git diff --name-only` on a branch the PR author wrote,
    and these strings become argv for the checkers. A path that resolves outside
    the repository is dropped rather than refused: one bad entry should not cost
    the evidence for the rest, and if nothing survives the caller gets None,
    which is the honest answer anyway.
    """
    root = project_root.resolve()
    inside: list[str] = []
    for name in changed_files:
        if not name.endswith(_PYTHON_SUFFIX):
            continue
        try:
            resolved = (root / name).resolve()
        except (OSError, RuntimeError):
            continue
        if resolved.is_relative_to(root) and resolved.is_file():
            inside.append(str(resolved.relative_to(root)))
    return inside


def _run(command: list[str], project_root: Path) -> str | None:
    """Run one checker and return its combined output, or None if it could not run.

    None is not "clean" — it is "no answer". The caller drops the whole evidence
    on it, because a checker that failed to start has not said the code is fine.
    """
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            cwd=project_root,
            check=False,
            timeout=EVIDENCE_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return f"{result.stdout}{result.stderr}"


def collect_evidence(project_root: Path, changed_files: tuple[str, ...]) -> Evidence | None:
    """What this repository's checkers say about the changed files, or None.

    None whenever the question cannot be answered: no `pyproject.toml`, no
    changed Python file, every path outside the tree, or any checker that could
    not run. The skeptic then behaves exactly as it does before this module
    existed, so an unsupported repository loses nothing.
    """
    if not (project_root / "pyproject.toml").is_file():
        return None
    files = _python_files_inside(project_root, changed_files)
    if not files:
        return None

    commands = [
        ["uv", "run", "--frozen", "mypy", "--strict", *files],
        ["uv", "run", "--frozen", "ruff", "check", *files],
    ]
    sections: list[str] = []
    for command in commands:
        output = _run(command, project_root)
        if output is None:
            return None
        sections.append(f"$ {' '.join(command)}\n{output.strip() or '(no output)'}")
    return Evidence(commands=tuple(" ".join(c) for c in commands), output="\n\n".join(sections))


#: Ships inert, like `impact_threshold` (#246 §3.5): the effect on noise and
#: recall has not been measured, and a prompt change that has not been measured
#: is a claim, not a result. Set to "1" to enable.
EVIDENCE_FLAG = "GUARDIAN_EVIDENCE"


def evidence_for(collector: "ContextCollector", env: Mapping[str, str]) -> Evidence | None:
    """Evidence for a review, or None when it is switched off or unavailable.

    The single seam every caller uses, so "is the flag on" is decided in one
    place rather than at four call sites that could drift.
    """
    if (env.get(EVIDENCE_FLAG) or "").strip() != "1":
        return None
    return collect_evidence(collector.project_root, tuple(collector.get_changed_source_files()))
