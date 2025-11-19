#!/usr/bin/env python3
"""
Backward-compatible wrapper for report parsing.

This script maintains the same interface as the original scraper.py
but uses the new refactored code underneath.
"""

import sys
from pathlib import Path

# Add current directory to path
sys.path.insert(0, str(Path(__file__).parent))

from official_foreign_travel.scrapers.report_parser import ReportParser
from official_foreign_travel.utils.logging import setup_logger
from official_foreign_travel.utils.config import get_config

# Setup logging
setup_logger("official_foreign_travel")


def print_help():
    """Print help message."""
    print("usage: python scraper_new.py {source directory} {output filename}")
    sys.exit()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print_help()

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])

    if not input_path.exists():
        print(f"Error: Input path does not exist: {input_path}")
        sys.exit(1)

    # Get config
    config = get_config()

    # Create parser
    parser = ReportParser(config)

    print(f"Parsing reports from {input_path}")
    print(f"Output will be written to {output_path}")

    # Parse based on input type
    if input_path.is_file():
        # Single file
        records = parser.parse_file(input_path, include_metadata=True)
    else:
        # Directory
        records = parser.parse_directory(input_path, include_metadata=True)

    # Write output
    stats = parser.write_csv(records, output_path, validate=False)

    print(f"\nComplete! Wrote {stats['valid']} records to {output_path}")
