"""Tests for the corrections overlay: dotted-path get/set, load/save, and merging."""

import pytest

from official_foreign_travel.review.corrections import get_path, set_path


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
