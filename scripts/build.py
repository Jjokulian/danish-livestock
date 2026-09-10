#!/usr/bin/env python3
"""Assemble site/index.html from its parts.

The page is one self-contained file: styles, markup, the dataset inlined as
JSON, and the chart code. Editing happens in the parts, not in index.html.

  site/head.html  <title>, the Google Fonts link, and all CSS tokens
  site/body.html  the markup and every word of copy
  site/app.js     SVG chart rendering (no libraries)
  data/livestock.json  inlined into a <script type="application/json">

Usage:  python3 scripts/build.py
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE = ROOT / "site"


def main():
    parts = [
        (SITE / "head.html").read_text(),
        (SITE / "body.html").read_text(),
        '<script id="dataset" type="application/json">',
        (ROOT / "data" / "livestock.json").read_text(),
        "</script>",
        "<script>",
        (SITE / "app.js").read_text(),
        "</script>",
    ]
    out = SITE / "index.html"
    out.write_text("\n".join(parts) + "\n")
    print(f"wrote {out} ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
