"""Travel record models."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator, ConfigDict


class TravelRecordInput(BaseModel):
    """Raw input data from parsed travel report."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(..., description="Representative's name")
    member_id: Optional[str] = Field(None, description="Bioguide ID")
    honorific: Optional[str] = Field(None, description="Title (Hon., Speaker, etc.)")
    arrival_date: str = Field(..., description="Arrival date (M/D/YYYY)")
    departure_date: str = Field(..., description="Departure date (M/D/YYYY)")
    country: str = Field(..., description="Country visited")
    table_header: Optional[str] = Field(None, description="Report table header")
    committee: Optional[str] = Field(None, description="Committee name")
    committee_code: Optional[str] = Field(None, description="Committee code")
    source_file: Optional[str] = Field(None, description="Source filename")


class TravelRecord(BaseModel):
    """Validated and processed travel record."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(..., min_length=1, description="Representative's name")
    member_id: Optional[str] = Field(None, pattern=r"^[A-Z][0-9]{6}$", description="Bioguide ID")
    honorific: Optional[str] = Field(None, description="Title")
    arrival_date: datetime = Field(..., description="Arrival date")
    departure_date: datetime = Field(..., description="Departure date")
    country: str = Field(..., min_length=1, description="Country visited")
    table_header: Optional[str] = Field(None, description="Report table header")
    committee: Optional[str] = Field(None, description="Committee name")
    committee_code: Optional[str] = Field(None, description="Committee code")
    source_file: Optional[str] = Field(None, description="Source filename")
    year: int = Field(..., description="Year of travel")

    @field_validator("departure_date")
    @classmethod
    def validate_dates(cls, v: datetime, info) -> datetime:
        """Ensure departure is after arrival."""
        if "arrival_date" in info.data:
            arrival = info.data["arrival_date"]
            if isinstance(arrival, datetime) and v < arrival:
                raise ValueError("Departure date must be after arrival date")
        return v

    @classmethod
    def from_input(cls, input_data: TravelRecordInput) -> "TravelRecord":
        """Create TravelRecord from input data."""
        # Parse dates
        arrival_dt = datetime.strptime(input_data.arrival_date, "%m/%d/%Y")
        departure_dt = datetime.strptime(input_data.departure_date, "%m/%d/%Y")

        return cls(
            name=input_data.name,
            member_id=input_data.member_id,
            honorific=input_data.honorific,
            arrival_date=arrival_dt,
            departure_date=departure_dt,
            country=input_data.country,
            table_header=input_data.table_header,
            committee=input_data.committee,
            committee_code=input_data.committee_code,
            source_file=input_data.source_file,
            year=arrival_dt.year,
        )


class TravelRecordOutput(BaseModel):
    """Output format for CSV export."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str
    member_id: str
    honorific: str
    arrival_date: str
    departure_date: str
    country: str
    table_header: str
    committee: str
    committee_code: str
    source_file: str

    @classmethod
    def from_travel_record(cls, record: TravelRecord) -> "TravelRecordOutput":
        """Convert TravelRecord to output format."""
        return cls(
            name=record.name,
            member_id=record.member_id or "",
            honorific=record.honorific or "",
            arrival_date=record.arrival_date.strftime("%m/%d/%Y"),
            departure_date=record.departure_date.strftime("%m/%d/%Y"),
            country=record.country,
            table_header=record.table_header or "",
            committee=record.committee or "",
            committee_code=record.committee_code or "",
            source_file=record.source_file or "",
        )
