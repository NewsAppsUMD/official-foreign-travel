"""Pytest configuration and fixtures."""

import pytest
from pathlib import Path
from datetime import datetime


@pytest.fixture
def sample_travel_data():
    """Sample travel record data for testing."""
    return {
        "name": "Hon. John Doe",
        "member_id": "D000123",
        "honorific": "Hon.",
        "arrival_date": "1/15/2019",
        "departure_date": "1/20/2019",
        "country": "United Kingdom",
        "table_header": "REPORT OF EXPENDITURES FOR OFFICIAL FOREIGN TRAVEL, 2019",
        "committee": "COMMITTEE ON FOREIGN AFFAIRS",
        "committee_code": "HSFA",
        "source_file": "2019q1jan15.txt",
    }


@pytest.fixture
def sample_member_data():
    """Sample member data for testing."""
    return {
        "name": "John A. Doe",
        "bioguide_id": "D000123",
    }


@pytest.fixture
def temp_test_dir(tmp_path):
    """Create a temporary test directory."""
    test_dir = tmp_path / "test_data"
    test_dir.mkdir()
    return test_dir
