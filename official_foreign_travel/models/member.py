"""Member/legislator models."""

from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class MemberInput(BaseModel):
    """Raw member data from CSV."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(..., description="Member name (uppercase)")
    bioguide_id: str = Field(..., description="Bioguide ID")


class Member(BaseModel):
    """Complete member information from YAML data."""

    model_config = ConfigDict(str_strip_whitespace=True)

    bioguide_id: str = Field(..., description="Bioguide ID")
    first_name: str = Field(..., description="First name")
    middle_name: Optional[str] = Field(None, description="Middle name")
    last_name: str = Field(..., description="Last name")
    suffix: Optional[str] = Field(None, description="Suffix (Jr., Sr., etc.)")
    nickname: Optional[str] = Field(None, description="Nickname")

    # Normalized versions for matching
    first_name_lower: str = Field(..., description="Lowercase normalized first name")
    middle_name_lower: str = Field("", description="Lowercase normalized middle name")
    last_name_lower: str = Field(..., description="Lowercase normalized last name")
    suffix_lower: str = Field("", description="Lowercase normalized suffix")
    nickname_lower: str = Field("", description="Lowercase normalized nickname")


class MemberTerm(BaseModel):
    """A term of service for a member."""

    start_date: date = Field(..., description="Term start date")
    end_date: date = Field(..., description="Term end date")
    chamber: str = Field(..., description="Chamber (house/senate)")
    state: Optional[str] = Field(None, description="State represented")
    party: Optional[str] = Field(None, description="Party affiliation")
