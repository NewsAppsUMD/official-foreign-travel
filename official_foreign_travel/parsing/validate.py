"""Arithmetic and date invariant checks. Flags problems; never drops records."""

from decimal import Decimal
from typing import Optional

from ..models.report import CostCell, CostGroup, Costs, Report, TravelSegment
from .dates import recover_empty_dates

DEFAULT_TOLERANCE = Decimal("0.02")


def _group_total(costs: Costs) -> Decimal:
    """Sum the per-category US-dollar amounts (foreign-currency cells are informational only)."""
    groups = (costs.per_diem, costs.transportation, costs.other)
    return sum((g.us_dollar.amount for g in groups if g.us_dollar.amount is not None), Decimal("0"))


def _fc_group_total(costs: Costs) -> Decimal:
    """Sum the per-category foreign-currency amounts."""
    groups = (costs.per_diem, costs.transportation, costs.other)
    return sum(
        (g.foreign_currency.amount for g in groups if g.foreign_currency.amount is not None),
        Decimal("0"),
    )


def _amount_or_zero(cell: CostCell) -> Decimal:
    return cell.amount if cell.amount is not None else Decimal("0")


def _infer_committee_total_from_segments(
    all_segments: list[TravelSegment],
) -> Optional[Costs]:
    """Synthesize a committee-total ``Costs`` from the per-segment cost
    cells when the source table had no total row (``committee_total is
    None``). Each US-dollar cell (per_diem / transportation / other /
    total) is the sum of the corresponding seg cells, with
    ``computed=True`` so callers can distinguish "inferred from segments"
    from "declared by the source." Returns None when no segment has any
    cost data -- in that case there's nothing to infer and the report
    stays ``MISSING_COMMITTEE_TOTAL``.
    """
    pd_sum = sum(
        (_amount_or_zero(s.costs.per_diem.us_dollar) for s in all_segments),
        Decimal("0"),
    )
    tr_sum = sum(
        (_amount_or_zero(s.costs.transportation.us_dollar) for s in all_segments),
        Decimal("0"),
    )
    ot_sum = sum(
        (_amount_or_zero(s.costs.other.us_dollar) for s in all_segments),
        Decimal("0"),
    )
    total_sum = sum(
        (
            (s.costs.total.us_dollar.amount if s.costs.total.us_dollar.amount is not None else Decimal("0"))
            for s in all_segments
        ),
        Decimal("0"),
    )
    if pd_sum == 0 and tr_sum == 0 and ot_sum == 0 and total_sum == 0:
        return None

    def _cell(amount: Decimal) -> CostCell:
        return CostCell(amount=amount, raw="", computed=True)

    return Costs(
        per_diem=CostGroup(foreign_currency=CostCell(raw=""), us_dollar=_cell(pd_sum)),
        transportation=CostGroup(foreign_currency=CostCell(raw=""), us_dollar=_cell(tr_sum)),
        other=CostGroup(foreign_currency=CostCell(raw=""), us_dollar=_cell(ot_sum)),
        total=CostGroup(foreign_currency=CostCell(raw=""), us_dollar=_cell(total_sum)),
    )


def _table_rounding_threshold(total: Decimal) -> Decimal:
    """Same threshold the TABLE_SUM_ROUNDING classifier uses: the smaller of
    $5 or 1% of the total. Used to gate the COMMITTEE_TOTAL_COMPUTED recovery
    so that small rounding-noise diffs fall through to TABLE_SUM_ROUNDING."""
    return min(TABLE_SUM_ROUNDING_ABSOLUTE, abs(total) * TABLE_SUM_ROUNDING_PERCENT)


def _delta_matches_segment_component(
    delta: Decimal, all_segments: list[TravelSegment], tolerance: Decimal
) -> bool:
    """True when |delta| exactly matches one segment's per_diem,
    transportation, other, or total amount -- the TABLE_SUM_COMPONENT_DELTA
    pattern (source intentionally excluded or double-counted that component).
    Used to gate the COMMITTEE_TOTAL_COMPUTED recovery so the more specific
    COMPONENT_DELTA classifier wins."""
    abs_delta = abs(delta)
    for seg in all_segments:
        for group in (seg.costs.per_diem, seg.costs.transportation, seg.costs.other, seg.costs.total):
            amt = group.us_dollar.amount
            if amt is not None and amt > 0 and abs(abs_delta - amt) <= tolerance:
                return True
    return False


def _per_diem_times_days_match(
    segment: TravelSegment, declared_total: Decimal, tolerance: Decimal
) -> bool:
    """True when the source total equals per_diem × days-in-segment, with only
    per_diem populated among the cost components.

    A recognizable source convention: the "per_diem" column is per-day, and
    the source computes the segment total as per_diem × (departure -
    arrival).days without breaking the multiplication into a separate
    component. E.g. Gingrich's 1997 China segment: per_diem=$255,
    3/27→3/30 (3 days), total=$765 = $255 × 3. Transportation and other
    are empty (often DoD-provided transport, which is why no component
    breaks out the extra amount).

    This is NOT a double-count (the source didn't add per_diem to itself --
    it multiplied per_diem by the day count) and NOT a mismatch (the
    declared total is correct under the convention). The double-count
    recovery would otherwise misfire on 2-day segments where
    per_diem × (days-1) = per_diem × 1 = per_diem, indistinguishable from
    a per_diem double-count without the day-count check.

    Requires both arrival and departure dates to be present. The
    arithmetic gate protects against wrong inferred dates: per_diem ×
    wrong_days won't match the declared total, so the check simply
    declines. Also verifies the foreign-currency side, when both foreign
    cells are populated, to rule out a USD-side coincidence.
    """
    pd = segment.costs.per_diem.us_dollar.amount
    tr_amt = segment.costs.transportation.us_dollar.amount
    ot_amt = segment.costs.other.us_dollar.amount
    if pd is None or pd <= 0 or tr_amt is not None or ot_amt is not None:
        return False
    arr = segment.arrival_date
    dep = segment.departure_date
    if arr is None or dep is None:
        return False
    days = (dep - arr).days
    if days <= 1:
        # days==1 → total==per_diem (no mismatch reaches this check);
        # days==0 → total would be 0, can't match a positive declared total.
        return False
    expected = pd * days
    if abs(declared_total - expected) > tolerance:
        return False
    fpd = segment.costs.per_diem.foreign_currency.amount
    ftot = segment.costs.total.foreign_currency.amount
    if fpd is not None and fpd > 0 and ftot is not None:
        fx_expected = fpd * days
        # Looser tolerance for FX rounding (exchange rates produce cents-level noise)
        fx_tolerance = max(tolerance * 10, abs(fx_expected) * Decimal("0.01"))
        if abs(ftot - fx_expected) > fx_tolerance:
            return False
    return True


def _find_trip_total_segments(report: Report, tolerance: Decimal) -> set[int]:
    """Identify segments carrying a source-declared trip total, keyed by id().

    A recognizable source convention: a traveler has 2+ segments, every
    segment has only per_diem populated (transport and other empty on the
    USD side; foreign-currency is informational only), and exactly one
    segment has a source-declared (non-computed) total equal to the sum of
    per_diems across all the traveler's segments. The source is filling
    the trip total once per traveler -- in either the first or last
    segment -- rather than a per-segment total. E.g. 1995q4dec13-005:
    each traveler has France (per_diem=834.46, no total) and Belgium
    (per_diem=606.00, total=1440.46); 834.46 + 606.00 = 1440.46.

    Without recovery, the segment carrying the trip total gets flagged
    ROW_SUM_MISMATCH (its per_diem alone doesn't sum to the trip total);
    the other segments are already ROW_TOTAL_COMPUTED via the source-
    omitted path. The recovery overwrites the trip-total segment's total
    with its own per_diem and preserves the source trip total in
    `source_amount`, so per-segment consumers see a per-segment total
    while the trip total is retained for traceability and the table-level
    sum check (the committee total equals the sum of trip totals, which
    equals the sum of all per_diems, which equals the post-recovery sum
    of segment totals).

    Returns id() of the segment carrying the trip total for each
    traveler whose segments fit the shape.
    """
    out: set[int] = set()
    for traveler in report.travelers:
        segs = traveler.segments
        if len(segs) < 2:
            continue
        pds = [s.costs.per_diem.us_dollar.amount for s in segs]
        if any(p is None or p <= 0 for p in pds):
            continue
        if any(
            s.costs.transportation.us_dollar.amount is not None
            or s.costs.other.us_dollar.amount is not None
            for s in segs
        ):
            continue
        declared = [
            s
            for s in segs
            if s.costs.total.us_dollar.amount is not None
            and not s.costs.total.us_dollar.computed
        ]
        if len(declared) != 1:
            continue
        seg = declared[0]
        if abs(seg.costs.total.us_dollar.amount - sum(pds, Decimal("0"))) > tolerance:
            continue
        out.add(id(seg))
    return out


def _source_double_counted_component(diff: Decimal, costs: Costs, tolerance: Decimal) -> bool:
    """True when the source total exceeds the component sum by exactly one component.

    A declared total that is bigger than the sum of components by an amount
    equal to a single component (within tolerance) is a strong signal that the
    source double-counted that component -- e.g. a 1997 Korea trip where every
    row has per_diem=305 and total=610 (the source added per_diem to itself).
    The component sum is the true total; recovery overwrites with computed.

    Only `diff > 0` (declared exceeds computed) qualifies. The inverse --
    `diff < 0`, source total excludes a component -- is overwhelmingly an
    intentional source convention (military airfare listed separately,
    per_diem reimbursed via a different mechanism), not a source error, and
    is left flagged.
    """
    if diff <= 0:
        return False
    abs_diff = abs(diff)
    for group in (costs.per_diem, costs.transportation, costs.other):
        amt = group.us_dollar.amount
        if amt is not None and amt > 0 and abs(abs_diff - amt) <= tolerance:
            return True
    return False


def _is_rounding_delta(diff: Decimal, declared_total: Decimal, tolerance: Decimal) -> bool:
    """True when |diff| is within the rounding threshold: min($5.00, 1% of
    the declared total), mirroring the table-level `TABLE_SUM_ROUNDING` rule.

    A small residual delta after the specific recoveries (supplement-merge,
    trip-total, per_diem × days, double-count) have all declined is
    overwhelmingly source rounding or a small typo in the source document
    -- not a genuine arithmetic error. The source-declared total is kept
    as-is (consumers care about the source's stated amount) and the
    segment is flagged `ROW_SUM_ROUNDING` (informational) so downstream
    consumers can distinguish "small noise" from "genuine mismatch".

    The `max(tolerance, ...)` floor matches the table-level rule: when the
    percent-based threshold shrinks below the absolute tolerance for tiny
    totals, the tolerance governs.
    """
    abs_total = abs(declared_total) if declared_total is not None else Decimal("0")
    rounding_threshold = min(TABLE_SUM_ROUNDING_ABSOLUTE, abs_total * TABLE_SUM_ROUNDING_PERCENT)
    return abs(diff) <= max(tolerance, rounding_threshold)


def _is_100x_typo(declared_total: Decimal, computed: Decimal, segment: TravelSegment) -> bool:
    """True when declared_total == computed × 100 exactly, for a
    single-component segment.

    A recurring source typo: the writer used a comma where a decimal point
    should be (e.g. per_diem=`1,204.00`, total=`1,204,00` which the parser
    reads as 120400). The intended total equals the single component
    amount; recovery overwrites with the component value and preserves
    the source-declared total in `source_amount` for traceability.

    Only single-component segments qualify: a multi-component segment
    with a 100× total would be a different kind of error, not the
    comma-decimal typo. The exact-100× gate is tight enough that
    coincidental hits are negligible.
    """
    if computed is None or computed <= 0:
        return False
    if declared_total is None:
        return False
    pd = segment.costs.per_diem.us_dollar.amount
    tr_amt = segment.costs.transportation.us_dollar.amount
    ot_amt = segment.costs.other.us_dollar.amount
    components = [x for x in (pd, tr_amt, ot_amt) if x is not None and x > 0]
    if len(components) != 1:
        return False
    return declared_total == computed * 100


def _row_total_typo_divisor(
    declared_total: Decimal, computed: Decimal, segment: TravelSegment
) -> Optional[Decimal]:
    """Return the divisor (100 or 1000) when the segment's total cell has a
    comma-as-decimal typo where dividing the declared total by the divisor
    exactly equals the component sum.

    Extends `_is_100x_typo` (which only handles single-component segments
    where `declared_total == computed * 100`) to multi-component segments.
    The total cell's `raw` must have the comma-as-decimal shape (no decimal
    point, comma-separated, last group 2 or 3 digits -- e.g. `2,345,36` →
    divisor 100, `7,202,000` → divisor 1000), and `declared_total / divisor`
    must exactly equal the component sum (`computed`).

    The raw-shape gate (in addition to the exact-arithmetic gate) makes
    coincidental hits negligible for the multi-component case, where
    arithmetic alone is a weaker signal than for single-component
    segments. Single-component segments with typo-shaped raws continue to
    be recovered by `_is_100x_typo` first (which doesn't require the raw
    shape); this helper only fires for the multi-component case that
    `_is_100x_typo` explicitly excludes.

    Returns the divisor when the recovery applies, None otherwise. The
    caller overwrites the total with `declared_total / divisor`
    (== `computed`), preserves the source-declared (inflated) value in
    `source_amount`, and sets `computed=True` and `comma_decimal_typo=True`.
    """
    if computed is None or computed <= 0:
        return None
    if declared_total is None or declared_total <= 0:
        return None
    raw = segment.costs.total.us_dollar.raw
    div = _ct_total_typo_divisor(raw)
    if div is None:
        return None
    if declared_total / div == computed:
        return div
    return None


def _ct_total_typo_divisor(raw: str) -> Optional[Decimal]:
    """Return the divisor (100 or 1000) if `raw` has the comma-as-decimal
    typo shape: no decimal point, comma-separated, last comma group is 2 or
    3 digits. Otherwise return None.

    Mirrors the component-cell detection in `_detect_component_comma_decimal_typos`
    for the committee total cell. The 2-digit case (`,NN`) divides by 100
    (e.g. `3,312,32` → 331232, intended `3,312.32` = 3312.32). The 3-digit
    case (`,NNN`) divides by 1000 (e.g. `7,202,000` → 7202000, intended
    `7,202.000` = 7202). The 3-digit shape is ambiguous with a normal
    thousands separator; the exact-match arithmetic gate at the call site
    (ct_total/divisor == seg_total) distinguishes the typo from a genuine
    large total.
    """
    if not raw:
        return None
    rs = raw.strip()
    if "." in rs or "," not in rs:
        return None
    parts = rs.split(",")
    if len(parts) < 2:
        return None
    last = parts[-1]
    if not last.isdigit():
        return None
    if len(last) == 2:
        return Decimal("100")
    if len(last) == 3:
        return Decimal("1000")
    return None


def _detect_component_comma_decimal_typos(
    segment: TravelSegment, declared_total: Decimal, tolerance: Decimal
) -> Optional[list[tuple[CostCell, Decimal]]]:
    """Detect a comma-as-decimal typo in one or more component cells where
    fixing it makes the component sum equal the declared total.

    Mirror of `_is_100x_typo` for component cells: the source wrote a comma
    where a decimal point should be (e.g. `749,00` parsed as 74900, intended
    `749.00`; `1,123,000` parsed as 1123000, intended `1,123.000` = 1123).
    The component cell carries the typo; the declared total is correct. The
    recovery overwrites each typo'd component cell with `amount/divisor`,
    preserves the source-declared (inflated) value in `source_amount`, and
    sets `computed=True` and `comma_decimal_typo=True` on the cell.

    Returns a list of (cell, divisor) pairs to recover, or None when the
    pattern doesn't apply. The pattern requires:

    - declared_total is present (the source declared a total).
    - At least one component cell's `raw` ends with a `,NN` (2-digit,
      divisor=100) or `,NNN` (3-digit, divisor=1000) group, with no decimal
      point elsewhere in the raw -- the comma-as-decimal shape.
    - Dividing each typo'd component by its divisor makes the component
      sum exactly equal the declared total. The exact-match gate is tight
      enough that coincidental hits are negligible.

    The 3-digit `,NNN` case is ambiguous with a normal thousands separator
    (e.g. `1,123,000` could be 1123000 dollars or `1,123.000` = 1123). We
    only treat it as a typo when dividing by 1000 produces a component
    sum equal to the declared total -- the arithmetic gate distinguishes
    the typo from a genuine large component.
    """
    if declared_total is None:
        return None

    def divisor_for(raw: str, amt: Optional[Decimal]) -> Optional[Decimal]:
        if not raw or amt is None:
            return None
        rs = raw.strip()
        if "." in rs or "," not in rs:
            return None
        parts = rs.split(",")
        if len(parts) < 2:
            return None
        last = parts[-1]
        if not last.isdigit():
            return None
        if len(last) == 2:
            return Decimal("100")
        if len(last) == 3:
            return Decimal("1000")
        return None

    cells_and_divisors: list[tuple[CostCell, Decimal]] = []
    for group in (segment.costs.per_diem, segment.costs.transportation, segment.costs.other):
        cell = group.us_dollar
        div = divisor_for(cell.raw, cell.amount)
        if div is None:
            continue
        cells_and_divisors.append((cell, div))

    if not cells_and_divisors:
        return None

    typo_cells = {id(cell) for cell, _ in cells_and_divisors}
    fixed_sum = Decimal("0")
    for group in (segment.costs.per_diem, segment.costs.transportation, segment.costs.other):
        cell = group.us_dollar
        if cell.amount is None:
            continue
        if id(cell) in typo_cells:
            for c, div in cells_and_divisors:
                if id(c) == id(cell):
                    fixed_sum += cell.amount / div
                    break
        else:
            fixed_sum += cell.amount

    if abs(fixed_sum - declared_total) > tolerance:
        return None
    return cells_and_divisors


def _detect_ct_component_typos(
    report: Report, tolerance: Decimal
) -> Optional[list[tuple[CostCell, Decimal]]]:
    """Detect a comma-as-decimal typo in one or more committee-total
    component cells (per_diem / transportation / other) where fixing it
    makes the component sum equal the declared committee total.

    Mirror of `_detect_component_comma_decimal_typos` at the committee-
    total level: one or more ct component cells used a comma where a
    decimal point should be (e.g. `37,347,86` parsed as 3734786, intended
    `37,347.86` = 37347.86; `1,123,000` parsed as 1123000, intended
    `1,123.000` = 1123). The ct total cell is correct; recovery overwrites
    each typo'd component cell with `amount/divisor`, preserves the
    source-declared (inflated) value in `source_amount`, and sets
    `computed=True` and `comma_decimal_typo=True` on the cell.

    Returns a list of (cell, divisor) pairs to recover, or None when the
    pattern doesn't apply. Requires:

    - ct total is present (the source declared a committee total).
    - At least one ct component cell's `raw` has the comma-as-decimal
      shape (no decimal point, last group 2 or 3 digits).
    - Dividing each typo'd component by its divisor makes the component
      sum exactly equal the declared ct total. The exact-match gate
      distinguishes a real typo from a genuine large component (the
      `,NNN` shape is otherwise ambiguous with a normal thousands
      separator).
    """
    ct = report.committee_total
    if ct is None:
        return None
    ct_total = ct.total.us_dollar.amount
    if ct_total is None:
        return None
    cells = [
        ct.per_diem.us_dollar,
        ct.transportation.us_dollar,
        ct.other.us_dollar,
    ]
    cells_and_divisors: list[tuple[CostCell, Decimal]] = []
    for cell in cells:
        div = _ct_total_typo_divisor(cell.raw)
        if div is None or cell.amount is None:
            continue
        cells_and_divisors.append((cell, div))
    if not cells_and_divisors:
        return None
    typo_cells = {id(cell) for cell, _ in cells_and_divisors}
    fixed_sum = Decimal("0")
    for cell in cells:
        if cell.amount is None:
            continue
        if id(cell) in typo_cells:
            for c, div in cells_and_divisors:
                if id(c) == id(cell):
                    fixed_sum += cell.amount / div
                    break
        else:
            fixed_sum += cell.amount
    if abs(fixed_sum - ct_total) > tolerance:
        return None
    return cells_and_divisors


def _classify_unmatched_by_delta_sign(
    segment: TravelSegment, declared_total: Decimal, computed: Decimal, tolerance: Decimal
) -> Optional[str]:
    """For a segment whose declared total doesn't match the sum of its
    populated cost components (delta exceeds rounding), return the
    appropriate informational flag name (replacing ROW_SUM_MISMATCH), or
    None if the shape doesn't apply.

    Handles both single-component and multi-component segments. For
    multi-component segments, this runs AFTER the subset-exclusion check
    (`_classify_component_excluded_subset`), so it only sees segments
    where declared_total doesn't equal any subset sum of the populated
    components.

    Source conventions this distinguishes:
    - `ROW_TOTAL_INCLUDES_UNBROKEN_COSTS`: declared_total > component
      sum (positive delta). The source declared a total that includes
      costs not broken out into per_diem/transport/other (often a shared
      commercial airfare charged to the delegation but not broken out
      per-traveler). The source-declared total is kept as-is.
    - `ROW_TOTAL_LESS_THAN_COMPONENT`: declared_total < component sum
      (negative delta). The source declared a total less than the
      component sum -- often the per_diem column shows the full rate
      and the total reflects deductions (returned per-diem,
      host-provided meals). The source-declared total is kept as-is.

    Both downgrades only apply when |delta| exceeds the rounding
    threshold; sub-rounding deltas are handled by `ROW_SUM_ROUNDING`.
    """
    pd = segment.costs.per_diem.us_dollar.amount
    tr_amt = segment.costs.transportation.us_dollar.amount
    ot_amt = segment.costs.other.us_dollar.amount
    components = [x for x in (pd, tr_amt, ot_amt) if x is not None and x > 0]
    if not components:
        return None
    delta = declared_total - sum(components)
    if _is_rounding_delta(delta, declared_total, tolerance):
        return None  # ROW_SUM_ROUNDING handles it
    if delta > 0:
        return "ROW_TOTAL_INCLUDES_UNBROKEN_COSTS"
    if delta < 0:
        return "ROW_TOTAL_LESS_THAN_COMPONENT"
    return None


def _is_negative_per_diem_refund(
    segment: TravelSegment, declared_total: Optional[Decimal]
) -> bool:
    """A segment with a negative per_diem, no other populated components,
    and a declared total equal to the absolute value of per_diem, is a
    source convention: the source wrote the per_diem with a trailing
    minus (e.g. `1,060.00-`) which the parser reads as -1060.00, and the
    total as the absolute value (1,060.00). All three segments in
    `2017q4dec06-000` (Janice Robinson) follow this shape. Keep the
    source-declared total as-is and flag informationally.
    """
    if declared_total is None or declared_total <= 0:
        return False
    pd = segment.costs.per_diem.us_dollar.amount
    tr_amt = segment.costs.transportation.us_dollar.amount
    ot_amt = segment.costs.other.us_dollar.amount
    if pd is None or pd >= 0:
        return False
    if (tr_amt is not None and tr_amt > 0) or (ot_amt is not None and ot_amt > 0):
        return False
    return declared_total == -pd


def _classify_component_excluded_subset(
    segment: TravelSegment, declared_total: Decimal, tolerance: Decimal
) -> Optional[list[str]]:
    """For a multi-component segment whose declared total equals the sum of
    a subset of its populated components, return the list of
    per-component-excluded informational flag names (replacing
    ROW_SUM_MISMATCH), or None if no subset matches.

    Mirrors the table-level `TABLE_SUM_TRANSPORT_EXCLUDED` convention:
    the source excludes certain components from the declared total
    (e.g. DoD-provided transport not counted, per_diem reimbursed
    separately / returned). Three flags, one per excluded component:
    - `ROW_TOTAL_TRANSPORT_EXCLUDED`
    - `ROW_TOTAL_OTHER_EXCLUDED`
    - `ROW_TOTAL_PER_DIEM_EXCLUDED`

    Tries single-component exclusions first (more common, less
    ambiguous). For ambiguous cases -- two populated components with
    equal amounts where the total matches either single-component
    subset -- prefers transport-excluded (the most common convention
    per the table-level precedent), then other-excluded, then
    per_diem-excluded. Then tries two-component exclusions for the
    "declared = one component only" shape where two other components
    are populated but excluded.

    Keep the source-declared total as-is; this is informational.
    """
    pd = segment.costs.per_diem.us_dollar.amount
    tr_amt = segment.costs.transportation.us_dollar.amount
    ot_amt = segment.costs.other.us_dollar.amount
    populated: list[tuple[str, Decimal]] = []
    if pd is not None and pd > 0:
        populated.append(("per_diem", pd))
    if tr_amt is not None and tr_amt > 0:
        populated.append(("transportation", tr_amt))
    if ot_amt is not None and ot_amt > 0:
        populated.append(("other", ot_amt))
    if len(populated) < 2:
        return None

    flag_name = {
        "per_diem": "ROW_TOTAL_PER_DIEM_EXCLUDED",
        "transportation": "ROW_TOTAL_TRANSPORT_EXCLUDED",
        "other": "ROW_TOTAL_OTHER_EXCLUDED",
    }

    # Try excluding one component at a time. Order matters for ambiguous
    # cases (two populated components with equal amounts): prefer the
    # most common convention first.
    single_exclusion_order = ["transportation", "other", "per_diem"]
    for excluded in single_exclusion_order:
        if not any(c == excluded for c, _ in populated):
            continue
        included_sum = sum(amt for c, amt in populated if c != excluded)
        if abs(declared_total - included_sum) <= tolerance:
            return [flag_name[excluded]]

    # Try excluding two components (declared = one component only, two
    # others populated but excluded).
    pairs = [
        ("transportation", "other"),
        ("transportation", "per_diem"),
        ("other", "per_diem"),
    ]
    populated_names = {c for c, _ in populated}
    for e1, e2 in pairs:
        if e1 not in populated_names or e2 not in populated_names:
            continue
        included = [c for c, _ in populated if c not in (e1, e2)]
        if len(included) != 1:
            continue
        included_amt = populated[[c for c, _ in populated].index(included[0])][1]
        if abs(declared_total - included_amt) <= tolerance:
            return [flag_name[e1], flag_name[e2]]

    return None


def validate_report(report: Report, tolerance: Decimal = DEFAULT_TOLERANCE) -> Report:
    """
    Check arithmetic and date invariants, appending flags in place.

    Mutates a segment's `costs.total.us_dollar` only to recover a total
    whose source value is missing or unreliable:

    - **Source-omitted**: `total.us_dollar.amount` is None and component
      amounts are present (the total cell is dot-filled -- a common
      convention in older reports where per_diem IS the total). The total
      is filled with the sum of the non-null component amounts.
    - **Supplement-outdated**: `total.us_dollar.amount` is set but
      `COST_SUPPLEMENT_MERGED` is also present on the segment and the
      declared total doesn't match the component sum. A supplement row
      (Commercial transportation, Delegation expenses) was merged into
      the components after the source declared its total, and the source
      value wasn't updated. The total is overwritten with the computed
      sum.
    - **Source double-counted a component**: `total.us_dollar.amount` is
      set, no supplement merge is present, and the declared total exceeds
      the component sum by exactly one component amount. The source
      added that component to the total twice (e.g. 1997 Korea trips
      where per_diem=305 and total=610). The total is overwritten with
      the computed sum. Tagged `ROW_TOTAL_DOUBLE_COUNTED` to distinguish
      from the supplement-merge recovery; both also carry
      `ROW_TOTAL_COMPUTED`.

    In all three cases the segment is tagged `ROW_TOTAL_COMPUTED`
    (informational) and `costs.total.us_dollar.computed` is set to True
    as the idempotency marker. Source-declared totals that match the
    component sum are never overwritten.

    Idempotent: clears its own flags before recomputing, so it's safe to
    call again on a report that's already been through validation (e.g.
    after a correction). `ROW_TOTAL_COMPUTED` is re-added on revalidation
    whenever `costs.total.us_dollar.computed` is True -- the detector is
    the explicit boolean, not a one-shot side effect.
    `ROW_TOTAL_DOUBLE_COUNTED` is re-added on revalidation whenever
    `costs.total.us_dollar.double_counted` is True (the explicit marker
    for the double-count recovery, distinct from the source-omitted and
    supplement-merged paths which only set `computed`).

    Checks:
      - Each segment's per_diem + transportation + other ~= total (ROW_SUM_MISMATCH)
      - A segment has a declared total but no component breakdown, so there
        is nothing to arithmetically check (ROW_NO_COMPONENT_BREAKDOWN --
        informational, not an error; distinguishes a source convention from
        a genuine arithmetic mismatch)
      - A segment's declared total equals per_diem × days-in-segment with
        only per_diem populated (ROW_TOTAL_IS_PER_DIEM_X_DAYS --
        informational; the source convention is per-day per_diem with the
        total computed as per_diem × days, no component breakdown of the
        multiplier. Not a mismatch and not a double-count, though a 2-day
        segment of this shape is arithmetic-indistinguishable from a
        per_diem double-count without the day-count check)
      - Sum of all segment totals ~= the table's committee total (TABLE_SUM_MISMATCH)
      - A committee total row was expected but missing (MISSING_COMMITTEE_TOTAL)

    Args:
        report: Assembled Report to validate
        tolerance: Allowed absolute difference in dollars before flagging a mismatch

    Returns:
        The same Report, with `flags` (report-level) and segment `flags` extended
    """
    all_segments = [seg for traveler in report.travelers for seg in traveler.segments]

    trip_total_segments = _find_trip_total_segments(report, tolerance)

    recover_empty_dates(report)

    for segment in all_segments:
        if "ROW_SUM_MISMATCH" in segment.flags:
            segment.flags.remove("ROW_SUM_MISMATCH")
        if "ROW_TOTAL_MISSING" in segment.flags:
            segment.flags.remove("ROW_TOTAL_MISSING")
        if "ROW_TOTAL_COMPUTED" in segment.flags:
            segment.flags.remove("ROW_TOTAL_COMPUTED")
        if "ROW_TOTAL_DOUBLE_COUNTED" in segment.flags:
            segment.flags.remove("ROW_TOTAL_DOUBLE_COUNTED")
        if "ROW_NO_COMPONENT_BREAKDOWN" in segment.flags:
            segment.flags.remove("ROW_NO_COMPONENT_BREAKDOWN")
        if "ROW_BREAKDOWN_IN_FC_COLUMN" in segment.flags:
            segment.flags.remove("ROW_BREAKDOWN_IN_FC_COLUMN")
        if "ROW_TOTAL_IS_PER_DIEM_X_DAYS" in segment.flags:
            segment.flags.remove("ROW_TOTAL_IS_PER_DIEM_X_DAYS")
        if "ROW_TOTAL_IS_TRIP_TOTAL" in segment.flags:
            segment.flags.remove("ROW_TOTAL_IS_TRIP_TOTAL")
        if "ROW_SUM_ROUNDING" in segment.flags:
            segment.flags.remove("ROW_SUM_ROUNDING")
        if "ROW_TOTAL_COMMA_DECIMAL_TYPO" in segment.flags:
            segment.flags.remove("ROW_TOTAL_COMMA_DECIMAL_TYPO")
        if "ROW_COMPONENT_COMMA_DECIMAL_TYPO" in segment.flags:
            segment.flags.remove("ROW_COMPONENT_COMMA_DECIMAL_TYPO")
        if "ROW_TOTAL_INCLUDES_UNBROKEN_COSTS" in segment.flags:
            segment.flags.remove("ROW_TOTAL_INCLUDES_UNBROKEN_COSTS")
        if "ROW_TOTAL_LESS_THAN_COMPONENT" in segment.flags:
            segment.flags.remove("ROW_TOTAL_LESS_THAN_COMPONENT")
        if "ROW_TOTAL_TRANSPORT_EXCLUDED" in segment.flags:
            segment.flags.remove("ROW_TOTAL_TRANSPORT_EXCLUDED")
        if "ROW_TOTAL_OTHER_EXCLUDED" in segment.flags:
            segment.flags.remove("ROW_TOTAL_OTHER_EXCLUDED")
        if "ROW_TOTAL_PER_DIEM_EXCLUDED" in segment.flags:
            segment.flags.remove("ROW_TOTAL_PER_DIEM_EXCLUDED")
        if "ROW_TOTAL_NEGATIVE_PER_DIEM" in segment.flags:
            segment.flags.remove("ROW_TOTAL_NEGATIVE_PER_DIEM")
    report.flags = [
        f
        for f in report.flags
        if f
        not in (
            "TABLE_SUM_MISMATCH",
            "MISSING_COMMITTEE_TOTAL",
            "COMMITTEE_TOTAL_COMPUTED",
            "COMMITTEE_TOTAL_INFERRED_FROM_SEGMENTS",
            "COMMITTEE_TOTAL_COMMA_DECIMAL_TYPO",
            "COMMITTEE_COMPONENT_COMMA_DECIMAL_TYPO",
            "TABLE_SUM_EXPLAINED_BY_SUPPLEMENT",
            "TABLE_SUM_EXPLAINED_BY_ROW_TOTAL_FLAGS",
            "TABLE_SUM_TRANSPORT_EXCLUDED",
            "TABLE_SUM_COMPONENT_DELTA",
            "TABLE_SUM_ROUNDING",
            "TABLE_SUM_CT_NO_BREAKDOWN",
            "TABLE_SUM_NO_SEG_BREAKDOWN",
            "TABLE_SUM_CT_HAS_UNBROKEN_COMPONENT",
            "TABLE_SUM_SEG_HAS_UNBROKEN_COMPONENT",
        )
    ]

    for segment in all_segments:
        total_cell = segment.costs.total.us_dollar
        declared_total = total_cell.amount
        if total_cell.computed:
            # Total was filled in by a prior pass -- the amount is by
            # definition the sum of components, so skip the mismatch
            # check and re-tag as computed. Re-tag double-counted when
            # that was the recovery path (the explicit marker
            # distinguishes it from source-omitted and supplement-merged
            # recoveries, which also set `computed=True`).
            segment.flags.append("ROW_TOTAL_COMPUTED")
            if total_cell.double_counted:
                segment.flags.append("ROW_TOTAL_DOUBLE_COUNTED")
            if total_cell.trip_total:
                segment.flags.append("ROW_TOTAL_IS_TRIP_TOTAL")
            if total_cell.comma_decimal_typo:
                segment.flags.append("ROW_TOTAL_COMMA_DECIMAL_TYPO")
            continue
        # Re-derive ROW_COMPONENT_COMMA_DECIMAL_TYPO when component cells
        # were typo-recovered in a prior pass. After recovery, comp_sum ==
        # declared_total so no mismatch check fires; the marker on the
        # component cells is the only signal that the segment was recovered.
        if (
            segment.costs.per_diem.us_dollar.comma_decimal_typo
            or segment.costs.transportation.us_dollar.comma_decimal_typo
            or segment.costs.other.us_dollar.comma_decimal_typo
        ):
            segment.flags.append("ROW_COMPONENT_COMMA_DECIMAL_TYPO")
            continue
        if declared_total is None:
            computed = _group_total(segment.costs)
            if computed != 0:
                total_cell.amount = computed
                total_cell.computed = True
                segment.flags.append("ROW_TOTAL_COMPUTED")
            continue
        computed = _group_total(segment.costs)
        if computed == 0 and declared_total is not None and declared_total > 0:
            # The source declared a total but no US-dollar per-component
            # breakdown (all three US-dollar component cells are empty/dot-
            # filled). Check whether the foreign-currency components sum to
            # the declared US-dollar total -- a source convention where the
            # per-category amounts were entered in the foreign-currency
            # column even though they are US-dollar figures (e.g. Kuwait
            # tr.fc=742.00 with tot.us=742.00; neither KWD nor the report's
            # other currencies are 1:1 with USD, so the FC column is carrying
            # US-dollar amounts). When the FC sum matches, downgrade to
            # ROW_BREAKDOWN_IN_FC_COLUMN so downstream consumers can
            # distinguish "no breakdown at all" from "breakdown in FC column".
            fc_total = _fc_group_total(segment.costs)
            if fc_total > 0 and abs(fc_total - declared_total) <= tolerance:
                segment.flags.append("ROW_BREAKDOWN_IN_FC_COLUMN")
                continue
            # No FC breakdown either (or FC sum doesn't match): genuine
            # total-only row. There's nothing to arithmetically check --
            # this is a source convention (e.g. 2009q2may13.txt declares
            # only USD totals with no breakdown for any row), not a mismatch.
            # Flag it informationally so downstream consumers can distinguish
            # "source didn't provide a breakdown" from "source provided a
            # breakdown that doesn't add up" (ROW_SUM_MISMATCH).
            segment.flags.append("ROW_NO_COMPONENT_BREAKDOWN")
            continue
        if abs(computed - declared_total) > tolerance:
            if "COST_SUPPLEMENT_MERGED" in segment.flags:
                # A supplement row was merged into the components after
                # the source declared its total, and the source value
                # wasn't updated to reflect it. The component sum is the
                # true total; overwrite the stale source value. Preserve
                # the original source total so the table-level sum check
                # can verify the committee total against the pre-supplement
                # sum (the source declared the committee total before
                # supplements were merged in).
                total_cell.source_amount = declared_total
                total_cell.amount = computed
                total_cell.computed = True
                segment.flags.append("ROW_TOTAL_COMPUTED")
            elif id(segment) in trip_total_segments:
                # The source filled the trip total (cumulative per_diem across
                # all the traveler's segments) into this one segment's total
                # cell. The per-segment total is the segment's own per_diem;
                # preserve the source trip total in `source_amount` so the
                # table-level sum check can verify the committee total
                # (which equals the sum of trip totals, == sum of per_diems,
                # == post-recovery sum of segment totals). Runs before the
                # per_diem_x_days check so a 2-day segment whose per_diem
                # coincidentally equals another segment's per_diem doesn't
                # get misclassified as per_diem × days.
                total_cell.source_amount = declared_total
                total_cell.amount = segment.costs.per_diem.us_dollar.amount
                total_cell.computed = True
                total_cell.trip_total = True
                segment.flags.append("ROW_TOTAL_COMPUTED")
                segment.flags.append("ROW_TOTAL_IS_TRIP_TOTAL")
            elif _per_diem_times_days_match(segment, declared_total, tolerance):
                # The source total is per_diem × days-in-segment (only
                # per_diem populated; T and O empty -- often DoD-provided
                # transport). The declared total is correct under this
                # convention; keep it as-is. This must run before the
                # double-count check, otherwise 2-day segments (where
                # per_diem × (days-1) = per_diem) get misidentified as a
                # per_diem double-count and the correct total is overwritten.
                segment.flags.append("ROW_TOTAL_IS_PER_DIEM_X_DAYS")
            elif _source_double_counted_component(declared_total - computed, segment.costs, tolerance):
                # The source total exceeds the component sum by exactly
                # one component amount -- the source double-counted that
                # component. The component sum is the true total;
                # overwrite the inflated source value.
                total_cell.amount = computed
                total_cell.computed = True
                total_cell.double_counted = True
                segment.flags.append("ROW_TOTAL_COMPUTED")
                segment.flags.append("ROW_TOTAL_DOUBLE_COUNTED")
            elif _is_rounding_delta(declared_total - computed, declared_total, tolerance):
                # The component sum is within a small threshold of the
                # declared total -- source rounding or a small typo, not a
                # genuine arithmetic error. Keep the source-declared total
                # (consumers care about the source's stated amount) and flag
                # informationally. Runs after the more specific recoveries
                # (supplement-merge, trip-total, per_diem × days,
                # double-count) so each of those patterns stays specific.
                segment.flags.append("ROW_SUM_ROUNDING")
            elif _is_100x_typo(declared_total, computed, segment):
                # The source used a comma where a decimal point should be
                # (e.g. per_diem=`1,204.00`, total=`1,204,00` parsed as
                # 120400). The intended total equals the single component;
                # overwrite with the component value and preserve the
                # source-declared (100×) total in `source_amount`.
                total_cell.source_amount = declared_total
                total_cell.amount = computed
                total_cell.computed = True
                total_cell.comma_decimal_typo = True
                segment.flags.append("ROW_TOTAL_COMPUTED")
                segment.flags.append("ROW_TOTAL_COMMA_DECIMAL_TYPO")
            elif (
                div := _row_total_typo_divisor(declared_total, computed, segment)
            ) is not None:
                # Multi-component segment with a comma-as-decimal typo in
                # the total cell raw (e.g. raw `2,345,36` parsed as 234536,
                # intended `2,345.36` = 1622.76 + 722.60; raw `7,202,000`
                # parsed as 7202000, intended `7,202.000` = 7202). The
                # declared total divided by the divisor (100 for `,NN`,
                # 1000 for `,NNN`) exactly equals the component sum. The
                # raw-shape gate (in addition to the exact-arithmetic gate)
                # distinguishes the typo from a genuine large total.
                # Overwrite with the component sum and preserve the
                # source-declared (inflated) total in `source_amount`.
                total_cell.source_amount = declared_total
                total_cell.amount = computed
                total_cell.computed = True
                total_cell.comma_decimal_typo = True
                segment.flags.append("ROW_TOTAL_COMPUTED")
                segment.flags.append("ROW_TOTAL_COMMA_DECIMAL_TYPO")
            elif (
                excluded := _classify_component_excluded_subset(
                    segment, declared_total, tolerance
                )
            ) is not None:
                # A multi-component segment whose declared total equals the
                # sum of a subset of its populated components follows a
                # recognized source convention -- certain components are
                # excluded from the total (e.g. DoD-provided transport not
                # counted, per_diem reimbursed separately / returned). Keep
                # the source-declared total and flag each excluded
                # component informationally.
                for f in excluded:
                    segment.flags.append(f)
            elif (
                component_typos := _detect_component_comma_decimal_typos(
                    segment, declared_total, tolerance
                )
            ) is not None:
                # One or more component cells (per_diem / transportation /
                # other) used a comma where a decimal point should be (e.g.
                # `749,00` parsed as 74900, `1,123,000` parsed as 1123000).
                # The declared total is correct; recovery overwrites each
                # typo'd component with `amount/divisor`, preserves the
                # source-declared (inflated) value in `source_amount`, and
                # marks the cell. The segment becomes internally consistent
                # (comp_sum == declared_total), so the segment-level
                # ROW_TOTAL_LESS_THAN_COMPONENT flag is replaced with the
                # more specific ROW_COMPONENT_COMMA_DECIMAL_TYPO flag.
                # Runs after subset-exclusion so a real subset-exclusion
                # shape wins, and before _classify_unmatched_by_delta_sign
                # (which would set ROW_TOTAL_LESS_THAN_COMPONENT).
                for cell, divisor in component_typos:
                    cell.source_amount = cell.amount
                    cell.amount = cell.amount / divisor
                    cell.computed = True
                    cell.comma_decimal_typo = True
                segment.flags.append("ROW_COMPONENT_COMMA_DECIMAL_TYPO")
            elif (
                flag := _classify_unmatched_by_delta_sign(
                    segment, declared_total, computed, tolerance
                )
            ) is not None:
                # A segment whose declared total doesn't match the sum of
                # its populated components (delta exceeds rounding, and no
                # subset-exclusion shape matched) is a recognized source
                # convention -- not a genuine arithmetic error. Positive
                # delta: the source's total includes unbroken-out costs
                # (e.g. shared airfare not broken out per-traveler).
                # Negative delta: the source's total is less than the
                # component sum (e.g. per-diem deductions/returns). Keep
                # the source-declared total as-is and flag informationally.
                segment.flags.append(flag)
            elif _is_negative_per_diem_refund(segment, declared_total):
                # A segment with a negative per_diem (trailing minus in the
                # source, e.g. `1,060.00-`) and a declared total equal to
                # the absolute value, with no other populated components.
                # Source convention: the total is the absolute value of
                # the (negatively-written) per_diem. Keep the source-declared
                # total as-is and flag informationally.
                segment.flags.append("ROW_TOTAL_NEGATIVE_PER_DIEM")
            else:
                segment.flags.append("ROW_SUM_MISMATCH")

    if report.committee_total is not None:
        ct_total_cell = report.committee_total.total.us_dollar
        declared_table_total = ct_total_cell.amount
        ct_component_sum = (
            _amount_or_zero(report.committee_total.per_diem.us_dollar)
            + _amount_or_zero(report.committee_total.transportation.us_dollar)
            + _amount_or_zero(report.committee_total.other.us_dollar)
        )
        seg_component_sum = sum(
            (
                _group_total(seg.costs)
                for seg in all_segments
            ),
            Decimal("0"),
        )
        seg_total_sum = sum(
            (
                seg.costs.total.us_dollar.amount
                for seg in all_segments
                if seg.costs.total.us_dollar.amount is not None
            ),
            Decimal("0"),
        )
        # Idempotency: a previously-computed committee total is already the
        # component sum, so the recovery condition below won't fire. Re-tag
        # from the explicit `computed` marker. Re-tag comma_decimal_typo when
        # that was the recovery path (the explicit marker distinguishes it
        # from the component-sum recovery, which also sets `computed=True`).
        # When ALL FOUR ct cells are computed, the Costs was synthesized by
        # the MISSING_COMMITTEE_TOTAL inference (no other recovery sets the
        # component cells to computed=True) -- re-tag the inference flag
        # instead of COMMITTEE_TOTAL_COMPUTED.
        if ct_total_cell.computed:
            if (
                report.committee_total.per_diem.us_dollar.computed
                and report.committee_total.transportation.us_dollar.computed
                and report.committee_total.other.us_dollar.computed
            ):
                report.flags.append("COMMITTEE_TOTAL_INFERRED_FROM_SEGMENTS")
            else:
                report.flags.append("COMMITTEE_TOTAL_COMPUTED")
                if ct_total_cell.comma_decimal_typo:
                    report.flags.append("COMMITTEE_TOTAL_COMMA_DECIMAL_TYPO")
        elif (
            declared_table_total is not None
            and abs(declared_table_total - ct_component_sum)
            > max(tolerance, _table_rounding_threshold(ct_component_sum))
            and abs(ct_component_sum - seg_component_sum)
            <= max(tolerance, _table_rounding_threshold(ct_component_sum))
            and ct_component_sum > 0
            and not _delta_matches_segment_component(
                declared_table_total - ct_component_sum, all_segments, tolerance
            )
        ):
            # The committee total row's TOTAL cell doesn't match its own
            # component cells, but the component cells DO sum to the segment
            # components. The total cell is wrong (layout digit-shift, comma-
            # decimal typo, or source typo) while the components are intact.
            # Overwrite with the computed sum -- mirrors ROW_TOTAL_COMPUTED.
            # Small diffs (within the rounding threshold) are left to the
            # classifier as TABLE_SUM_ROUNDING; diffs that match a single
            # segment component are left to the classifier as
            # TABLE_SUM_COMPONENT_DELTA (the source intentionally excluded or
            # double-counted that component); only clear typos are recovered.
            ct_total_cell.source_amount = ct_total_cell.amount
            ct_total_cell.amount = ct_component_sum
            ct_total_cell.computed = True
            report.flags.append("COMMITTEE_TOTAL_COMPUTED")
            declared_table_total = ct_component_sum
        elif (
            declared_table_total is not None
            and declared_table_total > 0
            and (divisor := _ct_total_typo_divisor(ct_total_cell.raw)) is not None
            and seg_total_sum > 0
            and abs(declared_table_total / divisor - seg_total_sum)
            <= max(tolerance, _table_rounding_threshold(seg_total_sum))
        ):
            # The committee total cell used a comma where a decimal point
            # should be (e.g. `3,312,32` parsed as 331232, intended `3,312.32`;
            # `7,202,000` parsed as 7202000, intended `7,202.000` = 7202). The
            # segment totals are intact; recovery divides the declared total
            # by 100 (`,NN`) or 1000 (`,NNN`) and overwrites the cell,
            # preserving the source-declared (inflated) value in
            # `source_amount`. Mirrors ROW_TOTAL_COMMA_DECIMAL_TYPO at the
            # table level. Runs AFTER the COMMITTEE_TOTAL_COMPUTED branch --
            # when ct_components match seg_components, that recovery wins (it
            # recovers from the components, which are the source of truth
            # when intact). The 3-digit `,NNN` shape is ambiguous with a
            # normal thousands separator; the exact-match arithmetic gate
            # (ct_total/divisor == seg_total) distinguishes the typo from a
            # genuine large total.
            ct_total_cell.source_amount = declared_table_total
            ct_total_cell.amount = declared_table_total / divisor
            ct_total_cell.computed = True
            ct_total_cell.comma_decimal_typo = True
            report.flags.append("COMMITTEE_TOTAL_COMPUTED")
            report.flags.append("COMMITTEE_TOTAL_COMMA_DECIMAL_TYPO")
            declared_table_total = ct_total_cell.amount
        # CT-component typo recovery runs as a separate block (not tied to
        # the ct-total elif chain) so it can fire in the same pass as the
        # ct-total typo branch above. When both apply (e.g. 1996q3sep11-005
        # where ct_total and ct_pd both have raw `7,202,000`), the ct-total
        # branch fixes ct_total first, then this branch fixes the ct
        # component cells against the corrected ct_total. The idempotency
        # re-tag (markers on ct component cells) handles revalidation when
        # the ct-total elif chain short-circuits via `ct_total_cell.computed`.
        if (
            report.committee_total.per_diem.us_dollar.comma_decimal_typo
            or report.committee_total.transportation.us_dollar.comma_decimal_typo
            or report.committee_total.other.us_dollar.comma_decimal_typo
        ):
            # Idempotency: ct component cells were typo-recovered in a
            # prior pass. After recovery, the ct component amounts are the
            # fixed values, so _detect_ct_component_typos won't re-fire
            # (dividing the fixed amount by the divisor no longer matches
            # the ct total); the marker is the only signal that the report
            # was recovered. Re-tag the flag from the marker.
            report.flags.append("COMMITTEE_COMPONENT_COMMA_DECIMAL_TYPO")
        elif (
            ct_component_typos := _detect_ct_component_typos(report, tolerance)
        ) is not None:
            # One or more committee-total component cells (per_diem /
            # transportation / other) used a comma where a decimal point
            # should be (e.g. `37,347,86` parsed as 3734786, intended
            # `37,347.86` = 37347.86). The ct total cell is correct;
            # recovery overwrites each typo'd component with
            # `amount/divisor`, preserves the source-declared (inflated)
            # value in `source_amount`, and sets `computed=True` and
            # `comma_decimal_typo=True` on the cell. Mirrors
            # ROW_COMPONENT_COMMA_DECIMAL_TYPO at the committee-total
            # level. The exact-match arithmetic gate (fixed component sum
            # == ct total) distinguishes a real typo from a genuine large
            # component (the `,NNN` shape is otherwise ambiguous with a
            # normal thousands separator). Does not change
            # `declared_table_total` -- the ct total is already correct;
            # only the components were wrong.
            for cell, div in ct_component_typos:
                cell.source_amount = cell.amount
                cell.amount = cell.amount / div
                cell.computed = True
                cell.comma_decimal_typo = True
            report.flags.append("COMMITTEE_COMPONENT_COMMA_DECIMAL_TYPO")
        if declared_table_total is not None:
            delta = seg_total_sum - declared_table_total
            if abs(delta) > tolerance:
                _classify_table_sum_mismatch(
                    report, delta, declared_table_total, all_segments, tolerance
                )
    elif all_segments:
        # No committee-total row in the source. If the segments have any
        # cost data, synthesize a ct from the per-seg sums so downstream
        # consumers have a complete ct. Flagged
        # COMMITTEE_TOTAL_INFERRED_FROM_SEGMENTS (replaces
        # MISSING_COMMITTEE_TOTAL). When no segment has any cost cell
        # populated, there's nothing to infer -- stay
        # MISSING_COMMITTEE_TOTAL.
        inferred = _infer_committee_total_from_segments(all_segments)
        if inferred is not None:
            report.committee_total = inferred
            report.flags.append("COMMITTEE_TOTAL_INFERRED_FROM_SEGMENTS")
        else:
            report.flags.append("MISSING_COMMITTEE_TOTAL")

    return report


TABLE_SUM_ROUNDING_ABSOLUTE = Decimal("5.00")
TABLE_SUM_ROUNDING_PERCENT = Decimal("0.01")  # 1% of declared total

# Row-level flags where the validator intentionally keeps the source-declared
# segment total even though it doesn't equal the segment's own component sum.
# When the table-level delta is fully explained by the sum of these per-segment
# `total - component_sum` residuals, the mismatch is downgraded to
# TABLE_SUM_EXPLAINED_BY_ROW_TOTAL_FLAGS (informational, not a separate
# table-level error).
_ROW_TOTAL_EXPLANATORY_FLAGS = frozenset({
    "ROW_TOTAL_INCLUDES_UNBROKEN_COSTS",
    "ROW_TOTAL_LESS_THAN_COMPONENT",
    "ROW_NO_COMPONENT_BREAKDOWN",
    "ROW_TOTAL_TRANSPORT_EXCLUDED",
    "ROW_TOTAL_OTHER_EXCLUDED",
    "ROW_TOTAL_PER_DIEM_EXCLUDED",
    "ROW_TOTAL_NEGATIVE_PER_DIEM",
    "ROW_BREAKDOWN_IN_FC_COLUMN",
})


def _classify_table_sum_mismatch(
    report: Report,
    delta: Decimal,
    declared_table_total: Decimal,
    all_segments: list[TravelSegment],
    tolerance: Decimal,
) -> None:
    """Classify a table-level sum mismatch into a specific informational flag
    when the mismatch matches a known source convention or recovery artifact,
    rather than leaving it as a generic TABLE_SUM_MISMATCH.

    Checks in order of specificity (most specific first):
    1. Supplement-explained: the segment-level supplement merge inflated
       segment totals beyond the source-declared committee total. The
       pre-supplement sum (from preserved source_amount) matches the
       declared total.
    2. Transport excluded: the source excludes transportation from the
       committee total (DoD-provided transport not counted). declared
       ~= sum(per_diem) + sum(other).
    3. Component delta: |delta| exactly matches one segment's per_diem,
       transportation, other, or total amount -- a table-level double-count
       or component exclusion.
    4. Rounding: |delta| is within a small threshold (flat $5 or 1% of the
       declared total, whichever is smaller).
    5a. CT has no breakdown: the committee total is the only cell populated;
        segments have costs but the ct per-component cells are all empty.
        Source convention: ct total entered as a single number with no
        breakdown → TABLE_SUM_CT_NO_BREAKDOWN.
    5b. No segment breakdown: the committee total has full breakdown but no
        segment has any cost cells populated (or there are no segments at
        all). Source convention: ct breakdown exists but no per-traveler
        breakdown → TABLE_SUM_NO_SEG_BREAKDOWN.
    5c. CT has unbroken component: one ct component cell is populated but
        the corresponding segment component sum is 0, and that component
        fully accounts for the table delta (the rest of the ct components
        match the rest of the segment components). Source convention: the
        ct broke out a component (often transportation) that wasn't broken
        out per-segment → TABLE_SUM_CT_HAS_UNBROKEN_COMPONENT.
    6. TABLE_SUM_MISMATCH: genuine unexplained mismatch.

    The structural patterns (5a/5b/5c) run AFTER the arithmetic patterns
    (1-4) so a specific arithmetic explanation wins when one applies; the
    structural downgrades only catch shapes the arithmetic classifiers
    can't verify.
    """
    ct = report.committee_total
    ct_pd = _amount_or_zero(ct.per_diem.us_dollar)
    ct_tr = _amount_or_zero(ct.transportation.us_dollar)
    ct_ot = _amount_or_zero(ct.other.us_dollar)
    ct_components = ct_pd + ct_tr + ct_ot

    # Per-component segment sums (used by 0b, 0c, and Pattern 2 below).
    sum_pd = sum(
        (
            seg.costs.per_diem.us_dollar.amount
            for seg in all_segments
            if seg.costs.per_diem.us_dollar.amount is not None
        ),
        Decimal("0"),
    )
    sum_tr = sum(
        (
            seg.costs.transportation.us_dollar.amount
            for seg in all_segments
            if seg.costs.transportation.us_dollar.amount is not None
        ),
        Decimal("0"),
    )
    sum_ot = sum(
        (
            seg.costs.other.us_dollar.amount
            for seg in all_segments
            if seg.costs.other.us_dollar.amount is not None
        ),
        Decimal("0"),
    )
    seg_total_sum = sum(
        (
            seg.costs.total.us_dollar.amount
            for seg in all_segments
            if seg.costs.total.us_dollar.amount is not None
        ),
        Decimal("0"),
    )

    # Pattern 1: supplement-explained.
    has_supplement = any(
        seg.costs.total.us_dollar.source_amount is not None for seg in all_segments
    )
    if has_supplement:
        pre_supplement_sum = sum(
            (
                seg.costs.total.us_dollar.source_amount
                if seg.costs.total.us_dollar.source_amount is not None
                else seg.costs.total.us_dollar.amount
                for seg in all_segments
                if seg.costs.total.us_dollar.amount is not None
            ),
            Decimal("0"),
        )
        if abs(pre_supplement_sum - declared_table_total) <= tolerance:
            report.flags.append("TABLE_SUM_EXPLAINED_BY_SUPPLEMENT")
            return

    # Pattern 2: transport excluded from total.
    if abs(declared_table_total - (sum_pd + sum_ot)) <= tolerance:
        report.flags.append("TABLE_SUM_TRANSPORT_EXCLUDED")
        return

    # Pattern 2.5: delta is fully explained by segment row-total flags.
    # Some segments carry an informational row-level flag where the validator
    # intentionally keeps the source-declared segment total even though it
    # doesn't equal the segment's own component sum (ROW_TOTAL_INCLUDES_UNBROKEN_COSTS,
    # ROW_TOTAL_LESS_THAN_COMPONENT, ROW_NO_COMPONENT_BREAKDOWN, etc.). When
    # the table-level delta exactly equals the sum of these per-segment
    # `total - component_sum` residuals, the mismatch is fully explained by
    # those row-level source conventions -- not a separate table-level error.
    # Requires at least one flagged segment so we don't shadow the rounding
    # classifier for small deltas with no flagged segments.
    row_flag_residual = Decimal("0")
    has_flagged_seg = False
    for seg in all_segments:
        if seg.costs.total.us_dollar.amount is None:
            continue
        if any(f in _ROW_TOTAL_EXPLANATORY_FLAGS for f in seg.flags):
            has_flagged_seg = True
            comp_sum = _group_total(seg.costs)
            row_flag_residual += seg.costs.total.us_dollar.amount - comp_sum
    if (
        has_flagged_seg
        and abs(row_flag_residual) > tolerance
        and abs(delta - row_flag_residual) <= max(tolerance, Decimal("5"))
    ):
        report.flags.append("TABLE_SUM_EXPLAINED_BY_ROW_TOTAL_FLAGS")
        return

    # Pattern 3: |delta| matches one segment component or total.
    component_amounts: list[Decimal] = []
    for seg in all_segments:
        for group in (
            seg.costs.per_diem,
            seg.costs.transportation,
            seg.costs.other,
            seg.costs.total,
        ):
            amt = group.us_dollar.amount
            if amt is not None and amt > 0:
                component_amounts.append(amt)
    if component_amounts and any(
        abs(abs(delta) - amt) <= tolerance for amt in component_amounts
    ):
        report.flags.append("TABLE_SUM_COMPONENT_DELTA")
        return

    # Pattern 4: rounding.
    rounding_threshold = min(
        TABLE_SUM_ROUNDING_ABSOLUTE,
        abs(declared_table_total) * TABLE_SUM_ROUNDING_PERCENT,
    )
    if abs(delta) <= max(tolerance, rounding_threshold):
        report.flags.append("TABLE_SUM_ROUNDING")
        return

    # Pattern 5a: CT has no breakdown — ct_components all 0, declared total
    # > 0, segments have costs. Source convention: the committee total was
    # entered as a single number with no per-component breakdown, so the
    # table delta cannot be arithmetically verified. Informational
    # downgrade from TABLE_SUM_MISMATCH. Runs AFTER the arithmetic
    # classifiers (supplement, transport-excluded, row-flag residuals,
    # component delta, rounding) so that a specific arithmetic explanation
    # wins when one applies.
    if ct_components == 0 and declared_table_total > 0 and seg_total_sum > 0:
        report.flags.append("TABLE_SUM_CT_NO_BREAKDOWN")
        return

    # Pattern 5b: No segment breakdown — ct has full breakdown, no segment
    # has any cost cells populated (or no segments at all). Source convention:
    # the ct breakdown exists but no per-traveler breakdown was provided.
    if (
        ct_components > 0
        and declared_table_total > 0
        and seg_total_sum == 0
        and sum_pd == 0
        and sum_tr == 0
        and sum_ot == 0
    ):
        report.flags.append("TABLE_SUM_NO_SEG_BREAKDOWN")
        return

    # Pattern 5c: CT has unbroken component — one ct component is populated,
    # the corresponding segment component sum is 0, and the rest of ct
    # components matches the rest of segment components. Source convention:
    # the ct broke out a component (often transportation) that wasn't broken
    # out per-segment. The ct component fully accounts for the table delta.
    for ct_amt, seg_sum in (
        (ct_pd, sum_pd),
        (ct_tr, sum_tr),
        (ct_ot, sum_ot),
    ):
        if ct_amt > 0 and seg_sum == 0:
            rest_ct = ct_components - ct_amt
            rest_seg = (sum_pd + sum_tr + sum_ot) - seg_sum
            if abs(rest_ct - rest_seg) <= max(
                tolerance, _table_rounding_threshold(ct_components)
            ):
                report.flags.append("TABLE_SUM_CT_HAS_UNBROKEN_COMPONENT")
                return

    # Pattern 5d: Seg has unbroken component — mirror of 5c. One seg
    # component sum is populated, the corresponding ct component is 0, and
    # the rest of seg components matches the rest of ct components. Source
    # convention: a seg (or per-traveler rollup) broke out a component
    # (often per-segment other costs) that wasn't broken out at the
    # committee-total level. The seg component fully accounts for the
    # table delta, so this only fires when both sides are internally
    # consistent (A=Y, B=Y) — otherwise the rest-match is a coincidence and
    # the table delta is unexplained.
    ct_total_amt = _amount_or_zero(ct.total.us_dollar)
    a_ok = abs(ct_components - ct_total_amt) <= tolerance
    seg_components_sum = sum_pd + sum_tr + sum_ot
    b_ok = abs(seg_components_sum - seg_total_sum) <= tolerance
    if a_ok and b_ok:
        for ct_amt, seg_sum in (
            (ct_pd, sum_pd),
            (ct_tr, sum_tr),
            (ct_ot, sum_ot),
        ):
            if ct_amt == 0 and seg_sum > 0:
                rest_ct = ct_components - ct_amt
                rest_seg = seg_components_sum - seg_sum
                if abs(rest_ct - rest_seg) <= max(
                    tolerance, _table_rounding_threshold(ct_components)
                ):
                    report.flags.append("TABLE_SUM_SEG_HAS_UNBROKEN_COMPONENT")
                    return

    # Pattern 6: genuine mismatch.
    report.flags.append("TABLE_SUM_MISMATCH")


def validate_reports(reports: list[Report], tolerance: Decimal = DEFAULT_TOLERANCE) -> list[Report]:
    """Validate a list of reports in place, returning the same list."""
    for report in reports:
        validate_report(report, tolerance)
    return reports
