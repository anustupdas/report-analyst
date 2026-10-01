"""Start the Text Extraction Service from the command line."""

import argparse
import logging

from text_extraction_service.app import run_app
from text_extraction_service.constants import DEFAULT_SERVICE_PORT
from text_extraction_service.version import __version__

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-p",
        "--port",
        type=int,
        help=("Port to run the app on; evaluation order: " f"CLI -> YAML/env -> default ({DEFAULT_SERVICE_PORT})"),
    )
    parser.add_argument(
        "-l",
        "--log-level",
        type=str,
        help="Log level to show",
        choices=["debug", "info", "warning", "error", "critical"],
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args()


def run_cli() -> None:
    args = parse_args()
    run_app(port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    run_app(port=DEFAULT_SERVICE_PORT)
