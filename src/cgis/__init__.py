"""Top-level package for Cgis app."""

from importlib.metadata import PackageNotFoundError, version

__app_name__ = "cgis"

try:
    __version__ = version("codegraph-brain")
except PackageNotFoundError:  # source checkout without an install
    __version__ = "0.0.0+dev"
