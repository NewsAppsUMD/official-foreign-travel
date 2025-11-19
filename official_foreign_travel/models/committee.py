"""Committee models."""

from pydantic import BaseModel, Field, ConfigDict


class Committee(BaseModel):
    """House committee information."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(..., description="Committee name (uppercase)")
    code: str = Field(..., description="Committee code")
