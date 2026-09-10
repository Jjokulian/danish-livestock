#!/usr/bin/env python3
"""Pre-compress the served JSON so the map ships a fraction of the bytes.

http.server has no compression of its own, and neither will a static host, so
each data file gets a .gz sibling that serve.py hands over when the browser
says it accepts gzip.

Usage:  python3 scripts/gzip_data.py
"""
import gzip
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "site" / "map" / "data"


def main():
    raw = packed = 0
    files = sorted(DATA.rglob("*.json"))
    for path in files:
        body = path.read_bytes()
        out = path.with_suffix(".json.gz")
        out.write_bytes(gzip.compress(body, 9))
        raw += len(body)
        packed += out.stat().st_size
    print(f"{len(files):,} files: {raw / 1e6:.1f} MB -> {packed / 1e6:.1f} MB "
          f"({packed / max(raw, 1) * 100:.0f}%)")


if __name__ == "__main__":
    main()
