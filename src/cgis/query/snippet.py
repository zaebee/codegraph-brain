"""Extract the exact source lines of a focal node for the prompt compiler (#19).

Feeding an LLM the focal node's real code is the single biggest accuracy lever
in the GraphRAG context package, yet we never want to load whole files. This
leaf reads only ``start_line..end_line`` via :mod:`linecache` and degrades to an
empty string on any I/O problem so context generation never crashes.
"""

import linecache


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
    lines = [linecache.getline(file_path, i) for i in range(start, end_line + 1)]
    return "".join(lines)
