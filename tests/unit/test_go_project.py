"""The Go pack's `go.mod` reader (step 7b, Task B2)."""

from pathlib import Path

from cgis.languages.go.project import GO_MODULE_KEY, GoFacts, GoProjectReader, parse_go_mod

GO_MOD = """\
// The shop.
module example.com/app

go 1.22

require (
\tgithub.com/x/y v1.2.3
\tgolang.org/x/text v0.14.0 // indirect
)

require github.com/single/dep v0.1.0

replace example.com/lib => ./lib

replace (
\tgithub.com/x/y v1.2.3 => ../elsewhere
\tgithub.com/forked/z => github.com/me/z v1.0.0
)
"""


def _reader_over(root: Path) -> GoProjectReader:
    reader = GoProjectReader(root)
    for path in sorted(root.rglob("*")):
        if path.is_file():
            reader.note(path)
    reader.note_above([], bounded=True)
    return reader


def test_the_module_its_requires_and_internal_replaces(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text(GO_MOD, encoding="utf-8")
    (tmp_path / "lib").mkdir()
    assert _reader_over(tmp_path).facts().state() == {
        GO_MODULE_KEY: {
            "path": "example.com/app",
            "requires": ["github.com/single/dep", "github.com/x/y", "golang.org/x/text"],
            "replaces": {"example.com/lib": "lib"},
            "offset": "",
        }
    }


def test_a_replace_outside_the_repository_or_to_a_module_is_dropped(tmp_path: Path) -> None:
    module = parse_go_mod(GO_MOD, tmp_path / "go.mod", tmp_path)
    assert "github.com/x/y" not in module["replaces"]  # type: ignore[operator]
    assert "github.com/forked/z" not in module["replaces"]  # type: ignore[operator]


def test_no_go_mod_declares_no_key(tmp_path: Path) -> None:
    """Not `{}`: a Go-free graph's ingest_state stays as it was (step 7b ruling)."""
    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
    assert _reader_over(tmp_path).facts().state() == {}
    assert GoFacts().state() == {}


def test_only_the_root_go_mod_is_read_during_the_walk(tmp_path: Path) -> None:
    """A nested go.mod is another module (go.work is out of the pilot's scope)."""
    (tmp_path / "go.mod").write_text("module example.com/app\n", encoding="utf-8")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "go.mod").write_text("module example.com/tools\n", encoding="utf-8")
    facts = _reader_over(tmp_path).facts().state()
    assert facts[GO_MODULE_KEY]["path"] == "example.com/app"  # type: ignore[index]


def test_the_nearest_go_mod_above_counts_when_the_root_has_none(tmp_path: Path) -> None:
    """`cgis ingest internal/` still belongs to the module declared above it."""
    (tmp_path / "go.mod").write_text("module example.com/app\n", encoding="utf-8")
    root = tmp_path / "internal"
    root.mkdir()
    reader = GoProjectReader(root)
    reader.note_above([tmp_path], bounded=True)
    module = reader.facts().state()[GO_MODULE_KEY]
    assert module == {
        "path": "example.com/app",
        "requires": [],
        "replaces": {},
        "offset": "internal",
    }


def test_the_root_go_mod_wins_over_one_above(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/outer\n", encoding="utf-8")
    root = tmp_path / "inner"
    root.mkdir()
    (root / "go.mod").write_text("module example.com/inner\n", encoding="utf-8")
    reader = GoProjectReader(root)
    reader.note(root / "go.mod")
    reader.note_above([tmp_path], bounded=True)
    assert reader.facts().state()[GO_MODULE_KEY]["path"] == "example.com/inner"  # type: ignore[index]


def test_a_go_mod_is_claimed_and_other_files_are_not(tmp_path: Path) -> None:
    reader = GoProjectReader(tmp_path)
    assert reader.note(tmp_path / "go.mod") is True
    assert reader.note(tmp_path / "main.go") is False
