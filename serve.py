#!/usr/bin/env python3
"""Serve the map locally, and proxy the map tiles so the token stays server-side.

    python3 serve.py [--port 8765] [--host 127.0.0.1]

Routes
    /                     -> site/map/index.html
    /report               -> site/index.html, the 1920-2025 write-up
    /data/tiles/<k>.json  -> one cadastral grid cell, fetched by the map on demand
    /api/config           -> what this server can offer (basemaps, live lookup)
    /api/wms/topo|orto    -> Klimadatastyrelsen WMS with the token added here
    /api/enrich?cvr=...   -> live lookup for a clicked farm, as an event stream

The token is read from ~/.data-forsyningen-token (or DATAFORSYNINGEN_TOKEN) and
is never sent to the browser. Deploying this means setting that env var on the
host rather than baking a key into a page.
"""
import argparse
import http.server
import json
import os
import pathlib
import socketserver
import sys
import traceback
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
SITE = ROOT / "site"
sys.path.insert(0, str(ROOT / "scripts"))
import live_enrich
WMS = {
    "topo": ("https://api.dataforsyningen.dk/topo_skaermkort_DAF", "dtk_skaermkort"),
    "orto": ("https://api.dataforsyningen.dk/orto_foraar_DAF", "orto_foraar"),
}


def token():
    env = os.environ.get("DATAFORSYNINGEN_TOKEN")
    if env:
        return env.strip()
    path = pathlib.Path.home() / ".data-forsyningen-token"
    return path.read_text().strip() if path.exists() else ""


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(SITE / "map"), **kw)

    def log_message(self, fmt, *args):
        if "/data/tiles/" not in (args[0] if args else ""):
            super().log_message(fmt, *args)

    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def try_gzip(self, parsed):
        """Serve <file>.gz when the client accepts gzip. JSON shrinks 5-8x."""
        if "gzip" not in self.headers.get("Accept-Encoding", ""):
            return False
        if not parsed.path.endswith(".json"):
            return False
        target = (SITE / "map" / parsed.path.lstrip("/")).with_suffix(".json.gz")
        if not target.exists():
            return False
        payload = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(payload)
        return True

    def enrich(self, q):
        """Live lookup for one clicked farm, streamed as server-sent events.

        The work is a chain of increasingly slow steps -- a cached register
        record, then a live one, then a search, then fetching what the search
        found -- and the whole point is that the panel shows each as it lands
        rather than after the slowest.  So: no Content-Length, flush every
        event, and let the client close the stream when it moves on.
        """
        def one(key, cast=str):
            v = q.get(key, [None])[0]
            if v in (None, "", "null", "undefined"):
                return None
            try:
                return cast(v)
            except (TypeError, ValueError):
                return None

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()

        def emit(event, payload):
            chunk = (f"event: {event}\n"
                     f"data: {json.dumps(payload, ensure_ascii=False)}\n\n")
            self.wfile.write(chunk.encode("utf-8"))
            self.wfile.flush()

        try:
            live_enrich.enrich(cvr=one("cvr", int), chr_no=one("chr"),
                               name=one("name"), city=one("city"), bfe=one("bfe"),
                               pages=one("pages", int) or 4, emit=emit)
        except (BrokenPipeError, ConnectionResetError):
            pass          # the viewer clicked somewhere else; nothing to report
        except Exception as exc:
            traceback.print_exc()
            try:
                emit("error", {"message": str(exc)})
            except Exception:
                pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if self.try_gzip(parsed):
            return

        if parsed.path == "/api/config":
            return self.send_json({"basemaps": bool(token()),
                                   "enrich": True,
                                   "search": live_enrich.providers_available()})

        if parsed.path == "/api/enrich":
            return self.enrich(urllib.parse.parse_qs(parsed.query))

        if parsed.path.startswith("/api/wms/"):
            name = parsed.path.rsplit("/", 1)[-1]
            if name not in WMS or not token():
                return self.send_json({"error": "no such basemap"}, 404)
            base, layer = WMS[name]
            params = dict(urllib.parse.parse_qsl(parsed.query))
            params.setdefault("layers", layer)
            params["token"] = token()
            url = base + "?" + urllib.parse.urlencode(params)
            try:
                with urllib.request.urlopen(url, timeout=45) as r:
                    payload = r.read()
                    ctype = r.headers.get("Content-Type", "image/png")
            except Exception as exc:
                return self.send_json({"error": str(exc)}, 502)
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            return self.wfile.write(payload)

        if parsed.path == "/report":
            self.path = "/report.html"
            report = SITE / "map" / "report.html"
            if not report.exists():
                report.write_text((SITE / "index.html").read_text())
            return super().do_GET()

        return super().do_GET()

    def end_headers(self):
        if self.path.startswith("/data/tiles/") and not self._headers_buffer_has_cache():
            self.send_header("Cache-Control", "public, max-age=3600")
        super().end_headers()

    def _headers_buffer_has_cache(self):
        return any(b"Cache-Control" in line for line in (self._headers_buffer or []))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer((args.host, args.port), Handler) as httpd:
        have = "with" if token() else "without"
        print(f"serving {SITE / 'map'} on http://{args.host}:{args.port}  ({have} basemaps)")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
