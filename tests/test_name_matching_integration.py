"""Tests that assemble.py correctly wires exact-then-fuzzy member matching.

Uses a NameMatcher seeded with synthetic legislator data (no network / YAML
files required) rather than the real congress-legislators dataset.
"""

from datetime import date

import pytest

from official_foreign_travel.matchers.name_matcher import NameMatcher
from official_foreign_travel.models.report import CostCell, CostGroup, Costs, TravelSegment
from official_foreign_travel.parsing.assemble import _match_member
from official_foreign_travel.utils.config import Config

GOODLATTE = {
    "id": {"bioguide": "G000289"},
    "name": {
        "first": "Robert",
        "middle": "W.",
        "last": "Goodlatte",
        "suffix": "",
        "nickname": "Bob",
    },
    "terms": [{"start": "1993-01-05", "end": "2019-01-03"}],
}
SMITH = {
    "id": {"bioguide": "S000583"},
    "name": {"first": "Lamar", "middle": "", "last": "Smith", "suffix": "", "nickname": ""},
    "terms": [{"start": "1987-01-06", "end": "2019-01-03"}],
}


@pytest.fixture
def seeded_matcher(tmp_path):
    config = Config(
        data_dir=tmp_path,
        report_text_dir=tmp_path,
        output_dir=tmp_path,
        legislators_current_yaml=tmp_path / "missing-current.yaml",
        legislators_historical_yaml=tmp_path / "missing-historical.yaml",
    )
    matcher = NameMatcher(config=config)
    members_list = [GOODLATTE, SMITH]
    matcher.charset = matcher._get_charset(members_list)
    matcher.members_dict = matcher._generate_bioguide_dict(members_list)
    matcher.members_index = {}
    matcher._append_data(members_list)
    matcher._initialized = True
    return matcher


def empty_costs():
    empty = CostCell(amount=None, raw="")
    group = CostGroup(foreign_currency=empty, us_dollar=empty)
    return Costs(per_diem=group, transportation=group, other=group, total=group)


def dated_segment(arrival, departure):
    return TravelSegment(
        arrival_date=arrival,
        departure_date=departure,
        arrival_raw="",
        departure_raw="",
        country_raw="",
        costs=empty_costs(),
    )


class TestFuzzyFallback:
    def test_exact_match_short_circuits_fuzzy(self, seeded_matcher):
        member_index = {"HON. ROBERT W. GOODLATTE": "G000289"}
        segments = [dated_segment(date(2018, 10, 1), date(2018, 10, 10))]
        bioguide, confidence, flags = _match_member(
            "Hon. Robert W. Goodlatte", segments, member_index, seeded_matcher
        )
        assert bioguide == "G000289"
        assert confidence == 1.0
        assert flags == []

    def test_fuzzy_fallback_resolves_nickname_and_missing_middle_initial(self, seeded_matcher):
        """'Hon. Bob Goodlatte' doesn't exact-match 'HON. ROBERT W. GOODLATTE' in members.csv."""
        segments = [dated_segment(date(2018, 10, 1), date(2018, 10, 10))]
        bioguide, confidence, flags = _match_member(
            "Hon. Bob Goodlatte", segments, {}, seeded_matcher
        )
        assert bioguide == "G000289"
        assert "MEMBER_FUZZY_MATCHED" in flags

    def test_no_name_matcher_flags_unmatched_rather_than_crashing(self):
        segments = [dated_segment(date(2018, 10, 1), date(2018, 10, 10))]
        bioguide, confidence, flags = _match_member("Hon. Bob Goodlatte", segments, {}, None)
        assert bioguide is None
        assert flags == ["MEMBER_UNMATCHED"]

    def test_name_outside_any_members_service_dates_unmatched(self, seeded_matcher):
        segments = [dated_segment(date(1980, 1, 1), date(1980, 1, 5))]
        bioguide, confidence, flags = _match_member(
            "Hon. Bob Goodlatte", segments, {}, seeded_matcher
        )
        assert bioguide is None

    def test_staff_name_without_honorific_never_fuzzy_matched(self, seeded_matcher):
        """A bare staff name that happens to share a surname with a member must not be
        assigned that member's bioguide ID -- NameMatcher has no way to represent "not a
        member," so without this guard it would confidently return the wrong person."""
        segments = [dated_segment(date(2018, 10, 1), date(2018, 10, 10))]
        bioguide, confidence, flags = _match_member("Bob Goodlatte", segments, {}, seeded_matcher)
        assert bioguide is None
        assert flags == ["MEMBER_UNMATCHED"]
