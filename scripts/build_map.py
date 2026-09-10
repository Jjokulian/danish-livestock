#!/usr/bin/env python3
"""Assemble site/map.html from its parts.

Leaflet's script comes from cdnjs at run time, but its stylesheet cannot: an
Artifact's CSP allows external scripts and Google Fonts only. So leaflet.css is
inlined here, and the map carries no tile layer at all — external images are
blocked too. The basemap is the municipality geometry in data/kommuner.json,
drawn as vectors.

Usage:  python3 scripts/build_map.py
"""
import argparse
import json
import pathlib
import urllib.request

LEAFLET = "1.9.4"
CSS_URL = f"https://cdnjs.cloudflare.com/ajax/libs/leaflet/{LEAFLET}/leaflet.css"
JS_URL = f"https://cdnjs.cloudflare.com/ajax/libs/leaflet/{LEAFLET}/leaflet.js"

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
CACHE = ROOT / "site" / "leaflet.css"


def leaflet_css():
    if not CACHE.exists():
        with urllib.request.urlopen(CSS_URL, timeout=90) as r:
            CACHE.write_bytes(r.read())
        print(f"cached {CACHE.name}")
    return CACHE.read_text()


def basemap_token():
    """Read the Dataforsyningen token for the local build only."""
    path = pathlib.Path.home() / ".data-forsyningen-token"
    return path.read_text().strip() if path.exists() else ""


def parcel_block():
    """The LOD parcel pyramid, if it has been built."""
    tiles = ROOT / "data" / "parcel_tiles.json"
    if not tiles.exists():
        return ""
    return ('<script id="parcels" type="application/json">'
            + tiles.read_text() + "</script>")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--local", action="store_true",
                    help="write site/map_local.html with the aerial/topographic basemap "
                         "switched on. That file embeds the Dataforsyningen token — keep it "
                         "off the web.")
    args = ap.parse_args()

    token = basemap_token() if args.local else ""
    if args.local and not token:
        raise SystemExit("no ~/.data-forsyningen-token found — cannot build the local basemap")

    parts = [
        (SITE / "map_head.html").read_text(),
        "<style>\n/* Leaflet " + LEAFLET + " — inlined; the CSP blocks external stylesheets */\n"
        + leaflet_css() + "\n</style>",
        f'<script src="{JS_URL}"></script>',
        (SITE / "map_body.html").read_text(),
        '<script id="sites" type="application/json">',
        (ROOT / "data" / "map_sites.json").read_text(),
        "</script>",
        '<script id="kommuner" type="application/json">',
        (ROOT / "data" / "kommuner.json").read_text(),
        "</script>",
        parcel_block(),
        ('<script>window.DF_BASEMAP_TOKEN=' + json.dumps(token) + ";</script>") if token else "",
        "<script>",
        (SITE / "map.js").read_text(),
        "</script>",
    ]
    out = SITE / ("map_local.html" if token else "map.html")
    out.write_text("\n".join(parts) + "\n")
    print(f"wrote {out} ({out.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
