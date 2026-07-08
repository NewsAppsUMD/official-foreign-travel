"""Tests for Pydantic models."""

import pytest
from datetime import datetime
from pydantic import ValidationError

from official_foreign_travel.models.travel import (
    TravelRecord,
    TravelRecordInput,
    TravelRecordOutput,
)
from official_foreign_travel.models.member import MemberInput
from official_foreign_travel.models.committee import Committee
from official_foreign_travel.models.match import NameMatch, NameMatchResult


class TestTravelRecordInput:
    """Tests for TravelRecordInput model."""

    def test_valid_input(self, sample_travel_data):
        """Test creating valid travel record input."""
        record = TravelRecordInput(**sample_travel_data)
        assert record.name == "Hon. John Doe"
        assert record.member_id == "D000123"
        assert record.country == "United Kingdom"

    def test_strips_whitespace(self):
        """Test that whitespace is stripped from strings."""
        record = TravelRecordInput(
            name="  John Doe  ",
            arrival_date="1/15/2019",
            departure_date="1/20/2019",
            country="  UK  ",
        )
        assert record.name == "John Doe"
        assert record.country == "UK"

    def test_optional_fields(self):
        """Test that optional fields can be None."""
        record = TravelRecordInput(
            name="John Doe",
            arrival_date="1/15/2019",
            departure_date="1/20/2019",
            country="UK",
        )
        assert record.member_id is None
        assert record.honorific is None
        assert record.committee is None


class TestTravelRecord:
    """Tests for TravelRecord model."""

    def test_from_input(self, sample_travel_data):
        """Test creating TravelRecord from input data."""
        input_data = TravelRecordInput(**sample_travel_data)
        record = TravelRecord.from_input(input_data)

        assert record.name == "Hon. John Doe"
        assert record.member_id == "D000123"
        assert isinstance(record.arrival_date, datetime)
        assert isinstance(record.departure_date, datetime)
        assert record.year == 2019

    def test_bioguide_id_validation(self, sample_travel_data):
        """Test bioguide ID format validation."""
        input_data = TravelRecordInput(**sample_travel_data)

        # Valid bioguide ID
        record = TravelRecord.from_input(input_data)
        assert record.member_id == "D000123"

        # Invalid bioguide ID should fail
        sample_travel_data["member_id"] = "invalid"
        input_data = TravelRecordInput(**sample_travel_data)

        with pytest.raises(ValidationError):
            TravelRecord.from_input(input_data)

    def test_date_validation(self, sample_travel_data):
        """Test that departure must be after arrival."""
        # Swap dates to make departure before arrival
        sample_travel_data["arrival_date"] = "1/20/2019"
        sample_travel_data["departure_date"] = "1/15/2019"

        input_data = TravelRecordInput(**sample_travel_data)

        with pytest.raises(ValidationError, match="Departure date must be after arrival date"):
            TravelRecord.from_input(input_data)


class TestTravelRecordOutput:
    """Tests for TravelRecordOutput model."""

    def test_from_travel_record(self, sample_travel_data):
        """Test creating output from travel record."""
        input_data = TravelRecordInput(**sample_travel_data)
        record = TravelRecord.from_input(input_data)
        output = TravelRecordOutput.from_travel_record(record)

        assert output.name == "Hon. John Doe"
        assert output.member_id == "D000123"
        assert output.arrival_date == "01/15/2019"
        assert output.departure_date == "01/20/2019"

    def test_empty_fields_become_empty_strings(self, sample_travel_data):
        """Test that None values become empty strings in output."""
        sample_travel_data["member_id"] = None
        sample_travel_data["committee"] = None

        input_data = TravelRecordInput(**sample_travel_data)

        # Manually create record without member_id validation
        record = TravelRecord(
            name=input_data.name,
            member_id=None,
            honorific=input_data.honorific,
            arrival_date=datetime.strptime(input_data.arrival_date, "%m/%d/%Y"),
            departure_date=datetime.strptime(input_data.departure_date, "%m/%d/%Y"),
            country=input_data.country,
            year=2019,
        )

        output = TravelRecordOutput.from_travel_record(record)

        assert output.member_id == ""
        assert output.committee == ""


class TestMemberModels:
    """Tests for Member models."""

    def test_member_creation(self, sample_member_data):
        """Test creating a member input (raw CSV shape: name + bioguide_id)."""
        member = MemberInput(**sample_member_data)
        assert member.name == "John A. Doe"
        assert member.bioguide_id == "D000123"


class TestNameMatch:
    """Tests for NameMatch model."""

    def test_name_match_creation(self):
        """Test creating a name match."""
        match = NameMatch(
            bioguide_id="D000123",
            score=4.5,
            first_name="John",
            last_name="Doe",
        )
        assert match.bioguide_id == "D000123"
        assert match.score == 4.5


class TestNameMatchResult:
    """Tests for NameMatchResult model."""

    def test_confident_match(self):
        """Test identifying a confident match."""
        matches = [
            NameMatch(bioguide_id="D000123", score=5.0, first_name="John", last_name="Doe"),
            NameMatch(bioguide_id="S000456", score=2.0, first_name="Jane", last_name="Smith"),
        ]

        result = NameMatchResult(
            query_name="Hon. John Doe",
            arrival_date="1/15/2019",
            departure_date="1/20/2019",
            matches=matches,
        )

        result.validate_match(min_score=3.0, ambiguity_threshold=1.1)

        assert result.is_confident
        assert not result.is_inconclusive
        assert result.best_bioguide_id == "D000123"

    def test_inconclusive_match(self):
        """Test identifying an inconclusive match (ambiguous)."""
        matches = [
            NameMatch(bioguide_id="D000123", score=4.0, first_name="John", last_name="Doe"),
            NameMatch(bioguide_id="D000789", score=3.8, first_name="John", last_name="Dough"),
        ]

        result = NameMatchResult(
            query_name="Hon. John Doe",
            arrival_date="1/15/2019",
            departure_date="1/20/2019",
            matches=matches,
        )

        result.validate_match(min_score=3.0, ambiguity_threshold=1.1)

        assert not result.is_confident
        assert result.is_inconclusive

    def test_low_confidence_match(self):
        """Test identifying a low confidence match."""
        matches = [
            NameMatch(bioguide_id="D000123", score=2.0, first_name="John", last_name="Doe"),
        ]

        result = NameMatchResult(
            query_name="Hon. John Doe",
            arrival_date="1/15/2019",
            departure_date="1/20/2019",
            matches=matches,
        )

        result.validate_match(min_score=3.0, ambiguity_threshold=1.1)

        assert not result.is_confident
        assert not result.is_inconclusive
