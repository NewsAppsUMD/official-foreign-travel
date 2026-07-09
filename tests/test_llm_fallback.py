"""Tests for the optional LLM fallback stage, using a stub repairer (no network)."""

import json
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

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
from official_foreign_travel.parsing.assemble import assemble_file
from official_foreign_travel.parsing.llm_fallback import apply_llm_fallback, needs_repair
from official_foreign_travel.parsing.validate import validate_report

FIXTURES = Path(__file__).parent / "fixtures"


def cell(amount=None):
    return CostCell(amount=Decimal(amount) if amount is not None else None, raw="")


def group(amount=None):
    return CostGroup(foreign_currency=cell(), us_dollar=cell(amount))


def costs(total=None):
    # Per diem carries the full amount so per_diem + transportation(0) + other(0) == total,
    # satisfying validate.py's ROW_SUM_MISMATCH check for these "valid" fixtures.
    return Costs(per_diem=group(total), transportation=group(), other=group(), total=group(total))


def make_report(
    report_id="r-000", source_file="2019q1jan29.txt", table_index=0, flags=None, travelers=None
):
    return Report(
        report_id=report_id,
        source_file=source_file,
        table_index=table_index,
        sponsor=Sponsor(type="committee", name="COMMITTEE ON TEST", raw=""),
        period=Period(start=date(2018, 1, 1), end=date(2018, 3, 31), year=2018, quarter=1),
        header_raw="",
        flags=flags or [],
        travelers=travelers or [],
    )


def dated_segment(total=None, flags=None):
    return TravelSegment(
        arrival_date=date(2018, 1, 5),
        departure_date=date(2018, 1, 8),
        arrival_raw="1/5",
        departure_raw="1/8",
        country_raw="Testland",
        costs=costs(total),
        flags=flags or [],
    )


class StubRepairer:
    def __init__(self, result):
        self._result = result
        self.calls = []

    def repair(self, block, report):
        self.calls.append(report.report_id)
        if callable(self._result):
            return self._result(report)
        return self._result


class TestNeedsRepair:
    def test_clean_report_does_not_need_repair(self):
        report = make_report(
            flags=[], travelers=[Traveler(name="A", segments=[dated_segment("100.00")])]
        )
        assert needs_repair(report) is False

    def test_layout_undetected_triggers_repair(self):
        report = make_report(flags=["LAYOUT_UNDETECTED"])
        assert needs_repair(report) is True

    def test_layout_low_confidence_triggers_repair(self):
        report = make_report(flags=["LAYOUT_LOW_CONFIDENCE"])
        assert needs_repair(report) is True

    def test_no_travelers_extracted_triggers_repair(self):
        report = make_report(flags=["NO_TRAVELERS_EXTRACTED"])
        assert needs_repair(report) is True

    def test_table_sum_mismatch_alone_does_not_trigger(self):
        report = make_report(
            flags=["TABLE_SUM_MISMATCH"],
            travelers=[Traveler(name="A", segments=[dated_segment("100.00")])],
        )
        assert needs_repair(report) is False

    def test_table_sum_mismatch_with_multiple_unparseable_cells_triggers(self):
        segments = [
            dated_segment("100.00", flags=["UNPARSEABLE_COST_CELL"]),
            dated_segment("50.00", flags=["UNPARSEABLE_COST_CELL"]),
        ]
        report = make_report(
            flags=["TABLE_SUM_MISMATCH"], travelers=[Traveler(name="A", segments=segments)]
        )
        assert needs_repair(report) is True


class TestApplyLlmFallback:
    def test_valid_repair_replaces_the_report(self, tmp_path):
        original = make_report(
            report_id="2019q1jan29-000",
            source_file="2019q1jan29.txt",
            table_index=0,
            flags=["LAYOUT_UNDETECTED"],
        )
        repaired = make_report(
            report_id="2019q1jan29-000",
            source_file="2019q1jan29.txt",
            table_index=0,
            travelers=[Traveler(name="A", segments=[dated_segment("100.00")])],
        )
        repairer = StubRepairer(repaired)
        result = apply_llm_fallback([original], repairer, report_text_dir=FIXTURES)

        assert result[0] is repaired
        assert "LLM_PARSED" in result[0].flags
        assert repairer.calls == ["2019q1jan29-000"]

    def test_repaired_traveler_gets_exact_member_match(self, tmp_path):
        """A repaired report's travelers have no way to know about members.csv on their
        own -- apply_llm_fallback must run the same exact-match pass the deterministic
        path does, or every LLM-repaired table permanently loses bioguide IDs."""
        original = make_report(
            report_id="2019q1jan29-000",
            source_file="2019q1jan29.txt",
            table_index=0,
            flags=["LAYOUT_UNDETECTED"],
        )
        repaired = make_report(
            report_id="2019q1jan29-000",
            source_file="2019q1jan29.txt",
            table_index=0,
            travelers=[Traveler(name="Hon. Jane Doe", segments=[dated_segment("100.00")])],
        )
        repairer = StubRepairer(repaired)
        member_index = {"HON. JANE DOE": "D000123"}

        result = apply_llm_fallback(
            [original], repairer, report_text_dir=FIXTURES, member_index=member_index
        )

        assert result[0].travelers[0].bioguide_id == "D000123"
        assert result[0].travelers[0].match_confidence == 1.0

    def test_repaired_traveler_with_honorific_split_into_its_own_field_still_matches(
        self, tmp_path
    ):
        """Some models return name='Jane Doe', honorific='Hon.' as separate fields instead
        of embedding the prefix in name like the deterministic pipeline does. Confirmed with
        a real model (glm-5.2:cloud): without accounting for this, exact match against
        members.csv's "HON. JANE DOE"-style keys always misses, silently losing every
        bioguide ID on every LLM-repaired table."""
        original = make_report(
            report_id="2019q1jan29-000",
            source_file="2019q1jan29.txt",
            table_index=0,
            flags=["LAYOUT_UNDETECTED"],
        )
        repaired = make_report(
            report_id="2019q1jan29-000",
            source_file="2019q1jan29.txt",
            table_index=0,
            travelers=[
                Traveler(name="Jane Doe", honorific="Hon.", segments=[dated_segment("100.00")])
            ],
        )
        repairer = StubRepairer(repaired)
        member_index = {"HON. JANE DOE": "D000123"}

        result = apply_llm_fallback(
            [original], repairer, report_text_dir=FIXTURES, member_index=member_index
        )

        assert result[0].travelers[0].bioguide_id == "D000123"
        assert result[0].travelers[0].match_confidence == 1.0

    def test_repaired_traveler_without_a_match_is_flagged_not_guessed(self, tmp_path):
        original = make_report(
            report_id="2019q1jan29-000",
            source_file="2019q1jan29.txt",
            table_index=0,
            flags=["LAYOUT_UNDETECTED"],
        )
        repaired = make_report(
            report_id="2019q1jan29-000",
            source_file="2019q1jan29.txt",
            table_index=0,
            travelers=[Traveler(name="Some Staffer", segments=[dated_segment("100.00")])],
        )
        repairer = StubRepairer(repaired)

        result = apply_llm_fallback([original], repairer, report_text_dir=FIXTURES)

        assert result[0].travelers[0].bioguide_id is None
        assert "MEMBER_UNMATCHED" in result[0].flags

    def test_repair_failing_invariants_keeps_original_and_flags_it(self, tmp_path):
        original = make_report(
            report_id="2019q1jan29-000",
            source_file="2019q1jan29.txt",
            table_index=0,
            flags=["LAYOUT_UNDETECTED"],
        )
        # LLM draft where the declared total doesn't match its own per-diem/transportation/
        # other components -> fails ROW_SUM_MISMATCH on validation.
        bad_segment = TravelSegment(
            arrival_date=date(2018, 1, 5),
            departure_date=date(2018, 1, 8),
            arrival_raw="1/5",
            departure_raw="1/8",
            country_raw="Testland",
            costs=Costs(
                per_diem=group(),
                transportation=group(),
                other=group(),
                total=group("999999.00"),
            ),
        )
        bad_repair = make_report(
            report_id="2019q1jan29-000",
            source_file="2019q1jan29.txt",
            table_index=0,
            travelers=[Traveler(name="A", segments=[bad_segment])],
        )
        repairer = StubRepairer(bad_repair)
        fail_report = tmp_path / "failures.json"
        result = apply_llm_fallback(
            [original], repairer, report_text_dir=FIXTURES, fail_report_path=fail_report
        )

        assert result[0] is original
        assert "LLM_UNVERIFIED" in result[0].flags
        assert fail_report.exists()
        failures = json.loads(fail_report.read_text())
        assert failures[0]["report_id"] == "2019q1jan29-000"

    def test_repairer_returning_none_leaves_original_untouched(self, tmp_path):
        original = make_report(
            report_id="2019q1jan29-000",
            source_file="2019q1jan29.txt",
            table_index=0,
            flags=["LAYOUT_UNDETECTED"],
        )
        repairer = StubRepairer(None)
        fail_report = tmp_path / "failures.json"
        result = apply_llm_fallback(
            [original], repairer, report_text_dir=FIXTURES, fail_report_path=fail_report
        )
        assert result[0] is original
        failures = json.loads(fail_report.read_text())
        assert failures[0]["reason"] == "repair_returned_none"

    def test_reports_not_needing_repair_are_never_sent_to_repairer(self):
        clean = make_report(
            flags=[], travelers=[Traveler(name="A", segments=[dated_segment("100.00")])]
        )
        repairer = StubRepairer(None)
        apply_llm_fallback([clean], repairer, report_text_dir=FIXTURES)
        assert repairer.calls == []

    def test_missing_source_file_records_failure_not_crash(self, tmp_path):
        original = make_report(
            report_id="missing-000",
            source_file="does-not-exist.txt",
            table_index=0,
            flags=["LAYOUT_UNDETECTED"],
        )
        repairer = StubRepairer(None)
        fail_report = tmp_path / "failures.json"
        result = apply_llm_fallback(
            [original], repairer, report_text_dir=tmp_path, fail_report_path=fail_report
        )
        assert result[0] is original
        assert repairer.calls == []  # never reached because the block couldn't be loaded


@pytest.mark.skipif(
    not os.environ.get("RUN_LLM_INTEGRATION_TESTS"),
    reason="set RUN_LLM_INTEGRATION_TESTS=1 for a live call to an `llm`-registered model "
    "(set OFT_LLM_TEST_MODEL to target a specific model, e.g. an Ollama model id)",
)
class TestLiveLlmIntegration:
    def test_live_repair_of_a_low_confidence_table(self):
        from official_foreign_travel.parsing.llm_fallback import DEFAULT_MODEL, LLMTableRepairer

        model_id = os.environ.get("OFT_LLM_TEST_MODEL", DEFAULT_MODEL)
        reports = assemble_file(FIXTURES / "2007q4nov13.txt")
        failing = next((r for r in reports if needs_repair(r)), None)
        if failing is None:
            pytest.skip("no low-confidence table in this fixture to repair")

        validate_report(failing)
        repairer = LLMTableRepairer(model_id=model_id)
        result = apply_llm_fallback([failing], repairer, report_text_dir=FIXTURES)
        assert result[0].parse_method in ("llm", "deterministic")
