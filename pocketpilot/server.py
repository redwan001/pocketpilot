"""Minimal web server (Python standard library only) for the PocketPilot demo UI.

Run:  python -m pocketpilot.server            (offline planner unless ANTHROPIC_API_KEY is set)
      python -m pocketpilot.server --offline
Then open http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock

from .api import App

WEB = Path(__file__).resolve().parent.parent / "web"
LOGS = Path(__file__).resolve().parent.parent / "logs"
MAX_BODY = 8 * 1024
MAX_UPLOAD = 6 * 1024 * 1024        # statements (CSV text or PDF text items)


def make_handler(app: App, lock: Lock):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                             "worker-src 'self' blob: https://cdn.jsdelivr.net; connect-src 'self' https://cdn.jsdelivr.net; img-src 'self' data:")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj):
            self._send(code, json.dumps(obj, default=str).encode(), "application/json")

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                return self._send(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
            if self.path in ("/sample_statement.csv", "/sample_statement.pdf"):
                f = WEB / self.path.lstrip("/")
                ctype = "text/csv" if f.suffix == ".csv" else "application/pdf"
                return self._send(200, f.read_bytes(), ctype) if f.exists() else self._json(404, {"error": "not found"})
            with lock:
                self._json(*app.handle("GET", self.path))

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > (MAX_UPLOAD if self.path == "/api/statement" else MAX_BODY):
                return self._json(413, {"error": "request too large"})
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return self._json(400, {"error": "invalid JSON"})
            with lock:
                self._json(*app.handle("POST", self.path, body if isinstance(body, dict) else {}))

    return Handler


def main():
    ap = argparse.ArgumentParser(description="PocketPilot demo server")
    ap.add_argument("--offline", action="store_true", help="use the deterministic offline planner")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    app = App("offline" if args.offline else "auto", audit_path=LOGS / "audit_log.jsonl")
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(app, Lock()))
    print(f"PocketPilot running at http://127.0.0.1:{args.port}  (planner: {app.agent.planner.name})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
