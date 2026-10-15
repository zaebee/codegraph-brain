"""Top-level package for Cgis app."""

from importlib.metadata import PackageNotFoundError, version

__app_name__ = "cgis"

try:
    __version__ = version("codegraph-brain")
except PackageNotFoundError:  # source checkout without an install
    __version__ = "0.0.0+dev"


def log_to_stderr() -> None:
    """Send structlog's output to stderr; for the CLI and the MCP server only (#619).

    structlog's default writes to stdout, which is a command's own output and the
    MCP stdio protocol channel. Called by the entry points, not at import: a host
    that imports cgis as a library keeps its own logging configuration.
    """
    import sys  # noqa: PLC0415 — the one place the package configures logging

    import structlog  # noqa: PLC0415

    # The stream as it is when a line is logged, not when this runs: whoever swaps
    # sys.stderr later (a test's capture, a host) is written to, never a closed one.
    structlog.configure(logger_factory=lambda *_args: structlog.PrintLogger(sys.stderr))
