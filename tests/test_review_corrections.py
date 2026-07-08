"""Tests for the corrections overlay: dotted-path get/set, load/save, and merging."""

import json

import pytest

from official_foreign_travel.review.corrections import (
    get_path,
    load_corrections,
    save_report_correction,
    set_path,
)


class TestGetPath:
    def test_simple_field(self):
        assert get_path({"a": 1}, "a") == 1

    def test_nested_field(self):
        assert get_path({"a": {"b": 2}}, "a.b") == 2

    def test_list_index(self):
        assert get_path({"a": [1, 2, 3]}, "a[1]") == 2

    def test_nested_list_and_field(self):
        data = {"travelers": [{"name": "X"}]}
        assert get_path(data, "travelers[0].name") == "X"

    def test_deep_chain(self):
        data = {"travelers": [{"segments": [{"costs": {"total": {"us_dollar": {"amount": "5"}}}}]}]}
        assert get_path(data, "travelers[0].segments[0].costs.total.us_dollar.amount") == "5"

    def test_invalid_segment_raises(self):
        with pytest.raises(ValueError):
            get_path({"a": 1}, "a[")


class TestSetPath:
    def test_simple_field(self):
        data = {"a": 1}
        set_path(data, "a", 2)
        assert data == {"a": 2}

    def test_nested_field(self):
        data = {"a": {"b": 2}}
        set_path(data, "a.b", 3)
        assert data["a"]["b"] == 3

    def test_list_index_field(self):
        data = {"travelers": [{"name": "X"}]}
        set_path(data, "travelers[0].name", "Y")
        assert data["travelers"][0]["name"] == "Y"

    def test_deep_chain(self):
        data = {"travelers": [{"segments": [{"costs": {"total": {"us_dollar": {"amount": "5"}}}}]}]}
        set_path(data, "travelers[0].segments[0].costs.total.us_dollar.amount", "9.99")
        assert data["travelers"][0]["segments"][0]["costs"]["total"]["us_dollar"]["amount"] == "9.99"


class TestLoadCorrections:
    def test_missing_file_returns_empty_dict(self, tmp_path):
        assert load_corrections(tmp_path / "does-not-exist.json") == {}

    def test_loads_existing_file(self, tmp_path):
        path = tmp_path / "corrections.json"
        path.write_text(json.dumps({"r-1": {"status": "confirmed_ok", "edits": {}}}))
        assert load_corrections(path) == {"r-1": {"status": "confirmed_ok", "edits": {}}}


class TestSaveReportCorrection:
    def test_creates_file_if_missing(self, tmp_path):
        path = tmp_path / "corrections.json"
        entry = save_report_correction(path, "r-1", "edited", {"sponsor.name": "Fixed"})
        assert path.exists()
        assert entry["status"] == "edited"
        assert entry["edits"] == {"sponsor.name": "Fixed"}
        assert "reviewed_at" in entry

    def test_overwrites_existing_entry_for_same_report(self, tmp_path):
        path = tmp_path / "corrections.json"
        save_report_correction(path, "r-1", "edited", {"a": "1"})
        save_report_correction(path, "r-1", "edited", {"a": "2"})
        data = load_corrections(path)
        assert data["r-1"]["edits"] == {"a": "2"}

    def test_preserves_other_reports_entries(self, tmp_path):
        path = tmp_path / "corrections.json"
        save_report_correction(path, "r-1", "confirmed_ok", {})
        save_report_correction(path, "r-2", "edited", {"a": "1"})
        data = load_corrections(path)
        assert set(data.keys()) == {"r-1", "r-2"}


from datetime import date
from decimal import Decimal

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
from official_foreign_travel.review.corrections import apply_corrections


def _cell(amount=None):
    return CostCell(amount=Decimal(amount) if amount is not None else None, raw="")


def _costs(total=None):
    empty = _cell()
    return Costs(per_diem=CostGroup(foreign_currency=empty, us_dollar=_cell(total)),
                 transportation=CostGroup(foreign_currency=empty, us_dollar=empty),
                 other=CostGroup(foreign_currency=empty, us_dollar=empty),
                 total=CostGroup(foreign_currency=empty, us_dollar=_cell(total)))


def _report(report_id, sponsor_name="COMMITTEE ON TEST"):
    segment = TravelSegment(
        arrival_date=date(2018, 1, 5), departure_date=date(2018, 1, 8),
        arrival_raw="1/5", departure_raw="1/8", country_raw="Testland",
        costs=_costs("100.00"),
    )
    return Report(
        report_id=report_id, source_file="x.txt", table_index=0,
        sponsor=Sponsor(type="committee", name=sponsor_name, raw=""),
        period=Period(start=date(2018, 1, 1), end=date(2018, 3, 31), year=2018, quarter=1),
        header_raw="", travelers=[Traveler(name="A", segments=[segment])],
    )


class TestApplyCorrections:
    def test_report_with_no_correction_entry_is_unchanged(self):
        report = _report("r-1")
        result = apply_corrections([report], {})
        assert result[0] is report
        assert "MANUALLY_CORRECTED" not in result[0].flags

    def test_confirmed_ok_with_no_edits_gets_flagged_and_unchanged(self):
        report = _report("r-1")
        corrections = {"r-1": {"status": "confirmed_ok", "edits": {}}}
        result = apply_corrections([report], corrections)
        assert result[0].sponsor.name == "COMMITTEE ON TEST"
        assert "HUMAN_CONFIRMED" in result[0].flags

    def test_edit_applies_and_flags_manually_corrected(self):
        report = _report("r-1")
        corrections = {"r-1": {"status": "edited", "edits": {"sponsor.name": "Fixed Name"}}}
        result = apply_corrections([report], corrections)
        assert result[0].sponsor.name == "Fixed Name"
        assert "MANUALLY_CORRECTED" in result[0].flags

    def test_edit_to_a_cost_amount_is_reflected_and_revalidated(self):
        report = _report("r-1")
        corrections = {
            "r-1": {
                "status": "edited",
                "edits": {
                    "travelers[0].segments[0].costs.total.us_dollar.amount": "999.00",
                },
            }
        }
        result = apply_corrections([report], corrections)
        segment = result[0].travelers[0].segments[0]
        assert segment.costs.total.us_dollar.amount == Decimal("999.00")
        # per_diem (100.00) no longer matches the corrected total (999.00) -> flagged
        assert "ROW_SUM_MISMATCH" in segment.flags
