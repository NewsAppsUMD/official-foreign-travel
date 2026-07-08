"""Smoke test for the oft-test-matching CLI.

Regression guard: this CLI iterates ReportParser.parse_file() results, which
changed shape (flat records -> Report -> Traveler -> TravelSegment) when the
parser was rebuilt. Confirms the CLI still runs against real Report objects
without crashing, even with no legislator data available (NameMatcher
degrades gracefully to "no matches" rather than erroring).
"""

import sys
from pathlib import Path

from official_foreign_travel.cli.test_matching import main as run_test_matching_cli

FIXTURES = Path(__file__).parent / "fixtures"


def test_runs_without_crashing_against_real_reports(tmp_path, monkeypatch):
    cache_path = tmp_path / "names_index.pickle"
    output_path = tmp_path / "issues.log"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "oft-test-matching",
            str(FIXTURES),
            str(output_path),
            "--no-cache",
            "--cache",
            str(cache_path),
            "--legislators-current",
            str(tmp_path / "missing-current.yaml"),
            "--legislators-historical",
            str(tmp_path / "missing-historical.yaml"),
        ],
    )

    code = run_test_matching_cli()

    assert code == 0
    assert output_path.exists()
