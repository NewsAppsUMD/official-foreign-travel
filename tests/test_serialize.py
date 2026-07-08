"""Tests for JSON/CSV/JSONL serialization of assembled reports."""

import csv
import json
from pathlib import Path

from datetime import date

from official_foreign_travel.models.report import Period, Report, Sponsor
from official_foreign_travel.parsing.assemble import assemble_file, load_name_index
from official_foreign_travel.parsing.dedup import dedup_reports
from official_foreign_travel.parsing.serialize import (
    to_json_dict,
    write_csv,
    write_jsonl,
    write_json,
)

FIXTURES = Path(__file__).parent / "fixtures"
MEMBERS_CSV = Path(__file__).parent.parent / "members.csv"


def load_reports(filename):
    return assemble_file(FIXTURES / filename, member_index=load_name_index(MEMBERS_CSV))


class TestToJsonDict:
    def test_schema_has_expected_top_level_keys(self):
        reports = load_reports("2019q1jan29.txt")
        payload = to_json_dict(reports)
        assert payload["schema_version"] == "1.0.0"
        assert "generated_at" in payload
        assert len(payload["reports"]) == len(reports)

    def test_decimal_amounts_serialize_as_strings(self):
        reports = load_reports("2019q1jan29.txt")
        payload = to_json_dict(reports)
        report = next(r for r in payload["reports"] if r["travelers"])
        cell = report["travelers"][0]["segments"][0]["costs"]["total"]["us_dollar"]
        if cell["amount"] is not None:
            assert isinstance(cell["amount"], str)

    def test_superseded_reports_excluded_by_default(self):
        period = Period(start=date(1993, 10, 1), end=date(1993, 12, 31), year=1993, quarter=4)
        sponsor = Sponsor(type="committee", name="COMMITTEE ON ARMED SERVICES", raw="")
        original = Report(
            report_id="orig-000",
            source_file="1994q1feb10.txt",
            table_index=0,
            sponsor=sponsor,
            period=period,
            header_raw="",
            amended=False,
        )
        amendment = Report(
            report_id="amend-000",
            source_file="1994q2may17.txt",
            table_index=0,
            sponsor=sponsor,
            period=period,
            header_raw="",
            amended=True,
        )
        reports = [original, amendment]
        dedup_reports(reports)
        assert original.superseded_by == "amend-000"

        payload = to_json_dict(reports, include_superseded=False)
        assert len(payload["reports"]) == 1
        assert payload["reports"][0]["report_id"] == "amend-000"

    def test_include_superseded_flag_keeps_them(self):
        period = Period(start=date(1993, 10, 1), end=date(1993, 12, 31), year=1993, quarter=4)
        sponsor = Sponsor(type="committee", name="COMMITTEE ON ARMED SERVICES", raw="")
        reports = [
            Report(
                report_id="orig-000",
                source_file="1994q1feb10.txt",
                table_index=0,
                sponsor=sponsor,
                period=period,
                header_raw="",
                amended=False,
            ),
            Report(
                report_id="amend-000",
                source_file="1994q2may17.txt",
                table_index=0,
                sponsor=sponsor,
                period=period,
                header_raw="",
                amended=True,
            ),
        ]
        dedup_reports(reports)
        payload = to_json_dict(reports, include_superseded=True)
        assert len(payload["reports"]) == len(reports)


class TestWriteJson(object):
    def test_round_trips_through_disk(self, tmp_path):
        reports = load_reports("2019q1jan29.txt")
        out = tmp_path / "out.json"
        write_json(reports, out)
        data = json.loads(out.read_text())
        assert len(data["reports"]) == len(reports)


class TestWriteCsv:
    def test_row_count_matches_segment_count(self, tmp_path):
        reports = load_reports("2019q1jan29.txt")
        out = tmp_path / "out.csv"
        stats = write_csv(reports, out)
        with open(out) as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == stats["rows"]
        assert stats["rows"] == sum(len(t.segments) for r in reports for t in r.travelers)

    def test_legacy_column_names_present_and_first(self):
        from official_foreign_travel.parsing.serialize import CSV_FIELDNAMES

        legacy = [
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
        ]
        assert CSV_FIELDNAMES[: len(legacy)] == legacy

    def test_bioguide_id_present_for_exact_matched_traveler(self, tmp_path):
        reports = load_reports("2019q1jan29.txt")
        out = tmp_path / "out.csv"
        write_csv(reports, out)
        with open(out) as f:
            rows = list(csv.DictReader(f))
        goodlatte_rows = [r for r in rows if "Goodlatte" in r["name"]]
        assert goodlatte_rows
        assert goodlatte_rows[0]["member_id"] == "G000289"


class TestWriteJsonl:
    def test_one_line_per_segment(self, tmp_path):
        reports = load_reports("2018q4nov16.txt")
        out = tmp_path / "out.jsonl"
        stats = write_jsonl(reports, out)
        lines = out.read_text().strip().split("\n")
        assert len(lines) == stats["rows"]
        for line in lines:
            json.loads(line)  # each line is valid JSON
