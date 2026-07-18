"""Configuration management."""

from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    """Application configuration."""

    model_config = SettingsConfigDict(
        env_prefix="OFT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Paths
    data_dir: Path = Field(default=Path("data"), description="Data directory")
    report_text_dir: Path = Field(
        default=Path("report_text"), description="Downloaded reports directory"
    )
    output_dir: Path = Field(default=Path("output"), description="Output directory")

    # CSV files
    members_csv: Path = Field(default=Path("members.csv"), description="Members CSV file")
    committees_csv: Path = Field(default=Path("committees.csv"), description="Committees CSV file")
    member_disambiguation_csv: Path = Field(
        default=Path("member_disambiguation.csv"),
        description="Hand-curated (name, sponsor code) -> bioguide ID disambiguation CSV",
    )

    # YAML files (from congress-legislators repo)
    legislators_current_yaml: Path = Field(
        default=Path("legislators-current.yaml"),
        description="Current legislators YAML file",
    )
    legislators_historical_yaml: Path = Field(
        default=Path("legislators-historical.yaml"),
        description="Historical legislators YAML file",
    )

    # Scraper settings
    base_url: str = Field(
        default="https://disclosures-clerk.house.gov",
        description="Base URL for House Clerk disclosures site",
    )
    start_year: int = Field(default=1994, description="Start year for scraping")
    end_year: int = Field(default=2027, description="End year (exclusive) for scraping")
    request_timeout: int = Field(default=30, description="HTTP request timeout in seconds")
    retry_attempts: int = Field(default=3, description="Number of retry attempts")
    retry_delay: float = Field(default=2.0, description="Delay between retries in seconds")

    # Name matching settings
    min_match_score: float = Field(default=3.0, description="Minimum score for confident match")
    ambiguity_threshold: float = Field(default=1.1, description="Threshold for ambiguous matches")
    match_return_count: int = Field(default=5, description="Number of matches to return")

    # Logging
    log_level: str = Field(default="INFO", description="Logging level")
    log_file: Optional[Path] = Field(None, description="Log file path")

    def __init__(self, **kwargs):
        """Initialize and create directories if needed."""
        super().__init__(**kwargs)
        # Create directories if they don't exist
        for path_field in ["data_dir", "report_text_dir", "output_dir"]:
            path = getattr(self, path_field)
            if path and not path.exists():
                path.mkdir(parents=True, exist_ok=True)


# Global config instance
_config: Optional[Config] = None


def get_config() -> Config:
    """Get or create the global config instance."""
    global _config
    if _config is None:
        _config = Config()
    return _config


def set_config(config: Config) -> None:
    """Set the global config instance."""
    global _config
    _config = config
