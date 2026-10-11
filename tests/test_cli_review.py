"""Smoke tests for the oft-review CLI's argument validation and error paths."""

import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from official_foreign_travel.cli.review import main as review_main
from official_foreign_travel.models.report import (
    CostCell,
    CostGroup,
    Costs,
    Report,
    Sponsor,
    Traveler,
    TravelSegment,
)

FIXTURES = Path(__file__).parent / "fixtures"


def run_cli(args, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["oft-review"] + args)
    return review_main()


def _report_with_segment():
    empty = CostCell(raw="")
    costs = Costs(
        per_diem=CostGroup(foreign_currency=empty, us_dollar=CostCell(amount=Decimal("100"))),
        transportation=CostGroup(foreign_currency=empty, us_dollar=empty),
        other=CostGroup(foreign_currency=empty, us_dollar=empty),
        total=CostGroup(foreign_currency=empty, us_dollar=CostCell(amount=Decimal("100"))),
    )
    return Report(
        report_id="r-1",
        source_file="source.txt",
        table_index=0,
        sponsor=Sponsor(type="committee", name="COMMITTEE ON TEST"),
        travelers=[
            Traveler(
                name="A",
                segments=[
                    TravelSegment(
                        arrival_date=date(2018, 1, 1),
                        country_raw="Testland",
                        costs=costs,
                    )
                ],
            )
        ],
    )


class TestReviewCli:
    def test_missing_report_text_dir_returns_error_code(self, tmp_path, monkeypatch, capsys):
        code = run_cli([str(tmp_path / "does-not-exist"), str(tmp_path / "out.json")], monkeypatch)
        assert code == 1
        assert "not a directory" in capsys.readouterr().out

    def test_missing_parsed_json_returns_error_code(self, tmp_path, monkeypatch, capsys):
        code = run_cli([str(tmp_path), str(tmp_path / "does-not-exist.json")], monkeypatch)
        assert code == 1
        assert "not found" in capsys.readouterr().out

    def test_invalid_json_returns_error_code(self, tmp_path, monkeypatch, capsys):
        bad_json = tmp_path / "bad.json"
        bad_json.write_text("not valid json")
        code = run_cli([str(tmp_path), str(bad_json)], monkeypatch)
        assert code == 1
        assert "invalid JSON" in capsys.readouterr().out

    def test_missing_reports_key_returns_error_code(self, tmp_path, monkeypatch, capsys):
        wrong_shape = tmp_path / "wrong.json"
        wrong_shape.write_text(json.dumps({"not_reports": []}))
        code = run_cli([str(tmp_path), str(wrong_shape)], monkeypatch)
        assert code == 1
        assert "reports" in capsys.readouterr().out

    def test_invalid_report_data_returns_error_code(self, tmp_path, monkeypatch, capsys):
        bad_report = tmp_path / "bad_report.json"
        bad_report.write_text(json.dumps({"reports": [{"not": "a valid report"}]}))
        code = run_cli([str(tmp_path), str(bad_report)], monkeypatch)
        assert code == 1
        assert "invalid report data" in capsys.readouterr().out

    def test_applies_saved_segment_corrections_before_starting_server(self, tmp_path, monkeypatch):
        parsed_json = tmp_path / "output.json"
        parsed_json.write_text(json.dumps({"reports": [_report_with_segment().model_dump(mode="json")]}))
        corrections = tmp_path / "corrections.json"
        corrections.write_text(
            json.dumps(
                {
                    "r-1": {
                        "status": "edited",
                        "edits": {
                            "travelers[0].segments[0].country_raw": "Updatedland",
                            "travelers[0].segments[0].notes": ["Verified."],
                        },
                    }
                }
            )
        )
        captured = {}

        def fake_run_server(reports, *_args, **_kwargs):
            captured["report"] = reports[0]

        monkeypatch.setattr("official_foreign_travel.cli.review.run_server", fake_run_server)

        code = run_cli(
            [str(tmp_path), str(parsed_json), "--corrections", str(corrections)], monkeypatch
        )

        segment = captured["report"].travelers[0].segments[0]
        assert code == 0
        assert segment.country_raw == "Updatedland"
        assert segment.notes == ["Verified."]


class TestReviewCliTrips:
    def test_derives_trips_after_applying_corrections(self, tmp_path, monkeypatch):
        # A correction that fixes a segment's arrival date must change the
        # derived trip: corrections are applied BEFORE build_trips runs.
        parsed_json = tmp_path / "output.json"
        parsed_json.write_text(
            json.dumps({"reports": [_report_with_segment().model_dump(mode="json")]})
        )
        corrections = tmp_path / "corrections.json"
        corrections.write_text(
            json.dumps(
                {
                    "r-1": {
                        "status": "edited",
                        "edits": {"travelers[0].segments[0].arrival_date": "2018-02-01"},
                    }
                }
            )
        )
        captured = {}

        def fake_run_server(reports, _report_text_dir, _corrections_path, trips, **_kwargs):
            captured["trips"] = trips

        monkeypatch.setattr("official_foreign_travel.cli.review.run_server", fake_run_server)

        code = run_cli(
            [str(tmp_path), str(parsed_json), "--corrections", str(corrections)], monkeypatch
        )

        assert code == 0
        assert [t.start_date for t in captured["trips"]] == [date(2018, 2, 1)]

    def test_excludes_superseded_reports_from_trips(self, tmp_path, monkeypatch):
        superseded = _report_with_segment()
        superseded.superseded_by = "r-2"
        parsed_json = tmp_path / "output.json"
        parsed_json.write_text(json.dumps({"reports": [superseded.model_dump(mode="json")]}))
        captured = {}

        def fake_run_server(reports, _report_text_dir, _corrections_path, trips, **_kwargs):
            captured["trips"] = trips
            captured["reports"] = reports

        monkeypatch.setattr("official_foreign_travel.cli.review.run_server", fake_run_server)
        # The CLI's default corrections path is CWD-relative; chdir into
        # tmp_path so the default file simply doesn't exist (the loader
        # returns {}) rather than reading a real corrections.json that
        # happens to sit in the working directory.
        monkeypatch.chdir(tmp_path)

        code = run_cli([str(tmp_path), str(parsed_json)], monkeypatch)

        assert code == 0
        # Superseded filings contribute no trips...
        assert captured["trips"] == []
        # ...but stay browsable as filings in the report list.
        assert len(captured["reports"]) == 1
