"""Serialize assembled Reports to JSON (canonical), flat CSV, or flat JSONL."""

import collections
import csv
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

from ..models.report import Report, Traveler, TravelSegment
from .trips import Trip, build_trips

SCHEMA_VERSION = "1.0.0"

# Envelope label shared by the reports JSON and the trips JSON so the two
# can't drift.
_SOURCE_LABEL = "Congressional Record via House Clerk (Official Foreign Travel reports)"

# Fields dropped in --slim mode. The review tool re-loads the source text
# from report_text/ at view time (source_lookup.get_raw_lines) and never
# reads these fields from the JSON, so dropping them only affects
# analytical consumers that don't need source-text provenance. Each field
# has a model default ("" or None) so rehydrating via Report.model_validate
# still works.
_COST_GROUPS = ("per_diem", "transportation", "other", "total")
_COST_CURRENCIES = ("foreign_currency", "us_dollar")


def _cell_raw_excludes() -> dict:
    """Build the nested-exclude dict shape that drops `raw` from every
    CostCell under a Costs block (per_diem/transportation/other/total,
    foreign_currency/us_dollar). Pydantic v2's `exclude` parameter takes
    a nested dict with the `__all__` sentinel for list-of-model fields."""
    return {grp: {cur: {"raw": True} for cur in _COST_CURRENCIES} for grp in _COST_GROUPS}


def _slim_exclude() -> dict:
    """Build the full `exclude` dict for --slim mode."""
    seg_cost_excludes = _cell_raw_excludes()
    return {
        "header_raw": True,
        "signature_raw": True,
        "sponsor": {"raw": True},
        "committee_total": _cell_raw_excludes(),
        "travelers": {
            "__all__": {
                "segments": {
                    "__all__": {
                        "arrival_raw": True,
                        "departure_raw": True,
                        "costs": seg_cost_excludes,
                    }
                }
            }
        },
    }


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


def _ensure_segment_notes(payload: dict) -> None:
    """Keep the editable notes field in JSON even when it is empty."""
    for traveler in payload.get("travelers", []):
        for segment in traveler.get("segments", []):
            segment.setdefault("notes", [])


def visible_reports(reports: list[Report], include_superseded: bool) -> list[Report]:
    if include_superseded:
        return reports
    return [r for r in reports if r.superseded_by is None]


def to_json_dict(
    reports: list[Report],
    include_superseded: bool = False,
    *,
    exclude_defaults: bool = True,
    slim: bool = False,
) -> dict:
    """Build the canonical JSON-serializable dict for a set of reports.

    Args:
        exclude_defaults: Omit fields whose value equals the model default.
            Pydantic rehydrates defaults on ``Report.model_validate``, so
            downstream consumers see the same model. Saves ~25 MB on the
            full corpus.
        slim: Additionally drop source-text fields (CostCell.raw,
            arrival_raw, departure_raw, header_raw, signature_raw,
            Sponsor.raw) that the review tool doesn't read -- it re-loads
            source text from report_text/ at view time. Saves ~9 MB on
            the full corpus.
    """
    visible = visible_reports(reports, include_superseded)
    exclude = _slim_exclude() if slim else None
    serialized_reports = [
        r.model_dump(mode="json", exclude_defaults=exclude_defaults, exclude=exclude)
        for r in visible
    ]
    for report in serialized_reports:
        _ensure_segment_notes(report)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": _SOURCE_LABEL,
        "reports": serialized_reports,
    }


def write_json(
    reports: list[Report],
    output_path: Path,
    include_superseded: bool = False,
    *,
    exclude_defaults: bool = True,
    slim: bool = False,
) -> None:
    """Write the canonical JSON representation: {schema_version, reports: [...]}.

    Auto-gzips when ``output_path`` ends in ``.gz`` (so ``output.json.gz``
    produces a gzip-compressed file). The review CLI and any consumer that
    loads via ``Report.model_validate`` see the same model regardless of
    compression.
    """
    payload = to_json_dict(
        reports,
        include_superseded,
        exclude_defaults=exclude_defaults,
        slim=slim,
    )
    if output_path.suffix == ".gz":
        with gzip.open(output_path, "wt", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    else:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)


def write_json_dir(
    reports: list[Report],
    output_dir: Path,
    include_superseded: bool = False,
    *,
    exclude_defaults: bool = True,
    slim: bool = False,
    compress: bool = False,
) -> dict[str, int]:
    """Write per-year JSON files into ``output_dir``, partitioned by
    ``source_file[:4]`` (the Congressional Record publication year, which
    matches how ``report_text/`` is named).

    For each year present in the corpus, writes ``<output_dir>/<year>.json``
    (or ``<year>.json.gz`` when ``compress=True``). Each file has the same
    ``{schema_version, generated_at, source, reports}`` envelope as the
    single-file ``write_json`` output, so the review CLI loads either
    form transparently via ``_read_json`` + directory globbing.

    Returns ``{year: report_count}`` for the per-year breakdown.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    visible = visible_reports(reports, include_superseded)
    by_year: dict[str, list[Report]] = collections.defaultdict(list)
    for r in visible:
        year = r.source_file[:4]
        by_year[year].append(r)
    counts: dict[str, int] = {}
    for year, year_reports in sorted(by_year.items()):
        filename = f"{year}.json.gz" if compress else f"{year}.json"
        path = output_dir / filename
        # include_superseded=True here because we already filtered above;
        # passing True makes visible_reports a no-op so the year's reports
        # pass through unfiltered.
        write_json(
            year_reports,
            path,
            include_superseded=True,
            exclude_defaults=exclude_defaults,
            slim=slim,
        )
        counts[year] = len(year_reports)
    return counts


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


TRIP_CSV_FIELDNAMES = [
    "trip_id",
    "name",
    "member_id",
    "honorific",
    "sponsor_type",
    "sponsor_name",
    "committee_code",
    "start_date",
    "end_date",
    "days",
    "countries",
    "n_legs",
    "n_filings",
    "source_report_ids",
    "source_files",
    "per_diem_usd",
    "transportation_usd",
    "other_usd",
    "total_usd",
    "military_air",
    "amended",
    "flags",
    "notes",
    "conflict_with",
]


def _flatten_trip_row(trip: Trip) -> dict:
    days = (
        (trip.end_date - trip.start_date).days + 1
        if trip.start_date is not None and trip.end_date is not None
        else ""
    )
    return {
        "trip_id": trip.trip_id,
        "name": trip.name,
        "member_id": trip.bioguide_id or "",
        "honorific": trip.honorific or "",
        "sponsor_type": trip.sponsor_type,
        "sponsor_name": trip.sponsor_name,
        "committee_code": trip.sponsor_code or "",
        "start_date": trip.start_date.strftime("%m/%d/%Y") if trip.start_date else "",
        "end_date": trip.end_date.strftime("%m/%d/%Y") if trip.end_date else "",
        "days": days,
        "countries": "; ".join(trip.countries),
        "n_legs": trip.n_legs,
        "n_filings": trip.n_filings,
        "source_report_ids": "; ".join(trip.source_report_ids),
        "source_files": "; ".join(trip.source_files),
        "per_diem_usd": trip.per_diem_usd if trip.per_diem_usd is not None else "",
        "transportation_usd": (
            trip.transportation_usd if trip.transportation_usd is not None else ""
        ),
        "other_usd": trip.other_usd if trip.other_usd is not None else "",
        "total_usd": trip.total_usd if trip.total_usd is not None else "",
        "military_air": trip.military_air,
        "amended": trip.amended,
        "flags": ";".join(trip.flags),
        "notes": ";".join(trip.notes),
        "conflict_with": "; ".join(trip.conflict_with),
    }


def _trip_stats(trips: list[Trip]) -> dict[str, int]:
    return {
        "trips": len(trips),
        "trips_merged_across_filings": sum(1 for t in trips if t.n_filings > 1),
        "trips_flagged": sum(1 for t in trips if t.flags),
    }


def write_trips_csv(
    reports: list[Report], output_path: Path, include_superseded: bool = False
) -> dict[str, int]:
    """
    Write a flat CSV of canonical trip records, one row per person-journey.

    Returns:
        Dict with counts: {"trips": n, "trips_merged_across_filings": n,
        "trips_flagged": n}
    """
    trips = build_trips(visible_reports(reports, include_superseded))
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TRIP_CSV_FIELDNAMES, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        for trip in trips:
            writer.writerow(_flatten_trip_row(trip))
    return _trip_stats(trips)


def write_trips_jsonl(
    reports: list[Report], output_path: Path, include_superseded: bool = False
) -> dict[str, int]:
    """Write flat trip records, one JSON object per trip, one per line."""
    trips = build_trips(visible_reports(reports, include_superseded))
    with open(output_path, "w", encoding="utf-8") as f:
        for trip in trips:
            row = _flatten_trip_row(trip)
            for key in ("per_diem_usd", "transportation_usd", "other_usd", "total_usd"):
                row[key] = str(row[key]) if row[key] != "" else None
            f.write(json.dumps(row) + "\n")
    return _trip_stats(trips)


def write_trips_json(
    reports: list[Report], output_path: Path, include_superseded: bool = False
) -> dict[str, int]:
    """
    Write the nested trips JSON: {schema_version, generated_at, source, trips}.

    Each trip object is the flat CSV row's fields plus ``bioguide_id``,
    ``publication_date``, ``legs`` (the winning filing's full nested
    segments, including foreign-currency costs, with per-leg provenance)
    and ``filings`` (every filing that reported the trip, newest first).
    Auto-gzips when ``output_path`` ends in ``.gz``.

    Returns the same counts dict as ``write_trips_csv``.
    """
    trips = build_trips(visible_reports(reports, include_superseded))
    serialized_trips = []
    for trip in trips:
        row = _flatten_trip_row(trip)
        for key in ("per_diem_usd", "transportation_usd", "other_usd", "total_usd"):
            row[key] = str(row[key]) if row[key] != "" else None
        row["bioguide_id"] = trip.bioguide_id
        row["publication_date"] = (
            trip.publication_date.isoformat() if trip.publication_date else None
        )
        row["legs"] = [
            {
                **leg.segment.model_dump(mode="json"),
                "report_id": leg.report_id,
                "source_file": leg.source_file,
            }
            for leg in trip.legs
        ]
        row["filings"] = [
            {
                "report_id": filing.report_id,
                "source_file": filing.source_file,
                "sponsor_type": filing.sponsor_type,
                "sponsor_name": filing.sponsor_name,
                "sponsor_code": filing.sponsor_code,
                "publication_date": (
                    filing.publication_date.isoformat() if filing.publication_date else None
                ),
                "amended": filing.amended,
            }
            for filing in trip.filings
        ]
        serialized_trips.append(row)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": _SOURCE_LABEL,
        "trips": serialized_trips,
    }
    if output_path.suffix == ".gz":
        with gzip.open(output_path, "wt", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    else:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    return _trip_stats(trips)
