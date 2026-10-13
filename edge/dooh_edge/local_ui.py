"""On-device dashboard served by the thin client (http://127.0.0.1:8080 by default).

Shows the live audience in front of this screen and today's totals, even when
the central server is unreachable. Bound to localhost by default.
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"


def make_handler(agent, screen_name_fn):
    class Handler(BaseHTTPRequestHandler):
        server_version = "dooh-edge"

        def log_message(self, fmt, *args):  # route to logging instead of stderr
            log.debug("local-ui: " + fmt, *args)

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send(200, (STATIC / "local.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/status":
                body = {
                    "screen": screen_name_fn(),
                    "status": agent.status(),
                    "today": agent.outbox.today_summary(),
                    "preview": agent.cfg.local_ui.debug_preview,
                }
                self._send(200, json.dumps(body).encode(), "application/json")
            elif path == "/preview.jpg" and agent.cfg.local_ui.debug_preview:
                jpeg = agent.preview_jpeg()
                if jpeg is None:
                    self._send(503, b"no frame yet", "text/plain")
                else:
                    self._send(200, jpeg, "image/jpeg")
            else:
                self._send(404, b"not found", "text/plain")

    return Handler


def start_local_ui(agent, bind: str, port: int, screen_name_fn=lambda: None) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((bind, port), make_handler(agent, screen_name_fn))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="local-ui", daemon=True).start()
    log.info("Local dashboard on http://%s:%d/", bind, port)
    return server
