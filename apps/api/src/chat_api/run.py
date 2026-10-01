"""Start the Annual Report Analyst API from the command line."""

from __future__ import annotations

import argparse

from chat_api.main import run_app
from chat_api.version import __version__


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-p", "--port", type=int, help="Port to bind")
    parser.add_argument("-H", "--host", type=str, help="Host to bind")
    parser.add_argument(
        "-l",
        "--log-level",
        type=str,
        choices=["debug", "info", "warning", "error", "critical"],
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args()


def run_cli() -> None:
    args = parse_args()
    run_app(host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    run_cli()
