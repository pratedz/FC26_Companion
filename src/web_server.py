"""Local HTTP host for LE Companion web UI.

Serves static files from web/ and JSON under /api/* via web_api.handle.
Uses only the Python standard library (no Flask/FastAPI required).
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import socket
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from . import paths
from . import web_api


def web_root() -> Path:
    """Directory containing index.html (dev tree or frozen onedir/_MEIPASS)."""
    candidates = [
        paths.resource_root() / "web",  # PyInstaller datas (frozen _MEIPASS/_internal)
        paths.app_root() / "web",  # dev tree or portable copy next to exe
        Path(__file__).resolve().parent.parent / "web",
    ]
    for c in candidates:
        try:
            if (c / "index.html").is_file():
                return c
        except OSError:
            continue
    return candidates[0]


def _parse_query(path: str) -> Tuple[str, Dict[str, Any]]:
    parsed = urllib.parse.urlparse(path)
    q: Dict[str, Any] = {}
    for k, v in urllib.parse.parse_qs(parsed.query, keep_blank_values=True).items():
        q[k] = v[0] if len(v) == 1 else v
    return parsed.path, q


def _safe_log(msg: str) -> None:
    """Write log line; never raise (windowed .exe has sys.stderr/stdout = None)."""
    try:
        stream = sys.stderr if sys.stderr is not None else sys.stdout
        if stream is not None:
            stream.write(msg if msg.endswith("\n") else msg + "\n")
            try:
                stream.flush()
            except Exception:
                pass
            return
    except Exception:
        pass
    try:
        from . import paths as _paths

        log = _paths.app_root() / "web_server.log"
        with log.open("a", encoding="utf-8") as f:
            f.write(msg if msg.endswith("\n") else msg + "\n")
    except Exception:
        pass


class CompanionHandler(BaseHTTPRequestHandler):
    server_version = "LECompanionWeb/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        # Must not touch None stderr — PyInstaller --windowed sets stdout/stderr to None
        # and BaseHTTPRequestHandler.log_request → log_message would kill the response.
        try:
            _safe_log("%s - %s" % (self.address_string(), fmt % args))
        except Exception:
            pass

    def log_error(self, fmt: str, *args: Any) -> None:
        try:
            _safe_log("ERROR %s - %s" % (self.address_string(), fmt % args))
        except Exception:
            pass

    def _send_json(self, status: int, data: Any) -> None:
        body = json.dumps(data, default=str, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(
        self, status: int, body: bytes, content_type: str, *, cache: bool = True
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if cache:
            self.send_header("Cache-Control", "public, max-age=60")
        else:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path, query = _parse_query(self.path)
        if path.startswith("/api"):
            result = web_api.handle("GET", path, query=query, body=None)
            self._send_json(int(result["status"]), result["data"])
            return
        self._serve_static(path)

    def do_POST(self) -> None:  # noqa: N802
        path, query = _parse_query(self.path)
        if not path.startswith("/api"):
            self._send_json(404, {"ok": False, "error": "POST only under /api"})
            return
        body = self._read_json_body()
        result = web_api.handle("POST", path, query=query, body=body)
        self._send_json(int(result["status"]), result["data"])

    def _serve_static(self, path: str) -> None:
        root = web_root()
        rel = path if path not in ("", "/") else "/index.html"
        # Prevent path traversal
        rel = rel.lstrip("/").replace("\\", "/")
        if ".." in rel.split("/"):
            self._send_json(403, {"ok": False, "error": "forbidden"})
            return
        file_path = (root / rel).resolve()
        try:
            file_path.relative_to(root.resolve())
        except ValueError:
            self._send_json(403, {"ok": False, "error": "forbidden"})
            return
        if not file_path.is_file():
            # SPA fallback
            index = root / "index.html"
            if index.is_file() and not rel.startswith("api"):
                data = index.read_bytes()
                self._send_bytes(200, data, "text/html; charset=utf-8", cache=False)
                return
            self._send_json(404, {"ok": False, "error": "not found", "path": path})
            return
        ctype, _ = mimetypes.guess_type(str(file_path))
        if not ctype:
            ctype = "application/octet-stream"
        if file_path.suffix in (".html", ".js", ".css"):
            ctype = {
                ".html": "text/html; charset=utf-8",
                ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8",
            }.get(file_path.suffix, ctype)
        self._send_bytes(
            200,
            file_path.read_bytes(),
            ctype,
            cache=file_path.suffix not in (".html", ".js"),
        )


def make_server(
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), CompanionHandler)


def wait_for_port(host: str, port: int, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def run_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    open_browser: bool = True,
    ready_event: Optional[threading.Event] = None,
) -> None:
    """Block forever serving the web UI (primary GUI path)."""
    root = web_root()
    if not (root / "index.html").is_file():
        raise FileNotFoundError(
            f"Web UI missing: expected index.html under {root}. "
            "Ensure the web/ directory ships with the app."
        )
    httpd = make_server(host, port)
    url = f"http://{host}:{port}/"
    _safe_log(f"LE Companion web UI: {url}")
    _safe_log(f"Static root: {root}")
    _safe_log("API: /api/health  /api/status  /api/cards/search …")
    _safe_log("Ctrl+C to stop.")
    if ready_event is not None:
        ready_event.set()
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(description="LE Companion web UI server")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args(argv)
    try:
        run_server(args.host, args.port, open_browser=not args.no_browser)
    except KeyboardInterrupt:
        _safe_log("Stopped.")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
