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

    def test_invalid_calendar_date_is_clamped_not_dropped(self):
        """Source typo "SEPT. 31" (Sep has 30 days) is clamped to Sep 30 and
        flagged, not dropped -- dropping the end date loses year inference for
        every segment in the table, and the period is still useful."""
        period, flags = parse_period("EXPENDED BETWEEN JULY 1 AND SEPT. 31, 1998")
        assert "PERIOD_END_DAY_CLAMPED" in flags
        assert period.end == date(1998, 9, 30)
        assert period.start == date(1998, 7, 1)

    def test_invalid_end_day_june_31_clamped(self):
        """Source typo "JUNE 31" (June has 30 days) is clamped to June 30."""
        period, flags = parse_period("EXPENDED BETWEEN APR. 1 AND JUNE 31, 2002")
        assert "PERIOD_END_DAY_CLAMPED" in flags
        assert period.end == date(2002, 6, 30)
        assert period.start == date(2002, 4, 1)

    def test_psept_typo_parses_as_september(self):
        """Source typo "PSEPT." (leading P) parses as September."""
        period, flags = parse_period("EXPENDED BETWEEN JULY 1 AND PSEPT. 30, 2019")
        assert flags == []
        assert period.start == date(2019, 7, 1)
        assert period.end == date(2019, 9, 30)
        assert period.quarter == 3

    def test_md_numeric_period(self):
        """Speaker report uses 'BETWEEN 7/1 AND 9/30, 2009' (numeric M/D)."""
        period, flags = parse_period("EXPENDED BETWEEN 7/1 AND 9/30, 2009")
        assert period.start == date(2009, 7, 1)
        assert period.end == date(2009, 9, 30)
        assert period.quarter == 3
        assert flags == []

    def test_md_numeric_period_invalid_month_is_unparseable(self):
        period, flags = parse_period("EXPENDED BETWEEN 13/1 AND 9/30, 2009")
        assert period is None
        assert flags == ["PERIOD_UNPARSEABLE"]

    def test_missing_end_month_infers_from_start(self):
        """'BETWEEN FEB. 3 AND 6, 2000' (no end month) — end_mon = start_mon."""
        period, flags = parse_period("EXPENDED BETWEEN FEB. 3 AND 6, 2000")
        assert period.start == date(2000, 2, 3)
        assert period.end == date(2000, 2, 6)
        assert flags == []

    def test_dash_separated_day_range(self):
        """'BETWEEN FEB. 21-26, 2002' — dash-separated day range, same month."""
        period, flags = parse_period("EXPENDED BETWEEN FEB. 21-26, 2002")
        assert period.start == date(2002, 2, 21)
        assert period.end == date(2002, 2, 26)
        assert flags == []

    def test_dash_separated_day_range_nov(self):
        period, flags = parse_period("EXPENDED BETWEEN NOV. 16-18, 2001")
        assert period.start == date(2001, 11, 16)
        assert period.end == date(2001, 11, 18)

    def test_tail_fallback_handles_duplicate_and_clauses(self):
        """'BETWEEN ARMED SERVICES AND JAN. 1 AND MAR. 31, 2008' — a
        'COMMITTEE ON,' typo let 'ARMED SERVICES' leak into the BETWEEN
        clause. The tail fallback takes the LAST 'AND <mon> <day>, <year>'
        as the end and the preceding '<mon> <day>' as the start."""
        period, flags = parse_period(
            "EXPENDED BETWEEN ARMED SERVICES AND JAN. 1 AND MAR. 31, 2008"
        )
        assert period.start == date(2008, 1, 1)
        assert period.end == date(2008, 3, 31)
        assert period.quarter == 1
        assert flags == []

    def test_tail_fallback_handles_leading_duplicate_and(self):
        """'BETWEEN AND MAR. 17 AND MAR. 26, 2008' — a leading duplicate AND."""
        period, flags = parse_period(
            "EXPENDED BETWEEN AND MAR. 17 AND MAR. 26, 2008"
        )
        assert period.start == date(2008, 3, 17)
        assert period.end == date(2008, 3, 26)

    def test_tail_fallback_infers_start_month_when_truncated(self):
        """'REPRE 14 AND FEB. 22, 1998' — 'REPRESENTATIVES, EXPENDED BETWEEN
        JAN.' was truncated to 'REPRE', losing the real start month. The
        start_day survives; infer start_mon = end_mon and flag it."""
        period, flags = parse_period(
            "HOUSE OF REPRE 14 AND FEB. 22, 1998"
        )
        assert "PERIOD_START_MONTH_INFERRED" in flags
        assert period.start == date(1998, 2, 14)
        assert period.end == date(1998, 2, 22)

    def test_betweenp_typo_still_parses(self):
        period, flags = parse_period("EXPENDED BETWEENP JULY 1 AND SEPT. 30, 2014")
        assert period.start == date(2014, 7, 1)
        assert period.end == date(2014, 9, 30)
        assert flags == []

    def test_betweeeen_typo_still_parses(self):
        period, flags = parse_period("EXPENDED BETWEEEN MAR. 22 AND MAR. 26, 2001")
        assert period.start == date(2001, 3, 22)
        assert period.end == date(2001, 3, 26)

    def test_expended_on_single_date(self):
        period, flags = parse_period("EXPENDED ON NOV. 5, 2001")
        assert period.start == date(2001, 11, 5)
        assert period.end == date(2001, 11, 5)
        assert period.year == 2001
        assert flags == []

    def test_missing_between_word_still_parses(self):
        period, flags = parse_period("EXPENDED FEB. 17 AND FEB. 25, 2007")
        assert period.start == date(2007, 2, 17)
        assert period.end == date(2007, 2, 25)

    def test_comma_after_end_month_parses(self):
        """Source typo 'DEC, 31' (comma instead of period) should not block parsing."""
        period, flags = parse_period("EXPENDED BETWEEN OCT. 1 AND DEC, 31, 2007")
        assert period.start == date(2007, 10, 1)
        assert period.end == date(2007, 12, 31)

    def test_partial_between_only_with_start_year_infers_quarter_end(self):
        """'EXPENDED BETWEEN OCT. 1 1997 AN' (truncated before AND) infers
        end = Dec 31 1997 from the quarter, using the explicit start year."""
        period, flags = parse_period(
            "EXPENDED BETWEEN OCT. 1 1997 AN", source_file="1998q1feb24.txt"
        )
        assert period.start == date(1997, 10, 1)
        assert period.end == date(1997, 12, 31)
        assert period.quarter == 4
        assert "PERIOD_END_INFERRED" in flags
        assert "PERIOD_YEAR_INFERRED_FROM_FILENAME" not in flags

    def test_partial_between_only_infers_year_from_filename(self):
        """'EXPENDED BETWEEN OCT. 1' with no year at all infers the period
        year from the filing year/quarter in the source filename (Q4 period
        in a Q1 filing → prior year)."""
        period, flags = parse_period(
            "COMMITTEE ON AGRICULTURE, EXPENDED BETWEEN OCT. 1",
            source_file="1998q1feb24.txt",
        )
        assert period.start == date(1997, 10, 1)
        assert period.end == date(1997, 12, 31)
        assert "PERIOD_END_INFERRED" in flags
        assert "PERIOD_YEAR_INFERRED_FROM_FILENAME" in flags

    def test_partial_between_and_no_year_infers_from_filename(self):
        """'EXPENDED BETWEEN JULY 1 AND SEP' (truncated before year) infers
        end day = 30 (quarter end) and year from the filename."""
        period, flags = parse_period(
            "EXPENDED BETWEEN JULY 1 AND SEP", source_file="1998q1mar11.txt"
        )
        assert period.start == date(1997, 7, 1)
        assert period.end == date(1997, 9, 30)
        assert "PERIOD_END_INFERRED" in flags
        assert "PERIOD_YEAR_INFERRED_FROM_FILENAME" in flags

    def test_partial_between_and_end_year_only_uses_end_year(self):
        """'EXPENDED BETWEEN SEPT. AND DEC. 1997' has end_year but no days;
        start day defaults to 1, end day defaults to month-end."""
        period, flags = parse_period(
            "EXPENDED BETWEEN SEPT. AND DEC. 1997", source_file="1998q1feb24.txt"
        )
        assert period.start == date(1997, 9, 1)
        assert period.end == date(1997, 12, 31)
        assert "PERIOD_START_DAY_ASSUMED" in flags
        assert "PERIOD_END_INFERRED" in flags
        assert "PERIOD_YEAR_INFERRED_FROM_FILENAME" not in flags

    def test_partial_between_only_non_quarter_start_falls_back_to_filename(self):
        """'EXPENDED BETWEEN MAR. 5' — March isn't a quarter start, so the
        partial-match path can't infer the quarter end. Now falls through to
        filename-based inference: filing Q2 1998 -> period Q1 1998
        (Jan 1 - Mar 31, 1998). Tagged PERIOD_INFERRED_FROM_FILENAME so the
        recovery is auditable. (Previously this case stayed unparseable --
        the filename fallback was added to recover the 11 truncated-title
        reports in the corpus.)"""
        period, flags = parse_period(
            "EXPENDED BETWEEN MAR. 5", source_file="1998q2may05.txt"
        )
        assert period is not None
        assert period.start == date(1998, 1, 1)
        assert period.end == date(1998, 3, 31)
        assert period.year == 1998
        assert period.quarter == 1
        assert "PERIOD_INFERRED_FROM_FILENAME" in flags

    def test_partial_with_no_source_file_stays_unparseable_when_year_needed(self):
        """'EXPENDED BETWEEN OCT. 1' with no source file can't infer the
        year from the filename — stay unparseable, don't guess."""
        period, flags = parse_period("EXPENDED BETWEEN OCT. 1")
        assert period is None
        assert flags == ["PERIOD_UNPARSEABLE"]

    def test_partial_year_inference_q1_filing_prior_year(self):
        """A Q4 period (start month OCT) filed in Q1 (Feb 1998) belongs to
        the prior year (1997)."""
        period, flags = parse_period(
            "EXPENDED BETWEEN OCT. 1", source_file="1998q1feb24.txt"
        )
        assert period.year == 1997

    def test_partial_year_inference_q2_filing_same_year(self):
        """A Q1 period (start month JAN) filed in Q2 (May 1998) belongs to
        the same year (1998)."""
        period, flags = parse_period(
            "EXPENDED BETWEEN JAN. 1", source_file="1998q2may05.txt"
        )
        assert period.year == 1998
        assert period.end == date(1998, 3, 31)

    def test_partial_year_inference_non_standard_period_month(self):
        """'EXPENDED BETWEEN NOV. 19 AND NOV. 27' (no year) — Nov is Q4,
        filed in Q1 (Feb 2007) → prior year (2006)."""
        period, flags = parse_period(
            "EXPENDED BETWEEN NOV. 19 AND NOV. 27", source_file="2007q1feb16.txt"
        )
        assert period.start == date(2006, 11, 19)
        assert period.end == date(2006, 11, 27)
        assert "PERIOD_YEAR_INFERRED_FROM_FILENAME" in flags


class TestParsePeriodDuringQuarters:
    """Speaker / annual-summary wrappers: '... during the <quarter(s)> of
    <year> ...' -- no EXPENDED BETWEEN clause, no per-traveler rows. The
    period is the listed quarter(s) of the stated year."""

    def test_single_quarter(self):
        period, flags = parse_period(
            "Reports concerning foreign currencies and U.S. dollars utilized "
            "for Speaker-Authorized Official Travel during the first quarter of "
            "2008, pursuant to Public Law 95-384 are as follows:"
        )
        assert period is not None
        assert period.start == date(2008, 1, 1)
        assert period.end == date(2008, 3, 31)
        assert period.year == 2008
        assert period.quarter == 1
        assert flags == []

    def test_second_quarter(self):
        period, flags = parse_period("during the second quarter of 2009")
        assert period is not None
        assert period.start == date(2009, 4, 1)
        assert period.end == date(2009, 6, 30)
        assert period.quarter == 2

    def test_third_quarter(self):
        period, flags = parse_period("during the third quarter of 2009")
        assert period.start == date(2009, 7, 1)
        assert period.end == date(2009, 9, 30)
        assert period.quarter == 3

    def test_fourth_quarter(self):
        period, flags = parse_period("during the fourth quarter of 2009")
        assert period.start == date(2009, 10, 1)
        assert period.end == date(2009, 12, 31)
        assert period.quarter == 4

    def test_all_four_quarters_annual_summary(self):
        """'during the first, second, third, and fourth quarters of 2018' ->
        full year (Jan 1 - Dec 31, 2018). quarter=None (not a single quarter)."""
        period, flags = parse_period(
            "during the first, second, third, and fourth quarters of 2018"
        )
        assert period is not None
        assert period.start == date(2018, 1, 1)
        assert period.end == date(2018, 12, 31)
        assert period.year == 2018
        assert period.quarter is None
        assert flags == []


class TestParsePeriodFilenameFallback:
    """Last-resort inference: when no period clause survives in the title
    text, infer a quarter-wide period from the source filename. The House
    Clerk files reports the quarter AFTER the travel ended (with some
    same-quarter exceptions), so a report filed in Q2 typically covers Q1
    travel; filed Q1 typically covers Q4 of the prior year; etc."""

    def test_filing_q1_infers_q4_prior_year(self):
        """Filed Q1 (e.g. 1998q1mar11.txt) -> period Q4 of prior year
        (Oct 1 - Dec 31, 1997). The year-rollover logic in dates.py then
        handles segment dates that fall into Jan-Mar of the filing year."""
        period, flags = parse_period(
            "REPORT OF EXPENDITURES FOR OFFICIAL FOREIGN TRAVEL, TRAVEL TO "
            "ISRAEL, JORDAN, EGYPT, MOROCCO, AND IRELAND, HOUSE OF "
            "REPRESENTATIVES, EXPENDED BETWEEN",
            source_file="1998q1mar11.txt",
        )
        assert period is not None
        assert period.start == date(1997, 10, 1)
        assert period.end == date(1997, 12, 31)
        assert period.year == 1997
        assert period.quarter == 4
        assert "PERIOD_INFERRED_FROM_FILENAME" in flags

    def test_filing_q2_infers_q1_filing_year(self):
        period, flags = parse_period(
            "EXPENDED BETWEEN",
            source_file="1998q2may05.txt",
        )
        assert period is not None
        assert period.start == date(1998, 1, 1)
        assert period.end == date(1998, 3, 31)
        assert period.year == 1998
        assert period.quarter == 1
        assert "PERIOD_INFERRED_FROM_FILENAME" in flags

    def test_filing_q3_infers_q2_filing_year(self):
        period, flags = parse_period("EXPENDED", source_file="2009q3sep16.txt")
        assert period is not None
        assert period.start == date(2009, 4, 1)
        assert period.end == date(2009, 6, 30)
        assert period.quarter == 2

    def test_filing_q4_infers_q3_filing_year(self):
        period, flags = parse_period("EXPENDED", source_file="2007q4nov13.txt")
        assert period is not None
        assert period.start == date(2007, 7, 1)
        assert period.end == date(2007, 9, 30)
        assert period.quarter == 3

    def test_no_source_file_stays_unparseable(self):
        """Without a filename to infer from, we can't recover -- stay unparseable."""
        period, flags = parse_period("EXPENDED BETWEEN")
        assert period is None
        assert flags == ["PERIOD_UNPARSEABLE"]

    def test_non_house_filename_stays_unparseable(self):
        """A filename that doesn't match YYYYqQmmmdd.txt can't be inferred from."""
        period, flags = parse_period(
            "EXPENDED BETWEEN", source_file="some_other_format.txt"
        )
        assert period is None
        assert flags == ["PERIOD_UNPARSEABLE"]


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

    def test_interparliamentary_nato_pa(self):
        assert classify_sponsor("NATO PARLIAMENTARY ASSEMBLY TO THE NETHERLANDS")[0] == "interparliamentary"
        assert classify_sponsor("NATO PARLIAMENTARY ASSEMBLY GROUP")[0] == "interparliamentary"

    def test_interparliamentary_osce(self):
        assert classify_sponsor("OSCE PARLIAMENTARY ASSEMBLY")[0] == "interparliamentary"
        assert classify_sponsor("ORGANIZATION FOR SECURITY AND COOPERATION IN EUROPE PARLIAMENTARY ASSEMBLY")[0] == "interparliamentary"

    def test_interparliamentary_truncated_north_atlantic(self):
        """'NORTH ATLANTIC' (truncated 'NORTH ATLANTIC ASSEMBLY') still classifies."""
        assert classify_sponsor("NORTH ATLANTIC")[0] == "interparliamentary"

    def test_interparliamentary_transatlantic_legislators(self):
        assert classify_sponsor("TRANSATLANTIC LEGISLATORS' DIALOGUE")[0] == "interparliamentary"

    def test_joint_economic_committee(self):
        """'JOINT ECONOMIC COMMITTEE' -- committee without 'COMMITTEE ON'."""
        assert classify_sponsor("JOINT ECONOMIC COMMITTEE")[0] == "committee"

    def test_task_force(self):
        assert (
            classify_sponsor("TASK FORCE ON THE ATTEMPTED ASSASSINATION OF DONALD J. TRUMP")
            == ("committee", [])
        )

    def test_committee_onstandards_typo(self):
        """'COMMITTEE ONSTANDARDS' (missing space after ON) still classifies."""
        assert classify_sponsor("COMMITTEE ONSTANDARDS OF OFFICIAL CONDUCT")[0] == "committee"

    def test_delegation_travel_to(self):
        assert classify_sponsor("TRAVEL TO RUSSIA")[0] == "delegation"
        assert classify_sponsor("TRAVEL TO SOUTH KOREA AND NORTH KOREA")[0] == "delegation"
        assert classify_sponsor("TRAVEL TO ESTONIA, LATVIA, POLAND AND THE CZECH REPUBLIC")[0] == "delegation"

    def test_delegation_to_truncated(self):
        """'TO NICARAGUA' -- 'TRAVEL' was stripped with the leading boilerplate."""
        assert classify_sponsor("TO NICARAGUA")[0] == "delegation"
        assert classify_sponsor("TO BELGIUM AND ALBANIA")[0] == "delegation"

    def test_delegation_with_chamber_prefix(self):
        """'HOUSE OF REPRESENTATIVES, TRAVEL TO <place>' -- delegation with chamber prefix."""
        assert classify_sponsor("HOUSE OF REPRESENTATIVES, TRAVEL TO CANADA")[0] == "delegation"

    def test_speaker_consolidated_report(self):
        assert classify_sponsor("CONSOLIDATED SPEAKER'S REPORT")[0] == "speaker"

    def test_speaker_in_text(self):
        assert classify_sponsor("Speaker-Authorized Travel")[0] == "speaker"

    def test_individual_with_honorific(self):
        assert classify_sponsor("MR. BRETT W. O'BRIEN")[0] == "individual"
        assert classify_sponsor("HON. FRANK R. WOLF")[0] == "individual"

    def test_individual_honorable_full_word(self):
        assert classify_sponsor("HONORABLE WERNER W. BRANDT")[0] == "individual"

    def test_individual_reverend(self):
        assert classify_sponsor("REV. DANIEL P. COUGHLIN")[0] == "individual"

    def test_individual_father(self):
        assert classify_sponsor("FATHER DANIEL P. COUGHLIN")[0] == "individual"

    def test_individual_bare_name_two_words(self):
        assert classify_sponsor("DANIEL SILVERBERG")[0] == "individual"

    def test_individual_bare_name_with_initial(self):
        assert classify_sponsor("JENNIFER M. STEWART")[0] == "individual"

    def test_individual_bare_name_with_phd_suffix(self):
        assert classify_sponsor("KAY A. KING, PH.D.")[0] == "individual"

    def test_individual_bare_name_with_apostrophe(self):
        assert classify_sponsor("CATLIN O'NEILL")[0] == "individual"

    def test_individual_bare_name_with_hyphen(self):
        assert classify_sponsor("MARIO DIAZ-BALART")[0] == "individual"

    def test_individual_bare_name_mc_prefix(self):
        """'Mc' prefix with lowercase 'c' (McKINNEY, McHENRY) still classifies."""
        assert classify_sponsor("PATRICK T. McHENRY")[0] == "individual"
        assert classify_sponsor("JANICE C. McKINNEY")[0] == "individual"

    def test_individual_bare_name_three_words(self):
        assert classify_sponsor("KAY A. KING")[0] == "individual"

    def test_individual_bare_name_with_suffix_iii(self):
        assert classify_sponsor("WILLIE LYLES III")[0] == "individual"

    def test_individual_bare_name_with_jr_suffix(self):
        assert classify_sponsor("THOMAS W. ROSS, JR.")[0] == "individual"

    def test_travel_to_phrase_not_individual(self):
        """'TRAVEL TO RUSSIA' must be delegation, not classified as a bare name."""
        assert classify_sponsor("TRAVEL TO RUSSIA")[0] == "delegation"

    def test_unclassified_garbage_still_flagged(self):
        """Genuinely unparseable sponsor text is still flagged, not guessed."""
        sponsor_type, flags = classify_sponsor(
            "Reports concerning the foreign currencies and U.S. dollars utilized "
            "for Official Foreign Travel during the first, second, third, and "
            "fourth quarters of 2018"
        )
        assert sponsor_type == "other"
        assert flags == ["SPONSOR_UNCLASSIFIED"]

    def test_unclassified_trailing_junk_still_flagged(self):
        """A personal name with unstripped ', HOUSE OF REPRESENTATIVES, EXPENDED
        BETWEEN...' trailing junk is still flagged -- parse_header should have
        stripped it, but didn't. classify_sponsor shouldn't paper over that."""
        sponsor_type, flags = classify_sponsor(
            "DAVID TEBBE, HOUSE OF REPRESENTATIVES, EXPENDED BETWEEN MAY 27 AND MAY 28, 20O2"
        )
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

    def test_committee_header_with_trailing_clipped_text_after_chamber(self):
        """The title-line 193-char fixed-width limit sometimes clips the
        committee name mid-token, leaving trailing gunk after 'HOUSE OF
        REPRESENTATIVES' (e.g. ',P' is the clipped start of a longer word
        like 'PERIOD'). The TRAILING_CHAMBER_RE strip consumes that gunk
        so the sponsor.name resolves cleanly to the committee name and
        committee_index lookup can find the sponsor_code. Live cases:
        2015q1feb20 'PERMANENT SELECT COMMITTEE ON INTELLIGENCE, HOUSE OF
        REPRESENTATIVES,P' and 2015q2apr27 'COMMITTEE ON ARMED SERVICES,
        HOUSE OF REPRESENTATIVES,P'."""
        info = parse_header(
            "REPORT OF EXPENDITURES FOR OFFICIAL FOREIGN TRAVEL, COMMITTEE ON ARMED "
            "SERVICES, HOUSE OF REPRESENTATIVES,P"
        )
        assert info.sponsor.type == "committee"
        assert info.sponsor.name == "COMMITTEE ON ARMED SERVICES"

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
