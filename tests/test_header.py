"""Tests for sponsor/period extraction from table titles."""

from datetime import date

from official_foreign_travel.parsing.header import classify_sponsor, parse_header, parse_period


class TestParsePeriod:
    def test_standard_period(self):
        period, flags = parse_period("EXPENDED BETWEEN JULY 1 AND SEPT. 30, 2018")
        assert flags == []
        assert period.start == date(2018, 7, 1)
        assert period.end == date(2018, 9, 30)
        assert period.quarter == 3

    def test_period_crossing_calendar_year(self):
        period, flags = parse_period("EXPENDED BETWEEN DEC. 7 AND DEC. 22, 2009")
        assert period.start == date(2009, 12, 7)
        assert period.end == date(2009, 12, 22)

    def test_period_without_expended_prefix(self):
        period, flags = parse_period("BETWEEN APR. 1 AND JUNE 30, 2012")
        assert period.start == date(2012, 4, 1)
        assert period.end == date(2012, 6, 30)

    def test_missing_start_day_defaults_to_first(self):
        period, flags = parse_period("EXPENDED BETWEEN JAN. AND MAR. 31, 2003")
        assert "PERIOD_START_DAY_ASSUMED" in flags
        assert period.start == date(2003, 1, 1)
        assert period.end == date(2003, 3, 31)

    def test_btween_typo_still_parses(self):
        period, flags = parse_period("EXPENDED BTWEEN DEC. 4 AND DEC. 11, 1993")
        assert period.start == date(1993, 12, 4)
        assert period.end == date(1993, 12, 11)

    def test_period_with_year_after_start_day(self):
        period, flags = parse_period("EXPENDED BETWEEN SEPT. 1, 2006 AND DEC. 31, 2006")
        assert period.start == date(2006, 9, 1)
        assert period.end == date(2006, 12, 31)

    def test_comma_before_and_no_year(self):
        period, flags = parse_period("EXPENDED BETWEEN JULY 1, AND SEPT. 30, 2008.")
        assert period.start == date(2008, 7, 1)
        assert period.end == date(2008, 9, 30)

    def test_genuinely_truncated_period_is_flagged_not_guessed(self):
        period, flags = parse_period("COMMITTEE ON AGRICULTURE, EXPENDED BETWEEN OCT. 1")
        assert period is None
        assert flags == ["PERIOD_UNPARSEABLE"]

    def test_invalid_calendar_date_is_flagged_not_silently_corrected(self):
        period, flags = parse_period("EXPENDED BETWEEN JULY 1 AND SEPT. 31, 1998")
        assert "PERIOD_END_DATE_INVALID" in flags
        assert period.end is None
        assert period.start == date(1998, 7, 1)


class TestClassifySponsor:
    def test_committee(self):
        assert classify_sponsor("COMMITTEE ON ARMED SERVICES") == ("committee", [])

    def test_permanent_select_committee(self):
        assert classify_sponsor("PERMANENT SELECT COMMITTEE ON INTELLIGENCE")[0] == "committee"

    def test_delegation_anywhere_in_text(self):
        assert classify_sponsor("HOUSE DELEGATION TO ARGENTINA")[0] == "delegation"

    def test_commission(self):
        assert (
            classify_sponsor("COMMISSION ON SECURITY AND COOPERATION IN EUROPE")[0] == "commission"
        )

    def test_interparliamentary_group(self):
        assert classify_sponsor("NORTH ATLANTIC ASSEMBLY")[0] == "interparliamentary"
        assert classify_sponsor("BRITISH-AMERICAN PARLIAMENTARY GROUP")[0] == "interparliamentary"

    def test_individual_with_honorific(self):
        assert classify_sponsor("MR. BRETT W. O'BRIEN")[0] == "individual"
        assert classify_sponsor("HON. FRANK R. WOLF")[0] == "individual"

    def test_unclassified_bare_name_flagged_not_guessed(self):
        sponsor_type, flags = classify_sponsor("HUGH HALPERN")
        assert sponsor_type == "other"
        assert flags == ["SPONSOR_UNCLASSIFIED"]


class TestParseHeader:
    def test_full_committee_header(self):
        info = parse_header(
            "REPORT OF EXPENDITURES FOR OFFICIAL FOREIGN TRAVEL, COMMITTEE ON ARMED "
            "SERVICES, HOUSE OF REPRESENTATIVES, EXPENDED BETWEEN JULY 1 AND SEPT. 30, 2018"
        )
        assert info.amended is False
        assert info.sponsor.type == "committee"
        assert info.sponsor.name == "COMMITTEE ON ARMED SERVICES"
        assert info.period.start == date(2018, 7, 1)
        assert info.period.end == date(2018, 9, 30)
        assert info.flags == []

    def test_amended_word_prefix(self):
        info = parse_header(
            "AMENDED REPORT OF EXPENDITURES FOR OFFICIAL FOREIGN TRAVEL, COMMITTEE ON "
            "NATIONAL SECURITY, HOUSE OF REPRESENTATIVES, EXPENDED BETWEEN JULY 1 AND SEPT. 30, 1995"
        )
        assert info.amended is True

    def test_amended_parenthesized_prefix(self):
        info = parse_header(
            "(AMENDED) REPORT OF EXPENDITURES FOR OFFICIAL FOREIGN TRAVEL, HOUSE COMMITTEE "
            "ON FOREIGN AFFAIRS, EXPENDED BETWEEN APR. 1 AND JUNE 30, 2009"
        )
        assert info.amended is True
        assert info.sponsor.type == "committee"

    def test_official_travel_without_foreign(self):
        """Some titles omit 'FOREIGN': 'OFFICIAL TRAVEL' instead of 'OFFICIAL FOREIGN TRAVEL'."""
        info = parse_header(
            "REPORT OF EXPENDITURES FOR OFFICIAL TRAVEL, DELEGATION TO GERMANY, "
            "EXPENDED BETWEEN JULY 31 AND AUG. 13, 2009"
        )
        assert info.sponsor.type == "delegation"
        assert info.period.start == date(2009, 7, 31)

    def test_delegation_sponsor_keeps_internal_commas(self):
        info = parse_header(
            "REPORT OF EXPENDITURES FOR OFFICIAL FOREIGN TRAVEL, DELEGATION TO MALTA, LIBYA, "
            "EGYPT, KOSOVO, AND MACEDONIA, HOUSE OF REPRESENTATIVES, EXPENDED BETWEEN MAR. 30 "
            "AND APR. 6, 2012"
        )
        assert info.sponsor.type == "delegation"
        assert info.sponsor.name == "DELEGATION TO MALTA, LIBYA, EGYPT, KOSOVO, AND MACEDONIA"

    def test_individual_sponsor(self):
        info = parse_header(
            "REPORT OF EXPENDITURES FOR OFFICIAL FOREIGN TRAVEL, MR. BRETT W. O'BRIEN, "
            "HOUSE OF REPRESENTATIVES, EXPENDED BETWEEN JULY 16 AND JULY 19, 1994"
        )
        assert info.sponsor.type == "individual"
        assert info.sponsor.name == "MR. BRETT W. O'BRIEN"

    def test_prior_year_table_uses_header_year_not_filename(self):
        """A table inside a 2012 file can legitimately cover a 2011 quarter."""
        info = parse_header(
            "REPORT OF EXPENDITURES FOR OFFICIAL FOREIGN TRAVEL, COMMITTEE ON RULES, "
            "HOUSE OF REPRESENTATIVES, EXPENDED BETWEEN APR. 1 AND JUNE 30, 2011"
        )
        assert info.period.start == date(2011, 4, 1)
        assert info.period.end == date(2011, 6, 30)
