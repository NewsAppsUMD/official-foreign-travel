"""Golden values for tables that exposed layout-boundary bugs.

The 1994q1feb10 Energy & Commerce table has right-justified amounts of mixed
widths plus dot-filled empties -- the exact shape that made the old
token-start refinement truncate digits (per diem parsed as 79.00 instead of
2,079.00) and collide boundaries (transportation/total swallowed entirely).
Values below are read directly from the raw fixture text.
"""

from decimal import Decimal
from pathlib import Path

from official_foreign_travel.parsing.assemble import assemble_file

FIXTURES = Path(__file__).parent / "fixtures"


class TestEnergyCommerce1994Goldens:
    def _report(self):
        reports = assemble_file(FIXTURES / "1994q1feb10_energy.txt")
        assert len(reports) == 1
        return reports[0]

    def test_wide_amounts_are_not_truncated(self):
        report = self._report()
        finnegan = report.travelers[0]
        assert finnegan.name == "Mr. David Finnegan"
        seg = finnegan.segments[0]
        assert seg.costs.per_diem.us_dollar.amount == Decimal("2079.00")

    def test_transportation_and_total_are_not_swallowed(self):
        report = self._report()
        seg = report.travelers[0].segments[0]
        assert seg.costs.transportation.us_dollar.amount == Decimal("3049.45")
        assert seg.costs.total.us_dollar.amount == Decimal("5128.45")

    def test_multi_segment_traveler_amounts(self):
        report = self._report()
        endres = next(t for t in report.travelers if "Endres" in t.name)
        amounts = [s.costs.per_diem.us_dollar.amount for s in endres.segments]
        assert amounts == [
            Decimal("467.00"),
            Decimal("398.00"),
            Decimal("592.00"),
            Decimal("621.00"),
        ]
        # The Spain leg carries the transportation charge.
        assert endres.segments[-1].costs.transportation.us_dollar.amount == Decimal(
            "3461.45"
        )

    def test_committee_total_row(self):
        report = self._report()
        total = report.committee_total
        assert total is not None
        assert total.per_diem.us_dollar.amount == Decimal("9826.00")
        assert total.transportation.us_dollar.amount == Decimal("16868.50")
        assert total.total.us_dollar.amount == Decimal("26694.50")