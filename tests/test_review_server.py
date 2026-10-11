"""Review server tests: HTTP endpoints in-process, plus pure payload builders."""

import http.client
import json
import threading
from datetime import date
from decimal import Decimal
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from official_foreign_travel.models.report import (
    CostCell,
    CostGroup,
    Costs,
    Period,
    Report,
    Sponsor,
    Traveler,
    TravelSegment,
)
from official_foreign_travel.parsing.trips import build_trips
from official_foreign_travel.review.server import _trip_detail, _trip_row, make_handler

FIXTURES = Path(__file__).parent / "fixtures"


def _cell(amount=None):
    return CostCell(amount=Decimal(amount) if amount is not None else None, raw="")


def _costs(total=None):
    empty = _cell()
    return Costs(
        per_diem=CostGroup(foreign_currency=empty, us_dollar=empty),
        transportation=CostGroup(foreign_currency=empty, us_dollar=empty),
        other=CostGroup(foreign_currency=empty, us_dollar=empty),
        total=CostGroup(foreign_currency=empty, us_dollar=_cell(total)),
    )


def _flagged_report(report_id):
    segment = TravelSegment(
        arrival_date=date(2018, 1, 5),
        departure_date=date(2018, 1, 8),
        arrival_raw="1/5",
        departure_raw="1/8",
        country_raw="Testland",
        costs=_costs("100.00"),
        source_lines=[1],
    )
    return Report(
        report_id=report_id,
        source_file="2019q1jan29.txt",
        table_index=0,
        sponsor=Sponsor(type="committee", name="COMMITTEE ON TEST", raw=""),
        period=Period(start=date(2018, 1, 1), end=date(2018, 3, 31), year=2018, quarter=1),
        header_raw="",
        flags=["LAYOUT_LOW_CONFIDENCE"],
        travelers=[Traveler(name="A", segments=[segment])],
    )


def _unflagged_report(report_id):
    report = _flagged_report(report_id)
    report.flags = []
    return report


def _trip(total=None):
    """One derived trip: Jane Smith, FRANCE then GERMANY, March 2019.

    ``total`` fills both legs' total.us_dollar amount (for the cost
    serialization tests); None leaves every cost cell empty.
    """
    traveler = Traveler(
        name="Hon. Jane Smith",
        bioguide_id="S001",
        segments=[
            TravelSegment(
                arrival_date=date(2019, 3, 1),
                departure_date=date(2019, 3, 5),
                country_raw="FRANCE",
                countries=["FRANCE"],
                costs=_costs(total),
            ),
            TravelSegment(
                arrival_date=date(2019, 3, 5),
                departure_date=date(2019, 3, 8),
                country_raw="GERMANY",
                countries=["GERMANY"],
                costs=_costs(total),
            ),
        ],
    )
    report = Report(
        report_id="f-000",
        source_file="2019q1jan02.txt",
        table_index=0,
        sponsor=Sponsor(type="committee", name="COMMITTEE ON TEST", code="HS00"),
        travelers=[traveler],
    )
    return build_trips([report])[0]


@pytest.fixture
def running_server(tmp_path):
    reports = [_flagged_report("r-1"), _flagged_report("r-2")]
    corrections_path = tmp_path / "corrections.json"
    handler_cls = make_handler(reports, FIXTURES, corrections_path, trips=[])
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, corrections_path
    server.shutdown()
    thread.join()


@pytest.fixture
def trips_server(tmp_path):
    trips = [_trip()]
    reports = [_flagged_report("r-1")]
    handler_cls = make_handler(reports, FIXTURES, tmp_path / "corrections.json", trips=trips)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, trips
    server.shutdown()
    thread.join()


def _get(server, path):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
    conn.request("GET", path)
    response = conn.getresponse()
    body = response.read()
    conn.close()
    return response.status, body


class TestListEndpoint:
    def test_returns_all_reports_as_unreviewed(self, running_server):
        server, _ = running_server
        status, body = _get(server, "/api/reports")
        assert status == 200
        data = json.loads(body)
        assert len(data) == 2
        assert {r["report_id"] for r in data} == {"r-1", "r-2"}
        assert all(r["status"] == "unreviewed" for r in data)


class TestUnflaggedReportsAreBrowsable:
    """Every parsed report is served, not just the flagged review queue --
    the list view's 'flagged only' toggle is a client-side filter."""

    def test_list_and_detail_include_unflagged_reports(self, tmp_path):
        reports = [_flagged_report("r-1"), _unflagged_report("r-clean")]
        handler_cls = make_handler(reports, FIXTURES, tmp_path / "corrections.json", trips=[])
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status, body = _get(server, "/api/reports")
            assert status == 200
            listed = {r["report_id"] for r in json.loads(body)}
            assert listed == {"r-1", "r-clean"}

            status, body = _get(server, "/api/reports/r-clean")
            assert status == 200
            assert json.loads(body)["report"]["flags"] == []
        finally:
            server.shutdown()
            thread.join()


class TestCacheHeaders:
    def test_api_and_static_responses_are_never_cached(self, running_server):
        """Browsers heuristically cache responses with no Cache-Control across
        server restarts, so a re-parse's changes silently wouldn't show up."""
        server, _ = running_server
        for path in ("/api/reports", "/api/reports/r-1", "/"):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
            conn.request("GET", path)
            response = conn.getresponse()
            response.read()
            conn.close()
            assert response.getheader("Cache-Control") == "no-store", path


class TestStaticFiles:
    def test_serves_index_html(self, running_server):
        server, _ = running_server
        status, body = _get(server, "/")
        assert status == 200
        assert b"Report Review" in body

    def test_unknown_path_is_404(self, running_server):
        server, _ = running_server
        status, _ = _get(server, "/nonexistent")
        assert status == 404


class TestMalformedInput:
    def test_corrupted_corrections_file_returns_500_not_dropped_connection(self, running_server):
        server, corrections_path = running_server
        corrections_path.write_text("not valid json")
        status, _ = _get(server, "/api/reports")
        assert status == 500

    def test_malformed_post_body_returns_500_not_dropped_connection(self, running_server):
        server, _ = running_server
        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
        conn.request(
            "POST",
            "/api/reports/r-1/corrections",
            body=b"not valid json",
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        response.read()
        conn.close()
        assert response.status == 500


class TestDetailEndpoint:
    def test_returns_report_raw_lines_and_default_correction(self, running_server):
        server, _ = running_server
        status, body = _get(server, "/api/reports/r-1")
        assert status == 200
        data = json.loads(body)
        assert data["report"]["report_id"] == "r-1"
        assert isinstance(data["raw_lines"], list)
        assert data["correction"] == {"status": "unreviewed", "edits": {}}

    def test_unknown_report_id_is_404(self, running_server):
        server, _ = running_server
        status, _ = _get(server, "/api/reports/does-not-exist")
        assert status == 404


def _post(server, path, payload):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
    body = json.dumps(payload).encode("utf-8")
    conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
    response = conn.getresponse()
    result = response.read()
    conn.close()
    return response.status, result


class TestSaveCorrectionsEndpoint:
    def test_save_then_list_reflects_status(self, running_server):
        server, _ = running_server
        status, body = _post(
            server,
            "/api/reports/r-1/corrections",
            {"status": "edited", "edits": {"sponsor.name": "Fixed"}},
        )
        assert status == 200
        entry = json.loads(body)
        assert entry["status"] == "edited"
        assert entry["edits"] == {"sponsor.name": "Fixed"}

        _, list_body = _get(server, "/api/reports")
        reports = json.loads(list_body)
        r1 = next(r for r in reports if r["report_id"] == "r-1")
        assert r1["status"] == "edited"
        r2 = next(r for r in reports if r["report_id"] == "r-2")
        assert r2["status"] == "unreviewed"

    def test_confirm_ok_round_trip(self, running_server):
        server, _ = running_server
        self_status, _ = _post(
            server, "/api/reports/r-2/corrections", {"status": "confirmed_ok", "edits": {}}
        )
        assert self_status == 200
        _, detail_body = _get(server, "/api/reports/r-2")
        detail = json.loads(detail_body)
        assert detail["correction"]["status"] == "confirmed_ok"

    def test_unknown_report_id_is_404(self, running_server):
        server, _ = running_server
        status, _ = _post(
            server, "/api/reports/does-not-exist/corrections", {"status": "edited", "edits": {}}
        )
        assert status == 404


class TestTripRow:
    def test_row_fields_are_client_ready(self):
        # Lists (not joined strings) and ISO dates (string sorting in the
        # table is chronological) -- the CSV row shape is for flat files,
        # this one is for the browser.
        row = _trip_row(_trip())
        assert row["trip_id"]
        assert row["name"] == "Hon. Jane Smith"
        assert row["bioguide_id"] == "S001"
        assert row["start_date"] == "2019-03-01"
        assert row["end_date"] == "2019-03-08"
        assert row["days"] == 8
        assert row["countries"] == ["FRANCE", "GERMANY"]
        assert row["n_legs"] == 2
        assert row["n_filings"] == 1
        assert row["source_report_ids"] == ["f-000"]
        assert row["source_files"] == ["2019q1jan02.txt"]
        assert row["flags"] == []
        assert row["conflict_with"] == []
        assert row["total_usd"] is None

    def test_undated_row_has_empty_dates_and_none_days(self):
        trip = _trip()
        trip.start_date = None
        trip.end_date = None
        row = _trip_row(trip)
        assert row["start_date"] == ""
        assert row["end_date"] == ""
        assert row["days"] is None


class TestTripDetail:
    def test_detail_extends_row_with_legs_and_filings(self):
        trip = _trip()
        detail = _trip_detail(trip)
        assert detail["trip_id"] == trip.trip_id
        assert [leg["arrival_date"] for leg in detail["legs"]] == ["2019-03-01", "2019-03-05"]
        assert detail["legs"][0]["country_raw"] == "FRANCE"
        assert detail["legs"][0]["report_id"] == "f-000"
        assert detail["filings"][0]["report_id"] == "f-000"
        assert detail["filings"][0]["sponsor_name"] == "COMMITTEE ON TEST"
        assert detail["filings"][0]["sponsor_code"] == "HS00"
        assert detail["filings"][0]["publication_date"] == "2019-01-02"
        assert detail["filings"][0]["amended"] is False
        assert detail["conflict_with"] == []


class TestTripCostSerialization:
    def test_amounts_reach_the_client_as_strings(self):
        # json.dumps raises TypeError on Decimal, which would 500 /api/trips
        # for the cost-bearing majority of the corpus: every money field in
        # the payload must be a string or None, never a Decimal.
        trip = _trip(Decimal("100"))  # both legs: total.us_dollar = 100
        assert _trip_row(trip)["total_usd"] == "200"
        detail = _trip_detail(trip)
        assert detail["per_diem_usd"] is None
        assert detail["transportation_usd"] is None


class TestTripsEndpoints:
    def test_list_returns_one_row_per_trip(self, trips_server):
        server, trips = trips_server
        status, body = _get(server, "/api/trips")
        assert status == 200
        rows = json.loads(body)
        assert len(rows) == 1
        assert rows[0]["trip_id"] == trips[0].trip_id

    def test_detail_returns_legs_for_the_requested_trip(self, trips_server):
        server, trips = trips_server
        status, body = _get(server, f"/api/trips/{trips[0].trip_id}")
        assert status == 200
        detail = json.loads(body)
        assert detail["trip_id"] == trips[0].trip_id
        assert len(detail["legs"]) == 2

    def test_unknown_trip_id_is_404(self, trips_server):
        server, _ = trips_server
        status, _ = _get(server, "/api/trips/no-such-trip")
        assert status == 404

    def test_trips_page_serves(self, trips_server):
        # The /trips.html whitelist entry only works now that the file exists.
        server, _ = trips_server
        status, body = _get(server, "/trips.html")
        assert status == 200
        assert b"Trip Review" in body
