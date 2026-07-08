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
