"""Parse foreign travel reports from text files."""

import re
import csv
from pathlib import Path
from typing import List, Dict, Iterator, Optional, TextIO

from ..models.travel import TravelRecordInput, TravelRecord, TravelRecordOutput
from ..models.member import MemberInput
from ..models.committee import Committee
from ..utils.logging import get_logger
from ..utils.text import clean_cell, get_honorific
from ..utils.config import Config, get_config

logger = get_logger(__name__)


class ReportParser:
    """Parse foreign travel reports from fixed-width text files."""

    # Regex patterns for report sections
    START_LINE_PATTERN = r"-{107}\\2\\-{23}\\2\\"
    HEADER_PATTERN = r"REPORTS? OF EXPENDITURES FOR "
    COMMITTEE_PATTERN = r"COMMITTEE ON "
    DELEGATION_PATTERN = r"DELEGATION TO "
    SELECT_COMMITTEE_PATTERN = r"PERMANENT SELECT COMMITTEE "
    COMMISSION_PATTERN = r"COMMISSION ON "
    INTERPARLIAMENTARY_PATTERN = r"MEXICO-UNITED STATES"

    # Patterns for lines to skip
    SKIP_PATTERNS = [
        r"^ *\[.*\] *$",
        r"Please Note:",
        r"Commercial (Airfare|Aircraft|Transportation)",
    ]

    # Patterns for end of section
    END_PATTERNS = [
        r"^ *-+$",
        r" {113}0 {77}0",
    ]

    # Column positions for fixed-width parsing
    COL_NAME = (0, 39)
    COL_ARRIVAL = (43, 48)
    COL_DEPARTURE = (55, 60)
    COL_COUNTRY = (63, 88)

    def __init__(self, config: Optional[Config] = None):
        """
        Initialize parser.

        Args:
            config: Optional configuration object
        """
        self.config = config or get_config()
        self.members: Dict[str, str] = {}
        self.committees: Dict[str, str] = {}
        self._load_lookup_data()

    def _load_lookup_data(self) -> None:
        """Load member and committee lookup data from CSV files."""
        # Load members
        try:
            with open(self.config.members_csv, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    self.members[row["name"].upper()] = row["bioguide_id"]
            logger.info(f"Loaded {len(self.members)} members")
        except FileNotFoundError:
            logger.warning(f"Members CSV not found: {self.config.members_csv}")
        except Exception as e:
            logger.error(f"Error loading members CSV: {e}", exc_info=True)

        # Load committees
        try:
            with open(self.config.committees_csv, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    self.committees[row["name"].upper()] = row["code"]
            logger.info(f"Loaded {len(self.committees)} committees")
        except FileNotFoundError:
            logger.warning(f"Committees CSV not found: {self.config.committees_csv}")
        except Exception as e:
            logger.error(f"Error loading committees CSV: {e}", exc_info=True)

    def _should_skip_line(self, line: str) -> bool:
        """Check if line should be skipped."""
        return any(
            re.search(pattern, line, flags=re.IGNORECASE)
            for pattern in self.SKIP_PATTERNS
        )

    def _is_end_line(self, line: str) -> bool:
        """Check if line marks end of section."""
        return any(
            re.search(pattern, line, flags=re.IGNORECASE)
            for pattern in self.END_PATTERNS
        )

    def _extract_committee_info(self, header_line: str) -> tuple[str, str]:
        """
        Extract committee name and code from header line.

        Args:
            header_line: Header line text

        Returns:
            Tuple of (committee_name, committee_code)
        """
        committee = ""
        committee_code = ""

        # Check different committee patterns
        if re.search(self.COMMITTEE_PATTERN, header_line):
            parts = header_line.split(",")
            if len(parts) > 1:
                committee = parts[1].strip()
                committee_code = self.committees.get(committee.upper(), "")

        elif re.search(self.SELECT_COMMITTEE_PATTERN, header_line):
            parts = header_line.split(",")
            if len(parts) > 1:
                committee = parts[1].strip()
                committee_code = self.committees.get(committee.upper(), "")

        elif re.search(self.DELEGATION_PATTERN, header_line):
            parts = header_line.split(",")
            if len(parts) > 1:
                committee = parts[1].strip()

        elif re.search(self.COMMISSION_PATTERN, header_line):
            parts = header_line.split(",")
            if len(parts) > 1:
                committee = parts[1].strip()

        elif re.search(self.INTERPARLIAMENTARY_PATTERN, header_line):
            parts = header_line.split(",")
            if len(parts) > 1:
                committee = parts[1].strip()

        return committee, committee_code

    def _parse_line(
        self, line: str, year: str, current_name: str
    ) -> Optional[tuple[TravelRecordInput, str]]:
        """
        Parse a single data line from the report.

        Args:
            line: Line text
            year: Report year
            current_name: Current member name (for repeated entries)

        Returns:
            Tuple of (TravelRecordInput, updated_current_name) or None
        """
        # Extract columns using fixed positions
        name = clean_cell(line[self.COL_NAME[0] : self.COL_NAME[1]])
        arrival_date = clean_cell(line[self.COL_ARRIVAL[0] : self.COL_ARRIVAL[1]])
        departure_date = clean_cell(line[self.COL_DEPARTURE[0] : self.COL_DEPARTURE[1]])
        country = clean_cell(line[self.COL_COUNTRY[0] : self.COL_COUNTRY[1]])

        # Validate required fields
        if not arrival_date or not departure_date:
            return None

        # Handle repeated names (empty name means same as previous)
        if not name:
            name = current_name
        else:
            current_name = name

        # Look up bioguide ID
        member_id = self.members.get(name.upper(), "")

        # Extract honorific
        honorific = get_honorific(name)

        # Format dates
        arrival_date_full = f"{arrival_date}/{year}"
        departure_date_full = f"{departure_date}/{year}"

        record = TravelRecordInput(
            name=name,
            member_id=member_id or None,
            honorific=honorific or None,
            arrival_date=arrival_date_full,
            departure_date=departure_date_full,
            country=country,
            table_header=None,
            committee=None,
            committee_code=None,
            source_file=None,
        )

        return record, current_name

    def parse_file(
        self, file_path: Path, include_metadata: bool = True
    ) -> Iterator[TravelRecordInput]:
        """
        Parse a single report file.

        Args:
            file_path: Path to report text file
            include_metadata: Include table header and committee info

        Yields:
            TravelRecordInput objects
        """
        filename = file_path.name
        year = filename[:4]

        logger.info(f"Parsing {filename} (year: {year})")

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                yield from self._parse_file_content(
                    f, year, filename, include_metadata
                )
        except Exception as e:
            logger.error(f"Error parsing {filename}: {e}", exc_info=True)

    def _parse_file_content(
        self, file: TextIO, year: str, filename: str, include_metadata: bool
    ) -> Iterator[TravelRecordInput]:
        """Parse file content and yield records."""
        record_lines = False
        current_name = ""
        header_line = ""
        committee = ""
        committee_code = ""
        count = 0

        for line in file:
            # Check for header
            if re.search(self.HEADER_PATTERN, line):
                header_line = line.strip()
                committee, committee_code = self._extract_committee_info(header_line)

                # Extract year from header if present
                year_match = re.search(r"[0-9]{4}\.?$", header_line.strip())
                if year_match:
                    year = year_match.group(0)[:4]

            # Check for start delimiter
            elif re.search(self.START_LINE_PATTERN, line):
                record_lines = True

            # Check for end delimiter
            elif self._is_end_line(line):
                record_lines = False

            # Process data lines
            elif record_lines:
                if self._should_skip_line(line):
                    continue

                result = self._parse_line(line, year, current_name)
                if result is None:
                    continue

                record, current_name = result

                # Add metadata if requested
                if include_metadata:
                    record.table_header = header_line
                    record.committee = committee
                    record.committee_code = committee_code
                record.source_file = filename

                count += 1
                yield record

        logger.info(f"Parsed {count} records from {filename}")

    def parse_directory(
        self, directory: Path, include_metadata: bool = True
    ) -> Iterator[TravelRecordInput]:
        """
        Parse all report files in a directory.

        Args:
            directory: Directory containing report text files
            include_metadata: Include table header and committee info

        Yields:
            TravelRecordInput objects
        """
        files = sorted(directory.glob("*.txt"))
        logger.info(f"Found {len(files)} report files in {directory}")

        for file_path in files:
            yield from self.parse_file(file_path, include_metadata)

    def write_csv(
        self,
        records: Iterator[TravelRecordInput],
        output_file: Path,
        validate: bool = True,
    ) -> Dict[str, int]:
        """
        Write records to CSV file.

        Args:
            records: Iterator of TravelRecordInput objects
            output_file: Output CSV file path
            validate: Whether to validate records

        Returns:
            Dict with statistics
        """
        stats = {"total": 0, "valid": 0, "invalid": 0}

        with open(output_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "name",
                    "member_id",
                    "honorific",
                    "arrival_date",
                    "departure_date",
                    "country",
                    "table_header",
                    "committee",
                    "committee_code",
                    "source_file",
                ],
                quoting=csv.QUOTE_ALL,
            )
            writer.writeheader()

            for record in records:
                stats["total"] += 1

                if validate:
                    try:
                        # Validate by converting to TravelRecord
                        validated = TravelRecord.from_input(record)
                        # Convert back to output format
                        output = TravelRecordOutput.from_travel_record(validated)
                        writer.writerow(output.model_dump())
                        stats["valid"] += 1
                    except Exception as e:
                        logger.warning(f"Invalid record: {e}")
                        stats["invalid"] += 1
                else:
                    writer.writerow(record.model_dump())
                    stats["valid"] += 1

        logger.info(
            f"Wrote {stats['valid']} records to {output_file} "
            f"({stats['invalid']} invalid, {stats['total']} total)"
        )
        return stats
