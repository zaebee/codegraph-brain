"""Smoke test for __main__ entrypoint."""

from unittest.mock import patch

from cgis.__main__ import main


def test_main_invokes_cli_app() -> None:
    with patch("cgis.cli.app") as mock_app:
        main()
        mock_app.assert_called_once_with(prog_name="cgis")
