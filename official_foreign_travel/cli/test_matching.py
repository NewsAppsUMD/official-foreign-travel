#!/usr/bin/env python3
"""CLI for testing name matching on travel reports."""

import argparse
import logging
from pathlib import Path

from ..scrapers.report_parser import ReportParser
from ..matchers.name_matcher import NameMatcher
from ..utils.logging import setup_logger
from ..utils.config import Config, get_config


def main():
    """Main entry point for name matching test CLI."""
    parser = argparse.ArgumentParser(
        description="Test name matching on travel reports"
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Input directory containing report text files",
    )
    parser.add_argument(
        "output",
        type=Path,
        help="Output log file for matching issues",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path("names_index.pickle"),
        help="Cache file for name index (default: names_index.pickle)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Don't use cache",
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
        "--legislators-current",
        type=Path,
        help="Current legislators YAML file",
    )
    parser.add_argument(
        "--legislators-historical",
        type=Path,
        help="Historical legislators YAML file",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level",
    )

    args = parser.parse_args()

    # Validate input
    if not args.input.exists() or not args.input.is_dir():
        print(f"Error: Input must be an existing directory: {args.input}")
        return 1

    # Setup logging
    log_level = getattr(logging, args.log_level)
    setup_logger("official_foreign_travel", level=log_level)

    # Get or create config
    config = get_config()

    # Override config with CLI args
    if args.members_csv:
        config.members_csv = args.members_csv
    if args.committees_csv:
        config.committees_csv = args.committees_csv
    if args.legislators_current:
        config.legislators_current_yaml = args.legislators_current
    if args.legislators_historical:
        config.legislators_historical_yaml = args.legislators_historical

    print(f"Input directory: {args.input}")
    print(f"Output log: {args.output}")
    print(f"Using cache: {not args.no_cache}")

    # Initialize name matcher
    print("Initializing name matcher...")
    matcher = NameMatcher(config)
    matcher.initialize(use_cache=not args.no_cache, cache_path=args.cache)

    # Initialize report parser
    report_parser = ReportParser(config)

    # Process all reports
    print("Processing reports...")
    count = 0
    missing_count = 0
    inconclusive_count = 0
    error_count = 0

    with open(args.output, "w", encoding="utf-8") as log_file:
        files = sorted(args.input.glob("*.txt"))
        total_files = len(files)

        for i, file_path in enumerate(files, 1):
            print(f"[{i}/{total_files}] {file_path.name}")

            try:
                for record in report_parser.parse_file(file_path, include_metadata=False):
                    count += 1

                    # Only test records with honorifics
                    if not record.honorific or not (
                        record.name.startswith("Hon") or record.name.startswith("Speaker")
                    ):
                        continue

                    try:
                        result = matcher.search_by_name(
                            record.name, record.arrival_date, record.departure_date
                        )

                        if not result.is_confident:
                            if not result.matches or result.matches[0].score < config.min_match_score:
                                missing_count += 1
                                log_file.write(
                                    f"Missing {file_path.name}: {record.name}, "
                                    f"{record.arrival_date}, {record.departure_date} - "
                                    f"{result.matches[:3] if result.matches else 'no matches'}\n"
                                )
                            elif result.is_inconclusive:
                                inconclusive_count += 1
                                log_file.write(
                                    f"Inconcl. {file_path.name}: {record.name}, "
                                    f"{record.arrival_date}, {record.departure_date} - "
                                    f"{result.matches[:3]}\n"
                                )

                    except Exception as e:
                        error_count += 1
                        log_file.write(
                            f"ERROR {file_path.name}: {record.name}, "
                            f"{record.arrival_date}, {record.departure_date} - {e}\n"
                        )

            except Exception as e:
                print(f"  Error processing file: {e}")
                error_count += 1

    print("\nMatching test complete!")
    print(f"  Total records: {count}")
    print(f"  Missing matches: {missing_count}")
    print(f"  Inconclusive matches: {inconclusive_count}")
    print(f"  Errors: {error_count}")
    print(f"\nResults written to: {args.output}")

    return 0


if __name__ == "__main__":
    exit(main())
