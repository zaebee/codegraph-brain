"""The `diff_check` feature: diff-check's dependants as leads in the finder's context (#733)."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from cgis.guardian.collector import ContextCollector, parse_features
from cgis.guardian.prompts import PromptBuilder
from cgis.languages import extractors
from cgis.pipeline import IngestionPipeline
from cgis.storage.sqlite_store import SQLiteStore

CALLEE = "def total(items):\n    return sum(items)\n"
CALLER = "from calc import total\n\n\ndef report(items):\n    return f'{total(items)}'\n"


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def _commit(repo: Path, files: dict[str, str], message: str) -> str:
    for name, text in files.items():
        (repo / name).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def changed_callee(tmp_path: Path) -> tuple[Path, str, Path]:
    """(repo, base sha, graph db): `calc.total` changes, `report.report` calls it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    base = _commit(repo, {"calc.py": CALLEE, "report.py": CALLER}, "base")
    _commit(repo, {"calc.py": CALLEE.replace("sum(items)", "sum(items) or None")}, "edit")
    db = tmp_path / "g.db"
    store = SQLiteStore(str(db))
    store.connect()
    with store:
        IngestionPipeline(extractors([], data_keys=True), data_keys=True).run(
            str(repo), store=store, rebuild=True
        )
    return repo, base, db


def _collector(repo: Path, base: str, db: Path | None, *features: str) -> ContextCollector:
    return ContextCollector(
        project_root=repo, base_ref=base, db_path=db, source_root="", features=frozenset(features)
    )


def test_diff_check_is_a_feature() -> None:
    """A typo in GUARDIAN_FEATURES raises; the real name must not."""
    assert parse_features("diff_check") == frozenset({"diff_check"})


def test_the_caller_of_a_changed_function_is_listed(changed_callee: tuple[Path, str, Path]) -> None:
    """The lead the finder would otherwise never see: a caller outside the diff."""
    repo, base, db = changed_callee
    context = _collector(repo, base, db, "diff_check").collect_all()
    assert "report.report" in context["dependants"]
    assert "report.py" in context["dependants"]


def test_without_the_flag_nothing_is_added(changed_callee: tuple[Path, str, Path]) -> None:
    """Off by default: the production prompt is unchanged until the flag is set."""
    repo, base, db = changed_callee
    assert "dependants" not in _collector(repo, base, db).collect_all()


def test_without_a_graph_nothing_is_added(changed_callee: tuple[Path, str, Path]) -> None:
    """No graph, no leads — and no error."""
    repo, base, _db = changed_callee
    assert "dependants" not in _collector(repo, base, None, "diff_check").collect_all()


def test_a_range_git_cannot_read_degrades_to_nothing(
    changed_callee: tuple[Path, str, Path],
) -> None:
    """A failing diff-check costs the section, never the review."""
    repo, _base, db = changed_callee
    context = _collector(repo, "no-such-ref", db, "diff_check").collect_all()
    assert "dependants" not in context


def test_the_leads_get_their_own_section_and_rule() -> None:
    """The finder is told these are leads to read, anchored on a line of the diff."""
    prompt = PromptBuilder.build_user_prompt(
        {"diff": "+x", "dependants": "  [caller] report.report (report.py:4) - calls total"}
    )
    assert "CODE OUTSIDE THIS DIFF THAT DEPENDS ON IT" in prompt
    assert "[caller] report.report" in prompt
    assert "leads, not findings" in prompt


def test_no_leads_no_section() -> None:
    """Without the key the prompt is byte-identical to today's."""
    prompt = PromptBuilder.build_user_prompt({"diff": "+x"})
    assert "DEPENDS ON IT" not in prompt


def test_a_base_git_cannot_diff_drops_the_section(changed_callee: tuple[Path, str, Path]) -> None:
    """A merge base that resolves but cannot be diffed is DiffError, logged and dropped."""
    repo, _base, db = changed_callee
    graph = _collector(repo, "HEAD", db, "diff_check").graph
    assert graph.collect_dependants(repo, "0" * 40) == ""


def test_an_unexpected_error_drops_the_section(changed_callee: tuple[Path, str, Path]) -> None:
    """Whatever diff-check raises, the review goes on without the section."""
    repo, base, db = changed_callee
    graph = _collector(repo, base, db, "diff_check").graph
    with patch("cgis.guardian.collector.check_database", side_effect=RuntimeError("boom")):
        assert graph.collect_dependants(repo, base) == ""


def test_a_change_nothing_depends_on_adds_no_section(
    changed_callee: tuple[Path, str, Path],
) -> None:
    """An empty list is not a section: the finder would read 'nothing outside tests' as a claim."""
    repo, _base, db = changed_callee
    head = _git(repo, "rev-parse", "HEAD")
    graph = _collector(repo, head, db, "diff_check").graph
    assert graph.collect_dependants(repo, head) == ""
