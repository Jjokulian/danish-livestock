#!/usr/bin/env python3
"""Download the CHR livestock-farm point layer from Landbrugsstyrelsen's WFS.

geodata.fvm.dk publishes one layer per year (Jordbrugsanalyser:CHR00 .. CHR24)
holding every CHR-registered livestock site: a point, the species, the animal
units (DE), and the herd size in up to three animal categories. No key, no
registration, no rate limit that we hit.

The GeoServer JSON writer labels its output UTF-8 but emits ISO-8859-1 bytes,
so responses are decoded latin-1 before parsing.

Usage:  python3 scripts/fetch_chr.py [--year 24] [-o data/chr_2024.json]
"""
import argparse
import json
import pathlib
import time
import urllib.parse
import urllib.request

WFS = "https://geodata.fvm.dk/geoserver/ows"
PAGE = 10000
UA = {"User-Agent": "danish-livestock/1.0 (CHR WFS pull)"}
KEEP = ("CHRNR", "CVRNR", "DE", "DYRKODE", "DYRTEKST", "BRUGKODE", "BRUGTEKST",
        "VIRKART", "Kommune", "Postnr",
        "BSTR1", "BSTR2", "BSTR4", "BSTRTEKST1", "BSTRTEKST2", "BSTRTEKST4")


def get(params):
    url = WFS + "?" + urllib.parse.urlencode(params)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=180) as r:
                return r.read()
        except Exception as exc:  # transient WFS hiccups are common
            if attempt == 3:
                raise
            print(f"  retry {attempt + 1} after {exc}")
            time.sleep(4 * (attempt + 1))


def count(layer):
    xml = get({"service": "WFS", "version": "2.0.0", "request": "GetFeature",
               "typeNames": layer, "resultType": "hits"}).decode("iso-8859-1")
    return int(xml.split('numberMatched="')[1].split('"')[0])


def page(layer, start):
    raw = get({"service": "WFS", "version": "2.0.0", "request": "GetFeature",
               "typeNames": layer, "outputFormat": "application/json",
               "srsName": "EPSG:4326", "count": PAGE, "startIndex": start})
    return json.loads(raw.decode("iso-8859-1"))["features"]


def main():
    root = pathlib.Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--year", default="24", help="two-digit layer year, e.g. 24")
    ap.add_argument("-o", "--out")
    args = ap.parse_args()

    layer = f"Jordbrugsanalyser:CHR{args.year}"
    out = pathlib.Path(args.out or root / "data" / f"chr_20{args.year}.json")

    total = count(layer)
    print(f"{layer}: {total:,} features")

    records = []
    for start in range(0, total, PAGE):
        feats = page(layer, start)
        for f in feats:
            geom = f.get("geometry")
            if not geom or not geom.get("coordinates"):
                continue
            lon, lat = geom["coordinates"][:2]
            props = f["properties"]
            rec = {k: props.get(k) for k in KEEP}
            rec["lon"], rec["lat"] = round(lon, 6), round(lat, 6)
            records.append(rec)
        print(f"  {min(start + PAGE, total):,}/{total:,}")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {out} ({out.stat().st_size:,} bytes, {len(records):,} sites)")


if __name__ == "__main__":
    main()
