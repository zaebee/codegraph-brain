"""Declared Python dependencies decide a root the graph cannot (#495).

`app/alembic/` and the alembic library share a root; only the manifest says which
references are the library. These tests cover which manifests are found, what is
read from them, and that the pipeline classifies by them.
"""

from pathlib import Path

from cgis.core.models import Node, NodeNamespace
from cgis.extractors.python_extractor import PythonExtractor
from cgis.pipeline import IngestionPipeline
from cgis.python_dependencies import declared_import_roots, import_name
from cgis.storage.sqlite_store import SQLiteStore

PYPROJECT = """
[project]
name = "owner-api"
dependencies = ["alembic>=1.12.1", "python-dateutil", "SQLAlchemy[asyncio]==2.0"]

[project.optional-dependencies]
docs = ["mkdocs ; python_version >= '3.12'"]

[dependency-groups]
dev = ["pytest", {include-group = "docs"}]

[tool.poetry.dependencies]
python = "^3.12"
PyYAML = "*"

[tool.poetry.group.test.dependencies]
Faker = "*"
"""

MIGRATION = """\
from alembic import op


def upgrade() -> None:
    op.add_column("users", "x")
"""

ENV = """\
def run_migrations() -> None:
    pass
"""

MAIN = """\
from alembic.env import run_migrations


def boot() -> None:
    run_migrations()
"""


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _owner_api(root: Path, dependencies: str = '"alembic>=1.12.1"') -> Path:
    """A project with its manifest one level above the ingest root, as owner-api has."""
    _write(
        root,
        {
            ".git/HEAD": "",
            "pyproject.toml": f"[project]\nname = 'x'\ndependencies = [{dependencies}]\n",
            "app/alembic/env.py": ENV,
            "app/alembic/versions/0001_add.py": MIGRATION,
            "app/main.py": MAIN,
        },
    )
    return root / "app"


def _namespaces(nodes: list[Node]) -> dict[str, NodeNamespace]:
    return {node.id: node.namespace for node in nodes}


def test_reads_every_pyproject_table(tmp_path: Path) -> None:
    """PEP 621 deps and extras, PEP 735 groups, Poetry deps and groups; never `python`."""
    _write(tmp_path, {"pyproject.toml": PYPROJECT, ".git/HEAD": ""})
    assert declared_import_roots(tmp_path) == {
        "alembic",
        "dateutil",
        "sqlalchemy",
        "mkdocs",
        "pytest",
        "yaml",
        "faker",
    }


def test_reads_requirements_files_and_skips_options(tmp_path: Path) -> None:
    """Every `requirements*.txt` beside the manifest; `-r`, `-e`, paths and URLs name nothing."""
    _write(
        tmp_path,
        {
            ".git/HEAD": "",
            "requirements.txt": "alembic==1.13  # migrations\n-r requirements-dev.txt\n\n",
            "requirements-dev.txt": (
                "-e .\n./vendored\nlibs/pkg\nC:\\libs\\pkg\nhttps://x/y.whl\n"
                "beautifulsoup4\nhttpx @ https://x/httpx.whl\n"
            ),
        },
    )
    assert declared_import_roots(tmp_path) == {"alembic", "bs4", "httpx"}


def test_a_byte_order_mark_does_not_hide_the_first_requirement(tmp_path: Path) -> None:
    """Editors on Windows save UTF-8 with a BOM; the first line and the TOML must still parse."""
    _write(tmp_path, {".git/HEAD": ""})
    (tmp_path / "requirements.txt").write_text("alembic\n", encoding="utf-8-sig")
    (tmp_path / "pyproject.toml").write_text(
        "[project]\ndependencies = ['fastapi']\n", encoding="utf-8-sig"
    )
    assert declared_import_roots(tmp_path) == {"alembic", "fastapi"}


def test_finds_the_nearest_manifest_above_the_ingest_root(tmp_path: Path) -> None:
    """`cgis ingest backend/app` reads `backend/pyproject.toml`, not one further up."""
    _write(
        tmp_path,
        {
            ".git/HEAD": "",
            "pyproject.toml": "[project]\ndependencies = ['django']\n",
            "backend/pyproject.toml": "[project]\ndependencies = ['alembic']\n",
            "backend/app/main.py": "",
        },
    )
    assert declared_import_roots(tmp_path / "backend" / "app") == {"alembic"}


def test_never_crosses_the_repository_boundary(tmp_path: Path) -> None:
    """A checkout nested inside another project must not read its parent's dependencies."""
    _write(
        tmp_path,
        {
            "pyproject.toml": "[project]\ndependencies = ['alembic']\n",
            "checkout/.git/HEAD": "",
            "checkout/app/main.py": "",
        },
    )
    assert declared_import_roots(tmp_path / "checkout" / "app") == frozenset()


def test_an_unreadable_manifest_declares_nothing(tmp_path: Path) -> None:
    """One malformed file must not stop the ingest."""
    _write(tmp_path, {".git/HEAD": "", "pyproject.toml": "[project\n"})
    assert declared_import_roots(tmp_path) == frozenset()


def test_import_name_normalises_and_maps_known_exceptions() -> None:
    """PEP 503 folding, lowercased, then the table of distributions named unlike their import."""
    assert import_name("Flask-SQLAlchemy") == "flask_sqlalchemy"
    assert import_name("python-dateutil") == "dateutil"
    assert import_name("Pillow") == "PIL"


def test_pipeline_classifies_the_library_external_and_our_package_internal(
    tmp_path: Path,
) -> None:
    """The acceptance case: `from alembic import op` beside our own `app/alembic/`."""
    app = _owner_api(tmp_path)
    nodes, _, _ = IngestionPipeline({".py": PythonExtractor()}).run(str(app))
    namespaces = _namespaces(nodes)
    assert namespaces["alembic.op.add_column"] is NodeNamespace.EXTERNAL
    assert namespaces["alembic.env.run_migrations"] is NodeNamespace.INTERNAL


def test_pipeline_without_the_dependency_keeps_the_honest_floor(tmp_path: Path) -> None:
    """Undeclared, the collision stays UNKNOWN — counted unresolved, as since #459."""
    app = _owner_api(tmp_path, dependencies='"fastapi"')
    nodes, _, _ = IngestionPipeline({".py": PythonExtractor()}).run(str(app))
    assert _namespaces(nodes)["alembic.op.add_column"] is NodeNamespace.UNKNOWN


def test_declaring_a_dependency_rebuilds_an_incremental_graph(tmp_path: Path) -> None:
    """No source changed, but what `alembic.op` means did: the stored graph must follow."""
    app = _owner_api(tmp_path, dependencies='"fastapi"')
    pipeline = IngestionPipeline({".py": PythonExtractor()})
    with SQLiteStore(str(tmp_path / "graph.db")) as store:
        pipeline.run(str(app), store=store)
        assert store.get_python_dependencies() == ["fastapi"]
        (tmp_path / "pyproject.toml").write_text(
            "[project]\nname = 'x'\ndependencies = ['fastapi', 'alembic']\n", encoding="utf-8"
        )
        nodes, _, _ = pipeline.run(str(app), store=store)
        assert store.get_python_dependencies() == ["alembic", "fastapi"]
    assert _namespaces(nodes)["alembic.op.add_column"] is NodeNamespace.EXTERNAL
