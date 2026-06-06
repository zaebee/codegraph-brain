"""Cgis app entry point script."""

from cgis import __app_name__, cli


def main() -> None:
    cli.app(prog_name=__app_name__)


if __name__ == "__main__":
    main()
