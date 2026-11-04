"""Offline localhost dashboard. Run: python -m fairway_fusion.demo.server"""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Timer
from urllib.parse import urlsplit
import webbrowser

from fairway_fusion.analysis.flight import FlightConditions
from fairway_fusion.demo.fixtures import fixtures
from fairway_fusion.demo.replay import process_observations

STATIC = Path(__file__).parent / "static"
FIXTURES = fixtures()


class DemoHandler(BaseHTTPRequestHandler):
    def send(self, status, data, content_type="application/json"):
        if isinstance(data, dict) or isinstance(data, list):
            data = json.dumps(data, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/fixtures":
            self.send(
                200,
                [
                    {"id": key, "label": value[0]["label"], "source": "synthetic"}
                    for key, value in FIXTURES.items()
                ],
            )
        elif path.startswith("/api/fixtures/"):
            fixture = FIXTURES.get(path.rsplit("/", 1)[-1])
            self.send(
                200, {"capture": fixture[0], "reference": fixture[1]}
            ) if fixture else self.send(404, {"error": "Unknown fixture"})
        elif path in ("/", "/app.js", "/style.css"):
            name = "index.html" if path == "/" else path[1:]
            mime = {
                "index.html": "text/html; charset=utf-8",
                "app.js": "text/javascript; charset=utf-8",
                "style.css": "text/css; charset=utf-8",
            }[name]
            self.send(200, (STATIC / name).read_bytes(), mime)
        else:
            self.send(404, {"error": "Not found"})

    def do_POST(self):
        if self.path != "/api/replay":
            self.send(404, {"error": "Not found"})
            return
        # Bind loopback and reject cross-origin browser requests; no upload is persisted.
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{self.headers.get('Host')}":
            self.send(403, {"error": "Cross-origin requests are disabled"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1_000_000:
                self.send(413, {"error": "Provide a JSON payload no larger than 1 MB"})
                return
            data = json.loads(self.rfile.read(length))
            conditions = FlightConditions(**data.get("conditions", {}))
            conditions.validate()
            result = process_observations(data["capture"], conditions)
            self.send(200, result)
        except (
            ValueError,
            TypeError,
            KeyError,
            IndexError,
            OverflowError,
            AttributeError,
        ) as exc:
            self.send(400, {"error": f"Invalid capture: {exc}"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--open", action="store_true", help="Open the dashboard after the server starts"
    )
    args = parser.parse_args()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), DemoHandler)
    except OSError as exc:
        parser.exit(2, f"Cannot start local demo: {exc}. Try --port 8766.\n")
    print(
        f"Fairway Fusion pre-alpha demo: http://127.0.0.1:{server.server_port}",
        flush=True,
    )
    print("Offline synthetic sensor replay. Ctrl+C stops the demo.", flush=True)
    if args.open:
        opener = Timer(
            0.5, webbrowser.open, args=(f"http://127.0.0.1:{server.server_port}",)
        )
        opener.daemon = True
        opener.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
