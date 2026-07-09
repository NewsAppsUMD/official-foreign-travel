"""CLI tool to download legislator and committee YAML files from congress-legislators."""

import argparse
import logging
import sys
from pathlib import Path

import requests

from ..utils.logging import get_logger, setup_logger

logger = get_logger(__name__)

BASE_URL = "https://raw.githubusercontent.com/unitedstates/congress-legislators/master"
FILES_BY_ERA = {
    "current": {
        "legislators-current.yaml": f"{BASE_URL}/legislators-current.yaml",
        "committees-current.yaml": f"{BASE_URL}/committees-current.yaml",
    },
    "historical": {
        "legislators-historical.yaml": f"{BASE_URL}/legislators-historical.yaml",
        "committees-historical.yaml": f"{BASE_URL}/committees-historical.yaml",
    },
}


def download_file(url: str, destination: Path, timeout: int = 30) -> bool:
    """
    Download a file from URL to destination.

    Args:
        url: URL to download from
        destination: Path to save file
        timeout: Request timeout in seconds

    Returns:
        True if successful, False otherwise
    """
    logger.info(f"Downloading {url}...")
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()

        with open(destination, "w", encoding="utf-8") as f:
            f.write(response.text)

        logger.info(f"Saved to {destination} ({len(response.text)} bytes)")
        return True

    except requests.exceptions.RequestException as e:
        logger.error(f"Error downloading {url}: {e}")
        return False
    except OSError as e:
        logger.error(f"Error saving to {destination}: {e}")
        return False


def main():
    """Main CLI function."""
    parser = argparse.ArgumentParser(
        description="Download legislator and committee YAML files from "
        "unitedstates/congress-legislators"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("."),
        help="Output directory (default: current directory)",
    )
    parser.add_argument(
        "--current-only",
        action="store_true",
        help="Download only current legislators/committees",
    )
    parser.add_argument(
        "--historical-only",
        action="store_true",
        help="Download only historical legislators/committees",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Request timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (default: INFO)",
    )

    args = parser.parse_args()

    # Setup logging
    log_level = getattr(logging, args.log_level)
    setup_logger("official_foreign_travel", level=log_level)

    # Ensure output directory exists
    args.output_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Output directory: {args.output_dir.absolute()}")

    # Determine which eras to download
    if args.current_only:
        eras = ["current"]
    elif args.historical_only:
        eras = ["historical"]
    else:
        eras = ["current", "historical"]

    files_to_download = {}
    for era in eras:
        files_to_download.update(FILES_BY_ERA[era])

    # Download files
    success_count = 0
    for filename, url in files_to_download.items():
        destination = args.output_dir / filename

        if download_file(url, destination, timeout=args.timeout):
            success_count += 1

    # Summary
    logger.info(f"Downloaded {success_count}/{len(files_to_download)} files successfully")

    if success_count == len(files_to_download):
        logger.info("All reference data downloaded successfully!")
        return 0
    else:
        logger.error("Some downloads failed. Please check the errors above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
