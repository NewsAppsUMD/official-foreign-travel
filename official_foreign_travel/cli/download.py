#!/usr/bin/env python3
"""CLI for downloading foreign travel reports."""

import argparse
import logging
from pathlib import Path

from ..scrapers.report_downloader import ReportDownloader
from ..utils.logging import setup_logger
from ..utils.config import Config, get_config


def main() -> None:
    """Main entry point for download CLI."""
    parser = argparse.ArgumentParser(
        description="Download foreign travel reports from House Clerk website"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory to save reports (default: report_text/)",
    )
    parser.add_argument("--start-year", type=int, help="Start year (default: 1994)")
    parser.add_argument("--end-year", type=int, help="End year (default: 2020)")
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

    # Default URL (from original scraper)
    base_url = (
        "http://clerk.house.gov/public_disc/foreign/index.aspx"
        "?__VIEWSTATE=LcCYaWgr%2FRHIHVjN3iaAdAB8y%2FjzWc1l1NRAsXRwgRARiNPYhQKiBKKhCoElQSwz9EBmwDApKfnNeMjt07qkc7gWEamvCh1zC5qdiuF06lqbyDhPnkO7GY5po4Shp97BhqjyRp5L028gQyVNM0mGnBNcq9NGUJRfZqYX7Ljzr1EN56tfI3PrhSdHSMWSZGR8UWuRqHGVV4k5u%2BY6O%2FLYGredDMPmMH27J5f5O5kXVSP2o8taPY5oExshjGZVXsaUZ6rQXHoGdGBv%2BvsG%2BghboSnzRt%2BiO%2BF%2BWCPNXavzsBbPW9hx%2FrIqv0kog%2Bhc4KNso12AxoF1NSMdAkGYvmJ2gSCdc5jC9ai%2FHcaKSCZgu9LQUYumZRnc8xe4vVpE14lR2NAnksSTR%2FdYkvmcC%2BStzjmEtP%2Bf%2FJtJEF99WRugMOdlGz6SoQjDqZUmq5nWrMjNCkALnqSVPprDgixG%2Fw%2BvtU73vRPd1zaAZctbVIHeP1Ui83C3MGMAynfB%2BvQyWj%2Bms%2FZrmWQ0dY9TkiQ5LxEDBGHvo%2FYLrnFpThD94Dv30oDWJ5GFNo9V9tQCkMO9%2Fp9%2FpVKErn5B77Dl4v0GXtDd%2BuWFJ%2BN64DER9%2FeYTYLOCr6ze08YWznh8rqek%2BDiEZ%2FW8JqQJ6h9cByzExTuIhsA716ox4%2F6mVljxbDTmiobYg6mzUv39WbdT2fA97anmQ8fou%2BHQE9k4BCqIC6Qy4%2FKI6B%2BIbhaK9ZXMs7pKeKQAUHvnoOYD9aqAQKOQ3cUxsnKtnoKb%2Fp3OxjxOeowvIOct%2BqPOioifoxtebth3B25PPgGsJ23NJ%2FUwbKVYfc5ZlgU%2FUzUGA%3D%3D"
        "&__VIEWSTATEGENERATOR=E19C725B&__EVENTTARGET=&__EVENTARGUMENT=&__VIEWSTATEENCRYPTED=&__EVENTVALIDATION=PRPv2D%2F1w%2FFyLtifsL16NOTPrElLLBuuSzDQPFfZ73EGb6xA739xVch2RWiGqc%2FZIfkkmVwaf4sIVOMeSUIDrIpBv0NpRTt1c%2BAE8kvuS9m2%2BQ4qzZfBqJdyVjyX6mMwMDgrSqqhAXynifkNhTI3aS6anp%2FcHIsKssLn9E4Ok4i5MrYYCtfY%2FThEgBMrMqabGSbeeKxf5gm%2BJsfsjMXW8vfGX97fESzNYF8ZL21LIWELKg4PeuPeAcBVIuDWJCHttAuIMKBOzqSV8mgyXykHXhQkpepvHoMedWxgqi5v8F5oeV82ce3yc2K960SEISX4QVNt%2FsslPsmFsu36WTWSROW7Q%2BFF5b%2BPxmSN7QuzQEZxMU%2BjA5Nx5Co%2F0aqdBjBbdOCCMcquSFrrLTwXakHXODIVM5gpDwP%2B6bwI%2FDCkQEAxWhKybvwpmN%2BvIfifzCYvt9waeD5CD18gRtC0j%2FCAAkrkqcL58XH2kbgXpzx9pooYzsAc3asz2Fz3oHoUEZv1mtwZpSdCwr%2BA9LFxGSB8BxpUh1Tk74kWUy3evOVPmfGpr04j%2B90aM5va7x3a9fGax1sR1y2QGbMfeoO5wpETl0yXY3FZArhFFXj%2BLFm36oScQrlT7%2Fg%2Bgpn0cuRyldI9kHXL4QxE3BUccIP26FhAYeGWaRhCqCFQgP5M5hQqe%2BmgosKaLPCcelb7ccqdrs83vhnWjHNJXdYafWeaMavkUyoYlXPWW37ADGX%2FUcGNwN82%2FDcZJNX3X5AOck7vspPY0dW8mg%3D%3D"
        "&ctl00%24txbSearchBox=&ctl00%24cphMain%24ddlYearField=2017&ctl00%24cphMain%24ddlQuartField=q1&ctl00%24cphMain%24btnSearch=Search"
    )

    print(f"Downloading reports from {config.start_year} to {config.end_year}")
    print(f"Output directory: {config.report_text_dir}")

    # Get quarterly URLs
    quarterly_urls = downloader.get_quarterly_urls(base_url)
    print(f"Generated {len(quarterly_urls)} quarterly URLs")

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
