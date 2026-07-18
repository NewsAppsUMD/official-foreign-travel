"""Canonical parsed-report models: Report -> Sponsor/Period/Traveler/TravelSegment/Costs."""

from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer

SponsorType = Literal[
    "committee",
    "delegation",
    "commission",
    "interparliamentary",
    "speaker",
    "individual",
    "other",
]


class CostCell(BaseModel):
    """A single dollar-amount cell: an amount, or an empty/footnote-only marker."""

    amount: Optional[Decimal] = None
    raw: str = ""
    footnotes: list[str] = Field(default_factory=list)
    military_air: bool = False
    # True when `amount` was filled in by `validate_report` rather than the
    # source -- either because the source omitted the total (components
    # present, total cell dot-filled) or because a supplement row was
    # merged in after the source declared its total and the source value
    # no longer matches the components. Distinguishes "we computed this"
    # from "the source declared this" for downstream consumers and for
    # `validate_report`'s idempotency check.
    computed: bool = False
    # True when `amount` was overwritten because the source double-counted
    # one of the component amounts in its declared total (the source total
    # exceeded the component sum by exactly one component). Set alongside
    # `computed=True`; lets `validate_report` re-derive `ROW_TOTAL_DOUBLE_COUNTED`
    # on revalidation without conflating with the source-omitted recovery
    # (which also sets `computed=True` but is not a double-count).
    double_counted: bool = False
    # True when `amount` was overwritten because the source filled the
    # trip total (the cumulative per_diem across all the traveler's
    # segments) into this one segment's total cell, rather than the
    # segment's own per-segment total. Set alongside `computed=True`;
    # lets `validate_report` re-derive `ROW_TOTAL_IS_TRIP_TOTAL` on
    # revalidation. The original source trip total is preserved in
    # `source_amount`.
    trip_total: bool = False
    # True when `amount` was overwritten because the source used a comma
    # where a decimal point should be (e.g. per_diem=`1,204.00`,
    # total=`1,204,00` parsed as 120400). Recovery overwrites with the
    # single component value; the source-declared (100×) total is
    # preserved in `source_amount`. Set alongside `computed=True`;
    # lets `validate_report` re-derive `ROW_TOTAL_COMMA_DECIMAL_TYPO`
    # on revalidation.
    comma_decimal_typo: bool = False
    # The original source-declared amount before a supplement-merge recovery
    # overwrote it. Set only on `total.us_dollar` cells when
    # `COST_SUPPLEMENT_MERGED` triggered a `ROW_TOTAL_COMPUTED` overwrite.
    # The table-level sum check uses this to verify the committee total
    # against the pre-supplement sum (the source declared the committee
    # total before supplements were merged into segment components).
    source_amount: Optional[Decimal] = None

    @field_serializer("amount")
    def _serialize_amount(self, value: Optional[Decimal]) -> Optional[str]:
        # Serialize as a string so JSON consumers never lose cent-level precision.
        return str(value) if value is not None else None

    @field_serializer("source_amount")
    def _serialize_source_amount(self, value: Optional[Decimal]) -> Optional[str]:
        return str(value) if value is not None else None


class CostGroup(BaseModel):
    """Foreign-currency / US-dollar-equivalent pair for one cost category."""

    foreign_currency: CostCell
    us_dollar: CostCell


class Costs(BaseModel):
    """The four cost categories reported for a segment or a table's total row."""

    per_diem: CostGroup
    transportation: CostGroup
    other: CostGroup
    total: CostGroup


class TravelSegment(BaseModel):
    """One arrival/departure/country/cost leg of a traveler's trip."""

    arrival_date: Optional[date] = None
    departure_date: Optional[date] = None
    arrival_raw: str
    departure_raw: str
    country_raw: str
    countries: list[str] = Field(default_factory=list)
    costs: Costs
    flags: list[str] = Field(default_factory=list)
    source_lines: list[int] = Field(default_factory=list)


class Traveler(BaseModel):
    """A named traveler and their segments within one report table."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str
    honorific: Optional[str] = None
    bioguide_id: Optional[str] = None
    match_confidence: Optional[float] = None
    segments: list[TravelSegment] = Field(default_factory=list)


class Sponsor(BaseModel):
    """A report's sponsoring entity, classified from its free-text title segment."""

    model_config = ConfigDict(str_strip_whitespace=True)

    type: SponsorType
    name: str
    code: Optional[str] = None
    raw: str


class Period(BaseModel):
    """The reporting period a table covers."""

    start: Optional[date] = None
    end: Optional[date] = None
    year: int
    quarter: Optional[int] = None


class Report(BaseModel):
    """One parsed report table: sponsor, period, and all traveler segments."""

    model_config = ConfigDict(str_strip_whitespace=True)

    report_id: str
    source_file: str
    table_index: int
    amended: bool = False
    superseded_by: Optional[str] = None
    parse_method: Literal["deterministic", "llm"] = "deterministic"
    sponsor: Sponsor
    period: Optional[Period] = None
    header_raw: str
    travelers: list[Traveler] = Field(default_factory=list)
    committee_total: Optional[Costs] = None
    footnotes: dict[str, str] = Field(default_factory=dict)
    signature_raw: Optional[str] = None
    flags: list[str] = Field(default_factory=list)
    layout_fingerprint: list[int] = Field(default_factory=list)
    layout_confidence: Optional[float] = None
