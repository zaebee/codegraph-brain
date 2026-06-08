"""Extract the SQLite schema from SQLiteStore._create_schema() and inject it into HOW_IT_WORKS.md.

Reads the Python source of _create_schema() via AST, extracts the SQL string, and replaces
the content between <!-- START_CGIS_SCHEMA --> and <!-- END_CGIS_SCHEMA --> anchors.
Idempotent — running multiple times produces identical output.
"""

import ast
from pathlib import Path

_STORE = Path("src/cgis/storage/sqlite_store.py")
_HOW_IT_WORKS = Path("docs/architecture/HOW_IT_WORKS.md")
_START = "<!-- START_CGIS_SCHEMA -->"
_END = "<!-- END_CGIS_SCHEMA -->"


def _extract_schema_sql(store_path: Path) -> str:
    """Parse _create_schema() with AST and return the SQL string literal it assigns to `schema`."""
    source = store_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_create_schema":
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.Assign):
                    for target in stmt.targets:
                        if (
                            isinstance(target, ast.Name)
                            and target.id == "schema"
                            and isinstance(stmt.value, ast.Constant)
                            and isinstance(stmt.value.value, str)
                        ):
                            return stmt.value.value.strip()
    msg = f'Cannot find `schema = """..."""` assignment in _create_schema() in {store_path}'
    raise ValueError(msg)


def inject_schema() -> None:
    """Extract schema SQL and inject it into HOW_IT_WORKS.md between anchor comments."""
    if not _STORE.exists():
        msg = f"Store source not found at {_STORE}"
        raise FileNotFoundError(msg)
    if not _HOW_IT_WORKS.exists():
        msg = f"Docs file not found at {_HOW_IT_WORKS}"
        raise FileNotFoundError(msg)

    sql = _extract_schema_sql(_STORE)
    block = f"{_START}\n```sql\n{sql}\n```\n{_END}"

    content = _HOW_IT_WORKS.read_text(encoding="utf-8")
    if _START not in content or _END not in content:
        msg = f"Anchor tags not found in {_HOW_IT_WORKS}. Add {_START!r} and {_END!r}."
        raise ValueError(msg)

    head, rest = content.split(_START, 1)
    _, tail = rest.split(_END, 1)
    _HOW_IT_WORKS.write_text("".join([head, block, tail]), encoding="utf-8")  # NOSONAR
    print(f"✅ Injected schema ({sql.count('CREATE TABLE')} tables) into {_HOW_IT_WORKS}")


if __name__ == "__main__":
    inject_schema()
