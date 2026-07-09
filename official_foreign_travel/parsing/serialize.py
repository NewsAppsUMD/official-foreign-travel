"""Serialize assembled Reports to JSON (canonical), flat CSV, or flat JSONL."""

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from ..models.report import Report, Traveler, TravelSegment

SCHEMA_VERSION = "1.0.0"

CSV_FIELDNAMES = [
    # Legacy column names/order, kept for backward compatibility with
    # travel_report_data.csv consumers.
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
    # New columns.
    "per_diem_usd",
    "per_diem_fc",
    "transportation_usd",
    "transportation_fc",
    "other_usd",
    "other_fc",
    "total_usd",
    "total_fc",
    "military_air",
    "report_id",
    "amended",
    "flags",
]


def visible_reports(reports: list[Report], include_superseded: bool) -> list[Report]:
    if include_superseded:
        return reports
    return [r for r in reports if r.superseded_by is None]


def to_json_dict(reports: list[Report], include_superseded: bool = False) -> dict:
    """Build the canonical JSON-serializable dict for a set of reports."""
    visible = visible_reports(reports, include_superseded)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "Congressional Record via House Clerk (Official Foreign Travel reports)",
        "reports": [r.model_dump(mode="json") for r in visible],
    }


def write_json(reports: list[Report], output_path: Path, include_superseded: bool = False) -> None:
    """Write the canonical JSON representation: {schema_version, reports: [...]}."""
    payload = to_json_dict(reports, include_superseded)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def _flatten_segment_row(report: Report, traveler: Traveler, segment: TravelSegment) -> dict:
    costs = segment.costs
    return {
        "name": traveler.name,
        "member_id": traveler.bioguide_id or "",
        "honorific": traveler.honorific or "",
        "arrival_date": segment.arrival_date.strftime("%m/%d/%Y") if segment.arrival_date else "",
        "departure_date": (
            segment.departure_date.strftime("%m/%d/%Y") if segment.departure_date else ""
        ),
        "country": segment.country_raw,
        "table_header": report.header_raw,
        "committee": report.sponsor.name,
        "committee_code": report.sponsor.code or "",
        "source_file": report.source_file,
        "per_diem_usd": (
            costs.per_diem.us_dollar.amount if costs.per_diem.us_dollar.amount is not None else ""
        ),
        "per_diem_fc": (
            costs.per_diem.foreign_currency.amount
            if costs.per_diem.foreign_currency.amount is not None
            else ""
        ),
        "transportation_usd": (
            costs.transportation.us_dollar.amount
            if costs.transportation.us_dollar.amount is not None
            else ""
        ),
        "transportation_fc": (
            costs.transportation.foreign_currency.amount
            if costs.transportation.foreign_currency.amount is not None
            else ""
        ),
        "other_usd": (
            costs.other.us_dollar.amount if costs.other.us_dollar.amount is not None else ""
        ),
        "other_fc": (
            costs.other.foreign_currency.amount
            if costs.other.foreign_currency.amount is not None
            else ""
        ),
        "total_usd": (
            costs.total.us_dollar.amount if costs.total.us_dollar.amount is not None else ""
        ),
        "total_fc": (
            costs.total.foreign_currency.amount
            if costs.total.foreign_currency.amount is not None
            else ""
        ),
        "military_air": any(
            cell.military_air
            for group in (costs.per_diem, costs.transportation, costs.other, costs.total)
            for cell in (group.foreign_currency, group.us_dollar)
        ),
        "report_id": report.report_id,
        "amended": report.amended,
        "flags": ";".join(segment.flags),
    }


def write_csv(
    reports: list[Report], output_path: Path, include_superseded: bool = False
) -> dict[str, int]:
    """
    Write a flat CSV, one row per traveler segment.

    Returns:
        Dict with counts: {"reports": n, "rows": n}
    """
    visible = visible_reports(reports, include_superseded)
    row_count = 0
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        for report in visible:
            for traveler in report.travelers:
                for segment in traveler.segments:
                    writer.writerow(_flatten_segment_row(report, traveler, segment))
                    row_count += 1
    return {"reports": len(visible), "rows": row_count}


def write_jsonl(
    reports: list[Report], output_path: Path, include_superseded: bool = False
) -> dict[str, int]:
    """Write flat records, one JSON object per traveler segment, one per line."""
    visible = visible_reports(reports, include_superseded)
    row_count = 0
    with open(output_path, "w", encoding="utf-8") as f:
        for report in visible:
            for traveler in report.travelers:
                for segment in traveler.segments:
                    row = _flatten_segment_row(report, traveler, segment)
                    row["per_diem_usd"] = (
                        str(row["per_diem_usd"]) if row["per_diem_usd"] != "" else None
                    )
                    for key in (
                        "per_diem_fc",
                        "transportation_usd",
                        "transportation_fc",
                        "other_usd",
                        "other_fc",
                        "total_usd",
                        "total_fc",
                    ):
                        row[key] = str(row[key]) if row[key] != "" else None
                    f.write(json.dumps(row) + "\n")
                    row_count += 1
    return {"reports": len(visible), "rows": row_count}
