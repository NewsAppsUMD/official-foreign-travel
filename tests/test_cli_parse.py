"""Smoke tests for the oft-parse CLI."""

import csv
import json
import sys
from pathlib import Path

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


class TestApplyCorrections:
    def test_correction_is_merged_into_output(self, tmp_path, monkeypatch):
        corrections_path = tmp_path / "corrections.json"
        # Real report_id for the first table in this fixture, from prior runs of oft-parse.
        out_first = tmp_path / "first.json"
        run_cli([str(FIXTURES / "2019q1jan29.txt"), str(out_first)], monkeypatch)
        first_data = json.loads(out_first.read_text())
        report_id = first_data["reports"][0]["report_id"]
        original_sponsor_name = first_data["reports"][0]["sponsor"]["name"]

        corrections_path.write_text(
            json.dumps(
                {
                    report_id: {
                        "status": "edited",
                        "edits": {"sponsor.name": "Corrected Sponsor Name"},
                    }
                }
            )
        )

        out_corrected = tmp_path / "corrected.json"
        code = run_cli(
            [
                str(FIXTURES / "2019q1jan29.txt"),
                str(out_corrected),
                "--apply-corrections",
                str(corrections_path),
            ],
            monkeypatch,
        )
        assert code == 0

        corrected_data = json.loads(out_corrected.read_text())
        corrected_report = next(r for r in corrected_data["reports"] if r["report_id"] == report_id)
        assert corrected_report["sponsor"]["name"] == "Corrected Sponsor Name"
        assert corrected_report["sponsor"]["name"] != original_sponsor_name
        assert "MANUALLY_CORRECTED" in corrected_report["flags"]

    def test_missing_corrections_file_returns_error_code(self, tmp_path, monkeypatch, capsys):
        out = tmp_path / "out.json"
        code = run_cli(
            [
                str(FIXTURES / "2019q1jan29.txt"),
                str(out),
                "--apply-corrections",
                str(tmp_path / "does-not-exist.json"),
            ],
            monkeypatch,
        )
        assert code == 1
        assert "corrections file not found" in capsys.readouterr().out

    def test_invalid_corrections_json_returns_error_code(self, tmp_path, monkeypatch, capsys):
        corrections_path = tmp_path / "corrections.json"
        corrections_path.write_text("not valid json")
        out = tmp_path / "out.json"
        code = run_cli(
            [
                str(FIXTURES / "2019q1jan29.txt"),
                str(out),
                "--apply-corrections",
                str(corrections_path),
            ],
            monkeypatch,
        )
        assert code == 1
        assert "invalid JSON" in capsys.readouterr().out

    def test_reports_how_many_corrections_matched(self, tmp_path, monkeypatch, capsys):
        corrections_path = tmp_path / "corrections.json"
        corrections_path.write_text(
            json.dumps(
                {
                    "does-not-exist-000": {"status": "edited", "edits": {"sponsor.name": "X"}},
                    "also-missing-001": {"status": "confirmed_ok", "edits": {}},
                }
            )
        )
        out = tmp_path / "out.json"
        code = run_cli(
            [
                str(FIXTURES / "2019q1jan29.txt"),
                str(out),
                "--apply-corrections",
                str(corrections_path),
            ],
            monkeypatch,
        )
        assert code == 0
        assert "0 of 2 matched a parsed report" in capsys.readouterr().out

    def test_non_dict_corrections_file_returns_error_code(self, tmp_path, monkeypatch, capsys):
        corrections_path = tmp_path / "corrections.json"
        corrections_path.write_text(json.dumps([1, 2, 3]))
        out = tmp_path / "out.json"
        code = run_cli(
            [
                str(FIXTURES / "2019q1jan29.txt"),
                str(out),
                "--apply-corrections",
                str(corrections_path),
            ],
            monkeypatch,
        )
        assert code == 1
        assert "not a corrections overlay file" in capsys.readouterr().out
