"""Smoke tests for the oft-parse CLI."""

import csv
import json
import sys
from pathlib import Path

import pytest

from official_foreign_travel.cli.parse import main as parse_main

FIXTURES = Path(__file__).parent / "fixtures"


def run_cli(args, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["oft-parse"] + args)
    return parse_main()


class TestParseCli:
    def test_json_output_default_format(self, tmp_path, monkeypatch, capsys):
        out = tmp_path / "out.json"
        code = run_cli([str(FIXTURES / "2019q1jan29.txt"), str(out)], monkeypatch)
        assert code == 0
        data = json.loads(out.read_text())
        assert len(data["reports"]) == 4

    def test_csv_output_by_extension(self, tmp_path, monkeypatch):
        out = tmp_path / "out.csv"
        code = run_cli([str(FIXTURES / "2019q1jan29.txt"), str(out)], monkeypatch)
        assert code == 0
        with open(out) as f:
            rows = list(csv.DictReader(f))
        assert len(rows) > 0

    def test_explicit_format_flag_overrides_extension(self, tmp_path, monkeypatch):
        out = tmp_path / "out.txt"
        code = run_cli(
            [str(FIXTURES / "2019q1jan29.txt"), str(out), "--format", "jsonl"], monkeypatch
        )
        assert code == 0
        lines = out.read_text().strip().split("\n")
        for line in lines:
            json.loads(line)

    def test_directory_input_parses_all_fixtures(self, tmp_path, monkeypatch):
        out = tmp_path / "out.json"
        code = run_cli([str(FIXTURES), str(out)], monkeypatch)
        assert code == 0
        data = json.loads(out.read_text())
        assert len(data["reports"]) > 50

    def test_missing_input_returns_error_code(self, tmp_path, monkeypatch):
        code = run_cli([str(tmp_path / "nonexistent.txt"), str(tmp_path / "out.json")], monkeypatch)
        assert code == 1

    def test_include_superseded_flag_never_yields_fewer_reports(self, tmp_path, monkeypatch):
        """--include-superseded adds back any amended-report duplicates; never removes reports."""
        out_default = tmp_path / "default.json"
        run_cli([str(FIXTURES), str(out_default)], monkeypatch)
        default_count = len(json.loads(out_default.read_text())["reports"])

        out_all = tmp_path / "all.json"
        run_cli([str(FIXTURES), str(out_all), "--include-superseded"], monkeypatch)
        all_count = len(json.loads(out_all.read_text())["reports"])

        assert all_count >= default_count
