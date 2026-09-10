#!/usr/bin/env python3
"""Aggregate the declared field parcels ("Marker") to hectares per CVR number.

Marker_<year> is every parcel Danish farmers declare for area support: about
616,000 polygons carrying the operator's CVR number and the parcel area. Asking
the WFS for the attributes only (propertyName, CSV output) keeps this to a few
tens of MB instead of gigabytes of geometry.

The CVR key is what joins this to CHR: the same company number identifies the
livestock sites in chr_<year>.json, so a farm point can say how much land the
business that runs it actually farms.

Usage:  python3 scripts/fetch_marker.py [--year 2025] [-o data/land_by_cvr.json]
"""
import argparse
import collections
import csv
import io
import json
import pathlib
import time
import urllib.parse
import urllib.request

WFS = "https://geodata.fvm.dk/geoserver/ows"
PAGE = 50000
UA = {"User-Agent": "danish-livestock/1.0 (Marker WFS pull)"}


def get(params):
    url = WFS + "?" + urllib.parse.urlencode(params)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300) as r:
                return r.read()
        except Exception as exc:
            if attempt == 3:
                raise
            print(f"  retry {attempt + 1} after {exc}")
            time.sleep(5 * (attempt + 1))


def main():
    root = pathlib.Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--year", default="2025")
    ap.add_argument("-o", "--out")
    args = ap.parse_args()

    layer = f"Marker:Marker_{args.year}"
    out = pathlib.Path(args.out or root / "data" / "land_by_cvr.json")

    hits = get({"service": "WFS", "version": "2.0.0", "request": "GetFeature",
                "typeNames": layer, "resultType": "hits"}).decode("iso-8859-1")
    total = int(hits.split('numberMatched="')[1].split('"')[0])
    print(f"{layer}: {total:,} parcels")

    hectares = collections.defaultdict(float)
    parcels = collections.Counter()
    crops = collections.defaultdict(collections.Counter)

    for start in range(0, total, PAGE):
        raw = get({"service": "WFS", "version": "2.0.0", "request": "GetFeature",
                   "typeNames": layer, "outputFormat": "csv",
                   "propertyName": "CVR,IMK_areal,Afgroede",
                   "count": PAGE, "startIndex": start})
        for row in csv.DictReader(io.StringIO(raw.decode("iso-8859-1"))):
            cvr = (row.get("CVR") or "").strip()
            if not cvr:
                continue
            try:
                area = float(row["IMK_areal"])
            except (TypeError, ValueError):
                continue
            hectares[cvr] += area
            parcels[cvr] += 1
            crop = (row.get("Afgroede") or "").strip()
            if crop:
                crops[cvr][crop] += area
        print(f"  {min(start + PAGE, total):,}/{total:,}  ({len(hectares):,} CVRs)")

    data = {
        cvr: [round(ha, 1), parcels[cvr], crops[cvr].most_common(1)[0][0] if crops[cvr] else ""]
        for cvr, ha in hectares.items()
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {out} ({out.stat().st_size:,} bytes, {len(data):,} CVR numbers, "
          f"{sum(hectares.values()):,.0f} ha)")


if __name__ == "__main__":
    main()
