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

    def test_bare_currency_name_is_label_not_flagged(self):
        """A bare currency name ('English', 'Euro', 'Zloty', 'Irish pound') is
        the source's labeling convention for "this leg was paid in <currency>"
        when only the U.S. dollar equivalent is reported. It is not a parse
        error or a value to recover."""
        cell, flag = parse_cost_cell("English")
        assert cell.amount is None
        assert flag is None

    def test_bare_currency_name_euro_is_label_not_flagged(self):
        cell, flag = parse_cost_cell("Euro")
        assert cell.amount is None
        assert flag is None

    def test_bare_currency_name_two_word_is_label_not_flagged(self):
        """Two-word currency names like 'Irish pound' are recognized."""
        cell, flag = parse_cost_cell("Irish pound")
        assert cell.amount is None
        assert flag is None

    def test_bare_currency_name_zloty_is_label_not_flagged(self):
        cell, flag = parse_cost_cell("Zloty")
        assert cell.amount is None
        assert flag is None

    def test_garbage_text_still_flagged(self):
        """Genuinely unparseable text that isn't a recognized currency label
        is still flagged for review."""
        cell, flag = parse_cost_cell("Xxxxxxxxxxx")
        assert cell.amount is None
        assert flag == "UNPARSEABLE_COST_CELL"

    def test_currency_prefixed_french_franc(self):
        cell, flag = parse_cost_cell("FF4,733.91")
        assert cell.amount == Decimal("4733.91")
        assert flag is None

    def test_currency_prefixed_deutsche_mark(self):
        cell, flag = parse_cost_cell("DM1,462.55")
        assert cell.amount == Decimal("1462.55")
        assert flag is None

    def test_currency_prefixed_swedish_krona(self):
        cell, flag = parse_cost_cell("SEK1,507.50")
        assert cell.amount == Decimal("1507.50")
        assert flag is None

    def test_single_letter_currency_prefix(self):
        cell, flag = parse_cost_cell("L865,576")
        assert cell.amount == Decimal("865576")
        assert flag is None

    def test_dollar_sign_prefix(self):
        cell, flag = parse_cost_cell("$315.00")
        assert cell.amount == Decimal("315.00")
        assert flag is None

    def test_european_thousands_separators(self):
        cell, flag = parse_cost_cell("5.723.37")
        assert cell.amount == Decimal("5723.37")
        assert flag is None

    def test_currency_prefix_with_european_thousands(self):
        cell, flag = parse_cost_cell("FF5.723.37")
        assert cell.amount == Decimal("5723.37")
        assert flag is None

    def test_dashes_are_empty(self):
        cell, flag = parse_cost_cell("--")
        assert cell.amount is None
        assert flag is None

    def test_value_with_trailing_dots(self):
        cell, flag = parse_cost_cell("462.00  ..")
        assert cell.amount == Decimal("462.00")
        assert flag is None

    def test_currency_prefix_with_trailing_dots(self):
        cell, flag = parse_cost_cell("FF4,733.91  ..")
        assert cell.amount == Decimal("4733.91")
        assert flag is None

    def test_symbolic_footnote_double_star_is_empty(self):
        """'**' is a whole-cell symbolic footnote ('Cancelled mission' per source)."""
        cell, flag = parse_cost_cell("**")
        assert cell.amount is None
        assert cell.footnotes == ["**"]
        assert flag is None

    def test_symbolic_footnote_triple_star_is_empty(self):
        cell, flag = parse_cost_cell("***")
        assert cell.amount is None
        assert cell.footnotes == ["***"]
        assert flag is None

    def test_symbolic_footnote_paren_star_is_empty(self):
        cell, flag = parse_cost_cell("(*)")
        assert cell.amount is None
        assert cell.footnotes == ["*"]
        assert flag is None

    def test_na_is_empty_not_flagged(self):
        cell, flag = parse_cost_cell("N/A")
        assert cell.amount is None
        assert flag is None

    def test_lowercase_na_is_empty_not_flagged(self):
        cell, flag = parse_cost_cell("n/a")
        assert cell.amount is None
        assert flag is None

    def test_bare_na_is_empty_not_flagged(self):
        cell, flag = parse_cost_cell("NA")
        assert cell.amount is None
        assert flag is None

    def test_none_is_empty_not_flagged(self):
        cell, flag = parse_cost_cell("None")
        assert cell.amount is None
        assert flag is None

    def test_zero_zero_dash_is_empty(self):
        cell, flag = parse_cost_cell("-0-")
        assert cell.amount is None
        assert flag is None

    def test_single_dash_is_empty(self):
        """A single '-' is the same dot/dash fill convention as '--'."""
        cell, flag = parse_cost_cell("-")
        assert cell.amount is None
        assert flag is None

    def test_milair_marker_is_military_air_empty(self):
        """'Milair' is the source's shorthand for military air transport."""
        cell, flag = parse_cost_cell("Milair\\3\\")
        assert cell.amount is None
        assert cell.military_air is True
        assert cell.footnotes == ["3"]
        assert flag is None

    def test_paren_footnote_with_trailing_dots_is_empty(self):
        """'(\\3\\)  ..' -- footnote marker wrapped in parens plus dotfill."""
        cell, flag = parse_cost_cell("(\\3\\)  ..")
        assert cell.amount is None
        assert cell.footnotes == ["3"]
        assert flag is None

    def test_paren_footnote_with_amount_parses(self):
        """'(\\3\\) 496.1' -- parens-wrapped footnote marker plus truncated amount."""
        footnote_map = {"3": "Military air transportation."}
        cell, flag = parse_cost_cell("(\\3\\) 496.1", footnote_map)
        assert cell.amount == Decimal("496.1")
        assert cell.footnotes == ["3"]
        assert cell.military_air is True
        assert flag is None

    def test_bare_paren_footnote_with_amount_parses(self):
        """'(3) 620.00' -- HTML-stripped bare paren footnote plus amount."""
        cell, flag = parse_cost_cell("(3) 620.00")
        assert cell.amount == Decimal("620.00")
        assert cell.footnotes == ["3"]
        assert flag is None

    def test_asterisk_prefix_amount_parses(self):
        """'* 2,443.46' -- leading asterisk symbolic footnote plus amount."""
        cell, flag = parse_cost_cell("* 2,443.46")
        assert cell.amount == Decimal("2443.46")
        assert cell.footnotes == ["*"]
        assert flag is None

    def test_double_asterisk_prefix_amount_parses(self):
        cell, flag = parse_cost_cell("** 1,001.67")
        assert cell.amount == Decimal("1001.67")
        assert cell.footnotes == ["**"]
        assert flag is None

    def test_asterisk_prefix_no_space_parses(self):
        cell, flag = parse_cost_cell("*2,944.00")
        assert cell.amount == Decimal("2944.00")
        assert cell.footnotes == ["*"]
        assert flag is None

    def test_three_space_separated_asterisks_parses(self):
        """'* * * 234.22' -- three space-separated asterisk markers, each a
        separate footnote reference. 2005q3jul26-013 Freeman Thailand.
        Without the iterative strip, only the first `*` was removed and the
        cell was flagged UNPARSEABLE_COST_CELL."""
        cell, flag = parse_cost_cell("* * * 234.22")
        assert cell.amount == Decimal("234.22")
        assert cell.footnotes == ["*", "*", "*"]
        assert flag is None

    def test_two_space_separated_asterisks_parses(self):
        """'* * 100.00' -- two space-separated asterisk markers."""
        cell, flag = parse_cost_cell("* * 100.00")
        assert cell.amount == Decimal("100.00")
        assert cell.footnotes == ["*", "*"]
        assert flag is None

    def test_trailing_asterisk_amount_parses(self):
        """'12,597.90*' -- trailing symbolic asterisk after an amount."""
        cell, flag = parse_cost_cell("12,597.90*")
        assert cell.amount == Decimal("12597.90")
        assert cell.footnotes == ["*"]
        assert flag is None

    def test_leading_digit_footnote_parses(self):
        """'4 6,912.00' -- leading single-digit footnote marker (no backslashes)."""
        cell, flag = parse_cost_cell("4 6,912.00")
        assert cell.amount == Decimal("6912.00")
        assert cell.footnotes == ["4"]
        assert flag is None

    def test_trailing_currency_code_strips(self):
        """'191,590 CFA' -- trailing 3-letter currency code after an amount."""
        cell, flag = parse_cost_cell("191,590 CFA")
        assert cell.amount == Decimal("191590")
        assert flag is None

    def test_trailing_currency_name_strips(self):
        """'722.55 euro' -- trailing lowercase currency name."""
        cell, flag = parse_cost_cell("722.55 euro")
        assert cell.amount == Decimal("722.55")
        assert flag is None

    def test_trailing_currency_no_space_strips(self):
        """'235.32Ls' -- trailing currency code with no space."""
        cell, flag = parse_cost_cell("235.32Ls")
        assert cell.amount == Decimal("235.32")
        assert flag is None

    def test_long_currency_prefix_strips(self):
        """'Euro237.80' -- 4-letter currency name prefix before an amount."""
        cell, flag = parse_cost_cell("Euro237.80")
        assert cell.amount == Decimal("237.80")
        assert flag is None

    def test_slash_decimal_typo_parses(self):
        """'1,484/00' -- slash instead of period in decimal."""
        cell, flag = parse_cost_cell("1,484/00")
        assert cell.amount == Decimal("1484.00")
        assert flag is None

    def test_space_decimal_typo_parses(self):
        """'27,368. 74' -- stray space inside decimal part."""
        cell, flag = parse_cost_cell("27,368. 74")
        assert cell.amount == Decimal("27368.74")
        assert flag is None

    def test_dollar_space_decimal_typo_parses(self):
        cell, flag = parse_cost_cell("$27,368. 74")
        assert cell.amount == Decimal("27368.74")
        assert flag is None

    def test_parenthesized_amount_is_negative(self):
        """'(7.48)' -- accounting-style parenthesized negative."""
        cell, flag = parse_cost_cell("(7.48)")
        assert cell.amount == Decimal("-7.48")
        assert flag is None

    def test_parenthesized_amount_with_commas_is_negative(self):
        cell, flag = parse_cost_cell("(325.32)")
        assert cell.amount == Decimal("-325.32")
        assert flag is None

    def test_lowercase_o_decimal_typo_parses(self):
        """'394.oo' -- lowercase-o typo for '394.00'."""
        cell, flag = parse_cost_cell("394.oo")
        assert cell.amount == Decimal("394.00")
        assert flag is None

    def test_trailing_brace_typo_strips(self):
        """'5,133.00}' -- stray trailing brace."""
        cell, flag = parse_cost_cell("5,133.00}")
        assert cell.amount == Decimal("5133.00")
        assert flag is None

    def test_currency_name_alone_is_label_not_flagged(self):
        """A bare currency name ('Euro', 'Zloty') is the source's labeling
        convention, not column misalignment. Returns empty with no flag."""
        cell, flag = parse_cost_cell("Euro")
        assert cell.amount is None
        assert flag is None

    def test_military_label_is_military_air_empty(self):
        """'Military' / 'Military air' standalone in a transportation cell
        marks military-air transport with no commercial cost."""
        for text in ("Military", "Military air", "MILITARY AIR"):
            cell, flag = parse_cost_cell(text)
            assert cell.amount is None, f"failed for {text!r}"
            assert cell.military_air is True, f"failed for {text!r}"
            assert flag is None, f"failed for {text!r}"

    def test_symbolic_backslash_star_footnote_marker(self):
        """'\\*\\18,340.2' -- symbolic footnote marker (star between backslashes)
        stripped, amount parsed, '*' recorded as footnote."""
        cell, flag = parse_cost_cell(r"\*\18,340.2")
        assert cell.amount == Decimal("18340.2")
        assert cell.footnotes == ["*"]
        assert flag is None

    def test_four_asterisk_prefix_amount_parses(self):
        """'**** 1,606.10' -- 4-asterisk variant of the cancelled-mission '**'
        marker, followed by an amount."""
        cell, flag = parse_cost_cell("**** 1,606.10")
        assert cell.amount == Decimal("1606.10")
        assert cell.footnotes == ["****"]
        assert flag is None

    def test_trailing_minus_is_negative(self):
        """'1,060.00-' -- accounting-style negative written with trailing dash."""
        cell, flag = parse_cost_cell("1,060.00-")
        assert cell.amount == Decimal("-1060.00")
        assert flag is None

    def test_leading_dots_before_amount_parses(self):
        """'..       287.' -- leading dot-fill residue before a truncated amount."""
        cell, flag = parse_cost_cell("..       287.")
        assert cell.amount == Decimal("287")
        assert flag is None

    def test_leading_dots_with_comma_amount_parses(self):
        """'.     1,450.' -- single leading dot before a thousands-sep amount."""
        cell, flag = parse_cost_cell(".     1,450.")
        assert cell.amount == Decimal("1450")
        assert flag is None

    def test_trailing_bracket_typo_strips(self):
        """'41.00]' -- stray trailing bracket typo."""
        cell, flag = parse_cost_cell("41.00]")
        assert cell.amount == Decimal("41.00")
        assert flag is None

    def test_leading_bang_typo_strips(self):
        """'!1,288.28' -- leading exclamation typo before an amount."""
        cell, flag = parse_cost_cell("!1,288.28")
        assert cell.amount == Decimal("1288.28")
        assert flag is None

    def test_dotfill_with_trailing_whitespace_is_empty(self):
        """'...........  ' -- dot-fill with trailing whitespace, no flag."""
        cell, flag = parse_cost_cell("...........  ")
        assert cell.amount is None
        assert flag is None

    def test_dotfill_with_trailing_backslash_is_empty(self):
        """'...........  \\' -- dot-fill with trailing backslash residue."""
        cell, flag = parse_cost_cell("...........  \\")
        assert cell.amount is None
        assert flag is None

    def test_dotfill_with_trailing_asterisk_records_footnote(self):
        """'...........  *' -- dot-fill with symbolic asterisk marker."""
        cell, flag = parse_cost_cell("...........  *")
        assert cell.amount is None
        assert flag is None
        assert "*" in cell.footnotes

    def test_dotfill_with_mixed_dots_and_spaces_is_empty(self):
        """'.........  .' -- dots + spaces + dot, all dot-fill residue."""
        cell, flag = parse_cost_cell(".........  .")
        assert cell.amount is None
        assert flag is None

    def test_dotfill_chain_from_supplement_merge_is_empty(self):
        """'........... + ...........' -- merged dot-fill cells."""
        cell, flag = parse_cost_cell("........... + ...........")
        assert cell.amount is None
        assert flag is None

    def test_dotfill_chain_with_backslash_residue_is_empty(self):
        """'...........  \\ + ...........  .' -- merged dot-fill with residue."""
        cell, flag = parse_cost_cell(
            "...........  \\ + ...........  . + ...........  . + ...........  . + ...........  ."
        )
        assert cell.amount is None
        assert flag is None

    def test_amount_with_trailing_dots_after_asterisk_parses(self):
        """'   732.00*  ..' -- amount + asterisk marker + trailing dot residue."""
        cell, flag = parse_cost_cell("   732.00*  ..")
        assert cell.amount == Decimal("732.00")
        assert flag is None
        assert "*" in cell.footnotes

    def test_incomplete_footnote_marker_with_backslash_parses(self):
        """'3\\ -700.00' -- footnote marker '3\\' (missing leading backslash)."""
        cell, flag = parse_cost_cell("3\\ -700.00  ")
        assert cell.amount == Decimal("-700.00")
        assert flag is None
        assert "3" in cell.footnotes

    def test_footnote_marker_with_1a_residue_parses(self):
        """'\\4\\1A184.00' -- footnote marker + '1A' layout residue + amount."""
        cell, flag = parse_cost_cell("\\4\\1A184.00  ")
        assert cell.amount == Decimal("184.00")
        assert flag is None
        assert "4" in cell.footnotes

    def test_footnote_marker_with_1a_residue_and_commas_parses(self):
        """'\\4\\1A5,018.' -- footnote marker + '1A' residue + amount with comma."""
        cell, flag = parse_cost_cell("\\4\\1A5,018.  ")
        assert cell.amount == Decimal("5018")
        assert flag is None
        assert "4" in cell.footnotes

    def test_1a_residue_without_footnote_not_stripped(self):
        """'1A184.00' without a preceding footnote marker is not '1A'-stripped --
        the '1A' might be a real (if unusual) cell prefix."""
        cell, flag = parse_cost_cell("1A184.00")
        # Without a footnote marker, '1A' is not recognized as residue.
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


class TestMergeCostCellWrapDigit:
    """merge_cost_cell detects when the second cell is a wrapped decimal
    fragment of the first (source line break split the decimal part onto the
    next line in the same cost column) and concatenates instead of summing.

    Without this, `\\3\\ 12,785.` + `48` would sum to `12,833.00` instead of
    `12,785.48`, and `234.2` + `2` would sum to `236.20` instead of `234.22`.
    """

    def _merge(self, a_amount, a_raw, b_amount, b_raw):
        from official_foreign_travel.parsing.costs import merge_cost_cell

        a = CostCell(amount=Decimal(a_amount) if a_amount is not None else None, raw=a_raw)
        b = CostCell(amount=Decimal(b_amount) if b_amount is not None else None, raw=b_raw)
        return merge_cost_cell(a, b)

    def test_one_digit_wrap_concats_as_next_decimal(self):
        # 2005q3jul26-013 Freeman: `* * * 234.2` + `2` -> `234.22`
        merged = self._merge("234.2", "* * * 234.2", "2", "2")
        assert merged.amount == Decimal("234.22")
        assert merged.raw == "* * * 234.22"

    def test_two_digit_wrap_concats_as_decimal_part(self):
        # 2000q2jun20-000 Gilman: `\3\ 12,785.` + `48` -> `12,785.48`
        merged = self._merge("12785", r"\3\ 12,785.", "48", "48")
        assert merged.amount == Decimal("12785.48")
        assert merged.raw == r"\3\ 12,785.48"

    def test_one_digit_wrap_zero_stays_same_amount(self):
        # `1,573.0` + `0` -> `1,573.00` (numerically equal, raw normalizes)
        merged = self._merge("1573.0", "1,573.0", "0", "0")
        assert merged.amount == Decimal("1573.00")
        assert merged.raw == "1,573.00"

    def test_two_digit_wrap_zero_stays_same_amount(self):
        # `13,979.` + `00` -> `13,979.00`
        merged = self._merge("13979", "13,979.", "0", "00")
        assert merged.amount == Decimal("13979.00")

    def test_two_decimal_prior_does_not_wrap(self):
        # `1,573.45` + `5` is a supplement, not a wrap (prior already has 2 decimals)
        merged = self._merge("1573.45", "1,573.45", "5", "5")
        assert merged.amount == Decimal("1578.45")
        assert " + " in merged.raw

    def test_one_digit_prior_with_two_digit_b_does_not_wrap(self):
        # `234.2` + `45` -- mismatched digit counts, not a recognized wrap pattern.
        # Falls through to sum (treats `45` as a $45 supplement).
        merged = self._merge("234.2", "234.2", "45", "45")
        assert merged.amount == Decimal("279.20")

    def test_no_decimal_prior_does_not_wrap(self):
        # `5,238` + `45` -- no decimal point in prior, can't be a wrap.
        merged = self._merge("5238", "5,238", "45", "45")
        assert merged.amount == Decimal("5283.00")

    def test_supplement_with_full_amount_does_not_wrap(self):
        # `1,000.00` + `50.00` is a normal supplement merge.
        merged = self._merge("1000.00", "1,000.00", "50.00", "50.00")
        assert merged.amount == Decimal("1050.00")
        assert merged.raw == "1,000.00 + 50.00"

    def test_wrap_preserves_footnotes(self):
        # parse_cost_cell extracts the `3` footnote from `\3\ 12,785.`; merge
        # must carry it onto the wrapped cell.
        a, _ = parse_cost_cell(r"\3\ 12,785.", {})
        b, _ = parse_cost_cell("48", {})
        from official_foreign_travel.parsing.costs import merge_cost_cell

        merged = merge_cost_cell(a, b)
        assert merged.amount == Decimal("12785.48")
        assert "3" in merged.footnotes

    def test_wrap_with_none_amount_falls_through(self):
        # If a.amount is None (unparseable prior), wrap-concat can't apply.
        # Falls through to existing logic (None + b -> b).
        merged = self._merge(None, r"\4\ 65.17.3", "4", "4")
        # Allen Thompson case: a is unparseable (two decimals), b is `4`
        assert merged.amount == Decimal("4")
