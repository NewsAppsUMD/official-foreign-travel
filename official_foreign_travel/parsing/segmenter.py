"""Split a raw report file into per-table blocks.

Unlike the legacy parser, table boundaries are found from the
"REPORT(S) OF EXPENDITURES FOR OFFICIAL FOREIGN TRAVEL" header lines
themselves rather than the inconsistently-present dashed start delimiter,
so files that never contain that delimiter still yield tables.
"""

import re
from dataclasses import dataclass, field
from typing import List

from ..utils.text import strip_html_tags

HEADER_RE = re.compile(
    r"^\s*(?:\(?AMENDED\)?\s+)?REPORTS?\s+OF\s+EXPENDITURES\s+FOR\s+OFFICIAL\s+"
    r"(?:FOREIGN\s+)?TRAVEL\b",
    re.IGNORECASE,
)
RULE_RE = re.compile(r"^-{20,}$")


@dataclass
class TableBlock:
    """A single report table, from its header line through its next table (or EOF)."""

    source_file: str
    table_index: int
    title_raw: str
    lines: List[str] = field(default_factory=list)
    start_line: int = 0


def segment_tables(text: str, source_file: str) -> List[TableBlock]:
    """
    Split file contents into TableBlocks, one per report header found.

    Args:
        text: Full raw file contents
        source_file: Filename, used for provenance

    Returns:
        List of TableBlock, in file order
    """
    raw_lines = strip_html_tags(text).split("\n")
    header_idxs = [i for i, line in enumerate(raw_lines) if HEADER_RE.search(line)]

    blocks = []
    for table_index, start in enumerate(header_idxs):
        end = header_idxs[table_index + 1] if table_index + 1 < len(header_idxs) else len(raw_lines)
        block_lines = raw_lines[start:end]

        title_lines: List[str] = []
        for line in block_lines:
            if title_lines and RULE_RE.match(line.strip()):
                break
            title_lines.append(line)
        title_raw = re.sub(r"\s+", " ", " ".join(t.strip() for t in title_lines)).strip()

        blocks.append(
            TableBlock(
                source_file=source_file,
                table_index=table_index,
                title_raw=title_raw,
                lines=block_lines,
                start_line=start + 1,
            )
        )
    return blocks
