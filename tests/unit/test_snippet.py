"""Unit tests for the focal-node source snippet extractor (#19)."""

from pathlib import Path

from cgis.query.snippet import extract_snippet


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
