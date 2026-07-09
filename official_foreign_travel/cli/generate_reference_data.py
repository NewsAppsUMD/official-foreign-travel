#!/usr/bin/env python3
"""CLI to regenerate members.csv/committees.csv from congress-legislators YAML data."""

import argparse
import sys
from pathlib import Path
from typing import Optional

import yaml

from ..scrapers.reference_data import (
    build_committees_index,
    build_members_index,
    write_name_index_csv,
)


def _load_yaml_docs(paths: list[Path], label: str) -> Optional[list[list]]:
    docs = []
    for path in paths:
        if not path.exists():
            print(f"Error: {label} file not found: {path} " "(run oft-download-legislators first)")
            return None
        docs.append(yaml.safe_load(path.read_text(encoding="utf-8")))
    return docs


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate members.csv/committees.csv from unitedstates/congress-legislators "
        "YAML data (download it first with oft-download-legislators)"
    )
    parser.add_argument(
        "--legislators-current",
        type=Path,
        default=Path("legislators-current.yaml"),
        help="Path to legislators-current.yaml (default: legislators-current.yaml)",
    )
    parser.add_argument(
        "--legislators-historical",
        type=Path,
        default=Path("legislators-historical.yaml"),
        help="Path to legislators-historical.yaml (default: legislators-historical.yaml)",
    )
    parser.add_argument(
        "--committees-current",
        type=Path,
        default=Path("committees-current.yaml"),
        help="Path to committees-current.yaml (default: committees-current.yaml)",
    )
    parser.add_argument(
        "--committees-historical",
        type=Path,
        default=Path("committees-historical.yaml"),
        help="Path to committees-historical.yaml (default: committees-historical.yaml)",
    )
    parser.add_argument(
        "--members-csv",
        type=Path,
        default=Path("members.csv"),
        help="Output path for members.csv (default: members.csv)",
    )
    parser.add_argument(
        "--committees-csv",
        type=Path,
        default=Path("committees.csv"),
        help="Output path for committees.csv (default: committees.csv)",
    )

    args = parser.parse_args()

    legislator_docs = _load_yaml_docs(
        [args.legislators_current, args.legislators_historical], "legislators"
    )
    if legislator_docs is None:
        return 1

    committee_docs = _load_yaml_docs(
        [args.committees_current, args.committees_historical], "committees"
    )
    if committee_docs is None:
        return 1

    members_result = build_members_index(legislator_docs)
    write_name_index_csv(members_result.rows, args.members_csv, ("name", "bioguide_id"))
    print(
        f"{args.members_csv}: {members_result.people_considered} House members considered, "
        f"{len(members_result.rows)} rows written "
        f"({len(members_result.dropped_ambiguous)} ambiguous names dropped, "
        f"{members_result.skipped_no_name} skipped for no usable name)"
    )

    committees_result = build_committees_index(committee_docs)
    write_name_index_csv(committees_result.rows, args.committees_csv, ("name", "code"))
    print(
        f"{args.committees_csv}: {committees_result.committees_considered} committees considered, "
        f"{len(committees_result.rows)} name rows written "
        f"({len(committees_result.collisions)} collisions resolved in favor of the earlier-listed doc)"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
