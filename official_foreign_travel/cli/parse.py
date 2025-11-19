#!/usr/bin/env python3
"""CLI for parsing foreign travel reports."""

import argparse
import logging
from pathlib import Path

from ..scrapers.report_parser import ReportParser
from ..utils.logging import setup_logger
from ..utils.config import Config, get_config


def main():
    """Main entry point for parse CLI."""
    parser = argparse.ArgumentParser(
        description="Parse foreign travel reports from text files"
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Input file or directory containing report text files",
    )
    parser.add_argument(
        "output",
        type=Path,
        help="Output CSV file",
    )
    parser.add_argument(
        "--members-csv",
        type=Path,
        help="Members CSV file (default: members.csv)",
    )
    parser.add_argument(
        "--committees-csv",
        type=Path,
        help="Committees CSV file (default: committees.csv)",
    )
    parser.add_argument(
        "--no-validate",
        action="store_true",
        help="Skip validation of records",
    )
    parser.add_argument(
        "--no-metadata",
        action="store_true",
        help="Skip table header and committee metadata",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        help="Log file path",
    )

    args = parser.parse_args()

    # Validate input
    if not args.input.exists():
        print(f"Error: Input path does not exist: {args.input}")
        return 1

    # Setup logging
    log_level = getattr(logging, args.log_level)
    setup_logger("official_foreign_travel", level=log_level, log_file=args.log_file)

    # Get or create config
    config = get_config()

    # Override config with CLI args
    if args.members_csv:
        config.members_csv = args.members_csv
    if args.committees_csv:
        config.committees_csv = args.committees_csv

    # Create parser
    report_parser = ReportParser(config)

    print(f"Input: {args.input}")
    print(f"Output: {args.output}")
    print(f"Validation: {'disabled' if args.no_validate else 'enabled'}")

    # Parse input
    if args.input.is_file():
        print(f"Parsing single file...")
        records = report_parser.parse_file(
            args.input, include_metadata=not args.no_metadata
        )
    else:
        print(f"Parsing directory...")
        records = report_parser.parse_directory(
            args.input, include_metadata=not args.no_metadata
        )

    # Write output
    stats = report_parser.write_csv(
        records, args.output, validate=not args.no_validate
    )

    print("\nParsing complete!")
    print(f"  Total records: {stats['total']}")
    print(f"  Valid records: {stats['valid']}")
    if stats['invalid'] > 0:
        print(f"  Invalid records: {stats['invalid']}")

    return 0


if __name__ == "__main__":
    exit(main())
