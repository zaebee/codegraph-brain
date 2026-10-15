"""The Go pack's naming: package scope, per-file FILE ids, registration (step 7b)."""

from cgis.languages import is_supported, pack_for_path
from cgis.languages.go.naming import GO_LANG, file_node_id, is_test, scope_of


def test_scope_is_the_package_directory() -> None:
    assert scope_of("internal/server/server.go", None) == "internal.server"
    assert scope_of("src/internal/server/server.go", "src") == "internal.server"
    assert scope_of("./src/internal/server/server.go".removeprefix("./"), "./src") == (
        "internal.server"
    )


def test_root_files_have_an_empty_directory_scope() -> None:
    """The extractor names root definitions by the package clause; scope_of cannot see it."""
    assert scope_of("main.go", None) == ""


def test_file_node_id_is_unique_per_file() -> None:
    assert file_node_id("internal/server/server.go", None) == "internal.server.server_go"
    assert file_node_id("internal/server/types.go", None) == "internal.server.types_go"
    assert file_node_id("main.go", None) == "main_go"


def test_a_windows_path_names_the_same_package() -> None:
    assert scope_of("internal\\server\\server.go", None) == "internal.server"


def test_test_files_end_in_test_go() -> None:
    assert is_test("internal/server/server_test.go")
    assert not is_test("internal/server/server.go")
    assert not is_test("internal/testing/helpers.go")


def test_go_is_registered() -> None:
    assert is_supported("cmd/app/main.go")
    pack = pack_for_path("cmd/app/main.go")
    assert pack is not None
    assert pack.name == GO_LANG == "go"
    assert pack.is_test("server_test.go")
    assert not pack.is_test("server.go")
    assert pack.file_node_id("cmd/app/main.go", None) == "cmd.app.main_go"
    assert pack.scope_of("cmd/app/main.go", None) == "cmd.app"
