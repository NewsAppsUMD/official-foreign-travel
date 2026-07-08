"""Re-locate a report's raw source lines, for the review UI's side-by-side view."""

from pathlib import Path
from typing import List, Optional

from ..models.report import Report
from ..parsing.llm_fallback import _load_block


def get_raw_lines(report: Report, report_text_dir: Path) -> Optional[List[str]]:
    """Return the raw lines of the table block a report was parsed from, or None."""
    block = _load_block(report, report_text_dir)
    return block.lines if block is not None else None
