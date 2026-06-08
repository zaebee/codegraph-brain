"""Cgis app entry point script."""

import logging

from cgis import __app_name__, cli

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def main() -> None:
    """Launch the CGIS CLI application."""
    cli.app(prog_name=__app_name__)


if __name__ == "__main__":  # pragma: no cover
    main()
