"""Tests for the corrections overlay: dotted-path get/set, load/save, and merging."""

import json
import threading
from datetime import date
from decimal import Decimal

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
from official_foreign_travel.parsing.validate import validate_report
from official_foreign_travel.review.corrections import (
    apply_corrections,
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
        assert (
            data["travelers"][0]["segments"][0]["costs"]["total"]["us_dollar"]["amount"] == "9.99"
        )


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

    def test_concurrent_saves_do_not_drop_each_others_entries(self, tmp_path):
        path = tmp_path / "corrections.json"
        barrier = threading.Barrier(2)

        def save(report_id):
            barrier.wait()
            save_report_correction(path, report_id, "edited", {"a": report_id})

        threads = [threading.Thread(target=save, args=(rid,)) for rid in ("r-1", "r-2")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        data = load_corrections(path)
        assert set(data.keys()) == {"r-1", "r-2"}


def _cell(amount=None):
    # Mirror real source conventions: dot-fill when empty, the amount
    # string when present. validate_report uses `raw` to distinguish
    # source-declared totals from computed ones, so an empty-string raw
    # on a set amount would be misread as "we computed it."
    if amount is None:
        return CostCell(amount=None, raw="...........")
    return CostCell(amount=Decimal(amount), raw=str(amount))


def _costs(total=None):
    # Each cell must be a distinct instance -- a shared `empty` cell would
    # alias across groups, so mutating one (e.g. setting
    # transportation.us_dollar.amount) would silently mutate the others.
    return Costs(
        per_diem=CostGroup(foreign_currency=_cell(), us_dollar=_cell(total)),
        transportation=CostGroup(foreign_currency=_cell(), us_dollar=_cell()),
        other=CostGroup(foreign_currency=_cell(), us_dollar=_cell()),
        total=CostGroup(foreign_currency=_cell(), us_dollar=_cell(total)),
    )


def _report(report_id, sponsor_name="COMMITTEE ON TEST"):
    segment = TravelSegment(
        arrival_date=date(2018, 1, 5),
        departure_date=date(2018, 1, 8),
        arrival_raw="1/5",
        departure_raw="1/8",
        country_raw="Testland",
        costs=_costs("100.00"),
    )
    return Report(
        report_id=report_id,
        source_file="x.txt",
        table_index=0,
        sponsor=Sponsor(type="committee", name=sponsor_name, raw=""),
        period=Period(start=date(2018, 1, 1), end=date(2018, 3, 31), year=2018, quarter=1),
        header_raw="",
        travelers=[Traveler(name="A", segments=[segment])],
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
        # Add a second component so the segment produces a real downgrade flag
        # (positive delta → ROW_TOTAL_INCLUDES_UNBROKEN_COSTS).
        report.travelers[0].segments[0].costs.transportation.us_dollar.amount = Decimal("50.00")
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
        # per_diem (100) + transport (50) = 150, no longer matches the corrected total (999) -> flagged
        assert "ROW_TOTAL_INCLUDES_UNBROKEN_COSTS" in segment.flags

    def test_correction_fixing_arithmetic_clears_stale_row_sum_mismatch(self):
        report = _report("r-1")
        segment = report.travelers[0].segments[0]
        # Add a second component so the segment produces a real downgrade flag
        # (positive delta → ROW_TOTAL_INCLUDES_UNBROKEN_COSTS).
        segment.costs.transportation.us_dollar.amount = Decimal("50.00")
        segment.costs.total.us_dollar.amount = Decimal("999.00")
        validate_report(report)
        assert "ROW_TOTAL_INCLUDES_UNBROKEN_COSTS" in segment.flags

        corrections = {
            "r-1": {
                "status": "edited",
                "edits": {
                    "travelers[0].segments[0].costs.total.us_dollar.amount": "150.00",
                },
            }
        }
        result = apply_corrections([report], corrections)
        fixed_segment = result[0].travelers[0].segments[0]
        assert fixed_segment.costs.total.us_dollar.amount == Decimal("150.00")
        assert "ROW_TOTAL_INCLUDES_UNBROKEN_COSTS" not in fixed_segment.flags

    def test_edit_with_invalid_path_does_not_silently_apply(self):
        report = _report("r-1")
        corrections = {"r-1": {"status": "edited", "edits": {"sponsor.nam": "Bogus"}}}
        result = apply_corrections([report], corrections)
        assert result[0].sponsor.name == "COMMITTEE ON TEST"
        assert "MANUALLY_CORRECTED" not in result[0].flags

    def test_bad_report_correction_does_not_block_other_reports(self):
        bad_report = _report("r-bad")
        good_report = _report("r-good")
        corrections = {
            "r-bad": {
                "status": "edited",
                "edits": {
                    "travelers[0].segments[0].costs.total.us_dollar.amount": "not-a-number",
                },
            },
            "r-good": {
                "status": "edited",
                "edits": {"sponsor.name": "Fixed Name"},
            },
        }
        result = apply_corrections([bad_report, good_report], corrections)
        assert result[0] is bad_report
        assert "MANUALLY_CORRECTED" not in result[0].flags
        assert result[1].sponsor.name == "Fixed Name"
        assert "MANUALLY_CORRECTED" in result[1].flags

    def test_null_amount_for_a_blank_cost_cell_does_not_block_other_edits(self):
        """The review UI sends null (not "") for a cost/date field a reviewer left
        blank -- Optional[Decimal]/Optional[date] reject "" outright, which would
        otherwise silently drop every edit on any report with a blank cost cell."""
        report = _report("r-1")
        corrections = {
            "r-1": {
                "status": "edited",
                "edits": {
                    "sponsor.name": "Fixed Name",
                    "travelers[0].segments[0].costs.transportation.us_dollar.amount": None,
                },
            }
        }
        result = apply_corrections([report], corrections)
        assert result[0].sponsor.name == "Fixed Name"
        assert result[0].travelers[0].segments[0].costs.transportation.us_dollar.amount is None
        assert "MANUALLY_CORRECTED" in result[0].flags
