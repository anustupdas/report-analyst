"""Start the LangGraph server from the command line."""

import argparse
import logging

from langgraph_server.main import run_app
from langgraph_server.version import __version__

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-p", "--port", type=int, help="Port to run the app on")
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
    run_app(port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    run_app()
