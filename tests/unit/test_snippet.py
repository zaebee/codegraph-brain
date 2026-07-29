"""Unit tests for the focal-node source snippet extractor (#19)."""

from pathlib import Path

import pytest

from cgis.query.context.snippet import extract_snippet, resolve_source_path


def _write(tmp_path: Path, lines: list[str]) -> str:
    path = tmp_path / "sample.py"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def test_extracts_inclusive_one_based_range(tmp_path: Path) -> None:
    """Lines start..end are returned inclusive, 1-based, in order."""
    path = _write(tmp_path, [f"line{i}" for i in range(1, 11)])

    snippet = extract_snippet(path, 5, 7)

    assert snippet == "line5\nline6\nline7\n"


def test_single_line_range(tmp_path: Path) -> None:
    """A start==end range yields exactly that one line."""
    path = _write(tmp_path, ["alpha", "beta", "gamma"])

    assert extract_snippet(path, 2, 2) == "beta\n"


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    """A nonexistent file degrades to an empty string, never raises."""
    assert extract_snippet(str(tmp_path / "nope.py"), 1, 5) == ""


def test_end_beyond_eof_truncates(tmp_path: Path) -> None:
    """An end line past EOF returns only the available lines, no crash."""
    path = _write(tmp_path, ["only", "two"])

    assert extract_snippet(path, 1, 99) == "only\ntwo\n"


def test_start_below_one_is_clamped(tmp_path: Path) -> None:
    """A start line below 1 is clamped to the first line."""
    path = _write(tmp_path, ["first", "second"])

    assert extract_snippet(path, 0, 2) == "first\nsecond\n"


def test_blank_middle_line_preserved(tmp_path: Path) -> None:
    """A blank line inside the range is kept — early-EOF break must trigger only on real EOF."""
    path = _write(tmp_path, ["a", "", "c"])
    assert extract_snippet(path, 1, 3) == "a\n\nc\n"


def _touch(tmp_path: Path, relative: str) -> Path:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x = 1\n", encoding="utf-8")
    return path


def test_resolve_joins_source_root_when_file_lives_under_it(tmp_path: Path) -> None:
    """The documented case: a stored path relative to the ingest root joins onto source_root."""
    _touch(tmp_path, "pkg/mod.py")

    assert resolve_source_path("pkg/mod.py", str(tmp_path)) == str(tmp_path / "pkg" / "mod.py")


def test_resolve_falls_back_to_stored_path_when_join_misses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#228: file_path already carries the source_root segment — the bare path still resolves."""
    _touch(tmp_path, "src/pkg/mod.py")
    monkeypatch.chdir(tmp_path)

    # Naive join would be src/src/pkg/mod.py, which does not exist.
    assert resolve_source_path("src/pkg/mod.py", "src") == "src/pkg/mod.py"


def test_resolve_collapses_duplicated_root_segment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#228 from another CWD: an absolute source_root overlapping the stored prefix collapses."""
    _touch(tmp_path, "src/pkg/mod.py")
    monkeypatch.chdir(tmp_path.parent)

    resolved = resolve_source_path("src/pkg/mod.py", str(tmp_path / "src"))

    assert resolved == str(tmp_path / "src" / "pkg" / "mod.py")


def test_resolve_keeps_source_root_join_when_nothing_exists(tmp_path: Path) -> None:
    """With no candidate on disk the explicit source_root join is kept — behaviour unchanged."""
    assert resolve_source_path("pkg/gone.py", str(tmp_path)) == str(tmp_path / "pkg" / "gone.py")


def test_resolve_without_source_root_returns_stored_path() -> None:
    """An empty source_root leaves the stored path untouched (CWD-relative lookup)."""
    assert resolve_source_path("pkg/mod.py", "") == "pkg/mod.py"


def test_resolve_skips_candidate_that_cannot_be_stat_ed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An un-stat-able candidate is skipped, never raised — the module degrades, it never crashes.

    ``Path.is_file()`` only swallows ENOENT/ENOTDIR/EBADF/ELOOP; EACCES and (here)
    ENAMETOOLONG propagate. A source_root the OS refuses to stat must not take
    down context generation.
    """
    _touch(tmp_path, "m.py")
    monkeypatch.chdir(tmp_path)

    assert resolve_source_path("m.py", "x" * 300) == "m.py"


def test_resolve_normalizes_windows_separators(tmp_path: Path) -> None:
    """A backslash path from a Windows ingest is normalised before any join."""
    _touch(tmp_path, "pkg/mod.py")

    assert resolve_source_path("pkg\\mod.py", str(tmp_path)) == str(tmp_path / "pkg" / "mod.py")
