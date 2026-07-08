"""Download foreign travel reports from House Clerk website."""

import re
import time
from pathlib import Path
from typing import List, Dict, Optional
import requests
from bs4 import BeautifulSoup

from ..utils.logging import get_logger
from ..utils.config import Config, get_config

logger = get_logger(__name__)


class ReportDownloader:
    """Downloads quarterly foreign travel reports from clerk.house.gov."""

    def __init__(self, config: Optional[Config] = None):
        """
        Initialize downloader.

        Args:
            config: Optional configuration object
        """
        self.config = config or get_config()
        self.base_url = self.config.base_url
        self.report_dir = Path(self.config.report_text_dir)
        self.report_dir.mkdir(parents=True, exist_ok=True)

    def get_quarterly_urls(self, base_query_url: str) -> List[Dict]:
        """
        Generate URLs for all quarterly reports.

        Args:
            base_query_url: Base URL with query parameters

        Returns:
            List of dicts with year, quarter, and URL
        """
        urls = []
        years = range(self.config.start_year, self.config.end_year)
        quarters = [1, 2, 3, 4]

        for year in years:
            for quarter in quarters:
                url = re.sub(r"ddlYearField=[0-9]{4}", f"ddlYearField={year}", base_query_url)
                url = re.sub(r"ddlQuartField=q[0-9]", f"ddlQuartField=q{quarter}", url)
                urls.append({"year": year, "quarter": quarter, "url": url})

        logger.info(
            f"Generated {len(urls)} quarterly URLs ({self.config.start_year}-{self.config.end_year})"
        )
        return urls

    def get_report_urls(self, quarterly_urls: List[Dict]) -> List[Dict]:
        """
        Extract report URLs from quarterly index pages.

        Args:
            quarterly_urls: List of quarterly URL dicts

        Returns:
            List of report URL dicts with year, quarter, and report_url
        """
        report_urls = []
        pattern = re.compile(r"^/foreign/reports/[A-Za-z0-9]+\.txt$")

        for quarter in quarterly_urls:
            year = quarter["year"]
            q = quarter["quarter"]
            url = quarter["url"]

            logger.info(f"Fetching report URLs for {year} Q{q}")

            try:
                response = self._fetch_with_retry(url)
                if response is None:
                    logger.warning(f"Failed to fetch {year} Q{q} after retries")
                    continue

                soup = BeautifulSoup(response.text, "html.parser")
                links = soup.find_all("a", href=True)

                reports = [link["href"] for link in links if pattern.match(link["href"])]
                entries = [{"year": year, "quarter": q, "report_url": r} for r in reports]

                report_urls.extend(entries)
                logger.info(f"Found {len(reports)} reports for {year} Q{q}")

            except Exception as e:
                logger.error(f"Error processing {year} Q{q}: {e}", exc_info=True)
                continue

        logger.info(f"Total reports found: {len(report_urls)}")
        return report_urls

    def download_report(self, report: Dict) -> bool:
        """
        Download a single report.

        Args:
            report: Dict with report_url

        Returns:
            True if successful, False otherwise
        """
        url = report["report_url"]
        filename = url.split("/")[-1]
        filepath = self.report_dir / filename

        # Skip if already downloaded
        if filepath.exists():
            logger.debug(f"Skipping {filename} (already exists)")
            return True

        try:
            full_url = self.base_url + url
            response = self._fetch_with_retry(full_url)

            if response is None:
                logger.warning(f"Failed to download {filename} after retries")
                return False

            # Write text content
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(response.text)

            logger.info(f"Downloaded {filename}")
            return True

        except Exception as e:
            logger.error(f"Error downloading {filename}: {e}", exc_info=True)
            return False

    def download_all_reports(self, report_urls: List[Dict]) -> Dict[str, int]:
        """
        Download all reports.

        Args:
            report_urls: List of report URL dicts

        Returns:
            Dict with success/failure counts
        """
        stats = {"success": 0, "failure": 0, "skipped": 0}

        for i, report in enumerate(report_urls, 1):
            url = report["report_url"]
            filename = url.split("/")[-1]
            filepath = self.report_dir / filename

            if filepath.exists():
                stats["skipped"] += 1
                continue

            logger.info(f"Downloading {i}/{len(report_urls)}: {filename}")

            if self.download_report(report):
                stats["success"] += 1
            else:
                stats["failure"] += 1

            # Be nice to the server
            time.sleep(0.5)

        logger.info(
            f"Download complete: {stats['success']} succeeded, "
            f"{stats['failure']} failed, {stats['skipped']} skipped"
        )
        return stats

    def _fetch_with_retry(self, url: str) -> Optional[requests.Response]:
        """
        Fetch URL with retry logic.

        Args:
            url: URL to fetch

        Returns:
            Response object or None if all retries failed
        """
        for attempt in range(self.config.retry_attempts):
            try:
                response = requests.get(url, timeout=self.config.request_timeout)
                response.raise_for_status()
                return response

            except requests.exceptions.RequestException as e:
                if attempt < self.config.retry_attempts - 1:
                    delay = self.config.retry_delay * (2**attempt)
                    logger.warning(
                        f"Request failed (attempt {attempt + 1}/{self.config.retry_attempts}): {e}"
                    )
                    logger.info(f"Retrying in {delay} seconds...")
                    time.sleep(delay)
                else:
                    logger.error(f"All retry attempts failed for {url}: {e}")
                    return None

        return None

    def save_report_urls_to_file(self, report_urls: List[Dict], output_file: Path) -> None:
        """
        Save report URLs to a text file.

        Args:
            report_urls: List of report URL dicts
            output_file: Output file path
        """
        with open(output_file, "w") as f:
            for url in report_urls:
                f.write(f"{url['report_url']}\n")

        logger.info(f"Saved {len(report_urls)} URLs to {output_file}")
