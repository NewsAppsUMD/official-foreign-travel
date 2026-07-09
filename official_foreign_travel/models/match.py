"""Name matching result models."""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class NameMatch(BaseModel):
    """A single name match result."""

    bioguide_id: str = Field(..., description="Bioguide ID")
    score: float = Field(..., ge=0.0, description="Match confidence score")
    first_name: str = Field(..., description="First name")
    last_name: str = Field(..., description="Last name")

    def __lt__(self, other: "NameMatch") -> bool:
        """Sort by descending score."""
        return self.score > other.score


class NameMatchResult(BaseModel):
    """Result of name matching operation."""

    model_config = ConfigDict(str_strip_whitespace=True)

    query_name: str = Field(..., description="Original query name")
    arrival_date: str = Field(..., description="Arrival date")
    departure_date: str = Field(..., description="Departure date")
    matches: list[NameMatch] = Field(default_factory=list, description="Ranked matches")
    top_match: Optional[NameMatch] = Field(None, description="Best match")
    is_confident: bool = Field(False, description="Whether match is confident")
    is_inconclusive: bool = Field(False, description="Whether match is inconclusive")

    @property
    def best_bioguide_id(self) -> Optional[str]:
        """Get the bioguide ID of the best match."""
        return self.top_match.bioguide_id if self.top_match else None

    def validate_match(self, min_score: float = 3.0, ambiguity_threshold: float = 1.1) -> None:
        """Validate and set confidence flags."""
        if not self.matches:
            self.is_confident = False
            self.is_inconclusive = False
            return

        self.top_match = self.matches[0]

        # Check if score is too low
        if self.top_match.score < min_score:
            self.is_confident = False
            self.is_inconclusive = False
            return

        # Check if multiple close matches (ambiguous)
        if len(self.matches) > 1:
            second_score = self.matches[1].score
            if second_score * ambiguity_threshold > self.top_match.score:
                self.is_confident = False
                self.is_inconclusive = True
                return

        self.is_confident = True
        self.is_inconclusive = False
