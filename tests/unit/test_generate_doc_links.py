"""The generated doc graph: signals, injection, idempotency.

The generator is imported in-process (not spawned) so its lines are covered by
the same coverage run Sonar reads; it is a standard-library script, so importing
it needs nothing but its path.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "generate_doc_links.py"

if TYPE_CHECKING:
    from types import ModuleType

START = "<!-- START_CGIS_RELATED -->"
END = "<!-- END_CGIS_RELATED -->"


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("cgis_generated_doc_links", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GENERATOR = _load_generator()


def _run(repo: Path, *flags: str) -> tuple[int, str, str]:
    """Run the generator against `repo`, returning (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = GENERATOR.main(["--repo", str(repo), *flags])
    return code, out.getvalue(), err.getvalue()


def _write(repo: Path, relative: str, text: str) -> Path:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_a_design_and_its_plan_link_to_each_other(tmp_path: Path) -> None:
    """Same date and slug is the strongest signal: the pair is written both ways."""
    _write(tmp_path, "docs/specs/2026-07-30-thing-design.md", "# Design\n\nA design.\n")
    _write(tmp_path, "docs/specs/plans/2026-07-30-thing-plan.md", "# Plan\n\nIts plan.\n")

    assert _run(tmp_path)[0] == 0

    design = (tmp_path / "docs/specs/2026-07-30-thing-design.md").read_text(encoding="utf-8")
    plan = (tmp_path / "docs/specs/plans/2026-07-30-thing-plan.md").read_text(encoding="utf-8")
    assert START in design
    assert END in design
    assert "**Related:**" in design
    assert "(plans/2026-07-30-thing-plan.md)" in design
    assert "**Related:**" in plan
    assert "(../2026-07-30-thing-design.md)" in plan


def test_shared_issue_references_link_two_documents(tmp_path: Path) -> None:
    """Two shared issue numbers is a conversation; one is a coincidence."""
    _write(tmp_path, "docs/specs/a.md", "# A\n\nContext from #495 and #496.\n")
    _write(tmp_path, "docs/lab-notes/b.md", "# B\n\nMeasured on #495, #496, #497.\n")
    _write(tmp_path, "docs/specs/c.md", "# C\n\nNothing in common at all here.\n")

    assert _run(tmp_path)[0] == 0

    first = (tmp_path / "docs/specs/a.md").read_text(encoding="utf-8")
    second = (tmp_path / "docs/lab-notes/b.md").read_text(encoding="utf-8")
    orphan = (tmp_path / "docs/specs/c.md").read_text(encoding="utf-8")
    assert "(../lab-notes/b.md)" in first
    assert "(../specs/a.md)" in second
    assert START not in orphan


def test_code_links_point_at_a_real_file_or_package(tmp_path: Path) -> None:
    """The module a document names becomes a link that resolves from the document."""
    _write(
        tmp_path,
        "docs/how-to/use.md",
        "# Use\n\nIt lives in `cgis.query` and `scripts/check_test_count.py`, "
        "see `src/cgis/query/engine.py`.\n",
    )
    _write(tmp_path, "src/cgis/query/__init__.py", "")
    _write(tmp_path, "src/cgis/query/engine.py", "")
    _write(tmp_path, "scripts/check_test_count.py", "")

    assert _run(tmp_path)[0] == 0

    body = (tmp_path / "docs/how-to/use.md").read_text(encoding="utf-8")
    assert "**Code:**" in body
    assert "(../../src/cgis/query/)" in body
    assert "(../../scripts/check_test_count.py)" in body
    # The package link already covers its files; a nested path would be noise.
    assert "(../../src/cgis/query/engine.py)" not in body
    for relative in ("../../src/cgis/query", "../../scripts/check_test_count.py"):
        assert (tmp_path / "docs/how-to" / relative).resolve().exists()


def test_running_twice_changes_nothing_and_check_passes(tmp_path: Path) -> None:
    """Autodoc commits the output, so a second run must be a no-op."""
    _write(tmp_path, "docs/specs/2026-07-30-thing-design.md", "# Design\n")
    _write(tmp_path, "docs/specs/2026-07-30-thing-plan.md", "# Plan\n")

    assert _run(tmp_path)[0] == 0
    first = (tmp_path / "docs/INDEX.md").read_text(encoding="utf-8")
    design = (tmp_path / "docs/specs/2026-07-30-thing-design.md").read_text(encoding="utf-8")
    assert _run(tmp_path)[0] == 0
    assert (tmp_path / "docs/INDEX.md").read_text(encoding="utf-8") == first
    assert (tmp_path / "docs/specs/2026-07-30-thing-design.md").read_text(
        encoding="utf-8"
    ) == design
    assert _run(tmp_path, "--check")[0] == 0


def test_check_fails_when_a_document_changed_without_rerunning(tmp_path: Path) -> None:
    """A new reference that nobody regenerated is what `--check` exists to catch."""
    _write(tmp_path, "docs/specs/a.md", "# A\n\nSee #495 and #496.\n")
    _write(tmp_path, "docs/specs/b.md", "# B\n\nAlso #495 and #496.\n")
    assert _run(tmp_path)[0] == 0

    _write(tmp_path, "docs/specs/a.md", "# A\n\nSee #495, #496 and #12345.\n")
    code, _out, err = _run(tmp_path, "--check")
    assert code == 1
    assert "stale: docs/specs/a.md" in err


def test_a_generated_link_that_no_longer_resolves_is_reported(tmp_path: Path) -> None:
    """A deleted target must not become a silent dead end in the index."""
    index = tmp_path / "docs/INDEX.md"
    index.parent.mkdir(parents=True, exist_ok=True)
    broken = GENERATOR.broken_links(
        tmp_path,
        {index: "See [missing](../src/cgis/gone.py) and [fine](./INDEX.md).\n"},
    )
    assert any("src/cgis/gone.py" in item for item in broken)
    assert not any("fine" in item for item in broken)


def test_a_git_checkout_only_counts_tracked_documents(tmp_path: Path) -> None:
    """An untracked draft is not part of the graph; git decides what is tracked."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=60)
    _write(tmp_path, "docs/specs/a.md", "# A\n")
    tracked = _write(tmp_path, "docs/specs/b.md", "# B\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "docs/specs/a.md"], check=True, timeout=60)

    assert _run(tmp_path)[0] == 0

    index = (tmp_path / "docs/INDEX.md").read_text(encoding="utf-8")
    assert "(specs/a.md)" in index
    assert "(specs/b.md)" not in index
    assert tracked.exists()


def test_the_index_names_the_pair_and_the_orphans(tmp_path: Path) -> None:
    """`docs/INDEX.md` is the one place that lists both, deterministically."""
    _write(tmp_path, "docs/specs/2026-07-30-thing-design.md", "# Design\n")
    _write(tmp_path, "docs/specs/2026-07-30-thing-plan.md", "# Plan\n")
    _write(tmp_path, "docs/specs/lonely.md", "# Lonely\n\nNo signal reaches this one.\n")

    assert _run(tmp_path)[0] == 0

    index = (tmp_path / "docs/INDEX.md").read_text(encoding="utf-8")
    assert "# Documentation index" in index
    assert "## Design and its plan" in index
    assert "(specs/2026-07-30-thing-plan.md)" in index
    assert "## No automatic links" in index
    assert "(specs/lonely.md)" in index


def test_mdx_documents_pair_by_date_and_slug(tmp_path: Path) -> None:
    """`.mdx` is a document too: the date+slug regex must not skip it."""
    _write(tmp_path, "docs/specs/2026-07-30-thing.mdx", "# Thing\n")
    _write(tmp_path, "docs/specs/plans/2026-07-30-thing-plan.md", "# Plan\n")

    assert _run(tmp_path)[0] == 0

    mdx = (tmp_path / "docs/specs/2026-07-30-thing.mdx").read_text(encoding="utf-8")
    assert "(plans/2026-07-30-thing-plan.md)" in mdx


def test_an_unpaired_marker_is_an_error(tmp_path: Path) -> None:
    """A half-removed block would be duplicated by a write, so the run has to stop."""
    _write(tmp_path, "docs/specs/x.md", f"body\n{START}\n")
    with pytest.raises(GENERATOR.MalformedMarkersError):
        _run(tmp_path)


def test_archived_documents_are_frozen(tmp_path: Path) -> None:
    """Archived history gets no generated block and no place in the index."""
    _write(tmp_path, "docs/archive/specs/2026-01-01-old.md", "# Old\n")
    _write(tmp_path, "docs/specs/new.md", "# New\n")

    assert _run(tmp_path)[0] == 0

    archived = (tmp_path / "docs/archive/specs/2026-01-01-old.md").read_text(encoding="utf-8")
    index = (tmp_path / "docs/INDEX.md").read_text(encoding="utf-8")
    assert START not in archived
    assert "archive/specs" not in index
