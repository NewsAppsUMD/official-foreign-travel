#!/usr/bin/env python3
"""CLI for parsing foreign travel reports."""

import argparse
import json
import logging
import sys
from pathlib import Path

from ..scrapers.report_parser import ReportParser
from ..utils.config import get_config
from ..utils.logging import setup_logger

FORMAT_BY_EXTENSION = {".json": "json", ".csv": "csv", ".jsonl": "jsonl"}


def _infer_format(output: Path, explicit: str) -> str:
    if explicit != "auto":
        return explicit
    return FORMAT_BY_EXTENSION.get(output.suffix.lower(), "json")


def main() -> int:
    """Main entry point for parse CLI."""
    parser = argparse.ArgumentParser(description="Parse foreign travel reports from text files")
    parser.add_argument(
        "input",
        type=Path,
        help="Input file or directory containing report text files",
    )
    parser.add_argument(
        "output",
        type=Path,
        help="Output file (format inferred from extension unless --format is given)",
    )
    parser.add_argument(
        "--format",
        choices=["auto", "json", "csv", "jsonl"],
        default="auto",
        help="Output format (default: inferred from output file extension, else json)",
    )
    parser.add_argument(
        "--members-csv",
        type=Path,
        help="Members CSV file (default: members.csv)",
    )
    parser.add_argument(
        "--committees-csv",
        type=Path,
        help="Committees CSV file (default: committees.csv)",
    )
    parser.add_argument(
        "--include-superseded",
        action="store_true",
        help="Include amended-report duplicates that were superseded by a later publication",
    )
    parser.add_argument(
        "--fuzzy-name-matching",
        action="store_true",
        help="Fall back to fuzzy name matching (via legislator YAML data) when a traveler "
        "name doesn't exactly match members.csv",
    )
    parser.add_argument(
        "--llm-fallback",
        action="store_true",
        help="Route tables that fail deterministic parsing/validation to an LLM via Simon "
        "Willison's `llm` library (requires the 'llm' extra plus whichever plugin/credentials "
        "--llm-model needs; off by default)",
    )
    parser.add_argument(
        "--llm-model",
        default=None,
        help="`llm`-registered model id for --llm-fallback, e.g. 'claude-opus-4.8' (default; "
        "needs ANTHROPIC_API_KEY) or an Ollama model id such as 'llama3.1:70b' (needs "
        "OLLAMA_HOST, and OLLAMA_API_KEY for Ollama's cloud models)",
    )
    parser.add_argument(
        "--fail-report",
        type=Path,
        help="Write tables that still fail after --llm-fallback to this JSON file for review",
    )
    parser.add_argument(
        "--apply-corrections",
        type=Path,
        help="Merge human corrections from this file (written by oft-review) into the output",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        help="Log file path",
    )

    args = parser.parse_args()

    if not args.input.exists():
        print(f"Error: Input path does not exist: {args.input}")
        return 1

    log_level = getattr(logging, args.log_level)
    setup_logger("official_foreign_travel", level=log_level, log_file=args.log_file)

    config = get_config()
    if args.members_csv:
        config.members_csv = args.members_csv
    if args.committees_csv:
        config.committees_csv = args.committees_csv

    name_matcher = None
    if args.fuzzy_name_matching:
        from ..matchers.name_matcher import NameMatcher

        name_matcher = NameMatcher(config)
        name_matcher.initialize()

    report_parser = ReportParser(config, name_matcher=name_matcher)

    output_format = _infer_format(args.output, args.format)

    print(f"Input: {args.input}")
    print(f"Output: {args.output} ({output_format})")

    reports = report_parser.parse_and_finalize(args.input)

    if args.llm_fallback:
        from ..parsing.llm_fallback import DEFAULT_MODEL, LLMTableRepairer, apply_llm_fallback

        model_id = args.llm_model or DEFAULT_MODEL
        print(f"LLM fallback model: {model_id}")
        report_text_dir = args.input if args.input.is_dir() else args.input.parent
        reports = apply_llm_fallback(
            reports,
            LLMTableRepairer(model_id=model_id),
            report_text_dir=report_text_dir,
            fail_report_path=args.fail_report,
            member_index=report_parser.member_index,
            name_matcher=name_matcher,
            disambiguation_index=report_parser.disambiguation_index,
        )

    if args.apply_corrections:
        if not args.apply_corrections.exists():
            print(f"Error: corrections file not found: {args.apply_corrections}")
            return 1

        from ..review.corrections import apply_corrections, load_corrections

        try:
            corrections = load_corrections(args.apply_corrections)
        except json.JSONDecodeError as e:
            print(f"Error: invalid JSON in {args.apply_corrections}: {e}")
            return 1

        if not isinstance(corrections, dict):
            print(
                f"Error: {args.apply_corrections} is not a corrections overlay file "
                "(expected a JSON object keyed by report_id, is this an oft-review "
                "corrections.json?)"
            )
            return 1

        matched = sum(1 for r in reports if r.report_id in corrections)
        print(f"Applying corrections: {matched} of {len(corrections)} matched a parsed report")
        reports = apply_corrections(reports, corrections)

    if output_format == "json":
        report_parser.write_json(reports, args.output, include_superseded=args.include_superseded)
        from ..parsing.serialize import visible_reports

        stats = {"reports": len(visible_reports(reports, args.include_superseded))}
    elif output_format == "csv":
        stats = report_parser.write_csv(
            reports, args.output, include_superseded=args.include_superseded
        )
    else:
        stats = report_parser.write_jsonl(
            reports, args.output, include_superseded=args.include_superseded
        )

    n_superseded = sum(1 for r in reports if r.superseded_by is not None)
    n_flagged = sum(1 for r in reports if r.flags)

    print("\nParsing complete!")
    print(f"  Total reports: {len(reports)}")
    print(f"  Superseded (amended) reports: {n_superseded}")
    print(f"  Reports with flags: {n_flagged}")
    for key, value in stats.items():
        print(f"  {key}: {value}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
