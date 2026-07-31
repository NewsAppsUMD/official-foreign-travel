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
        assert flags == ["STAFF_UNMATCHED"]


class TestWasServing:
    """Direct tests for the date-verification gate used by the bare-name
    recovery path. The gate accepts a bioguide only if the matched member was
    actually serving during the report's year (±1 for filing lag)."""

    def test_member_serving_in_year(self, seeded_matcher):
        assert seeded_matcher.was_serving("G000289", 2018) is True

    def test_member_serving_year_before_term_starts(self, seeded_matcher):
        assert seeded_matcher.was_serving("G000289", 1990) is False

    def test_member_serving_year_after_term_ends(self, seeded_matcher):
        # Goodlatte's term ended 2019-01-03; ±1 year window from 2025 doesn't reach it.
        assert seeded_matcher.was_serving("G000289", 2025) is False

    def test_window_of_one_captures_filing_lag(self, seeded_matcher):
        """Goodlatte's term started 1993-01-05; was_serving(1992, window=1)
        should return True because the ±1 year window reaches into 1993."""
        assert seeded_matcher.was_serving("G000289", 1992, window=1) is True

    def test_window_zero_strict(self, seeded_matcher):
        """window=0 means the member must be serving in exactly that year."""
        assert seeded_matcher.was_serving("G000289", 1992, window=0) is False

    def test_unknown_bioguide_returns_false(self, seeded_matcher):
        assert seeded_matcher.was_serving("Z999999", 2018) is False


class TestWasServingMonth:
    """Direct tests for the tighter, month-precision date-verification gate.

    Goodlatte's term ends 2019-01-03, so (2019, 1) is indexed as served but
    (2019, 2) onward is not -- unlike `was_serving`, which would say he was
    serving 'in 2019' for the whole year because part of it overlaps his
    term.
    """

    def test_serving_in_exact_month(self, seeded_matcher):
        assert seeded_matcher.was_serving_month("G000289", 2018, 6) is True

    def test_not_serving_several_months_after_term_ends(self, seeded_matcher):
        """Regression for a real false match: a member who resigned in
        January still passed the old whole-year `was_serving` check for a
        trip in August of that same year. Month precision rejects it."""
        assert seeded_matcher.was_serving_month("G000289", 2019, 8) is False

    def test_window_of_one_month_captures_filing_lag(self, seeded_matcher):
        """Term ends 2019-01-03; February 2019 is within a 1-month window
        of the last month he actually served (January)."""
        assert seeded_matcher.was_serving_month("G000289", 2019, 2, window_months=1) is True

    def test_window_zero_strict(self, seeded_matcher):
        assert seeded_matcher.was_serving_month("G000289", 2019, 2, window_months=0) is False

    def test_unknown_bioguide_returns_false(self, seeded_matcher):
        assert seeded_matcher.was_serving_month("Z999999", 2018, 6) is False


class TestBareNameDateVerifiedMatch:
    """The bare-name recovery path: a bare 'First Last' (no Hon. prefix) tries
    HON.-prefixed exact lookups against members.csv, accepted only if the
    matched bioguide was serving during the report's period (±1 year)."""

    def test_bare_name_matches_when_member_serving(self, seeded_matcher):
        """'Robert Goodlatte' (no Hon. prefix) matches 'HON. ROBERT GOODLATTE'
        via the date-verified path because Goodlatte was serving in 2018."""
        from official_foreign_travel.parsing.header import Period

        member_index = {"HON. ROBERT GOODLATTE": "G000289"}
        period = Period(start=date(2018, 10, 1), end=date(2018, 12, 31), year=2018, quarter=None, raw="")
        bioguide, confidence, flags = _match_member(
            "Robert Goodlatte",
            [],
            member_index,
            seeded_matcher,
            period=period,
        )
        assert bioguide == "G000289"
        assert confidence == 1.0
        assert flags == ["MEMBER_MATCHED_BY_NAME_DATE"]

    def test_bare_name_rejected_when_member_not_yet_serving(self, seeded_matcher):
        """A staffer named 'Robert Goodlatte' traveling in 1990 must NOT match
        'HON. ROBERT GOODLATTE' (term started 1993) -- the date gate rejects."""
        from official_foreign_travel.parsing.header import Period

        member_index = {"HON. ROBERT GOODLATTE": "G000289"}
        period = Period(start=date(1990, 1, 1), end=date(1990, 3, 31), year=1990, quarter=None, raw="")
        bioguide, _, flags = _match_member(
            "Robert Goodlatte",
            [],
            member_index,
            seeded_matcher,
            period=period,
        )
        assert bioguide is None
        assert "MEMBER_MATCHED_BY_NAME_DATE" not in flags
        assert "STAFF_UNMATCHED" in flags

    def test_bare_name_rejected_when_member_resigned_earlier_same_year(self, seeded_matcher):
        """Regression for a real false match: a staffer named 'Robert
        Goodlatte' traveling in August 2019 must NOT match 'HON. ROBERT
        GOODLATTE' just because Goodlatte's term overlapped January 2019
        (it ended 2019-01-03) -- a whole-year check would wrongly accept
        this trip, seven months after he'd left office. Month-precision
        date verification rejects it."""
        from official_foreign_travel.parsing.header import Period

        member_index = {"HON. ROBERT GOODLATTE": "G000289"}
        segments = [dated_segment(date(2019, 8, 10), date(2019, 8, 15))]
        period = Period(start=date(2019, 7, 1), end=date(2019, 9, 30), year=2019, quarter=3, raw="")
        bioguide, _, flags = _match_member(
            "Robert Goodlatte",
            segments,
            member_index,
            seeded_matcher,
            period=period,
        )
        assert bioguide is None
        assert "MEMBER_MATCHED_BY_NAME_DATE" not in flags
        assert "STAFF_UNMATCHED" in flags

    def test_bare_name_with_middle_initial_matches_via_first_last(self, seeded_matcher):
        """'Robert W. Goodlatte' (no Hon. prefix) tries HON. ROBERT W. GOODLATTE
        and HON. ROBERT GOODLATTE; the latter is in members.csv."""
        from official_foreign_travel.parsing.header import Period

        member_index = {"HON. ROBERT GOODLATTE": "G000289"}
        period = Period(start=date(2018, 10, 1), end=date(2018, 12, 31), year=2018, quarter=None, raw="")
        bioguide, confidence, flags = _match_member(
            "Robert W. Goodlatte",
            [],
            member_index,
            seeded_matcher,
            period=period,
        )
        assert bioguide == "G000289"
        assert confidence == 1.0
        assert flags == ["MEMBER_MATCHED_BY_NAME_DATE"]

    def test_bare_name_no_period_returns_unmatched(self, seeded_matcher):
        """Without a period, no year inference, no date-verified match."""
        member_index = {"HON. ROBERT GOODLATTE": "G000289"}
        bioguide, _, flags = _match_member(
            "Robert Goodlatte", [], member_index, seeded_matcher, period=None
        )
        assert bioguide is None
        assert flags == ["STAFF_UNMATCHED"]

    def test_single_token_bare_name_skipped(self, seeded_matcher):
        """'Goodlatte' (single token) is too ambiguous to date-verify."""
        from official_foreign_travel.parsing.header import Period

        member_index = {"HON. GOODLATTE": "G000289"}
        period = Period(start=date(2018, 10, 1), end=date(2018, 12, 31), year=2018, quarter=None, raw="")
        bioguide, _, flags = _match_member(
            "Goodlatte", [], member_index, seeded_matcher, period=period
        )
        assert bioguide is None
        assert flags == ["STAFF_UNMATCHED"]

    def test_bare_name_not_in_index_returns_unmatched(self, seeded_matcher):
        """A bare name that doesn't appear in members.csv at all falls
        through to the safety gate."""
        from official_foreign_travel.parsing.header import Period

        member_index = {"HON. SOMEBODY ELSE": "E000001"}
        period = Period(start=date(2018, 10, 1), end=date(2018, 12, 31), year=2018, quarter=None, raw="")
        bioguide, _, flags = _match_member(
            "Robert Goodlatte", [], member_index, seeded_matcher, period=period
        )
        assert bioguide is None
        assert flags == ["STAFF_UNMATCHED"]


# Same-surname member pairs for ambiguous-fuzzy disambiguation tests.
DONALD_PAYNE = {
    "id": {"bioguide": "P000149"},
    "name": {"first": "Donald", "middle": "M.", "last": "Payne", "suffix": "", "nickname": ""},
    "terms": [{"start": "1989-01-03", "end": "2013-01-03"}],
}
LEWIS_PAYNE = {
    "id": {"bioguide": "P000152"},
    "name": {"first": "Lewis", "middle": "", "last": "Payne", "suffix": "", "nickname": ""},
    "terms": [{"start": "1990-01-03", "end": "2010-01-03"}],
}
CHRISTOPHER_SMITH = {
    "id": {"bioguide": "S000522"},
    "name": {"first": "Christopher", "middle": "H.", "last": "Smith", "suffix": "", "nickname": "Chris"},
    "terms": [{"start": "1981-01-05", "end": "2023-01-03"}],
}
NEAL_SMITH = {
    "id": {"bioguide": "S000596"},
    "name": {"first": "Neal", "middle": "", "last": "Smith", "suffix": "", "nickname": ""},
    "terms": [{"start": "1989-01-03", "end": "1995-01-03"}],
}


@pytest.fixture
def ambiguous_matcher(tmp_path):
    """A matcher seeded with two same-surname pairs so fuzzy search returns
    ambiguous results for initial-only and typo'd queries."""
    config = Config(
        data_dir=tmp_path,
        report_text_dir=tmp_path,
        output_dir=tmp_path,
        legislators_current_yaml=tmp_path / "missing-current.yaml",
        legislators_historical_yaml=tmp_path / "missing-historical.yaml",
    )
    matcher = NameMatcher(config=config)
    members_list = [DONALD_PAYNE, LEWIS_PAYNE, CHRISTOPHER_SMITH, NEAL_SMITH]
    matcher.charset = matcher._get_charset(members_list)
    matcher.members_dict = matcher._generate_bioguide_dict(members_list)
    matcher.members_index = {}
    matcher._append_data(members_list)
    matcher._initialized = True
    return matcher


class TestAmbiguousDisambiguationByName:
    """When fuzzy matching returns an ambiguous result (two close candidates),
    a first-name + surname tiebreaker picks the candidate whose first name
    matches the source's first-name token AND whose surname matches. This
    recovers real members that the ambiguity threshold otherwise leaves
    inconclusive, without promoting staffers who share a surname."""

    def test_initial_disambiguates_same_surname(self, ambiguous_matcher):
        """'Hon. D. Payne' is ambiguous between Donald and Lewis Payne; 'D.'
        matches Donald only -> MEMBER_DISAMBIGUATED_BY_NAME."""
        bioguide, _, flags = _match_member(
            "Hon. D. Payne",
            [dated_segment(date(1995, 8, 28), date(1995, 8, 30))],
            {}, ambiguous_matcher,
        )
        assert bioguide == "P000149"
        assert "MEMBER_DISAMBIGUATED_BY_NAME" in flags

    def test_initial_disambiguates_same_surname_smith(self, ambiguous_matcher):
        """'Hon. C. Smith' is ambiguous between Christopher and Neal Smith;
        'C.' matches Christopher only -> MEMBER_DISAMBIGUATED_BY_NAME."""
        bioguide, _, flags = _match_member(
            "Hon. C. Smith",
            [dated_segment(date(1990, 7, 22), date(1990, 7, 24))],
            {}, ambiguous_matcher,
        )
        assert bioguide == "S000522"
        assert "MEMBER_DISAMBIGUATED_BY_NAME" in flags

    def test_neither_first_name_matches_stays_inconclusive(self, ambiguous_matcher):
        """A staffer 'Hon. Pat Q. Payne' where 'Pat' matches neither Donald
        nor Lewis -> stays inconclusive (the surname matches both, but the
        first name doesn't disambiguate)."""
        bioguide, _, flags = _match_member(
            "Hon. Pat Q. Payne",
            [dated_segment(date(1995, 8, 28), date(1995, 8, 30))],
            {}, ambiguous_matcher,
        )
        assert bioguide is None
        assert "MEMBER_MATCH_INCONCLUSIVE" in flags
        assert "MEMBER_DISAMBIGUATED_BY_NAME" not in flags

    def test_first_name_ratio_gate_blocks_short_name_typo(self, ambiguous_matcher):
        """'Hon. Jon Smith' (Jon vs Neal is 2 edits on 3 chars = 0.67, vs
        Christopher is far) -- the 1-edit rule is gated by ratio so 'Jon'
        shouldn't match 'Neal' on a 3-letter name. Neither matches -> stays
        inconclusive."""
        bioguide, _, flags = _match_member(
            "Hon. Jon Smith",
            [dated_segment(date(1990, 7, 22), date(1990, 7, 24))],
            {}, ambiguous_matcher,
        )
        assert bioguide is None
        assert "MEMBER_DISAMBIGUATED_BY_NAME" not in flags

    def test_parenthetical_annotation_stripped_before_tiebreak(self, ambiguous_matcher):
        """'Hon. D. Payne (Codel)' -- the parenthetical is stripped, so the
        surname is still 'Payne' and the tiebreaker works."""
        bioguide, _, flags = _match_member(
            "Hon. D. Payne (Codel)",
            [dated_segment(date(1995, 8, 28), date(1995, 8, 30))],
            {}, ambiguous_matcher,
        )
        assert bioguide == "P000149"
        assert "MEMBER_DISAMBIGUATED_BY_NAME" in flags

    def test_hyphenated_surname_compound_match(self, tmp_path):
        """'Hon. Helen Chenoweth' matches 'Chenoweth-Hage' via the hyphenated
        compound rule."""
        chenoweth = {
            "id": {"bioguide": "C000345"},
            "name": {"first": "Helen", "middle": "", "last": "Chenoweth-Hage",
                      "suffix": "", "nickname": ""},
            "terms": [{"start": "1995-01-04", "end": "2001-01-03"}],
        }
        horn = {
            "id": {"bioguide": "H000789"},
            "name": {"first": "Stephen", "middle": "", "last": "Horn",
                      "suffix": "", "nickname": ""},
            "terms": [{"start": "1993-01-05", "end": "2003-01-03"}],
        }
        config = Config(
            data_dir=tmp_path, report_text_dir=tmp_path, output_dir=tmp_path,
            legislators_current_yaml=tmp_path / "missing.yaml",
            legislators_historical_yaml=tmp_path / "missing.yaml",
        )
        matcher = NameMatcher(config=config)
        members_list = [chenoweth, horn]
        matcher.charset = matcher._get_charset(members_list)
        matcher.members_dict = matcher._generate_bioguide_dict(members_list)
        matcher.members_index = {}
        matcher._append_data(members_list)
        matcher._initialized = True
        bioguide, _, flags = _match_member(
            "Hon. Helen Chenoweth",
            [dated_segment(date(1997, 5, 13), date(1997, 5, 15))],
            {}, matcher,
        )
        assert bioguide == "C000345"
        assert "MEMBER_DISAMBIGUATED_BY_NAME" in flags


# Member with a married compound surname (maiden + married) for the
# maiden-name-prefix recovery tests. Source predates the marriage, so the
# source surname is the maiden prefix.
HERSETH_SANDLIN = {
    "id": {"bioguide": "H001037"},
    "name": {"first": "Stephanie", "middle": "", "last": "Herseth Sandlin",
              "suffix": "", "nickname": ""},
    "terms": [{"start": "2004-06-01", "end": "2011-01-03"}],
}
# A distractor with a different surname -- ensures the maiden path's top
# match is the right one, not a same-initial surname that scored similarly.
HONDA = {
    "id": {"bioguide": "H001034"},
    "name": {"first": "Michael", "middle": "", "last": "Honda",
              "suffix": "", "nickname": ""},
    "terms": [{"start": "2001-01-03", "end": "2013-01-03"}],
}
# Same-surname-as-prefix distractor: "Smith" is a prefix of "Smithers" but
# there's no separator at the boundary -- this is a different name, not a
# marriage extension, and the maiden gate must reject it.
SMITHERS = {
    "id": {"bioguide": "S000999"},
    "name": {"first": "Bob", "middle": "", "last": "Smithers",
              "suffix": "", "nickname": ""},
    "terms": [{"start": "2001-01-03", "end": "2019-01-03"}],
}


@pytest.fixture
def maiden_matcher(tmp_path):
    """A matcher seeded with a married-name member (Herseth Sandlin) plus
    distractors, so the fuzzy matcher scores the maiden-name query below
    `min_match_score` but the top result is unambiguously the right person."""
    config = Config(
        data_dir=tmp_path,
        report_text_dir=tmp_path,
        output_dir=tmp_path,
        legislators_current_yaml=tmp_path / "missing-current.yaml",
        legislators_historical_yaml=tmp_path / "missing-historical.yaml",
    )
    matcher = NameMatcher(config=config)
    members_list = [HERSETH_SANDLIN, HONDA]
    matcher.charset = matcher._get_charset(members_list)
    matcher.members_dict = matcher._generate_bioguide_dict(members_list)
    matcher.members_index = {}
    matcher._append_data(members_list)
    matcher._initialized = True
    return matcher


@pytest.fixture
def maiden_prefix_impostor_matcher(tmp_path):
    """A matcher seeded with Smithers (surname extends Smith with no
    separator) so the maiden-prefix gate's separator requirement can be
    exercised -- "Hon. Bob Smith" must NOT match "Bob Smithers"."""
    config = Config(
        data_dir=tmp_path,
        report_text_dir=tmp_path,
        output_dir=tmp_path,
        legislators_current_yaml=tmp_path / "missing-current.yaml",
        legislators_historical_yaml=tmp_path / "missing-historical.yaml",
    )
    matcher = NameMatcher(config=config)
    members_list = [SMITHERS]
    matcher.charset = matcher._get_charset(members_list)
    matcher.members_dict = matcher._generate_bioguide_dict(members_list)
    matcher.members_index = {}
    matcher._append_data(members_list)
    matcher._initialized = True
    return matcher


class TestMaidenNamePrefixMatch:
    """The maiden-name-prefix recovery: a source "Hon. Stephanie Herseth"
    (maiden surname) matches a member "Stephanie Herseth Sandlin" (married
    compound surname) when the fuzzy matcher scored the partial-name query
    below its confidence threshold but the top result is the right person
    under a strict maiden-name gate.

    Recovers real members whose source report predates their marriage,
    without promoting same-surname staffers (the strict-prefix requirement
    blocks equal surnames) or same-prefix non-marriage names (the separator
    requirement blocks "Smith" prefix of "Smithers")."""

    def test_maiden_prefix_matches_via_strict_prefix_gate(self, maiden_matcher):
        """'Hon. Stephanie Herseth' (maiden) -> 'Stephanie Herseth Sandlin'
        (married) with first name matching exactly and 'Herseth' a strict
        prefix of 'Herseth Sandlin' with a space separator."""
        from official_foreign_travel.parsing.header import Period

        period = Period(start=date(2005, 1, 1), end=date(2005, 3, 31), year=2005, quarter=1, raw="")
        bioguide, _, flags = _match_member(
            "Hon. Stephanie Herseth",
            [dated_segment(date(2005, 2, 18), date(2005, 2, 22))],
            {}, maiden_matcher,
            period=period,
        )
        assert bioguide == "H001037"
        assert "MEMBER_MATCHED_BY_MAIDEN_NAME" in flags

    def test_maiden_match_rejected_when_member_not_yet_serving(self, maiden_matcher):
        """A staffer 'Hon. Stephanie Herseth' traveling in 1990 (before
        Herseth Sandlin's 2004 term started) must NOT match -- the date
        gate rejects."""
        from official_foreign_travel.parsing.header import Period

        period = Period(start=date(1990, 1, 1), end=date(1990, 3, 31), year=1990, quarter=1, raw="")
        bioguide, _, flags = _match_member(
            "Hon. Stephanie Herseth",
            [dated_segment(date(1990, 2, 18), date(1990, 2, 22))],
            {}, maiden_matcher,
            period=period,
        )
        assert bioguide is None
        assert "MEMBER_MATCHED_BY_MAIDEN_NAME" not in flags
        assert "MEMBER_UNMATCHED" in flags

    def test_first_name_mismatch_stays_unmatched(self, maiden_matcher):
        """'Hon. Steve Herseth' (first name doesn't exactly match
        'Stephanie') -- the strict first-name-equality gate rejects it.
        A first-name slack here would let staffers whose first name
        resembles a member's ride this path."""
        from official_foreign_travel.parsing.header import Period

        period = Period(start=date(2005, 1, 1), end=date(2005, 3, 31), year=2005, quarter=1, raw="")
        bioguide, _, flags = _match_member(
            "Hon. Steve Herseth",
            [dated_segment(date(2005, 2, 18), date(2005, 2, 22))],
            {}, maiden_matcher,
            period=period,
        )
        assert bioguide is None
        assert "MEMBER_MATCHED_BY_MAIDEN_NAME" not in flags

    def test_same_surname_stays_unmatched(self, maiden_matcher):
        """'Hon. Stephanie Herseth Sandlin' (full married name) doesn't
        trigger the maiden path -- the source surname equals the member
        surname, not a strict prefix. Either the exact lookup or the fuzzy
        matcher handles it; the maiden path declines."""
        from official_foreign_travel.parsing.header import Period

        period = Period(start=date(2005, 1, 1), end=date(2005, 3, 31), year=2005, quarter=1, raw="")
        # The maiden path's strict-prefix requirement (len(mem_last) >
        # len(q_last)) blocks this; whatever else happens, the maiden flag
        # must not be set.
        bioguide, _, flags = _match_member(
            "Hon. Stephanie Herseth Sandlin",
            [dated_segment(date(2005, 2, 18), date(2005, 2, 22))],
            {}, maiden_matcher,
            period=period,
        )
        assert "MEMBER_MATCHED_BY_MAIDEN_NAME" not in flags

    def test_prefix_without_separator_stays_unmatched(self, maiden_prefix_impostor_matcher):
        """'Hon. Bob Smith' must NOT match 'Bob Smithers' -- 'Smith' is a
        prefix of 'Smithers' but there's no separator at the boundary, so
        it's a different name, not a marriage extension. The separator
        requirement blocks the false recovery."""
        from official_foreign_travel.parsing.header import Period

        period = Period(start=date(2010, 1, 1), end=date(2010, 3, 31), year=2010, quarter=1, raw="")
        bioguide, _, flags = _match_member(
            "Hon. Bob Smith",
            [dated_segment(date(2010, 2, 18), date(2010, 2, 22))],
            {}, maiden_prefix_impostor_matcher,
            period=period,
        )
        assert "MEMBER_MATCHED_BY_MAIDEN_NAME" not in flags

    def test_hyphenated_married_surname_matches(self, tmp_path):
        """'Hon. Helen Chenoweth' (maiden) -> 'Helen Chenoweth-Hage'
        (hyphenated married) -- the separator-at-boundary check accepts
        hyphen as well as space, so the maiden path recovers this shape
        when the fuzzy matcher happens to score it below threshold.

        Note: when the fuzzy matcher returns is_inconclusive (the live
        case for this name in 1997), the existing _disambiguate_ambiguous_match
        path handles it via the compound-surname rule in _surname_match.
        This test exercises the maiden path directly by seeding only
        Chenoweth-Hage so the top match is unambiguous below threshold."""
        chenoweth_hage = {
            "id": {"bioguide": "C000345"},
            "name": {"first": "Helen", "middle": "", "last": "Chenoweth-Hage",
                      "suffix": "", "nickname": ""},
            "terms": [{"start": "1995-01-04", "end": "2001-01-03"}],
        }
        config = Config(
            data_dir=tmp_path, report_text_dir=tmp_path, output_dir=tmp_path,
            legislators_current_yaml=tmp_path / "missing.yaml",
            legislators_historical_yaml=tmp_path / "missing.yaml",
        )
        matcher = NameMatcher(config=config)
        members_list = [chenoweth_hage]
        matcher.charset = matcher._get_charset(members_list)
        matcher.members_dict = matcher._generate_bioguide_dict(members_list)
        matcher.members_index = {}
        matcher._append_data(members_list)
        matcher._initialized = True
        from official_foreign_travel.parsing.header import Period

        period = Period(start=date(1997, 1, 1), end=date(1997, 3, 31), year=1997, quarter=1, raw="")
        bioguide, _, flags = _match_member(
            "Hon. Helen Chenoweth",
            [dated_segment(date(1997, 5, 13), date(1997, 5, 15))],
            {}, matcher,
            period=period,
        )
        assert bioguide == "C000345"
        # The maiden path is one valid recovery route for this shape; the
        # exact-match and inconclusive-disambiguation routes are others.
        # Whichever route fires, the bioguide must be right.
        assert "MEMBER_UNMATCHED" not in flags

    def test_no_period_stays_unmatched(self, maiden_matcher):
        """Without a period, the date-of-service gate can't run, so the
        maiden path declines (and the fuzzy matcher's below-threshold
        result falls through to MEMBER_UNMATCHED)."""
        bioguide, _, flags = _match_member(
            "Hon. Stephanie Herseth",
            [dated_segment(date(2005, 2, 18), date(2005, 2, 22))],
            {}, maiden_matcher,
            period=None,
        )
        assert bioguide is None
        assert "MEMBER_MATCHED_BY_MAIDEN_NAME" not in flags
        assert "MEMBER_UNMATCHED" in flags


class TestCommitteeDisambiguation:
    """Committee-based disambiguation (`MEMBER_DISAMBIGUATED_BY_COMMITTEE`):
    when two members share a name simultaneously (Mike Rogers of Michigan
    R000572 and Mike Rogers of Alabama R000575, both serving 2003-2015),
    neither exact matching nor date-aware fuzzy matching can choose. The
    report's sponsoring committee still separates them -- the hand-curated
    `member_disambiguation.csv` indexes (name, sponsor_code) -> bioguide,
    and `_match_member` consults that index before falling through to fuzzy.

    These tests exercise the disambiguation-index path directly with a
    synthetic index, so they don't depend on the live CSV."""

    def test_committee_disambiguation_resolves_same_name_pair(self, seeded_matcher):
        """'Hon. Mike Rogers' (no parenthetical) + sponsor_code=HSHM ->
        disambiguation_index[('HON. MIKE ROGERS', 'HSHM')] = R000575."""
        bioguide, _, flags = _match_member(
            "Hon. Mike Rogers",
            [dated_segment(date(2011, 2, 1), date(2011, 2, 5))],
            {}, seeded_matcher,
            sponsor_code="HSHM",
            disambiguation_index={("HON. MIKE ROGERS", "HSHM"): "R000575"},
        )
        assert bioguide == "R000575"
        assert "MEMBER_DISAMBIGUATED_BY_COMMITTEE" in flags

    def test_parenthetical_state_tag_stripped_before_committee_disambiguation(self, seeded_matcher):
        """'Hon. Mike Rogers (AL)' -- the parenthetical state tag is
        stripped before the disambiguation-index lookup, so the key
        'HON. MIKE ROGERS' matches the index entry. Without the stripping,
        the key would be 'HON. MIKE ROGERS (AL)' and the lookup would miss;
        the traveler would fall through to the fuzzy matcher's
        is_inconclusive path and stay MEMBER_MATCH_INCONCLUSIVE.

        This is the live 2011q2may23 case: sponsor=HOMELAND SECURITY with
        code HSHM, traveler name 'Hon. Mike Rogers (AL)'."""
        bioguide, _, flags = _match_member(
            "Hon. Mike Rogers (AL)",
            [dated_segment(date(2011, 2, 1), date(2011, 2, 5))],
            {}, seeded_matcher,
            sponsor_code="HSHM",
            disambiguation_index={("HON. MIKE ROGERS", "HSHM"): "R000575"},
        )
        assert bioguide == "R000575"
        assert "MEMBER_DISAMBIGUATED_BY_COMMITTEE" in flags

    def test_parenthetical_codel_annotation_stripped_before_committee_disambiguation(self, seeded_matcher):
        """'Hon. Mike Rogers (Codel)' -- same shape, different annotation.
        The parenthetical is stripped, the lookup matches, the right
        bioguide is returned."""
        bioguide, _, flags = _match_member(
            "Hon. Mike Rogers (Codel)",
            [dated_segment(date(2011, 2, 1), date(2011, 2, 5))],
            {}, seeded_matcher,
            sponsor_code="HSHM",
            disambiguation_index={("HON. MIKE ROGERS", "HSHM"): "R000575"},
        )
        assert bioguide == "R000575"
        assert "MEMBER_DISAMBIGUATED_BY_COMMITTEE" in flags

    def test_committee_disambiguation_picks_other_rogers_for_intel_committee(self, seeded_matcher):
        """Same name 'Hon. Mike Rogers', different committee (HLIG =
        Intelligence) -> disambiguation_index[('HON. MIKE ROGERS', 'HLIG')]
        = R000572 (the Michigan Rogers, who served on Intel). The
        disambiguation_index separates the two same-name members by
        committee assignment."""
        bioguide, _, flags = _match_member(
            "Hon. Mike Rogers",
            [dated_segment(date(2005, 2, 1), date(2005, 2, 5))],
            {}, seeded_matcher,
            sponsor_code="HLIG",
            disambiguation_index={
                ("HON. MIKE ROGERS", "HLIG"): "R000572",
                ("HON. MIKE ROGERS", "HSHM"): "R000575",
            },
        )
        assert bioguide == "R000572"
        assert "MEMBER_DISAMBIGUATED_BY_COMMITTEE" in flags

    def test_no_sponsor_code_falls_through_to_fuzzy(self, seeded_matcher):
        """Without a sponsor_code, the disambiguation index can't be
        consulted -- the lookup key requires (name, sponsor_code). The
        traveler falls through to the fuzzy matcher (and, if that's
        inconclusive too, ends up MEMBER_MATCH_INCONCLUSIVE)."""
        bioguide, _, flags = _match_member(
            "Hon. Mike Rogers",
            [dated_segment(date(2011, 2, 1), date(2011, 2, 5))],
            {}, seeded_matcher,
            sponsor_code=None,
            disambiguation_index={("HON. MIKE ROGERS", "HSHM"): "R000575"},
        )
        assert "MEMBER_DISAMBIGUATED_BY_COMMITTEE" not in flags
