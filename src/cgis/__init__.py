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
    import os  # noqa: PLC0415 — the one place the package configures logging
    import sys  # noqa: PLC0415

    import structlog  # noqa: PLC0415

    # Colours by stderr, where the lines now go — structlog decides them by stdout
    # at its import, which put escapes into `cgis ingest 2>log` (#622 review).
    # NO_COLOR and FORCE_COLOR keep the meaning structlog gives them.
    colours = not os.environ.get("NO_COLOR") and (
        bool(os.environ.get("FORCE_COLOR")) or sys.stderr.isatty()
    )
    processors = [
        structlog.dev.ConsoleRenderer(colors=colours)
        if isinstance(p, structlog.dev.ConsoleRenderer)
        else p
        for p in structlog.get_config()["processors"]
    ]
    # The stream as it is when a line is logged, not when this runs: whoever swaps
    # sys.stderr later (a test's capture, a host) is written to, never a closed one.
    # Uncached, so a module-level `structlog.getLogger(__name__)` made at import
    # reaches the factory — and the current sys.stderr — on every call.
    structlog.configure(
        processors=processors,
        logger_factory=lambda *_args: structlog.PrintLogger(sys.stderr),
        cache_logger_on_first_use=False,
    )
