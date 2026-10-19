"""Data-file names from paths — no parser, so the pack names files without loading one.

A JSON or YAML file is its own scope. Its FILE node id is the directory, dotted,
then the file name with its dots made underscores (`opensearch.mappings.events_json`),
as Go names `x.go` `x_go`: never the id of a Python module beside it (`config.py`).
"""

from pathlib import PurePosixPath

#: `Node.language` for everything this pack emits.
DATA_LANG = "data"


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


def file_node_id(file_path: str, source_root: str | None = None) -> str:
    """The FILE node id: `deploy/app.config.yml` -> `deploy.app_config_yml`."""
    relative = _relative(file_path, source_root)
    stem = relative.name.replace(".", "_")
    parent = relative.parent
    return stem if str(parent) == "." else ".".join((*parent.parts, stem))
