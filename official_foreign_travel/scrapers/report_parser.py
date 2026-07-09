"""Parse foreign travel reports from text files.

Thin orchestrator over the `official_foreign_travel.parsing` pipeline
(segmenter -> header -> layout -> rows -> dates -> assemble -> validate).
Kept as `ReportParser` for import-path backward compatibility; the v2
column-offset implementation this replaced dropped ~12% of records and
never extracted costs. See TECHNICAL_README.md for the v3 architecture.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Optional

from ..matchers.name_matcher import NameMatcher
from ..models.report import Report
from ..parsing.assemble import (
    assemble_directory,
    assemble_file,
    load_disambiguation_index,
    load_name_index,
)
from ..parsing.dedup import dedup_reports
from ..parsing.serialize import write_csv, write_json, write_jsonl
from ..parsing.validate import validate_reports
from ..utils.config import Config, get_config
from ..utils.logging import get_logger

logger = get_logger(__name__)


class ReportParser:
    """Parse foreign travel reports into validated, deduplicated Report objects."""

    def __init__(self, config: Optional[Config] = None, name_matcher: Optional[NameMatcher] = None):
        """
        Initialize parser.

        Args:
            config: Optional configuration object
            name_matcher: Optional NameMatcher for fuzzy fallback on unmatched names
        """
        self.config = config or get_config()
        self.name_matcher = name_matcher
        self.member_index: dict[str, str] = load_name_index(self.config.members_csv)
        self.committee_index: dict[str, str] = load_name_index(self.config.committees_csv)
        self.disambiguation_index: dict[tuple[str, str], str] = load_disambiguation_index(
            self.config.member_disambiguation_csv
        )

    def parse_file(self, file_path: Path) -> list[Report]:
        """Parse a single report file into Report objects, one per table."""
        return assemble_file(
            file_path,
            self.member_index,
            self.committee_index,
            self.name_matcher,
            self.disambiguation_index,
        )

    def parse_directory(self, directory: Path) -> Iterator[Report]:
        """Parse all *.txt report files in a directory, in filename order."""
        return assemble_directory(
            directory,
            self.member_index,
            self.committee_index,
            self.name_matcher,
            self.disambiguation_index,
        )

    def parse_and_finalize(self, path: Path) -> list[Report]:
        """
        Parse a file or directory, then validate and deduplicate the results.

        Args:
            path: A single report file or a directory of report files

        Returns:
            List of Report objects with validation flags applied and
            amended-report duplicates marked via `superseded_by`
        """
        if path.is_file():
            reports = self.parse_file(path)
        else:
            reports = list(self.parse_directory(path))

        validate_reports(reports)
        dedup_reports(reports)
        return reports

    def write_csv(
        self, reports: list[Report], output_file: Path, include_superseded: bool = False
    ) -> dict[str, int]:
        """Write reports to a flat CSV (one row per traveler segment)."""
        return write_csv(reports, output_file, include_superseded)

    def write_json(
        self, reports: list[Report], output_file: Path, include_superseded: bool = False
    ) -> None:
        """Write reports to the canonical JSON format."""
        write_json(reports, output_file, include_superseded)

    def write_jsonl(
        self, reports: list[Report], output_file: Path, include_superseded: bool = False
    ) -> dict[str, int]:
        """Write reports to flat JSON Lines (one traveler segment per line)."""
        return write_jsonl(reports, output_file, include_superseded)
