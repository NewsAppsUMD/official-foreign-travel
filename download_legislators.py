#!/usr/bin/env python3
"""
Download legislator YAML files from unitedstates/congress-legislators repository.

This script downloads the current and historical legislator data required for
name matching.
"""

import sys
import argparse
from pathlib import Path
from typing import Optional

try:
    import requests
except ImportError:
    print("Error: requests library not installed. Install with: pip install requests")
    sys.exit(1)


LEGISLATOR_URLS = {
    "current": "https://raw.githubusercontent.com/unitedstates/congress-legislators/master/legislators-current.yaml",
    "historical": "https://raw.githubusercontent.com/unitedstates/congress-legislators/master/legislators-historical.yaml",
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
    print(f"Downloading {url}...")
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()

        with open(destination, "w", encoding="utf-8") as f:
            f.write(response.text)

        print(f"✓ Saved to {destination} ({len(response.text)} bytes)")
        return True

    except requests.exceptions.RequestException as e:
        print(f"✗ Error downloading {url}: {e}")
        return False
    except IOError as e:
        print(f"✗ Error saving to {destination}: {e}")
        return False


def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description="Download legislator YAML files from GitHub"
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
        help="Download only current legislators",
    )
    parser.add_argument(
        "--historical-only",
        action="store_true",
        help="Download only historical legislators",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Request timeout in seconds (default: 30)",
    )

    args = parser.parse_args()

    # Ensure output directory exists
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Determine which files to download
    to_download = []
    if args.current_only:
        to_download = ["current"]
    elif args.historical_only:
        to_download = ["historical"]
    else:
        to_download = ["current", "historical"]

    # Download files
    success_count = 0
    for name in to_download:
        url = LEGISLATOR_URLS[name]
        filename = f"legislators-{name}.yaml"
        destination = args.output_dir / filename

        if download_file(url, destination, timeout=args.timeout):
            success_count += 1

    # Summary
    print(f"\nDownloaded {success_count}/{len(to_download)} files successfully")

    if success_count == len(to_download):
        print("\n✓ All legislator data downloaded successfully!")
        return 0
    else:
        print("\n✗ Some downloads failed. Please check the errors above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
