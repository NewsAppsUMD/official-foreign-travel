#!/usr/bin/env python3
"""
Backward-compatible wrapper for report downloading.

This script maintains the same interface as the original scraper_report_text.py
but uses the new refactored code underneath.
"""

import sys
from pathlib import Path

# Add current directory to path
sys.path.insert(0, str(Path(__file__).parent))

from official_foreign_travel.scrapers.report_downloader import ReportDownloader
from official_foreign_travel.utils.logging import setup_logger
from official_foreign_travel.utils.config import get_config

# Setup logging
setup_logger("official_foreign_travel")

# Get config
config = get_config()

# Create downloader
downloader = ReportDownloader(config)

# Default URL (from original scraper)
base_url = (
    "http://clerk.house.gov/public_disc/foreign/index.aspx"
    "?__VIEWSTATE=LcCYaWgr%2FRHIHVjN3iaAdAB8y%2FjzWc1l1NRAsXRwgRARiNPYhQKiBKKhCoElQSwz9EBmwDApKfnNeMjt07qkc7gWEamvCh1zC5qdiuF06lqbyDhPnkO7GY5po4Shp97BhqjyRp5L028gQyVNM0mGnBNcq9NGUJRfZqYX7Ljzr1EN56tfI3PrhSdHSMWSZGR8UWuRqHGVV4k5u%2BY6O%2FLYGredDMPmMH27J5f5O5kXVSP2o8taPY5oExshjGZVXsaUZ6rQXHoGdGBv%2BvsG%2BghboSnzRt%2BiO%2BF%2BWCPNXavzsBbPW9hx%2FrIqv0kog%2Bhc4KNso12AxoF1NSMdAkGYvmJ2gSCdc5jC9ai%2FHcaKSCZgu9LQUYumZRnc8xe4vVpE14lR2NAnksSTR%2FdYkvmcC%2BStzjmEtP%2Bf%2FJtJEF99WRugMOdlGz6SoQjDqZUmq5nWrMjNCkALnqSVPprDgixG%2Fw%2BvtU73vRPd1zaAZctbVIHeP1Ui83C3MGMAynfB%2BvQyWj%2Bms%2FZrmWQ0dY9TkiQ5LxEDBGHvo%2FYLrnFpThD94Dv30oDWJ5GFNo9V9tQCkMO9%2Fp9%2FpVKErn5B77Dl4v0GXtDd%2BuWFJ%2BN64DER9%2FeYTYLOCr6ze08YWznh8rqek%2BDiEZ%2FW8JqQJ6h9cByzExTuIhsA716ox4%2F6mVljxbDTmiobYg6mzUv39WbdT2fA97anmQ8fou%2BHQE9k4BCqIC6Qy4%2FKI6B%2BIbhaK9ZXMs7pKeKQAUHvnoOYD9aqAQKOQ3cUxsnKtnoKb%2Fp3OxjxOeowvIOct%2BqPOioifoxtebth3B25PPgGsJ23NJ%2FUwbKVYfc5ZlgU%2FUzUGA%3D%3D"
    "&__VIEWSTATEGENERATOR=E19C725B&__EVENTTARGET=&__EVENTARGUMENT=&__VIEWSTATEENCRYPTED=&__EVENTVALIDATION=PRPv2D%2F1w%2FFyLtifsL16NOTPrElLLBuuSzDQPFfZ73EGb6xA739xVch2RWiGqc%2FZIfkkmVwaf4sIVOMeSUIDrIpBv0NpRTt1c%2BAE8kvuS9m2%2BQ4qzZfBqJdyVjyX6mMwMDgrSqqhAXynifkNhTI3aS6anp%2FcHIsKssLn9E4Ok4i5MrYYCtfY%2FThEgBMrMqabGSbeeKxf5gm%2BJsfsjMXW8vfGX97fESzNYF8ZL21LIWELKg4PeuPeAcBVIuDWJCHttAuIMKBOzqSV8mgyXykHXhQkpepvHoMedWxgqi5v8F5oeV82ce3yc2K960SEISX4QVNt%2FsslPsmFsu36WTWSROW7Q%2BFF5b%2BPxmSN7QuzQEZxMU%2BjA5Nx5Co%2F0aqdBjBbdOCCMcquSFrrLTwXakHXODIVM5gpDwP%2B6bwI%2FDCkQEAxWhKybvwpmN%2BvIfifzCYvt9waeD5CD18gRtC0j%2FCAAkrkqcL58XH2kbgXpzx9pooYzsAc3asz2Fz3oHoUEZv1mtwZpSdCwr%2BA9LFxGSB8BxpUh1Tk74kWUy3evOVPmfGpr04j%2B90aM5va7x3a9fGax1sR1y2QGbMfeoO5wpETl0yXY3FZArhFFXj%2BLFm36oScQrlT7%2Fg%2Bgpn0cuRyldI9kHXL4QxE3BUccIP26FhAYeGWaRhCqCFQgP5M5hQqe%2BmgosKaLPCcelb7ccqdrs83vhnWjHNJXdYafWeaMavkUyoYlXPWW37ADGX%2FUcGNwN82%2FDcZJNX3X5AOck7vspPY0dW8mg%3D%3D"
    "&ctl00%24txbSearchBox=&ctl00%24cphMain%24ddlYearField=2017&ctl00%24cphMain%24ddlQuartField=q1&ctl00%24cphMain%24btnSearch=Search"
)

print("Downloading reports...")
quarterly_urls = downloader.get_quarterly_urls(base_url)
report_urls = downloader.get_report_urls(quarterly_urls)

# Save URLs to file
downloader.save_report_urls_to_file(report_urls, Path("report_urls.txt"))

# Download all reports
stats = downloader.download_all_reports(report_urls)

print(f"\nComplete! Success: {stats['success']}, Failed: {stats['failure']}, Skipped: {stats['skipped']}")
