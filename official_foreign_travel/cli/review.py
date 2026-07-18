#!/usr/bin/env python3
"""CLI for the local report review server."""

import argparse
import gzip
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from ..models.report import Report
from ..review.server import run_server


def _read_json(path: Path) -> str:
    """Read a JSON file, transparently decompressing .gz."""
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return f.read()
    return path.read_text(encoding="utf-8")


def _load_reports(parsed_json: Path) -> list[Report]:
    """Load parsed reports from either a single JSON file (from
    ``oft-parse <input> output.json``) or a directory of per-year JSON
    files (from ``oft-parse <input> output/ --split-by-year``). Files
    ending in ``.json.gz`` are decompressed transparently.
    """
    if parsed_json.is_dir():
        reports: list[Report] = []
        paths = sorted(
            p
            for p in parsed_json.iterdir()
            if p.name.endswith(".json") or p.name.endswith(".json.gz")
        )
        if not paths:
            raise FileNotFoundError(
                f"No .json or .json.gz files found in directory: {parsed_json}"
            )
        for path in paths:
            payload = json.loads(_read_json(path))
            reports.extend(Report.model_validate(r) for r in payload["reports"])
        return reports

    payload = json.loads(_read_json(parsed_json))
    return [Report.model_validate(r) for r in payload["reports"]]


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
        print(f"Error: file or directory not found: {args.parsed_json}")
        return 1

    try:
        reports = _load_reports(args.parsed_json)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        return 1
    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON in {args.parsed_json}: {e}")
        return 1
    except KeyError:
        print(
            f"Error: {args.parsed_json} is missing a top-level 'reports' key "
            "(is this an oft-parse output file?)"
        )
        return 1
    except ValidationError as e:
        print(f"Error: {args.parsed_json} contains invalid report data: {e}")
        return 1

    run_server(reports, args.report_text_dir, args.corrections, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
