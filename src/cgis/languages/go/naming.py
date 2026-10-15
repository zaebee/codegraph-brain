"""Go names from file paths — no tree-sitter, so the pack names files grammar-free.

A Go package is a directory: every file in it shares one namespace, so a file's
definitions live at `<package scope>.<Name>` while its FILE node needs an id of
its own, `<package scope>.<stem>_go` (step 7, ruling H1).
"""

from pathlib import PurePosixPath

#: `Node.language` for everything this pack emits.
GO_LANG = "go"

_GO_SUFFIX = ".go"
_TEST_SUFFIX = "_test.go"


def _relative(file_path: str, source_root: str | None) -> PurePosixPath:
    """The path with a drive letter, leading slashes and the source root removed."""
    clean = file_path
    if len(clean) >= 2 and clean[1] == ":" and clean[0].isalpha():
        clean = clean[2:]
    clean = clean.replace("\\", "/").lstrip("/")
    if source_root:
        root = source_root.replace("\\", "/").removeprefix("./").strip("/") + "/"
        clean = clean.removeprefix(root)
    return PurePosixPath(clean)


def scope_of(file_path: str, source_root: str | None = None) -> str:
    """The package directory, dotted: `internal/server/x.go` -> `internal.server`.

    Empty for a file at the root, whose definitions the extractor names by the
    package clause instead — a path cannot say what that is.
    """
    parent = _relative(file_path, source_root).parent
    return "" if str(parent) == "." else ".".join(parent.parts)


def file_node_id(file_path: str, source_root: str | None = None) -> str:
    """The FILE node id: the package scope, then the file stem with a `_go` suffix."""
    relative = _relative(file_path, source_root)
    stem = relative.name.removesuffix(_GO_SUFFIX) + "_go"
    scope = scope_of(file_path, source_root)
    return f"{scope}.{stem}" if scope else stem


def is_test(file_path: str) -> bool:
    """Go's own rule: a test file's name ends in `_test.go`, wherever it lives."""
    return file_path.replace("\\", "/").endswith(_TEST_SUFFIX)
