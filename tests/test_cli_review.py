"""Smoke tests for the oft-review CLI's argument validation and error paths."""

import json
import sys
from pathlib import Path

from official_foreign_travel.cli.review import main as review_main

FIXTURES = Path(__file__).parent / "fixtures"


def run_cli(args, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["oft-review"] + args)
    return review_main()


class TestReviewCli:
    def test_missing_report_text_dir_returns_error_code(self, tmp_path, monkeypatch, capsys):
        code = run_cli([str(tmp_path / "does-not-exist"), str(tmp_path / "out.json")], monkeypatch)
        assert code == 1
        assert "not a directory" in capsys.readouterr().out

    def test_missing_parsed_json_returns_error_code(self, tmp_path, monkeypatch, capsys):
        code = run_cli([str(tmp_path), str(tmp_path / "does-not-exist.json")], monkeypatch)
        assert code == 1
        assert "not found" in capsys.readouterr().out

    def test_invalid_json_returns_error_code(self, tmp_path, monkeypatch, capsys):
        bad_json = tmp_path / "bad.json"
        bad_json.write_text("not valid json")
        code = run_cli([str(tmp_path), str(bad_json)], monkeypatch)
        assert code == 1
        assert "invalid JSON" in capsys.readouterr().out

    def test_missing_reports_key_returns_error_code(self, tmp_path, monkeypatch, capsys):
        wrong_shape = tmp_path / "wrong.json"
        wrong_shape.write_text(json.dumps({"not_reports": []}))
        code = run_cli([str(tmp_path), str(wrong_shape)], monkeypatch)
        assert code == 1
        assert "reports" in capsys.readouterr().out

    def test_invalid_report_data_returns_error_code(self, tmp_path, monkeypatch, capsys):
        bad_report = tmp_path / "bad_report.json"
        bad_report.write_text(json.dumps({"reports": [{"not": "a valid report"}]}))
        code = run_cli([str(tmp_path), str(bad_report)], monkeypatch)
        assert code == 1
        assert "invalid report data" in capsys.readouterr().out
