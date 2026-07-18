"""Download foreign travel reports from the House Clerk disclosures site.

The site moved from ``clerk.house.gov/public_disc/foreign/index.aspx`` (an
ASP.NET WebForms page with a per-session ``__VIEWSTATE``) to
``disclosures-clerk.house.gov/ForeignTravel`` (an MVC site guarded by a
``__RequestVerificationToken``). The new flow:

1. ``GET /ForeignTravel/ViewReport`` -- returns the search form, from which
   the anti-forgery token is scraped.
2. ``POST /ForeignTravel/ViewSearchResult`` with the token plus ``Year`` and
   ``Quarter`` form fields -- returns the quarter's index page, with links
   of the form ``href="foreign-reports/<filename>.txt"``.
3. ``GET /foreign-reports/<filename>.txt`` for each report.
"""

import re
import time
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

from ..utils.config import Config, get_config
from ..utils.logging import get_logger

logger = get_logger(__name__)

VIEW_REPORT_PATH = "/ForeignTravel/ViewReport"
SEARCH_RESULT_PATH = "/ForeignTravel/ViewSearchResult"
TOKEN_RE = re.compile(
    r'name="__RequestVerificationToken"[^>]*value="([^"]+)"'
)
REPORT_LINK_RE = re.compile(r"^foreign-reports/[A-Za-z0-9]+\.txt$")
# Content guard: every legitimate foreign-travel filing is wrapped in a
# Congressional Record page whose header mentions "OFFICIAL FOREIGN TRAVEL"
# and whose per-trip tables say "EXPENDITURES FOR OFFICIAL FOREIGN TRAVEL".
# The Clerk site occasionally misfiles a non-travel Congressional Record
# page under the foreign-travel index (e.g. 2020q4dec02.txt, an executive-
# communications page); such files have zero foreign-travel content and
# would silently pollute the corpus if saved. Skip them.
TRAVEL_KEYWORD_RE = re.compile(
    r"OFFICIAL FOREIGN TRAVEL|EXPENDITURES FOR OFFICIAL FOREIGN TRAVEL"
)


class ReportDownloader:
    """Downloads quarterly foreign travel reports from disclosures-clerk.house.gov."""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or get_config()
        self.base_url = self.config.base_url.rstrip("/")
        self.report_dir = Path(self.config.report_text_dir)
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self._session: Optional[requests.Session] = None

    @property
    def session(self) -> requests.Session:
        # Reuse a single session so the anti-forgery cookie set on the
        # GET ViewReport request is sent back on the POST.
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(
                {
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0 Safari/537.36"
                    )
                }
            )
        return self._session

    def get_quarterly_urls(self, _unused: Optional[str] = None) -> list[dict]:
        """Return the list of (year, quarter) pairs to scrape.

        The ``_unused`` arg keeps backwards compatibility with the old
        CLI signature, which passed a hardcoded ``__VIEWSTATE`` URL. The
        new site has no such state -- the pairs are generated entirely
        from ``config.start_year`` / ``config.end_year``.
        """
        urls = []
        for year in range(self.config.start_year, self.config.end_year):
            for quarter in (1, 2, 3, 4):
                urls.append({"year": year, "quarter": quarter})
        logger.info(
            f"Generated {len(urls)} quarterly queries "
            f"({self.config.start_year}-{self.config.end_year})"
        )
        return urls

    def _fetch_token(self) -> str:
        """Fetch a fresh __RequestVerificationToken from the search form."""
        url = self.base_url + VIEW_REPORT_PATH
        response = self._fetch_with_retry(url)
        if response is None:
            raise RuntimeError(f"Failed to fetch anti-forgery token from {url}")
        match = TOKEN_RE.search(response.text)
        if not match:
            raise RuntimeError(
                f"No __RequestVerificationToken found on {url} -- the site "
                "layout may have changed."
            )
        return match.group(1)

    def get_report_urls(self, quarterly_urls: list[dict]) -> list[dict]:
        """For each (year, quarter), POST to the search endpoint and collect report links."""
        report_urls: list[dict] = []
        token = self._fetch_token()

        for entry in quarterly_urls:
            year = entry["year"]
            quarter = entry["quarter"]
            logger.info(f"Fetching report URLs for {year} Q{quarter}")

            try:
                links = self._fetch_quarter_links(year, quarter, token)
            except requests.exceptions.HTTPError as e:
                # A 500 with an anti-forgery error means the token has
                # expired (the site issues short-lived tokens). Refresh
                # and retry once.
                if e.response is not None and e.response.status_code == 500:
                    logger.warning(
                        f"Token expired mid-loop at {year} Q{quarter}; refreshing"
                    )
                    token = self._fetch_token()
                    try:
                        links = self._fetch_quarter_links(year, quarter, token)
                    except Exception as e2:
                        logger.error(
                            f"Error processing {year} Q{quarter} after token "
                            f"refresh: {e2}",
                            exc_info=True,
                        )
                        continue
                else:
                    logger.error(
                        f"Error processing {year} Q{quarter}: {e}", exc_info=True
                    )
                    continue
            except Exception as e:
                logger.error(f"Error processing {year} Q{quarter}: {e}", exc_info=True)
                continue

            entries = [
                {"year": year, "quarter": quarter, "report_url": link}
                for link in links
            ]
            report_urls.extend(entries)
            logger.info(f"Found {len(links)} reports for {year} Q{quarter}")

        logger.info(f"Total reports found: {len(report_urls)}")
        return report_urls

    def _fetch_quarter_links(
        self, year: int, quarter: int, token: str
    ) -> list[str]:
        """POST the search form for one quarter, return relative report paths."""
        url = self.base_url + SEARCH_RESULT_PATH
        response = self.session.post(
            url,
            data={
                "__RequestVerificationToken": token,
                "Year": str(year),
                "Quarter": f"q{quarter}",
            },
            headers={
                "Referer": self.base_url + VIEW_REPORT_PATH,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=self.config.request_timeout,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        links = []
        for a in soup.find_all("a", href=True):
            if REPORT_LINK_RE.match(a["href"]):
                links.append(a["href"])
        # De-duplicate while preserving order (the index links each .txt
        # alongside its .pdf sibling, so .txt links appear once each).
        seen: set[str] = set()
        unique: list[str] = []
        for link in links:
            if link not in seen:
                seen.add(link)
                unique.append(link)
        return unique

    def download_report(self, report: dict) -> bool:
        """Download a single report. ``report['report_url']`` is the relative
        ``foreign-reports/<filename>.txt`` path."""
        relative = report["report_url"]
        filename = relative.split("/")[-1]
        filepath = self.report_dir / filename
        if filepath.exists():
            logger.debug(f"Skipping {filename} (already exists)")
            return True

        try:
            full_url = self.base_url + "/" + relative
            response = self._fetch_with_retry(full_url)
            if response is None:
                logger.warning(f"Failed to download {filename} after retries")
                return False
            if not TRAVEL_KEYWORD_RE.search(response.text):
                logger.warning(
                    f"Skipping {filename} -- no foreign-travel content "
                    "(Clerk misfiling)"
                )
                return False
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(response.text)
            logger.info(f"Downloaded {filename}")
            return True
        except Exception as e:
            logger.error(f"Error downloading {filename}: {e}", exc_info=True)
            return False

    def download_all_reports(self, report_urls: list[dict]) -> dict[str, int]:
        stats = {"success": 0, "failure": 0, "skipped": 0}
        for i, report in enumerate(report_urls, 1):
            relative = report["report_url"]
            filename = relative.split("/")[-1]
            filepath = self.report_dir / filename
            if filepath.exists():
                stats["skipped"] += 1
                continue
            logger.info(f"Downloading {i}/{len(report_urls)}: {filename}")
            if self.download_report(report):
                stats["success"] += 1
            else:
                stats["failure"] += 1
            time.sleep(0.5)
        logger.info(
            f"Download complete: {stats['success']} succeeded, "
            f"{stats['failure']} failed, {stats['skipped']} skipped"
        )
        return stats

    def _fetch_with_retry(self, url: str) -> Optional[requests.Response]:
        for attempt in range(self.config.retry_attempts):
            try:
                response = self.session.get(url, timeout=self.config.request_timeout)
                response.raise_for_status()
                return response
            except requests.exceptions.RequestException as e:
                if attempt < self.config.retry_attempts - 1:
                    delay = self.config.retry_delay * (2**attempt)
                    logger.warning(
                        f"Request failed (attempt {attempt + 1}/"
                        f"{self.config.retry_attempts}): {e}"
                    )
                    logger.info(f"Retrying in {delay} seconds...")
                    time.sleep(delay)
                else:
                    logger.error(f"All retry attempts failed for {url}: {e}")
                    return None
        return None

    def save_report_urls_to_file(
        self, report_urls: list[dict], output_file: Path
    ) -> None:
        with open(output_file, "w") as f:
            for url in report_urls:
                f.write(f"{url['report_url']}\n")
        logger.info(f"Saved {len(report_urls)} URLs to {output_file}")