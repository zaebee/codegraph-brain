"""Diff check: what else depends on what a diff changed (subclasses, callers).

`hunks` reads the changed files and lines out of git; `check` maps them onto the
graph and lists the code that stands on the changed symbols.
"""

from cgis.query.diffcheck.check import (
    DiffItem,
    DiffReport,
    check_database,
    render_text,
    report_to_dict,
    run_diff_check,
)
from cgis.query.diffcheck.hunks import DiffError, FileChange, changed_files

__all__ = [
    "DiffError",
    "DiffItem",
    "DiffReport",
    "FileChange",
    "changed_files",
    "check_database",
    "render_text",
    "report_to_dict",
    "run_diff_check",
]
