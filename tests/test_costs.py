"""Tests for cost cell parsing."""

from decimal import Decimal

from official_foreign_travel.parsing.costs import (
    CostCell,
    CostGroup,
    Costs,
    merge_costs,
    parse_cost_cell,
    parse_footnote_map,
)


class TestParseFootnoteMap:
    def test_parses_standard_footnote_lines(self):
        lines = [
            r"\1\ Per diem constitutes lodging and meals.",
            r"\2\ If foreign currency is used, enter U.S. dollar equivalent.",
            r"\3\ Military air transportation.",
        ]
        result = parse_footnote_map(lines)
        assert result["1"] == "Per diem constitutes lodging and meals."
        assert result["3"] == "Military air transportation."

    def test_handles_no_space_after_marker(self):
        result = parse_footnote_map([r"\1\Per diem constitutes lodging and meals."])
        assert result["1"] == "Per diem constitutes lodging and meals."


class TestParseCostCell:
    def test_dotfill_is_empty(self):
        cell, flag = parse_cost_cell("...........")
        assert cell.amount is None
        assert flag is None

    def test_blank_is_empty(self):
        cell, flag = parse_cost_cell("   ")
        assert cell.amount is None
        assert flag is None

    def test_plain_amount(self):
        cell, flag = parse_cost_cell("537.38")
        assert cell.amount == Decimal("537.38")
        assert flag is None

    def test_amount_with_thousands_separator(self):
        cell, flag = parse_cost_cell("12,090.83")
        assert cell.amount == Decimal("12090.83")
        assert flag is None

    def test_footnote_prefixed_amount(self):
        cell, flag = parse_cost_cell(r"\3\138.00")
        assert cell.amount == Decimal("138.00")
        assert cell.footnotes == ["3"]
        assert flag is None

    def test_bare_footnote_marker_with_parens(self):
        footnote_map = {"3": "Military air transportation."}
        cell, flag = parse_cost_cell(r"(\3\)", footnote_map)
        assert cell.amount is None
        assert cell.footnotes == ["3"]
        assert cell.military_air is True
        assert flag is None

    def test_bare_footnote_after_html_tag_stripped(self):
        """'(<SUP>3)' becomes '(3)' once HTML tags are stripped upstream."""
        footnote_map = {"3": "Military air transportation."}
        cell, flag = parse_cost_cell("(3)", footnote_map)
        assert cell.amount is None
        assert cell.footnotes == ["3"]
        assert cell.military_air is True
        assert flag is None

    def test_non_military_footnote_not_flagged_as_military_air(self):
        footnote_map = {"4": "One-way."}
        cell, flag = parse_cost_cell(r"\4\ 484.95", footnote_map)
        assert cell.amount == Decimal("484.95")
        assert cell.military_air is False

    def test_unparseable_text_is_flagged(self):
        cell, flag = parse_cost_cell("N/A")
        assert cell.amount is None
        assert flag == "UNPARSEABLE_COST_CELL"


def _cost_cell(amount):
    return CostCell(amount=Decimal(amount) if amount is not None else None, raw=str(amount))


def _costs(total_usd):
    empty = _cost_cell(None)
    return Costs(
        per_diem=CostGroup(foreign_currency=empty, us_dollar=empty),
        transportation=CostGroup(foreign_currency=empty, us_dollar=empty),
        other=CostGroup(foreign_currency=empty, us_dollar=empty),
        total=CostGroup(foreign_currency=empty, us_dollar=_cost_cell(total_usd)),
    )


class TestMergeCosts:
    def test_merges_amounts(self):
        merged = merge_costs(_costs("100.00"), _costs("50.00"))
        assert merged.total.us_dollar.amount == Decimal("150.00")

    def test_both_none_stays_none(self):
        merged = merge_costs(_costs(None), _costs(None))
        assert merged.total.us_dollar.amount is None

    def test_one_none_keeps_the_other(self):
        merged = merge_costs(_costs("100.00"), _costs(None))
        assert merged.total.us_dollar.amount == Decimal("100.00")
