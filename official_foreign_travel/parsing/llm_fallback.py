"""Optional LLM fallback for tables that fail deterministic parsing/validation.

Off by default. Only routes tables that already failed to a reasonable
confidence bar (undetected/low-confidence layout, zero travelers extracted,
or an arithmetic mismatch alongside several unparseable cells) to the model.
The model's output is re-run through the same arithmetic invariant checks
used everywhere else -- it never gets a free pass, and amounts/dates in the
deterministic happy path are never touched by this module at all.

Uses Simon Willison's `llm` library (https://llm.datasette.io/) rather than
a provider-specific SDK, so any `llm`-registered model can be swapped in via
its model id -- e.g. an Anthropic model through the `llm-anthropic` plugin,
or a local or cloud-hosted model through `llm-ollama` (point `OLLAMA_HOST` at
`https://ollama.com` with `OLLAMA_API_KEY` set for Ollama's cloud models).
Requires the `llm` extra and whichever plugin(s)/credentials the chosen
model needs. Importing this module without `llm` installed raises ImportError
only when a repairer is actually constructed, not at import time.
"""

import json
import re
from pathlib import Path
from typing import Any, Optional, Protocol

from pydantic import BaseModel, Field, ValidationError

from ..matchers.name_matcher import NameMatcher
from ..models.report import Costs, Period, Report, Sponsor, Traveler
from ..utils.logging import get_logger
from .assemble import _match_member
from .segmenter import TableBlock, segment_tables
from .validate import validate_report

logger = get_logger(__name__)

DEFAULT_MODEL = "claude-opus-4.8"

REPORT_LEVEL_TRIGGERS = {"LAYOUT_LOW_CONFIDENCE", "LAYOUT_UNDETECTED", "NO_TRAVELERS_EXTRACTED"}
SEGMENT_PROBLEM_PREFIXES = ("UNPARSEABLE_", "ROW_SUM_MISMATCH")
MIN_SEGMENT_PROBLEMS_FOR_SUM_MISMATCH_TRIGGER = 2

PROMPT_TEMPLATE = """You are extracting structured data from a U.S. House of Representatives \
"Official Foreign Travel" expenditure report table. This is a fixed-width text table that \
failed automatic column-based parsing. Extract:

- The sponsor (committee/delegation/commission/individual/etc.) and the reporting period.
- Every named traveler and each of their travel segments: arrival date, departure date \
(as YYYY-MM-DD, inferring the year from the reporting period), and country/countries visited.
- The four cost categories (per diem, transportation, other purposes, total), each with a \
foreign-currency amount and a US-dollar-equivalent amount, exactly as printed. Use null for \
any amount that is blank or dot-filled in the source -- never invent a number.
- Footnote markers (e.g. "\\3\\") attached to a cost cell, and the footnote definitions \
themselves if present in the table.
- The committee/table total row, if present.

Respond with ONLY a single JSON object, no other text and no markdown code fences, that \
validates against this JSON Schema:
{schema_json}

Table text:
---
{table_text}
---
"""

# Defensive: some models wrap JSON in a markdown code fence despite instructions not to.
_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?|\n?```\s*$", re.MULTILINE)


class LLMReportDraft(BaseModel):
    """The subset of Report fields an LLM repair pass is asked to produce."""

    sponsor: Sponsor
    period: Optional[Period] = None
    header_raw: str = ""
    travelers: list[Traveler] = Field(default_factory=list)
    committee_total: Optional[Costs] = None
    footnotes: dict[str, str] = Field(default_factory=dict)


# Anthropic's (and possibly other backends') constrained-decoding structured-output
# mode hangs indefinitely on schemas much smaller than this one -- confirmed
# empirically: a flat 11-field schema alone was enough to reproduce it, well below
# what LLMReportDraft needs once Sponsor/Period/Traveler/TravelSegment/Costs are
# expanded. So the schema is embedded as prompt text instead of passed as `schema=`
# to `model.prompt()`; the model free-generates JSON, which is then validated the
# same way either way. This is slower to fail (no grammar rejects a bad token) but
# actually completes, and is more portable across `llm` plugins besides.
_REPORT_DRAFT_SCHEMA_JSON = json.dumps(LLMReportDraft.model_json_schema())


class TableRepairer(Protocol):
    """Interface for a fallback parser given a raw table block and the failed deterministic Report."""

    def repair(self, block: TableBlock, report: Report) -> Optional[Report]:
        """Return a replacement Report (parse_method="llm"), or None if it can't help."""
        ...


class LLMTableRepairer:
    """
    Repairs a table via any model registered with Simon Willison's `llm` library.

    `model_id` is resolved with `llm.get_model()`, so it can name a model from
    any installed `llm` plugin -- an Anthropic model via `llm-anthropic`
    (e.g. "claude-opus-4.8"), or a local/cloud Ollama model via `llm-ollama`
    (e.g. "llama3.1:70b", or a "*-cloud" model id against
    `OLLAMA_HOST=https://ollama.com`). Swap models by changing `model_id`;
    no other code here is provider-specific.
    """

    def __init__(self, model_id: str = DEFAULT_MODEL, model: Optional[Any] = None, **options: Any):
        self.model_id = model_id
        self.options = options
        self._model = model

    @property
    def model(self) -> Any:
        if self._model is None:
            import llm  # optional dependency; only imported if actually used

            self._model = llm.get_model(self.model_id)
        return self._model

    def repair(self, block: TableBlock, report: Report) -> Optional[Report]:
        table_text = "\n".join(block.lines)
        prompt = PROMPT_TEMPLATE.format(
            schema_json=_REPORT_DRAFT_SCHEMA_JSON, table_text=table_text
        )

        try:
            # Deliberately not passing schema= here -- see the note on
            # _REPORT_DRAFT_SCHEMA_JSON. The shape is described in the prompt
            # instead, and validated below exactly as it would be either way.
            response = self.model.prompt(prompt, **self.options)
            raw_text = _CODE_FENCE_RE.sub("", response.text()).strip()
        except Exception as e:
            logger.warning(
                f"LLM repair request failed for {report.report_id} ({self.model_id}): {e}"
            )
            return None

        try:
            draft = LLMReportDraft.model_validate_json(raw_text)
        except (ValidationError, ValueError) as e:
            logger.warning(
                f"LLM repair returned unparseable output for {report.report_id} "
                f"({self.model_id}): {e}"
            )
            return None

        return Report(
            report_id=report.report_id,
            source_file=report.source_file,
            table_index=report.table_index,
            amended=report.amended,
            parse_method="llm",
            sponsor=draft.sponsor,
            period=draft.period,
            header_raw=draft.header_raw or report.header_raw,
            travelers=draft.travelers,
            committee_total=draft.committee_total,
            footnotes=draft.footnotes,
            signature_raw=report.signature_raw,
            layout_fingerprint=report.layout_fingerprint,
            layout_confidence=report.layout_confidence,
        )


def needs_repair(report: Report) -> bool:
    """Whether a report is a good candidate for LLM repair, per the trigger criteria."""
    if any(flag in REPORT_LEVEL_TRIGGERS for flag in report.flags):
        return True

    if "TABLE_SUM_MISMATCH" in report.flags:
        segment_problems = sum(
            1
            for traveler in report.travelers
            for segment in traveler.segments
            for flag in segment.flags
            if flag.startswith(SEGMENT_PROBLEM_PREFIXES)
        )
        if segment_problems >= MIN_SEGMENT_PROBLEMS_FOR_SUM_MISMATCH_TRIGGER:
            return True

    return False


def _passes_invariants(report: Report) -> bool:
    if not report.travelers:
        return False
    bad_flags = {"ROW_SUM_MISMATCH", "TABLE_SUM_MISMATCH"}
    if any(flag in bad_flags for flag in report.flags):
        return False
    if any(flag in bad_flags for t in report.travelers for s in t.segments for flag in s.flags):
        return False
    return True


def _match_members_in_place(
    report: Report,
    member_index: Optional[dict[str, str]],
    name_matcher: Optional[NameMatcher],
    disambiguation_index: Optional[dict[tuple[str, str], str]] = None,
) -> None:
    """
    Run the same exact/fuzzy member-matching pipeline over LLM-repaired travelers.

    `LLMTableRepairer.repair()` has no way to know about members.csv or the
    fuzzy matcher, so a freshly-repaired Report's travelers all come back
    with bioguide_id=None. Without this, every table that ever goes through
    --llm-fallback would permanently lose member-ID enrichment, even for
    names an exact match would resolve instantly.
    """
    member_index = member_index or {}
    for traveler in report.travelers:
        bioguide_id, confidence, flags = _match_member(
            traveler.name,
            traveler.segments,
            member_index,
            name_matcher,
            honorific=traveler.honorific,
            sponsor_code=report.sponsor.code,
            disambiguation_index=disambiguation_index,
            period=report.period,
        )
        traveler.bioguide_id = bioguide_id
        traveler.match_confidence = confidence
        report.flags.extend(flags)


def _load_block(report: Report, report_text_dir: Path) -> Optional[TableBlock]:
    """Re-locate a report's raw table block by re-segmenting its source file.

    Shared by the LLM fallback and the review UI (official_foreign_travel.review.
    source_lookup) -- keep this caller-agnostic rather than LLM-repair-specific.
    """
    file_path = report_text_dir / report.source_file
    if not file_path.exists():
        logger.warning(f"Cannot load source file: {file_path}")
        return None
    text = file_path.read_text(encoding="utf-8", errors="replace")
    blocks = segment_tables(text, report.source_file)
    if report.table_index >= len(blocks):
        return None
    return blocks[report.table_index]


def apply_llm_fallback(
    reports: list[Report],
    repairer: TableRepairer,
    report_text_dir: Optional[Path] = None,
    fail_report_path: Optional[Path] = None,
    member_index: Optional[dict[str, str]] = None,
    name_matcher: Optional[NameMatcher] = None,
    disambiguation_index: Optional[dict[tuple[str, str], str]] = None,
) -> list[Report]:
    """
    Repair failing reports via `repairer`, replacing them only if the result validates.

    Args:
        reports: Assembled (and already validated/deduplicated) reports
        repairer: A TableRepairer, e.g. LLMTableRepairer()
        report_text_dir: Directory containing the original *.txt report files
            (default: current directory)
        fail_report_path: If given, write tables that still fail after repair
            to this JSON file for human review
        member_index: Uppercase traveler name -> bioguide ID, for exact matching
            on LLM-repaired travelers (same index used for the deterministic pass)
        name_matcher: Optional NameMatcher for fuzzy fallback on LLM-repaired
            travelers whose name doesn't exactly match `member_index`
        disambiguation_index: Optional (uppercase name, sponsor code) -> bioguide ID
            for names ambiguous even with dates (same index as the deterministic pass)

    Returns:
        The same list, with successfully-repaired reports replaced in place
    """
    report_text_dir = report_text_dir or Path(".")
    failures: list[dict[str, Any]] = []

    for index, report in enumerate(reports):
        if not needs_repair(report):
            continue

        block = _load_block(report, report_text_dir)
        if block is None:
            failures.append({"report_id": report.report_id, "reason": "source_block_unavailable"})
            continue

        candidate = repairer.repair(block, report)
        if candidate is None:
            failures.append({"report_id": report.report_id, "reason": "repair_returned_none"})
            continue

        _match_members_in_place(candidate, member_index, name_matcher, disambiguation_index)

        validate_report(candidate)
        if _passes_invariants(candidate):
            if "LLM_PARSED" not in candidate.flags:
                candidate.flags.append("LLM_PARSED")
            reports[index] = candidate
        else:
            report.flags.append("LLM_UNVERIFIED")
            failures.append(
                {
                    "report_id": report.report_id,
                    "reason": "llm_output_failed_invariants",
                    "llm_flags": candidate.flags,
                }
            )

    if fail_report_path is not None and failures:
        fail_report_path.write_text(json.dumps(failures, indent=2))

    return reports
