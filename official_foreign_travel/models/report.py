"""Canonical parsed-report models: Report -> Sponsor/Period/Traveler/TravelSegment/Costs."""

from datetime import date
from decimal import Decimal
from typing import Dict, List, Literal, Optional

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
    footnotes: List[str] = Field(default_factory=list)
    military_air: bool = False

    @field_serializer("amount")
    def _serialize_amount(self, value: Optional[Decimal]) -> Optional[str]:
        # Serialize as a string so JSON consumers never lose cent-level precision.
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
    countries: List[str] = Field(default_factory=list)
    costs: Costs
    flags: List[str] = Field(default_factory=list)
    source_lines: List[int] = Field(default_factory=list)


class Traveler(BaseModel):
    """A named traveler and their segments within one report table."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str
    honorific: Optional[str] = None
    bioguide_id: Optional[str] = None
    match_confidence: Optional[float] = None
    segments: List[TravelSegment] = Field(default_factory=list)


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
    travelers: List[Traveler] = Field(default_factory=list)
    committee_total: Optional[Costs] = None
    footnotes: Dict[str, str] = Field(default_factory=dict)
    signature_raw: Optional[str] = None
    flags: List[str] = Field(default_factory=list)
    layout_fingerprint: List[int] = Field(default_factory=list)
    layout_confidence: Optional[float] = None
