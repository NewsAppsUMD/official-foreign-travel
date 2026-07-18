#!/usr/bin/env python3
"""CLI for downloading foreign travel reports."""

import argparse
import logging
from pathlib import Path

from ..scrapers.report_downloader import ReportDownloader
from ..utils.config import get_config
from ..utils.logging import setup_logger


def main() -> None:
    """Main entry point for download CLI."""
    parser = argparse.ArgumentParser(
        description="Download foreign travel reports from the House Clerk disclosures site"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory to save reports (default: report_text/)",
    )
    parser.add_argument("--start-year", type=int, help="Start year (default: 1994)")
    parser.add_argument("--end-year", type=int, help="End year, exclusive (default: 2027)")
    parser.add_argument(
        "--save-urls",
        type=Path,
        help="Save report URLs to file",
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

    # Setup logging
    log_level = getattr(logging, args.log_level)
    setup_logger("official_foreign_travel", level=log_level, log_file=args.log_file)

    # Get or create config
    config = get_config()

    # Override config with CLI args
    if args.output_dir:
        config.report_text_dir = args.output_dir
    if args.start_year:
        config.start_year = args.start_year
    if args.end_year:
        config.end_year = args.end_year

    # Create downloader
    downloader = ReportDownloader(config)

    print(f"Downloading reports from {config.start_year} to {config.end_year}")
    print(f"Output directory: {config.report_text_dir}")
    print(f"Source: {config.base_url}/ForeignTravel/ViewReport")

    # Get quarterly URLs
    quarterly_urls = downloader.get_quarterly_urls()
    print(f"Generated {len(quarterly_urls)} quarterly queries")

    # Get report URLs
    report_urls = downloader.get_report_urls(quarterly_urls)
    print(f"Found {len(report_urls)} report URLs")

    # Save URLs if requested
    if args.save_urls:
        downloader.save_report_urls_to_file(report_urls, args.save_urls)

    # Download all reports
    stats = downloader.download_all_reports(report_urls)

    print("\nDownload complete!")
    print(f"  Success: {stats['success']}")
    print(f"  Failed: {stats['failure']}")
    print(f"  Skipped: {stats['skipped']}")


if __name__ == "__main__":
    main()