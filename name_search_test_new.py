#!/usr/bin/env python3
"""
Backward-compatible wrapper for name matching testing.

This script maintains the same interface as the original name_search_test.py
but uses the new refactored code underneath.
"""

import sys
from pathlib import Path

# Add current directory to path
sys.path.insert(0, str(Path(__file__).parent))

from official_foreign_travel.scrapers.report_parser import ReportParser
from official_foreign_travel.matchers.name_matcher import NameMatcher
from official_foreign_travel.utils.logging import setup_logger
from official_foreign_travel.utils.config import get_config

# Setup logging
setup_logger("official_foreign_travel")


def print_help():
    """Print help message."""
    print("usage: python name_search_test_new.py {source directory} {output filename}")
    sys.exit()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print_help()

    input_dir = Path(sys.argv[1])
    output_file = Path(sys.argv[2])

    if not input_dir.exists() or not input_dir.is_dir():
        print(f"Error: Input must be an existing directory: {input_dir}")
        sys.exit(1)

    # Get config
    config = get_config()

    print("Initializing name matcher...")
    matcher = NameMatcher(config)
    matcher.initialize(use_cache=True, cache_path=Path("names_index.pickle"))

    print("Processing reports...")
    parser = ReportParser(config)

    count = 0
    missing_count = 0
    inconclusive_count = 0
    error_count = 0

    with open(output_file, "w", encoding="utf-8") as log_file:
        files = sorted(input_dir.glob("*.txt"))
        total = len(files)

        for i, file_path in enumerate(files, 1):
            print(f"[{i}/{total}] {file_path.name}")

            try:
                for record in parser.parse_file(file_path, include_metadata=False):
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
                            if not result.matches or result.matches[0].score < 3.0:
                                missing_count += 1
                                log_file.write(
                                    f"Missing {file_path.name}: {record.name}, "
                                    f"{record.arrival_date}, {record.departure_date} - "
                                    f"{[(m.bioguide_id, m.score) for m in result.matches[:3]]}\n"
                                )
                            elif result.is_inconclusive:
                                inconclusive_count += 1
                                log_file.write(
                                    f"Inconcl. {file_path.name}: {record.name}, "
                                    f"{record.arrival_date}, {record.departure_date} - "
                                    f"{[(m.bioguide_id, m.score) for m in result.matches[:3]]}\n"
                                )

                    except Exception as e:
                        error_count += 1
                        log_file.write(
                            f"ERROR {file_path.name}: {record.name}, "
                            f"{record.arrival_date}, {record.departure_date} - {e}\n"
                        )

            except Exception as e:
                print(f"  Error: {e}")
                error_count += 1

    print(f"\nComplete!")
    print(f"  Total records: {count}")
    print(f"  Missing: {missing_count}")
    print(f"  Inconclusive: {inconclusive_count}")
    print(f"  Errors: {error_count}")
    print(f"\nResults written to: {output_file}")
