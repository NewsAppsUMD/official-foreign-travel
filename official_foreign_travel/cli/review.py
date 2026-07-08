#!/usr/bin/env python3
"""CLI for the local report review server."""

import argparse
import json
import sys
from pathlib import Path

from ..models.report import Report
from ..review.server import run_server


def main() -> int:
    """Main entry point for review CLI."""
    parser = argparse.ArgumentParser(
        description="Review flagged parser output side-by-side with the original source text"
    )
    parser.add_argument(
        "report_text_dir", type=Path, help="Directory of original *.txt report files"
    )
    parser.add_argument("parsed_json", type=Path, help="Parsed output JSON, from oft-parse")
    parser.add_argument(
        "--corrections",
        type=Path,
        default=Path("corrections.json"),
        help="Corrections overlay file to read/write (default: corrections.json)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind (default: 8765)")

    args = parser.parse_args()

    if not args.report_text_dir.is_dir():
        print(f"Error: not a directory: {args.report_text_dir}")
        return 1
    if not args.parsed_json.exists():
        print(f"Error: file not found: {args.parsed_json}")
        return 1

    payload = json.loads(args.parsed_json.read_text(encoding="utf-8"))
    reports = [Report.model_validate(r) for r in payload["reports"]]

    run_server(reports, args.report_text_dir, args.corrections, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
