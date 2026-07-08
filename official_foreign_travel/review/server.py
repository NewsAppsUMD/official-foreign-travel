"""Local review server: side-by-side raw/extracted view with editable corrections."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import List, Type
from urllib.parse import urlparse

from ..models.report import Report
from .corrections import load_corrections, save_report_correction
from .source_lookup import get_raw_lines

STATIC_DIR = Path(__file__).parent / "static"
CONTENT_TYPES = {".html": "text/html", ".css": "text/css", ".js": "application/javascript"}


def make_handler(
    reports: List[Report], report_text_dir: Path, corrections_path: Path
) -> Type[BaseHTTPRequestHandler]:
    """Build a request handler class closed over this run's reports and paths."""
    reports_by_id = {r.report_id: r for r in reports}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format_str: str, *args: object) -> None:
            pass  # keep test/server output quiet

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/":
                self._serve_static("index.html")
            elif path in ("/report.html", "/app.css", "/app.js"):
                self._serve_static(path.lstrip("/"))
            elif path == "/api/reports":
                self._send_json(self._list_reports())
            elif path.startswith("/api/reports/"):
                self._send_report_detail(path[len("/api/reports/") :])
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if path.startswith("/api/reports/") and path.endswith("/corrections"):
                report_id = path[len("/api/reports/") : -len("/corrections")]
                self._save_corrections(report_id)
            else:
                self.send_error(404)

        def _serve_static(self, name: str) -> None:
            file_path = STATIC_DIR / name
            if not file_path.is_file():
                self.send_error(404)
                return
            body = file_path.read_bytes()
            self.send_response(200)
            self.send_header(
                "Content-Type", CONTENT_TYPES.get(file_path.suffix, "application/octet-stream")
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, data: object, status: int = 200) -> None:
            body = json.dumps(data).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _list_reports(self) -> List[dict]:
            corrections = load_corrections(corrections_path)
            return [
                {
                    "report_id": r.report_id,
                    "sponsor": r.sponsor.name,
                    "source_file": r.source_file,
                    "flags": r.flags,
                    "traveler_count": len(r.travelers),
                    "status": corrections.get(r.report_id, {}).get("status", "unreviewed"),
                }
                for r in reports
            ]

        def _send_report_detail(self, report_id: str) -> None:
            report = reports_by_id.get(report_id)
            if report is None:
                self.send_error(404)
                return
            raw_lines = get_raw_lines(report, report_text_dir) or []
            corrections = load_corrections(corrections_path)
            self._send_json(
                {
                    "report": report.model_dump(mode="json"),
                    "raw_lines": raw_lines,
                    "correction": corrections.get(report_id, {"status": "unreviewed", "edits": {}}),
                }
            )

        def _save_corrections(self, report_id: str) -> None:
            if report_id not in reports_by_id:
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length))
            entry = save_report_correction(
                corrections_path,
                report_id,
                payload.get("status", "edited"),
                payload.get("edits", {}),
            )
            self._send_json(entry)

    return Handler


def run_server(
    reports: List[Report],
    report_text_dir: Path,
    corrections_path: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    """Start the review server and block until interrupted (Ctrl-C)."""
    flagged = [r for r in reports if r.flags]
    handler_cls = make_handler(flagged, report_text_dir, corrections_path)
    server = ThreadingHTTPServer((host, port), handler_cls)
    print(f"Review server running at http://{host}:{port}/ ({len(flagged)} flagged reports)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
