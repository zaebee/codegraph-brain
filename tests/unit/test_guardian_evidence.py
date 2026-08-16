"""Static evidence for the skeptic: what the repository's own checkers say (#401).

A guardian review of #399 produced 19 findings, 1 of which had substance. Six
were the same false claim — that `Any` is prohibited under strict mypy — and the
skeptic confirmed all six. That was the prompt working as written: it refutes
only on "concrete evidence that it is wrong", and it had none, because it cannot
run anything.

So the intervention is not a lower bar for refutation. It is a way to obtain the
evidence the existing bar already demands.
"""

import subprocess
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from cgis.guardian.evidence import (
    EVIDENCE_FLAG,
    MAX_EVIDENCE_CHARS,
    Evidence,
    collect_evidence,
    evidence_for,
)


def _python_repo(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    return tmp_path


class TestToolchainDetection:
    """What the collector will and will not claim to know."""

    def test_a_repo_with_no_pyproject_yields_no_evidence(self, tmp_path: Path) -> None:
        """Absence, stated as absence.

        Returning an empty `Evidence` instead would tell the skeptic "the
        checkers found nothing", which is the opposite of "no checker ran" — the
        same conflation `arm` and `had_graph` exist to prevent elsewhere.
        """
        assert collect_evidence(tmp_path, ("a.py",)) is None

    def test_a_non_python_change_yields_no_evidence(self, tmp_path: Path) -> None:
        """The verbs are Python-only, and the corpus this guardian reviews is not.

        `mypy` on a `.ts` file reports nothing, and reporting nothing would read
        as "the type checker is content" about a file it never looked at.
        """
        assert collect_evidence(_python_repo(tmp_path), ("src/app.ts", "README.md")) is None

    def test_no_changed_files_yields_no_evidence(self, tmp_path: Path) -> None:
        assert collect_evidence(_python_repo(tmp_path), ()) is None


class TestWhatIsCollected:
    """The evidence is the command and its output, together."""

    def test_the_commands_are_named_beside_their_output(self, tmp_path: Path) -> None:
        """A reader — human or model — must be able to re-run what produced this.

        Output alone is an assertion; output plus the command that made it is
        checkable. The skeptic is being asked to treat this as disproof, so it
        has to say where the disproof came from.
        """
        repo = _python_repo(tmp_path)
        (repo / "a.py").write_text("x: int = 1\n", encoding="utf-8")
        evidence = collect_evidence(repo, ("a.py",))
        assert evidence is not None
        assert any("mypy" in c for c in evidence.commands)
        assert any("ruff" in c for c in evidence.commands)

    def test_output_is_truncated_and_says_so(self) -> None:
        """A checker on a large diff can outrun the context window.

        Truncating silently would hand the skeptic a *prefix* of the verdict
        while it believes it has the whole one — and a prefix that happens to be
        clean reads as "no errors".
        """
        evidence = Evidence(
            commands=("mypy --strict a.py",), output="e" * (MAX_EVIDENCE_CHARS + 500)
        )
        rendered = evidence.render()
        assert len(rendered) < MAX_EVIDENCE_CHARS + 500
        assert "truncated" in rendered.lower()

    def test_short_output_is_not_marked_truncated(self) -> None:
        """The mirror: the common case must not carry a warning it does not need."""
        rendered = Evidence(commands=("ruff check a.py",), output="All checks passed!").render()
        assert "truncated" not in rendered.lower()
        assert "All checks passed!" in rendered


class TestFailureIsAbsenceNotEvidence:
    """A checker that could not run has not said the code is fine."""

    def test_a_crashing_checker_yields_no_evidence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Degrade, do not invent.

        If `uv run mypy` cannot start — no lockfile, no network, a broken venv —
        the honest report is that no evidence exists. Reporting its stderr as
        "what the type checker said" would let an infrastructure failure refute
        a real finding.
        """
        repo = _python_repo(tmp_path)
        (repo / "a.py").write_text("x = 1\n", encoding="utf-8")

        def boom(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            _msg = "no such executable"
            raise OSError(_msg)

        monkeypatch.setattr(subprocess, "run", boom)
        assert collect_evidence(repo, ("a.py",)) is None

    def test_a_timeout_yields_no_evidence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A checker that hung tells us nothing, and must not be quoted as if it did."""
        repo = _python_repo(tmp_path)
        (repo / "a.py").write_text("x = 1\n", encoding="utf-8")

        def slow(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired(cmd="mypy", timeout=1)

        monkeypatch.setattr(subprocess, "run", slow)
        assert collect_evidence(repo, ("a.py",)) is None


class TestPathsAreConfinedToTheRepository:
    """The changed-file list is derived from a diff, which the PR author writes."""

    @pytest.mark.parametrize(
        "path", ["/etc/passwd", "../outside.py", "src/../../escape.py", "C:\\windows\\a.py"]
    )
    def test_a_path_outside_the_repo_is_dropped(self, tmp_path: Path, path: str) -> None:
        """Not refused loudly — dropped, and if nothing survives there is no evidence.

        These arrive from `git diff --name-only` on author-controlled input, and
        the checkers are handed them as argv. A traversal that reached `mypy`
        would report on a file outside the review, and that report would enter
        the prompt as though it were about the diff.
        """
        assert collect_evidence(_python_repo(tmp_path), (path,)) is None


class TestTheFeatureShipsInert:
    """Off unless asked for, like `impact_threshold` before its threshold was chosen."""

    def test_absent_flag_yields_no_evidence(self, tmp_path: Path) -> None:
        """Default behaviour must be byte-identical to before this module existed.

        The effect on noise and recall is unmeasured — measuring it costs skeptic
        tokens — and a prompt change that has not been measured is a claim, not a
        result. #246 records what an unmeasured skeptic change did last time:
        few-shot examples swung recall from 0.7 to 0.0 and were reverted.
        """
        assert evidence_for(_collector(tmp_path), {}) is None

    @pytest.mark.parametrize("value", ["", "0", "true", "yes", " ", "on"])
    def test_only_the_literal_1_enables_it(self, tmp_path: Path, value: str) -> None:
        """A near-miss must not half-enable it.

        `"true"` reading as on would turn the feature on in an environment whose
        author believed they had written something inert, and the symptom —
        different verdicts — looks like model drift rather than a config typo.
        """
        assert evidence_for(_collector(tmp_path), {EVIDENCE_FLAG: value}) is None

    def test_the_flag_on_reaches_the_collector(self, tmp_path: Path) -> None:
        """And with it on, the repo is actually inspected rather than assumed."""
        repo = _python_repo(tmp_path)
        (repo / "a.py").write_text("x: int = 1\n", encoding="utf-8")
        evidence = evidence_for(_collector(repo, ("a.py",)), {EVIDENCE_FLAG: "1"})
        assert evidence is not None
        assert any("mypy" in c for c in evidence.commands)


class _FakeCollector:
    """The two attributes `evidence_for` reads, and nothing else."""

    def __init__(self, project_root: Path, changed: tuple[str, ...]) -> None:
        self.project_root = project_root
        self._changed = changed

    def get_changed_source_files(self) -> list[str]:
        return list(self._changed)


def _collector(root: Path, changed: tuple[str, ...] = ()) -> _FakeCollector:
    return _FakeCollector(root, changed)


class TestEveryOutcomeIsAnnounced:
    """A feature whose activation is unobservable cannot be verified after the fact.

    The first CI run of #402 had `GUARDIAN_EVIDENCE: 1` in the step's environment
    and the code on disk, and it was still impossible to say whether evidence was
    collected — because nothing said so. That is the defect this repository spent
    the day cataloguing, built fresh, hours after writing it down.
    """

    def test_the_off_state_is_logged(self, tmp_path: Path) -> None:
        with capture_logs() as logs:
            evidence_for(_collector(tmp_path), {})
        assert "disabled" in _text(logs)

    def test_a_missing_toolchain_is_logged(self, tmp_path: Path) -> None:
        with capture_logs() as logs:
            collect_evidence(tmp_path, ("a.py",))
        assert "pyproject" in _text(logs)

    def test_no_eligible_file_is_logged(self, tmp_path: Path) -> None:
        with capture_logs() as logs:
            collect_evidence(_python_repo(tmp_path), ("app.ts",))
        assert "no changed python file" in _text(logs)

    def test_success_reports_what_was_gathered(self, tmp_path: Path) -> None:
        """Counts, not just a boolean: "collected" with zero files is a lie of omission."""
        repo = _python_repo(tmp_path)
        (repo / "a.py").write_text("x: int = 1\n", encoding="utf-8")
        with capture_logs() as logs:
            assert collect_evidence(repo, ("a.py",)) is not None
        assert "evidence collected" in _text(logs)

    def test_a_failed_checker_is_a_warning_not_a_silence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The one branch that must not look like the ordinary absent case.

        No toolchain is normal. A checker that crashed is a broken environment,
        and reporting it at the same level as "this repo is TypeScript" would
        bury it.
        """
        repo = _python_repo(tmp_path)
        (repo / "a.py").write_text("x = 1\n", encoding="utf-8")

        def boom(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            _msg = "no such executable"
            raise OSError(_msg)

        monkeypatch.setattr(subprocess, "run", boom)
        with capture_logs() as logs:
            assert collect_evidence(repo, ("a.py",)) is None
        assert "could not run" in _text(logs)


def _text(logs: list[dict[str, Any]]) -> str:
    """Everything the captured structlog events said, lowercased.

    `caplog` sees nothing here: structlog is configured with its own pipeline
    and does not route through stdlib logging, so a caplog assertion would pass
    vacuously on an empty string — the shape these very tests exist to catch.
    """
    return " ".join(str(v) for entry in logs for v in entry.values()).lower()
