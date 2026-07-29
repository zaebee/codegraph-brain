"""Extract the exact source lines of a focal node for the prompt compiler (#19).

Feeding an LLM the focal node's real code is the single biggest accuracy lever
in the GraphRAG context package, yet we never want to load whole files. This
leaf reads only ``start_line..end_line`` via :mod:`linecache` and degrades to an
empty string on any I/O problem so context generation never crashes.

It also owns the mapping from a node's *stored* ``file_path`` to a real path on
disk (``resolve_source_path``), since where the file actually lives is a
filesystem question, not a graph one.
"""

import linecache
from pathlib import Path


def _collapsed_join(root: Path, parts: tuple[str, ...]) -> Path | None:
    """Join ``root`` with ``parts`` when the two overlap at the boundary.

    ``root=/repo/src`` and ``parts=("src", "pkg", "m.py")`` describe the same
    file from two directions; the longest matching overlap is dropped once so
    the result is ``/repo/src/pkg/m.py`` rather than ``/repo/src/src/pkg/m.py``.
    Returns ``None`` when the two do not overlap at all.
    """
    root_parts = root.parts
    for size in range(min(len(root_parts), len(parts)), 0, -1):
        if root_parts[-size:] == parts[:size]:
            return root.joinpath(*parts[size:])
    return None


def resolve_source_path(file_path: str, source_root: str = "") -> str:
    """Locate a node's stored ``file_path`` on disk, first existing candidate wins (#228).

    Stored paths are relative to whatever directory was ingested, so the same
    file reads as ``pkg/m.py`` (``cgis ingest ./src``) or ``src/pkg/m.py``
    (``cgis ingest .``). Blindly prepending ``source_root`` breaks the second
    layout — ``src/src/pkg/m.py`` — and the miss is silent, because a missing
    snippet degrades to "(source unavailable)". Candidates are therefore tried
    in order: the explicit ``source_root`` join, the stored path as-is
    (CWD-relative), then the join with a duplicated boundary segment collapsed.

    Backslash separators from a Windows ingest are normalised first. With no
    ``source_root`` the stored path is returned untouched; when nothing exists
    on disk the ``source_root`` join is returned, so the caller's explicit
    intent is what surfaces in any downstream diagnostics.
    """
    relative = file_path.replace("\\", "/")
    if not source_root:
        return relative
    root = Path(source_root)
    joined = root / relative
    candidates = [joined, Path(relative)]
    collapsed = _collapsed_join(root, Path(relative).parts)
    if collapsed is not None:
        candidates.append(collapsed)
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return str(joined)


def extract_snippet(file_path: str, start_line: int, end_line: int) -> str:
    """Return source lines ``start_line..end_line`` (inclusive, 1-based).

    ``start_line`` below 1 is clamped to the first line; an ``end_line`` past
    EOF truncates to whatever lines exist. A missing or unreadable file yields
    an empty string rather than raising — a snippet is best-effort enrichment,
    not a hard dependency of the context package.
    """
    # Drop any stale cache entry so freshly written/edited files read correctly.
    linecache.checkcache(file_path)
    start = max(1, start_line)
    lines: list[str] = []
    for i in range(start, end_line + 1):
        line = linecache.getline(file_path, i)
        if not line:
            # Empty string means EOF (a blank source line is "\n"); stop early so a
            # corrupt/huge end_line can't spin millions of empty reads.
            break
        lines.append(line)
    return "".join(lines)
