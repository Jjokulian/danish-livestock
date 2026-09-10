#!/usr/bin/env python3
"""Pull every annual CHR snapshot, so the map can show change rather than a date.

geodata.fvm.dk publishes one layer per year from CHR00 to CHR24 -- twenty-five
1 June snapshots of the Central Livestock Register, all on the same
unauthenticated WFS the 2024 pull already uses. Danish livestock consolidated
hard over that window, and the register recorded it holding by holding.

    python3 scripts/fetch_chr_years.py                 # 2000-2023 (2024 is done)
    python3 scripts/fetch_chr_years.py --years 10 15 20

Only what a time series needs is kept -- the site, its species and its
headcount -- because the full record for twenty-five years is most of a
gigabyte and the panel reads the current year from the existing file anyway.

Output: data/chr_years/<yy>.json, a list of [chr, cvr, dyrkode, head, de, lon, lat].
"""
import argparse
import json
import pathlib
import sys
import time
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "chr_years"
WFS = "https://geodata.fvm.dk/geoserver/ows"
PAGE = 10000
UA = {"User-Agent": "danish-livestock/1.0 (CHR WFS pull)"}

# The three size columns do not always sum: for cattle and pigs they partition
# the herd, but for sheep, goats, horses and mink one column is already a total
# and the others are subsets of it. build_map_data.py works this out per species
# from the column headings; the same rule is applied here.
TOTAL_WORDS = ("i alt", "ialt", "total")


def get(params):
    url = WFS + "?" + urllib.parse.urlencode(params)
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=240) as r:
                return r.read()
        except Exception as exc:
            if attempt == 3:
                raise
            print(f"    retry {attempt + 1} after {exc}", flush=True)
            time.sleep(5 * (attempt + 1))


def herd_size(props):
    """Animals at this site, without double-counting a column that is a total."""
    sizes, labels = [], []
    for n in ("1", "2", "4"):
        raw = props.get("BSTR" + n)
        try:
            sizes.append(int(float(raw)) if raw not in (None, "") else 0)
        except (TypeError, ValueError):
            sizes.append(0)
        labels.append((props.get("BSTRTEKST" + n) or "").lower())
    for i, label in enumerate(labels):
        if any(w in label for w in TOTAL_WORDS):
            return sizes[i]
    return sum(sizes)


def fetch_year(yy):
    """One annual layer, paged, as compact rows."""
    rows, start = [], 0
    while True:
        raw = get({"service": "WFS", "version": "2.0.0", "request": "GetFeature",
                   "typeNames": f"Jordbrugsanalyser:CHR{yy}",
                   "outputFormat": "application/json", "srsName": "EPSG:4326",
                   "count": PAGE, "startIndex": start})
        # GeoServer's JSON writer says UTF-8 and emits ISO-8859-1.
        page = json.loads(raw.decode("latin-1"))
        feats = page.get("features") or []
        for f in feats:
            p = f.get("properties") or {}
            g = f.get("geometry") or {}
            c = (g.get("coordinates") or [None, None])
            if c[0] is None:
                continue
            try:
                de = int(round(float(p.get("DE") or 0) * 10))
            except (TypeError, ValueError):
                de = 0
            rows.append([p.get("CHRNR"), p.get("CVRNR"), p.get("DYRKODE"),
                         herd_size(p), de,
                         round(c[0], 5), round(c[1], 5)])
        start += len(feats)
        print(f"    {start:,} rows", flush=True)
        if len(feats) < PAGE:
            break
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--years", nargs="*", help="two-digit years; default 00-23")
    ap.add_argument("--force", action="store_true", help="re-fetch years already on disk")
    args = ap.parse_args()

    years = args.years or [f"{y:02d}" for y in range(0, 24)]
    OUT.mkdir(parents=True, exist_ok=True)

    for yy in years:
        path = OUT / f"{yy}.json"
        if path.exists() and not args.force:
            print(f"  CHR{yy}: already have {path.name}")
            continue
        print(f"  CHR{yy}:", flush=True)
        try:
            rows = fetch_year(yy)
        except Exception as exc:
            print(f"    failed: {exc}", file=sys.stderr)
            continue
        path.write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")
        print(f"    {len(rows):,} sites -> {path.name} "
              f"({path.stat().st_size / 1048576:.1f} MB)", flush=True)

    have = sorted(p.stem for p in OUT.glob("*.json"))
    print(f"\n{len(have)} years on disk: {', '.join(have)}")


if __name__ == "__main__":
    sys.exit(main())
