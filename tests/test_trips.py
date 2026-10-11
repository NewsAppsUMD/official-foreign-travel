"""Tests for canonical trip construction across filings."""

import csv
import gzip
import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

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
from official_foreign_travel.parsing.assemble import assemble_file, load_name_index
from official_foreign_travel.parsing.dedup import dedup_reports
from official_foreign_travel.parsing.serialize import (
    TRIP_CSV_FIELDNAMES,
    _flatten_trip_row,
    visible_reports,
    write_trips_csv,
    write_trips_json,
    write_trips_jsonl,
)
from official_foreign_travel.parsing.trips import (
    TRIP_ATTENTION_FLAGS,
    TRIP_DATE_DESTINATION_CONFLICT,
    TRIP_DATES_MISSING,
    TRIP_GAP_FILLED_FROM_EARLIER_FILING,
    TRIP_MATCHED_NAME_ONLY,
    TRIP_MERGED_ACROSS_FILINGS,
    TRIP_MERGED_MIXED_IDENTITY,
    TRIP_SPLIT_BY_DATE_GAP,
    build_trips,
)
from official_foreign_travel.parsing.validate import validate_reports

FIXTURES = Path(__file__).parent / "fixtures"
MEMBERS_CSV = Path(__file__).parent.parent / "members.csv"


def make_cell(amount=None):
    return CostCell(amount=amount, raw=str(amount) if amount is not None else "")


def make_costs(per_diem_usd=None, transportation_usd=None, other_usd=None, total_usd=None):
    return Costs(
        per_diem=CostGroup(foreign_currency=make_cell(), us_dollar=make_cell(per_diem_usd)),
        transportation=CostGroup(
            foreign_currency=make_cell(), us_dollar=make_cell(transportation_usd)
        ),
        other=CostGroup(foreign_currency=make_cell(), us_dollar=make_cell(other_usd)),
        total=CostGroup(foreign_currency=make_cell(), us_dollar=make_cell(total_usd)),
    )


def make_segment(arrival=None, departure=None, countries=None, costs=None, flags=None):
    return TravelSegment(
        arrival_date=arrival,
        departure_date=departure,
        country_raw=", ".join(countries or []),
        countries=list(countries or []),
        costs=costs or make_costs(),
        flags=list(flags or []),
    )


def make_traveler(name, bioguide=None, segments=None):
    return Traveler(name=name, bioguide_id=bioguide, segments=segments or [])


def make_report(
    report_id,
    source_file,
    travelers=None,
    table_index=0,
    sponsor_name=None,
    period=None,
    amended=False,
):
    sponsor_name = sponsor_name or f"COMMITTEE ON {report_id[-3:]}"
    return Report(
        report_id=report_id,
        source_file=source_file,
        table_index=table_index,
        amended=amended,
        sponsor=Sponsor(type="committee", name=sponsor_name, code="XX00", raw=sponsor_name),
        period=period,
        travelers=travelers or [],
    )


def trips_for(trips, name):
    return [t for t in trips if t.name == name]


class TestWithinFilingJourneys:
    def test_contiguous_legs_are_one_trip(self):
        traveler = make_traveler(
            "Hon. Jerry Costello",
            bioguide="C000794",
            segments=[
                make_segment(date(2007, 8, 18), date(2007, 8, 21), ["TUNISIA"]),
                make_segment(date(2007, 8, 21), date(2007, 8, 25), ["TURKEY"]),
                make_segment(date(2007, 8, 25), date(2007, 8, 27), ["CROATIA"]),
                make_segment(date(2007, 8, 26), date(2007, 8, 29), ["GERMANY"]),
            ],
        )
        trips = build_trips([make_report("f-000", "2007q4nov13.txt", [traveler])])
        assert len(trips) == 1
        trip = trips[0]
        assert trip.n_legs == 4
        assert trip.start_date == date(2007, 8, 18)
        assert trip.end_date == date(2007, 8, 29)
        assert trip.countries == ["TUNISIA", "TURKEY", "CROATIA", "GERMANY"]
        assert TRIP_SPLIT_BY_DATE_GAP not in trip.flags
        assert trip.n_filings == 1
        assert trip.source_report_ids == ["f-000"]

    def test_date_gap_splits_journeys(self):
        # Peru 4/07-4/12 then Ireland 4/16-4/23: a 4-day gap is a new journey.
        traveler = make_traveler(
            "John Mackey",
            segments=[
                make_segment(date(1995, 4, 7), date(1995, 4, 12), ["PERU"]),
                make_segment(date(1995, 4, 16), date(1995, 4, 23), ["IRELAND"]),
            ],
        )
        trips = build_trips([make_report("f-000", "1995q3aug04.txt", [traveler])])
        assert len(trips) == 2
        assert all(TRIP_SPLIT_BY_DATE_GAP in t.flags for t in trips)
        assert {t.start_date for t in trips} == {date(1995, 4, 7), date(1995, 4, 16)}

    def test_small_gap_stays_one_trip(self):
        # A 3-day gap between legs is still one journey.
        traveler = make_traveler(
            "John Mackey",
            segments=[
                make_segment(date(1995, 4, 7), date(1995, 4, 12), ["PERU"]),
                make_segment(date(1995, 4, 15), date(1995, 4, 23), ["IRELAND"]),
            ],
        )
        trips = build_trips([make_report("f-000", "1995q3aug04.txt", [traveler])])
        assert len(trips) == 1
        assert trips[0].n_legs == 2
        assert TRIP_SPLIT_BY_DATE_GAP not in trips[0].flags

    def test_out_of_order_segments_stay_one_journey(self):
        # Source tables are not always chronological; spanning is computed
        # from sorted dates, never from list order.
        traveler = make_traveler(
            "Hon. Doug Bereuter",
            segments=[
                make_segment(date(1994, 8, 30), date(1994, 9, 5), ["JAPAN"]),
                make_segment(date(1994, 9, 9), date(1994, 9, 10), ["KOREA"]),
                make_segment(date(1994, 9, 6), date(1994, 9, 9), ["LAOS"]),
            ],
        )
        trips = build_trips([make_report("f-000", "1995q1feb09.txt", [traveler])])
        assert len(trips) == 1
        trip = trips[0]
        assert trip.start_date == date(1994, 8, 30)
        assert trip.end_date == date(1994, 9, 10)
        assert trip.n_legs == 3
        # Legs are re-sorted chronologically.
        assert [leg.segment.arrival_date for leg in trip.legs] == [
            date(1994, 8, 30),
            date(1994, 9, 6),
            date(1994, 9, 9),
        ]

    def test_undated_segment_attaches_to_previous_journey(self):
        traveler = make_traveler(
            "Hon. Jane Smith",
            segments=[
                make_segment(date(2005, 5, 1), date(2005, 5, 3), ["FRANCE"]),
                make_segment(None, None, ["UNITED STATES"]),
                make_segment(date(2005, 6, 10), date(2005, 6, 12), ["GERMANY"]),
            ],
        )
        trips = build_trips([make_report("f-000", "2005q2may02.txt", [traveler])])
        assert len(trips) == 2
        first, second = trips  # sorted by start date
        assert first.start_date == date(2005, 5, 1)
        assert first.n_legs == 2  # undated US leg rides along with FRANCE
        assert first.countries == ["FRANCE", "UNITED STATES"]
        assert second.n_legs == 1
        assert second.countries == ["GERMANY"]

    def test_fully_undated_traveler_is_flagged_and_never_merges(self):
        # No dates anywhere: the trips cannot be date-matched across filings,
        # so duplicate appearances stay separate records.
        traveler = make_traveler(
            "Staff Delgado",
            segments=[make_segment(None, None, ["ITALY"])],
        )
        other = make_traveler(
            "Staff Delgado",
            segments=[make_segment(None, None, ["ITALY"])],
        )
        trips = build_trips(
            [
                make_report("a-000", "2005q1feb02.txt", [traveler]),
                make_report("b-000", "2005q2may02.txt", [other]),
            ]
        )
        assert len(trips) == 2
        assert all(TRIP_DATES_MISSING in t.flags for t in trips)
        assert all(t.n_filings == 1 for t in trips)


class TestExclusions:
    def test_pseudo_travelers_and_unnamed_rows_excluded(self):
        travelers = [
            make_traveler(
                "Commercial airfare",
                segments=[
                    make_segment(
                        date(2001, 1, 1),
                        date(2001, 1, 2),
                        ["GERMANY"],
                        flags=["NON_PERSON_LABEL_ROW"],
                    )
                ],
            ),
            make_traveler(
                "STAFFDEL Expenses",
                segments=[
                    make_segment(
                        date(2001, 1, 1), date(2001, 1, 2), [], flags=["STAFFDEL_GROUP_EXPENSE"]
                    )
                ],
            ),
            make_traveler(""),
            make_traveler("Hon. Real Person"),
        ]
        trips = build_trips([make_report("f-000", "2001q1feb02.txt", travelers)])
        assert trips == []


class TestNonMutation:
    def test_input_reports_not_mutated(self):
        traveler = make_traveler(
            "Hon. Jerry Costello",
            bioguide="C000794",
            segments=[
                make_segment(date(2007, 8, 18), date(2007, 8, 21), ["TUNISIA"]),
                make_segment(None, None, ["UNITED STATES"]),
            ],
        )
        other = make_traveler(
            "Hon. Jerry Costello",
            bioguide="C000794",
            segments=[
                make_segment(date(2007, 8, 18), date(2007, 8, 27), ["TUNISIA"]),
                make_segment(date(2007, 8, 27), date(2007, 8, 27), ["UNITED STATES"]),
            ],
        )
        reports = [
            make_report("a-009", "2007q4nov13.txt", [traveler], table_index=9),
            make_report("a-010", "2007q4nov13.txt", [other], table_index=10),
        ]
        before = [r.model_dump() for r in reports]
        build_trips(reports)
        assert [r.model_dump() for r in reports] == before


class TestCrossFilingMatching:
    def test_cross_filing_merge_by_bioguide(self):
        legs = [
            make_segment(date(2007, 8, 18), date(2007, 8, 21), ["TUNISIA"]),
            make_segment(date(2007, 8, 21), date(2007, 8, 25), ["TURKEY"]),
            make_segment(date(2007, 8, 25), date(2007, 8, 27), ["CROATIA"]),
            make_segment(date(2007, 8, 27), date(2007, 8, 29), ["GERMANY"]),
        ]
        trips = build_trips(
            [
                make_report(
                    "2007q4nov13-009",
                    "2007q4nov13.txt",
                    [make_traveler("Hon. Jerry Costello", bioguide="C000794", segments=legs)],
                    table_index=9,
                    sponsor_name="COMMITTEE ON TRANSPORTATION",
                ),
                make_report(
                    "2007q4nov13-010",
                    "2007q4nov13.txt",
                    [
                        make_traveler(
                            "Hon. Jerry Costello",
                            bioguide="C000794",
                            segments=[
                                make_segment(date(2007, 8, 18), date(2007, 8, 21), ["TUNISIA"]),
                                make_segment(date(2007, 8, 21), date(2007, 8, 25), ["TURKEY"]),
                                make_segment(date(2007, 8, 25), date(2007, 8, 27), ["CROATIA"]),
                                make_segment(date(2007, 8, 27), date(2007, 8, 29), ["GERMANY"]),
                            ],
                        )
                    ],
                    table_index=10,
                    sponsor_name="COMMITTEE ON THE JUDICIARY",
                ),
            ]
        )
        assert len(trips) == 1
        trip = trips[0]
        assert TRIP_MERGED_ACROSS_FILINGS in trip.flags
        assert trip.n_filings == 2
        assert trip.source_report_ids == ["2007q4nov13-009", "2007q4nov13-010"]
        # Same publication file, same amended flag: higher table_index wins.
        assert trip.sponsor_name == "COMMITTEE ON THE JUDICIARY"
        assert trip.n_legs == 4

    def test_newest_publication_wins(self):
        old = make_report(
            "a-000",
            "2008q1feb01.txt",
            [
                make_traveler(
                    "Hon. John Smith",
                    bioguide="S000001",
                    segments=[make_segment(date(2008, 3, 10), date(2008, 3, 15), ["GERMANY"])],
                )
            ],
        )
        new = make_report(
            "b-000",
            "2008q2may01.txt",
            [
                make_traveler(
                    "Hon. John Smith",
                    bioguide="S000001",
                    segments=[
                        make_segment(date(2008, 3, 10), date(2008, 3, 15), ["GERMANY"]),
                        make_segment(date(2008, 3, 15), date(2008, 3, 17), ["BELGIUM"]),
                    ],
                )
            ],
        )
        trips = build_trips([old, new])
        assert len(trips) == 1
        trip = trips[0]
        assert trip.countries == ["GERMANY", "BELGIUM"]
        assert trip.n_legs == 2
        assert trip.source_report_ids == ["a-000", "b-000"]
        assert TRIP_MERGED_ACROSS_FILINGS in trip.flags

    def test_name_only_merge_is_flagged(self):
        old = make_report(
            "a-000",
            "2008q1feb01.txt",
            [
                make_traveler(
                    "Jane Smith",
                    segments=[make_segment(date(2008, 3, 10), date(2008, 3, 15), ["GERMANY"])],
                )
            ],
        )
        new = make_report(
            "b-000",
            "2008q2may01.txt",
            [
                make_traveler(
                    "Jane Smith",
                    segments=[make_segment(date(2008, 3, 10), date(2008, 3, 15), ["GERMANY"])],
                )
            ],
        )
        trips = build_trips([old, new])
        assert len(trips) == 1
        trip = trips[0]
        assert TRIP_MERGED_ACROSS_FILINGS in trip.flags
        assert TRIP_MATCHED_NAME_ONLY in trip.flags
        assert trip.bioguide_id is None

    def test_destination_disagreement_keeps_records_separate_and_flags_both(self):
        old = make_report(
            "a-000",
            "2008q1feb01.txt",
            [
                make_traveler(
                    "Hon. John Smith",
                    bioguide="S000001",
                    segments=[make_segment(date(2008, 3, 10), date(2008, 3, 15), ["EUROPE"])],
                )
            ],
        )
        new = make_report(
            "b-000",
            "2008q2may01.txt",
            [
                make_traveler(
                    "Hon. John Smith",
                    bioguide="S000001",
                    segments=[make_segment(date(2008, 3, 10), date(2008, 3, 15), ["DENMARK"])],
                )
            ],
        )
        trips = build_trips([old, new])
        assert len(trips) == 2
        assert all(TRIP_DATE_DESTINATION_CONFLICT in t.flags for t in trips)
        assert all(TRIP_MERGED_ACROSS_FILINGS not in t.flags for t in trips)

    def test_destination_substring_match_through_gunk(self):
        # split_countries gunk ("SWITZERLAND 31") still matches "SWITZERLAND".
        old = make_report(
            "a-000",
            "2008q1feb01.txt",
            [
                make_traveler(
                    "Hon. John Smith",
                    bioguide="S000001",
                    segments=[make_segment(date(2008, 3, 10), date(2008, 3, 15), ["SWITZERLAND"])],
                )
            ],
        )
        new = make_report(
            "b-000",
            "2008q2may01.txt",
            [
                make_traveler(
                    "Hon. John Smith",
                    bioguide="S000001",
                    segments=[
                        make_segment(date(2008, 3, 10), date(2008, 3, 15), ["SWITZERLAND 31"])
                    ],
                )
            ],
        )
        trips = build_trips([old, new])
        assert len(trips) == 1
        assert TRIP_MERGED_ACROSS_FILINGS in trips[0].flags

    def test_mixed_identity_secondary_merge(self):
        bg_filing = make_report(
            "a-000",
            "2008q1feb01.txt",
            [
                make_traveler(
                    "Hon. Frank R. Wolf",
                    bioguide="W000800",
                    segments=[make_segment(date(2008, 3, 10), date(2008, 3, 15), ["AUSTRIA"])],
                )
            ],
        )
        nm_filing = make_report(
            "b-000",
            "2008q2may01.txt",
            [
                make_traveler(
                    "Frank Wolf",
                    segments=[make_segment(date(2008, 3, 10), date(2008, 3, 15), ["AUSTRIA"])],
                )
            ],
        )
        trips = build_trips([bg_filing, nm_filing])
        assert len(trips) == 1
        trip = trips[0]
        assert TRIP_MERGED_MIXED_IDENTITY in trip.flags
        assert TRIP_MERGED_ACROSS_FILINGS in trip.flags
        assert trip.bioguide_id == "W000800"

    def test_mixed_identity_ambiguous_bioguide_does_not_merge(self):
        # Two different members share the (frank, wolf) name tokens: the
        # bare-name filing cannot be attributed to either one, so it stays
        # its own trip.
        filings = [
            make_report(
                "a-000",
                "2008q1feb01.txt",
                [
                    make_traveler(
                        "Hon. Frank R. Wolf",
                        bioguide="W000800",
                        segments=[make_segment(date(2008, 3, 10), date(2008, 3, 15), ["AUSTRIA"])],
                    )
                ],
            ),
            make_report(
                "b-000",
                "2008q2may01.txt",
                [
                    make_traveler(
                        "Frank Wolf",
                        segments=[make_segment(date(2008, 3, 10), date(2008, 3, 15), ["AUSTRIA"])],
                    )
                ],
            ),
            make_report(
                "c-000",
                "2008q3aug01.txt",
                [
                    make_traveler(
                        "Hon. Frank Wolf",
                        bioguide="Z000017",
                        segments=[make_segment(date(2008, 3, 10), date(2008, 3, 15), ["AUSTRIA"])],
                    )
                ],
            ),
        ]
        trips = build_trips(filings)
        assert len(trips) == 3
        assert all(TRIP_MERGED_MIXED_IDENTITY not in t.flags for t in trips)
        assert all(TRIP_MERGED_ACROSS_FILINGS not in t.flags for t in trips)


class TestConflictCrossLinking:
    def _conflicting_filings(self):
        # Same member, overlapping dates, disjoint destinations: two filings
        # that describe the same person in the same window but disagree on
        # where they went.
        older = make_report(
            report_id="f-000",
            source_file="2019q1jan02.txt",
            travelers=[
                make_traveler(
                    "Hon. Jane Smith",
                    bioguide="S001",
                    segments=[make_segment(date(2019, 3, 1), date(2019, 3, 5), ["EUROPE"])],
                )
            ],
        )
        newer = make_report(
            report_id="g-001",
            source_file="2019q2apr30.txt",
            travelers=[
                make_traveler(
                    "Hon. Jane Smith",
                    bioguide="S001",
                    segments=[make_segment(date(2019, 3, 2), date(2019, 3, 6), ["DENMARK"])],
                )
            ],
        )
        return older, newer

    def test_conflict_pairs_link_both_ways(self):
        older, newer = self._conflicting_filings()
        trips = build_trips([older, newer])
        assert len(trips) == 2
        assert all(TRIP_DATE_DESTINATION_CONFLICT in t.flags for t in trips)
        assert trips[0].conflict_with == [trips[1].trip_id]
        assert trips[1].conflict_with == [trips[0].trip_id]

    def test_conflict_pairs_accumulate_multiple_partners(self):
        # Three filings over mutually overlapping spans with pairwise
        # disjoint destinations: all three pairs conflict, so each trip
        # must carry both partners' ids -- accumulated, symmetric, sorted.
        europe = make_report(
            report_id="f-000",
            source_file="2019q1jan02.txt",
            travelers=[
                make_traveler(
                    "Hon. Jane Smith",
                    bioguide="S001",
                    segments=[make_segment(date(2019, 3, 1), date(2019, 3, 5), ["EUROPE"])],
                )
            ],
        )
        denmark = make_report(
            report_id="g-001",
            source_file="2019q2apr30.txt",
            travelers=[
                make_traveler(
                    "Hon. Jane Smith",
                    bioguide="S001",
                    segments=[make_segment(date(2019, 3, 2), date(2019, 3, 6), ["DENMARK"])],
                )
            ],
        )
        japan = make_report(
            report_id="h-000",
            source_file="2019q3jul29.txt",
            travelers=[
                make_traveler(
                    "Hon. Jane Smith",
                    bioguide="S001",
                    segments=[make_segment(date(2019, 3, 3), date(2019, 3, 7), ["JAPAN"])],
                )
            ],
        )
        trips = build_trips([europe, denmark, japan])
        assert len(trips) == 3
        assert all(TRIP_DATE_DESTINATION_CONFLICT in t.flags for t in trips)
        for trip in trips:
            assert trip.conflict_with == sorted(t.trip_id for t in trips if t is not trip)

    def test_merged_trip_has_no_conflict_with(self):
        # A clean cross-filing merge never carries conflict partners.
        traveler = make_traveler(
            "Hon. Jane Smith",
            bioguide="S001",
            segments=[make_segment(date(2019, 3, 1), date(2019, 3, 5), ["FRANCE"])],
        )
        other = make_traveler(
            "Hon. Jane Smith",
            bioguide="S001",
            segments=[make_segment(date(2019, 3, 1), date(2019, 3, 5), ["FRANCE"])],
        )
        trips = build_trips(
            [
                make_report("f-000", "2019q1jan02.txt", [traveler]),
                make_report("g-001", "2019q2apr30.txt", [other]),
            ]
        )
        assert len(trips) == 1
        assert trips[0].conflict_with == []

    def test_transient_conflict_merged_via_third_filing_no_self_link(self):
        # EUROPE and DENMARK filings conflict with each other, but a third
        # filing reporting both overlaps both -- all three merge into one
        # trip. That trip keeps the conflict flag but must not link to itself.
        europe = make_report(
            report_id="f-000",
            source_file="2019q1jan02.txt",
            travelers=[
                make_traveler(
                    "Hon. Jane Smith",
                    bioguide="S001",
                    segments=[make_segment(date(2019, 3, 1), date(2019, 3, 5), ["EUROPE"])],
                )
            ],
        )
        denmark = make_report(
            report_id="g-001",
            source_file="2019q2apr30.txt",
            travelers=[
                make_traveler(
                    "Hon. Jane Smith",
                    bioguide="S001",
                    segments=[make_segment(date(2019, 3, 2), date(2019, 3, 6), ["DENMARK"])],
                )
            ],
        )
        both = make_report(
            report_id="h-000",
            source_file="2019q3jul29.txt",
            travelers=[
                make_traveler(
                    "Hon. Jane Smith",
                    bioguide="S001",
                    segments=[
                        make_segment(date(2019, 3, 1), date(2019, 3, 6), ["EUROPE", "DENMARK"])
                    ],
                )
            ],
        )
        trips = build_trips([europe, denmark, both])
        assert len(trips) == 1
        assert TRIP_DATE_DESTINATION_CONFLICT in trips[0].flags
        assert trips[0].conflict_with == []

    def test_attention_flag_set(self):
        # Exactly the four flags that make a trip worth a reviewer's time.
        assert TRIP_ATTENTION_FLAGS == frozenset(
            {
                TRIP_DATE_DESTINATION_CONFLICT,
                TRIP_MATCHED_NAME_ONLY,
                TRIP_MERGED_MIXED_IDENTITY,
                TRIP_DATES_MISSING,
            }
        )
        # Deliberately excluded: these three are informational, not problems.
        assert TRIP_SPLIT_BY_DATE_GAP not in TRIP_ATTENTION_FLAGS
        assert TRIP_MERGED_ACROSS_FILINGS not in TRIP_ATTENTION_FLAGS
        assert TRIP_GAP_FILLED_FROM_EARLIER_FILING not in TRIP_ATTENTION_FLAGS


class TestTripAssembly:
    def test_costs_never_sum_across_filings(self):
        old = make_report(
            "a-000",
            "2008q1feb01.txt",
            [
                make_traveler(
                    "Hon. John Smith",
                    bioguide="S000001",
                    segments=[
                        make_segment(
                            date(2008, 3, 10),
                            date(2008, 3, 12),
                            ["GERMANY"],
                            costs=make_costs(
                                per_diem_usd=Decimal("100.00"), total_usd=Decimal("100.00")
                            ),
                        ),
                        make_segment(
                            date(2008, 3, 12),
                            date(2008, 3, 15),
                            ["FRANCE"],
                            costs=make_costs(
                                per_diem_usd=Decimal("200.00"), total_usd=Decimal("200.00")
                            ),
                        ),
                    ],
                )
            ],
        )
        new = make_report(
            "b-000",
            "2008q2may01.txt",
            [
                make_traveler(
                    "Hon. John Smith",
                    bioguide="S000001",
                    segments=[
                        make_segment(
                            date(2008, 3, 10),
                            date(2008, 3, 12),
                            ["GERMANY"],
                            costs=make_costs(
                                per_diem_usd=Decimal("100.00"), total_usd=Decimal("100.00")
                            ),
                        ),
                        make_segment(
                            date(2008, 3, 12),
                            date(2008, 3, 15),
                            ["FRANCE"],
                            costs=make_costs(
                                per_diem_usd=Decimal("200.00"), total_usd=Decimal("700.00")
                            ),
                        ),
                    ],
                )
            ],
        )
        trips = build_trips([old, new])
        assert len(trips) == 1
        trip = trips[0]
        # Winner (newer filing) supplies the amounts; they are summed over
        # the winner's legs only, never across filings.
        assert trip.per_diem_usd == Decimal("300.00")
        assert trip.total_usd == Decimal("800.00")

    def test_winner_with_fewer_legs_keeps_its_own_legs(self):
        old = make_report(
            "a-000",
            "2008q1feb01.txt",
            [
                make_traveler(
                    "Hon. John Smith",
                    bioguide="S000001",
                    segments=[
                        make_segment(date(2008, 3, 10), date(2008, 3, 12), ["GERMANY"]),
                        make_segment(date(2008, 3, 12), date(2008, 3, 14), ["FRANCE"]),
                        make_segment(date(2008, 3, 14), date(2008, 3, 16), ["UNITED KINGDOM"]),
                    ],
                )
            ],
        )
        new = make_report(
            "b-000",
            "2008q2may01.txt",
            [
                make_traveler(
                    "Hon. John Smith",
                    bioguide="S000001",
                    segments=[
                        make_segment(date(2008, 3, 10), date(2008, 3, 12), ["GERMANY"]),
                        make_segment(date(2008, 3, 12), date(2008, 3, 14), ["FRANCE"]),
                    ],
                )
            ],
        )
        trips = build_trips([old, new])
        assert len(trips) == 1
        trip = trips[0]
        assert trip.n_legs == 2
        assert trip.countries == ["GERMANY", "FRANCE"]
        assert trip.source_report_ids == ["a-000", "b-000"]

    def test_gap_fill_from_older_filing(self):
        old = make_report(
            "a-000",
            "2008q1feb01.txt",
            [
                make_traveler(
                    "Hon. John Smith",
                    bioguide="S000001",
                    segments=[
                        make_segment(date(2008, 3, 10), date(2008, 3, 15), ["GERMANY"]),
                        make_segment(date(2008, 3, 15), date(2008, 3, 16), ["UNITED STATES"]),
                    ],
                )
            ],
        )
        # The newer filing lost the arrival date on its first leg and every
        # destination on its US leg.
        new = make_report(
            "b-000",
            "2008q2may01.txt",
            [
                make_traveler(
                    "Hon. John Smith",
                    bioguide="S000001",
                    segments=[
                        make_segment(None, date(2008, 3, 15), ["GERMANY"]),
                        make_segment(date(2008, 3, 15), date(2008, 3, 16), []),
                    ],
                )
            ],
        )
        trips = build_trips([old, new])
        assert len(trips) == 1
        trip = trips[0]
        assert TRIP_GAP_FILLED_FROM_EARLIER_FILING in trip.flags
        first_leg = trip.legs[0].segment
        assert first_leg.arrival_date == date(2008, 3, 10)
        us_leg = trip.legs[1].segment
        assert us_leg.countries == ["UNITED STATES"]
        # The trip span reflects the gap-filled arrival.
        assert trip.start_date == date(2008, 3, 10)

    def test_bioguide_gap_filled_from_older_filing(self):
        old = make_report(
            "a-000",
            "2008q1feb01.txt",
            [
                make_traveler(
                    "Hon. John Smith",
                    bioguide="S000001",
                    segments=[make_segment(date(2008, 3, 10), date(2008, 3, 15), ["GERMANY"])],
                )
            ],
        )
        new = make_report(
            "b-000",
            "2008q2may01.txt",
            [
                make_traveler(
                    "Hon. John Smith",
                    segments=[make_segment(date(2008, 3, 10), date(2008, 3, 15), ["GERMANY"])],
                )
            ],
        )
        trips = build_trips([old, new])
        assert len(trips) == 1
        trip = trips[0]
        assert trip.bioguide_id == "S000001"
        assert TRIP_MERGED_MIXED_IDENTITY in trip.flags

    def test_trip_id_deterministic_and_stable(self):
        reports = [
            make_report(
                "a-000",
                "2008q1feb01.txt",
                [
                    make_traveler(
                        "Hon. John Smith",
                        bioguide="S000001",
                        segments=[make_segment(date(2008, 3, 10), date(2008, 3, 15), ["GERMANY"])],
                    )
                ],
            ),
            make_report(
                "b-000",
                "2008q2may01.txt",
                [
                    make_traveler(
                        "Jane Smith",
                        segments=[make_segment(date(2008, 4, 10), date(2008, 4, 15), ["ITALY"])],
                    )
                ],
            ),
        ]
        first_run = build_trips(reports)
        second_run = build_trips(reports)
        assert [t.trip_id for t in first_run] == [t.trip_id for t in second_run]
        for trip in first_run:
            assert re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9-]*-(\d{8}|undated)-[0-9a-f]{6}", trip.trip_id
            )

    def test_undated_duplicate_travelers_get_distinct_trip_ids(self):
        # Two identical undated appearances never merge, so their ids must
        # not collide either.
        trips = build_trips(
            [
                make_report(
                    "a-000",
                    "2005q1feb02.txt",
                    [
                        make_traveler(
                            "Jane Smith",
                            segments=[make_segment(None, None, ["ITALY"])],
                        )
                    ],
                ),
                make_report(
                    "b-000",
                    "2005q2may02.txt",
                    [
                        make_traveler(
                            "Jane Smith",
                            segments=[make_segment(None, None, ["ITALY"])],
                        )
                    ],
                ),
            ]
        )
        assert len(trips) == 2
        first, second = trips  # sorted by trip_id: the un-suffixed base first
        assert second.trip_id == first.trip_id + "-2"


def _amendment_pair():
    """An original + amendment (same sponsor and period) sharing one traveler."""
    period = Period(start=date(2008, 1, 1), end=date(2008, 3, 31), year=2008, quarter=1)
    legs = [make_segment(date(2008, 3, 10), date(2008, 3, 15), ["GERMANY"])]
    original = make_report(
        "orig-000",
        "2008q1feb01.txt",
        [make_traveler("Hon. John Smith", bioguide="S000001", segments=legs)],
        sponsor_name="COMMITTEE ON TEST",
        period=period,
    )
    amendment = make_report(
        "amend-000",
        "2008q2may01.txt",
        [make_traveler("Hon. John Smith", bioguide="S000001", segments=legs)],
        sponsor_name="COMMITTEE ON TEST",
        period=period,
        amended=True,
    )
    dedup_reports([original, amendment])
    return [original, amendment]


class TestTripsSerialization:
    def test_write_trips_csv_header_and_rows(self, tmp_path):
        reports = [
            make_report(
                "a-000",
                "2008q1feb01.txt",
                [
                    make_traveler(
                        "Hon. John Smith",
                        bioguide="S000001",
                        segments=[
                            make_segment(
                                date(2008, 3, 10),
                                date(2008, 3, 15),
                                ["GERMANY"],
                                costs=make_costs(total_usd=Decimal("500.00")),
                            )
                        ],
                    )
                ],
            )
        ]
        out = tmp_path / "trips.csv"
        stats = write_trips_csv(reports, out)
        with open(out, newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert reader.fieldnames == TRIP_CSV_FIELDNAMES
        assert stats == {"trips": 1, "trips_merged_across_filings": 0, "trips_flagged": 0}
        assert len(rows) == 1
        row = rows[0]
        assert row["trip_id"]
        assert row["name"] == "Hon. John Smith"
        assert row["member_id"] == "S000001"
        assert row["start_date"] == "03/10/2008"
        assert row["end_date"] == "03/15/2008"
        assert row["days"] == "6"
        assert row["countries"] == "GERMANY"
        assert row["n_legs"] == "1"
        assert row["n_filings"] == "1"
        assert row["source_report_ids"] == "a-000"
        assert row["total_usd"] == "500.00"

    def test_write_trips_csv_stats_count_merged_and_flagged(self, tmp_path):
        reports = [
            make_report(
                "a-000",
                "2008q1feb01.txt",
                [
                    make_traveler(
                        "Hon. John Smith",
                        bioguide="S000001",
                        segments=[make_segment(date(2008, 3, 10), date(2008, 3, 15), ["GERMANY"])],
                    )
                ],
            ),
            make_report(
                "b-000",
                "2008q2may01.txt",
                [
                    make_traveler(
                        "Hon. John Smith",
                        bioguide="S000001",
                        segments=[make_segment(date(2008, 3, 10), date(2008, 3, 15), ["GERMANY"])],
                    )
                ],
            ),
        ]
        stats = write_trips_csv(reports, tmp_path / "trips.csv")
        assert stats == {"trips": 1, "trips_merged_across_filings": 1, "trips_flagged": 1}

    def test_write_trips_csv_excludes_superseded_filings(self, tmp_path):
        reports = _amendment_pair()
        assert reports[0].superseded_by == "amend-000"
        out = tmp_path / "trips.csv"
        stats = write_trips_csv(reports, out)
        with open(out, newline="") as f:
            rows = list(csv.DictReader(f))
        assert stats["trips"] == 1
        assert rows[0]["source_report_ids"] == "amend-000"
        assert rows[0]["n_filings"] == "1"
        assert TRIP_MERGED_ACROSS_FILINGS not in rows[0]["flags"]

    def test_write_trips_csv_include_superseded_merges_both_filings(self, tmp_path):
        reports = _amendment_pair()
        out = tmp_path / "trips.csv"
        stats = write_trips_csv(reports, out, include_superseded=True)
        with open(out, newline="") as f:
            rows = list(csv.DictReader(f))
        assert stats["trips"] == 1
        assert rows[0]["source_report_ids"] == "amend-000; orig-000"
        assert rows[0]["n_filings"] == "2"
        assert TRIP_MERGED_ACROSS_FILINGS in rows[0]["flags"]

    def test_write_trips_jsonl_rows(self, tmp_path):
        reports = [
            make_report(
                "a-000",
                "2008q1feb01.txt",
                [
                    make_traveler(
                        "Hon. John Smith",
                        bioguide="S000001",
                        segments=[
                            make_segment(
                                date(2008, 3, 10),
                                date(2008, 3, 15),
                                ["GERMANY"],
                                costs=make_costs(per_diem_usd=Decimal("100.00")),
                            )
                        ],
                    )
                ],
            )
        ]
        out = tmp_path / "trips.jsonl"
        stats = write_trips_jsonl(reports, out)
        lines = out.read_text().strip().split("\n")
        assert stats["trips"] == len(lines) == 1
        row = json.loads(lines[0])
        assert row["trip_id"]
        assert row["per_diem_usd"] == "100.00"
        assert row["start_date"] == "03/10/2008"

    def test_write_trips_json_envelope_and_nested_legs(self, tmp_path):
        reports = [
            make_report(
                "a-000",
                "2008q1feb01.txt",
                [
                    make_traveler(
                        "Hon. John Smith",
                        bioguide="S000001",
                        segments=[
                            make_segment(
                                date(2008, 3, 10),
                                date(2008, 3, 15),
                                ["GERMANY"],
                                costs=make_costs(total_usd=Decimal("500.00")),
                            )
                        ],
                    )
                ],
                sponsor_name="COMMITTEE ON OLDER",
            ),
            make_report(
                "b-000",
                "2008q2may01.txt",
                [
                    make_traveler(
                        "Hon. John Smith",
                        bioguide="S000001",
                        segments=[
                            make_segment(
                                date(2008, 3, 10),
                                date(2008, 3, 15),
                                ["GERMANY"],
                                costs=make_costs(total_usd=Decimal("500.00")),
                            )
                        ],
                    )
                ],
                sponsor_name="COMMITTEE ON NEWER",
            ),
        ]
        out = tmp_path / "trips.json"
        stats = write_trips_json(reports, out)
        payload = json.loads(out.read_text())
        assert payload["schema_version"] == "1.0.0"
        assert "generated_at" in payload
        assert "trips" in payload
        assert stats["trips"] == len(payload["trips"]) == 1
        trip = payload["trips"][0]
        assert trip["bioguide_id"] == "S000001"
        assert trip["sponsor_name"] == "COMMITTEE ON NEWER"
        assert trip["total_usd"] == "500.00"
        # Legs carry the full nested segment (costs as strings) plus provenance.
        leg = trip["legs"][0]
        assert leg["report_id"] == "b-000"
        assert leg["source_file"] == "2008q2may01.txt"
        assert leg["costs"]["total"]["us_dollar"]["amount"] == "500.00"
        # Filings are listed newest-first (rank order).
        assert [f["report_id"] for f in trip["filings"]] == ["b-000", "a-000"]

    def test_write_trips_json_gz_roundtrip(self, tmp_path):
        reports = _amendment_pair()
        out = tmp_path / "trips.json.gz"
        stats = write_trips_json(reports, out)
        with gzip.open(out, "rt", encoding="utf-8") as f:
            payload = json.load(f)
        assert stats["trips"] == len(payload["trips"]) == 1

    def test_conflict_with_in_all_output_formats(self, tmp_path):
        older = make_report(
            report_id="f-000",
            source_file="2019q1jan02.txt",
            travelers=[
                make_traveler(
                    "Hon. Jane Smith",
                    bioguide="S001",
                    segments=[make_segment(date(2019, 3, 1), date(2019, 3, 5), ["EUROPE"])],
                )
            ],
        )
        newer = make_report(
            report_id="g-001",
            source_file="2019q2apr30.txt",
            travelers=[
                make_traveler(
                    "Hon. Jane Smith",
                    bioguide="S001",
                    segments=[make_segment(date(2019, 3, 2), date(2019, 3, 6), ["DENMARK"])],
                )
            ],
        )
        write_trips_csv([older, newer], tmp_path / "trips.csv")
        with open(tmp_path / "trips.csv", newline="") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2
        assert rows[0]["conflict_with"] == rows[1]["trip_id"]
        assert rows[1]["conflict_with"] == rows[0]["trip_id"]

        write_trips_jsonl([older, newer], tmp_path / "trips.jsonl")
        lines = [json.loads(line) for line in (tmp_path / "trips.jsonl").read_text().splitlines()]
        assert any(line["conflict_with"] for line in lines)
        assert lines[0]["conflict_with"] == lines[1]["trip_id"]
        assert lines[1]["conflict_with"] == lines[0]["trip_id"]

        write_trips_json([older, newer], tmp_path / "trips.json")
        payload = json.loads((tmp_path / "trips.json").read_text())
        assert payload["trips"][0]["conflict_with"] == payload["trips"][1]["trip_id"]
        assert payload["trips"][1]["conflict_with"] == payload["trips"][0]["trip_id"]

    def test_conflict_with_is_final_csv_column(self):
        assert TRIP_CSV_FIELDNAMES[-1] == "conflict_with"

    def test_flatten_trip_row_matches_csv_fieldnames(self):
        traveler = make_traveler(
            "Hon. Jane Smith",
            bioguide="S001",
            segments=[make_segment(date(2019, 3, 1), date(2019, 3, 5), ["FRANCE"])],
        )
        trip = build_trips([make_report("f-000", "2019q1jan02.txt", [traveler])])[0]
        assert list(_flatten_trip_row(trip)) == TRIP_CSV_FIELDNAMES


class TestEndToEndFixture:
    def test_cosponsored_codel_merges_across_filings(self):
        # 2007q4nov13.txt contains a co-sponsored CODEL filed by two
        # committees (tables 009 and 010) with 18 shared travelers.
        reports = assemble_file(
            FIXTURES / "2007q4nov13.txt", member_index=load_name_index(MEMBERS_CSV)
        )
        validate_reports(reports)
        dedup_reports(reports)
        trips = build_trips(visible_reports(reports, include_superseded=False))

        costello = [
            t for t in trips if t.bioguide_id == "C000794" and t.start_date == date(2007, 8, 18)
        ]
        assert len(costello) == 1
        trip = costello[0]
        assert trip.end_date == date(2007, 8, 27)
        assert trip.n_filings == 2
        assert trip.source_report_ids == ["2007q4nov13-009", "2007q4nov13-010"]
        assert TRIP_MERGED_ACROSS_FILINGS in trip.flags
        assert trip.countries == ["TUNISIA", "TURKEY", "CROATIA", "GERMANY"]
        assert trip.n_legs == 4
