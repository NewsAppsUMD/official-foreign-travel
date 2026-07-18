"""Split a raw report file into per-table blocks.

Unlike the legacy parser, table boundaries are found from the
"REPORT(S) OF EXPENDITURES FOR OFFICIAL FOREIGN TRAVEL" header lines
themselves rather than the inconsistently-present dashed start delimiter,
so files that never contain that delimiter still yield tables.

When a committee's report spans a page break, the next page begins with
a new `REPORT OF EXPENDITURES...--Continued` header. The segmenter splits
these into two TableBlocks, but they are one logical table: the original
block holds the data rows, the Continued block holds the trailing
supplemental rows and the `Committee total`. `segment_tables` merges the
two before returning so downstream code sees one block per logical
table.
"""

import re
from dataclasses import dataclass, field

from ..utils.text import strip_html_tags

HEADER_RE = re.compile(
    r"^\s*(?:\(?AMENDED\)?\s+)?REPORTS?\s+OF\s+EXPENDITURES\s+FOR\s+OFFICIAL\s+"
    r"(?:FOREIGN\s+)?TRAVEL\b",
    re.IGNORECASE,
)
RULE_RE = re.compile(r"^-{20,}$")
CONTINUED_SUFFIX_RE = re.compile(r"--Continued\s*$")


@dataclass
class TableBlock:
    """A single report table, from its header line through its next table (or EOF)."""

    source_file: str
    table_index: int
    title_raw: str
    lines: list[str] = field(default_factory=list)
    start_line: int = 0


def _merge_continued_blocks(blocks: list[TableBlock]) -> list[TableBlock]:
    """Fold each `--Continued` block into the most recent earlier block
    with the matching title (minus the `--Continued` suffix).

    The Continued block's lines are appended verbatim to the original
    block's lines. The duplicated column-header boilerplate (title,
    dashed separators, "Name of Member" label row) is preserved in the
    merged lines because `extract_rows`'s no-token branch safely ignores
    it: the label row fails `_looks_like_personal_name` (contains the
    stopword "of"), and `detect_layout._find_header_window` only uses the
    *first* "Name of Member" occurrence, so the second header section
    doesn't perturb column-boundary detection.
    """
    if not blocks:
        return blocks

    merged: list[TableBlock] = []
    for block in blocks:
        m = CONTINUED_SUFFIX_RE.search(block.title_raw)
        if m and merged:
            orig_title = CONTINUED_SUFFIX_RE.sub("", block.title_raw).strip()
            for j in range(len(merged) - 1, -1, -1):
                if merged[j].title_raw == orig_title:
                    merged[j].lines.extend(block.lines)
                    break
            else:
                merged.append(block)
        else:
            merged.append(block)

    for i, block in enumerate(merged):
        block.table_index = i

    return merged


def segment_tables(text: str, source_file: str) -> list[TableBlock]:
    """
    Split file contents into TableBlocks, one per report header found.

    `--Continued` blocks are merged into their matching earlier block so
    that one logical report table yields one TableBlock.

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

        title_lines: list[str] = []
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
    return _merge_continued_blocks(blocks)
