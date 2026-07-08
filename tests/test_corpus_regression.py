"""Full-corpus regression guard: never parse fewer records than the legacy parser did.

Skipped automatically if report_text/ isn't present (e.g. a minimal checkout).
This is the floor established in tests/baseline_counts.json from the
previously-published travel_report_data.csv (55,093 rows).
"""

import collections
import json
from pathlib import Path

import pytest

from official_foreign_travel.parsing.assemble import assemble_directory

REPORT_TEXT_DIR = Path(__file__).parent.parent / "report_text"
BASELINE_PATH = Path(__file__).parent / "baseline_counts.json"

pytestmark = pytest.mark.skipif(
    not REPORT_TEXT_DIR.is_dir(), reason="report_text/ corpus not present in this checkout"
)


@pytest.fixture(scope="module")
def corpus_counts():
    per_year = collections.Counter()
    total = 0
    for report in assemble_directory(REPORT_TEXT_DIR):
        year = report.source_file[:4]
        n_seg = sum(len(t.segments) for t in report.travelers)
        per_year[year] += n_seg
        total += n_seg
    return {"total": total, "per_year": dict(per_year)}


def test_total_segments_meet_or_exceed_baseline(corpus_counts):
    baseline = json.loads(BASELINE_PATH.read_text())
    assert corpus_counts["total"] >= baseline["total"]


def test_every_year_meets_or_exceeds_baseline(corpus_counts):
    baseline = json.loads(BASELINE_PATH.read_text())
    shortfalls = {
        year: (corpus_counts["per_year"].get(year, 0), count)
        for year, count in baseline["per_year"].items()
        if corpus_counts["per_year"].get(year, 0) < count
    }
    assert shortfalls == {}, f"years below baseline (new, old): {shortfalls}"


def test_every_file_yields_at_least_one_report():
    from official_foreign_travel.parsing.segmenter import segment_tables

    empty_files = []
    for file_path in sorted(REPORT_TEXT_DIR.glob("*.txt")):
        text = file_path.read_text(encoding="utf-8", errors="replace")
        if not segment_tables(text, file_path.name):
            empty_files.append(file_path.name)
    assert empty_files == []
