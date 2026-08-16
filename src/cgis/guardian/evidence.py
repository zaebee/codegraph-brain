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

import asyncio
import shlex
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from pydantic import BaseModel

if TYPE_CHECKING:  # pragma: no cover
    from cgis.guardian.collector import ContextCollector

log = structlog.getLogger(__name__)

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
        if not (resolved.is_relative_to(root) and resolved.is_file()):
            continue
        relative = resolved.relative_to(root)
        # A component starting with "-" reaches the checker as a *flag*, not a
        # path: `--config=evil.ini` is a legal filename and an argv option. The
        # `--` terminator below is the general guard; this is the belt, because
        # a tool that does not honour `--` would still be steered. Same
        # reasoning as `InvalidShaError` in the fingerprint backfill — a value
        # that never looked like a path has no business reaching a subprocess,
        # exploitable or not (pythonsecurity:S6350).
        if any(part.startswith("-") for part in relative.parts):
            log.warning("Dropping a changed file whose path could be read as a flag.", path=name)
            continue
        inside.append(str(relative))
    return inside


#: Exit codes at or above this mean the checker could not do its job, as opposed
#: to doing it and finding something. Measured on the two tools rather than
#: assumed, because the whole point is to tell a verdict from a non-verdict:
#:
#:     0  clean                      mypy ok / ruff "All checks passed!"
#:     1  ran, and reported findings  mypy type error / ruff F401
#:     2  could not run              mypy fatal / ruff bad argument /
#:                                   `uv run` failing to spawn the tool at all
#:
#: Classified by code rather than by matching "error:" in stderr. A substring
#: rule cannot tell `mypy: error: Cannot read file` from a checker legitimately
#: printing the word — the same defect #375 found in a `git show` reader, where a
#: missing commit and an absent path were indistinguishable by message.
_COULD_NOT_RUN = 2


def _run(command: list[str], project_root: Path, timeout: float) -> str | None:
    """Run one checker and return its combined output, or None if it could not run.

    None is not "clean" — it is "no answer". The caller drops the whole evidence
    on it, because a checker that failed to start has not said the code is fine.

    `uv run` does not raise when the tool is missing or the lockfile is stale: it
    exits non-zero and prints `error: Failed to spawn: ...` to stderr. Returning
    that string would hand the skeptic an infrastructure failure labelled as the
    type checker's verdict — which is the exact thing this module's docstring
    forbids, and which it did until this was measured.
    """
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            cwd=project_root,
            check=False,
            timeout=max(1.0, timeout),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode >= _COULD_NOT_RUN:
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
        log.info("No evidence: no pyproject.toml.", project_root=str(project_root))
        return None
    files = _python_files_inside(project_root, changed_files)
    if not files:
        log.info(
            "No evidence: no changed Python file inside the repository.",
            changed=len(changed_files),
        )
        return None

    # `--` so nothing after it is parsed as an option. Verified rather than
    # assumed: `mypy --strict -- <path>` treats the next argument as a file.
    commands = [
        ["uv", "run", "--frozen", "mypy", "--strict", "--", *files],
        ["uv", "run", "--frozen", "ruff", "check", "--", *files],
    ]
    # One budget shared across the checkers, because that is what the constant
    # says. Passing the full timeout to each made the real ceiling
    # len(commands) * EVIDENCE_TIMEOUT_S — the documentation describing a design
    # the code did not implement.
    deadline = time.monotonic() + EVIDENCE_TIMEOUT_S
    sections: list[str] = []
    for command in commands:
        output = _run(command, project_root, deadline - time.monotonic())
        if output is None:
            log.warning(
                "No evidence: a checker could not run; a failed checker has not said "
                "the code is fine.",
                command=" ".join(command),
            )
            return None
        sections.append(f"$ {shlex.join(command)}\n{output.strip() or '(no output)'}")
    evidence = Evidence(
        commands=tuple(shlex.join(c) for c in commands), output="\n\n".join(sections)
    )
    log.info(
        "Evidence collected for the skeptic.",
        files=len(files),
        commands=len(commands),
        chars=len(evidence.output),
    )
    return evidence


#: Ships inert, like `impact_threshold` (#246 §3.5): the effect on noise and
#: recall has not been measured, and a prompt change that has not been measured
#: is a claim, not a result. Set to "1" to enable.
EVIDENCE_FLAG = "GUARDIAN_EVIDENCE"


async def evidence_for(collector: "ContextCollector", env: Mapping[str, str]) -> Evidence | None:
    """Evidence for a review, or None when it is switched off or unavailable.

    The single seam every caller uses, so "is the flag on" is decided in one
    place rather than at four call sites that could drift.

    Async, with the checkers on a worker thread. Every caller is inside the
    event loop, and `collect_evidence` blocks for up to `EVIDENCE_TIMEOUT_S` —
    two minutes of a frozen loop, during which the concurrent skeptic calls this
    feature exists to improve would simply stop. `guardian_bench` already routes
    its blocking work through `asyncio.to_thread` for exactly this reason.
    """
    if (env.get(EVIDENCE_FLAG) or "").strip() != "1":
        log.info("Evidence disabled.", flag=EVIDENCE_FLAG)
        return None
    return await asyncio.to_thread(
        collect_evidence, collector.project_root, tuple(collector.get_changed_source_files())
    )
