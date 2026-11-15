#!/usr/bin/env python3
"""Local dashboard for Claude Code token costs.

    python3 server.py            # http://127.0.0.1:8787
    python3 server.py --port 9000 --open

Every request to /api/data re-scans ~/.claude/projects (unchanged files are cached
by mtime+size), so the dashboard always shows the latest values on this machine.
"""

import argparse
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cc_usage

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.json"
_lock = threading.Lock()


def read_config() -> dict:
    try:
        cfg = json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        cfg = {}
    if not isinstance(cfg.get("projects"), dict):
        cfg["projects"] = {}
    return cfg


def clean_config(raw) -> dict:
    """Keep only {project path: display name} string pairs."""
    projects = (raw or {}).get("projects") if isinstance(raw, dict) else None
    out = {}
    if isinstance(projects, dict):
        for k, v in projects.items():
            if isinstance(k, str) and isinstance(v, str) and v.strip():
                out[k[:1000]] = v.strip()[:120]
    return {"projects": out}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send(200, (ROOT / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif path in ("/favicon.svg", "/favicon.ico"):
            self._send(200, (ROOT / "app" / "icon.svg").read_bytes(), "image/svg+xml")
        elif path == "/api/data":
            with _lock:
                data = cc_usage.collect()
                data["config"] = read_config()
            self._send(200, json.dumps(data, separators=(",", ":")).encode(), "application/json")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path.split("?")[0] != "/api/config":
            return self._send(404, b"not found", "text/plain")
        # Only accept same-origin JSON so other web pages can't rewrite the config.
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if origin and origin.split("://", 1)[-1] != host:
            return self._send(403, b"forbidden", "text/plain")
        if "application/json" not in self.headers.get("Content-Type", ""):
            return self._send(415, b"expected application/json", "text/plain")
        try:
            length = min(int(self.headers.get("Content-Length", 0)), 1_000_000)
            cfg = clean_config(json.loads(self.rfile.read(length) or b"{}"))
        except ValueError:
            return self._send(400, b"bad json", "text/plain")
        with _lock:
            tmp = CONFIG.with_suffix(".tmp")
            tmp.write_text(json.dumps(cfg, indent=2, sort_keys=True) + "\n")
            tmp.replace(CONFIG)
        self._send(200, json.dumps(cfg).encode(), "application/json")

    def log_message(self, fmt, *args):
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"Claude Code cost dashboard → {url}  (reading {cc_usage.PROJECTS_DIR})")
    if args.open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
