"""Integration tests for the review server, driven in-process via http.client."""

import http.client
import json
import threading
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from official_foreign_travel.models.report import (
    Costs,
    CostCell,
    CostGroup,
    Period,
    Report,
    Sponsor,
    Traveler,
    TravelSegment,
)
from official_foreign_travel.review.server import make_handler
from http.server import ThreadingHTTPServer

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
