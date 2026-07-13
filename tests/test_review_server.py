"""Integration tests for the review server, driven in-process via http.client."""

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
from official_foreign_travel.review.server import make_handler

FIXTURES = Path(__file__).parent / "fixtures"


def _cell(amount=None):
    return CostCell(amount=Decimal(amount) if amount is not None else None, raw="")


def _costs(total=None):
    empty = _cell()
    return Costs(
        per_diem=CostGroup(foreign_currency=empty, us_dollar=_cell(total)),
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


@pytest.fixture
def running_server(tmp_path):
    reports = [_flagged_report("r-1"), _flagged_report("r-2")]
    corrections_path = tmp_path / "corrections.json"
    handler_cls = make_handler(reports, FIXTURES, corrections_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, corrections_path
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
        handler_cls = make_handler(reports, FIXTURES, tmp_path / "corrections.json")
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
