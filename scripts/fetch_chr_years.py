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
import urllib.error
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


def get(params, tries=3):
    """The bytes, or None if the server would not produce them.

    Returning None rather than raising is the point: a page this server cannot
    serve is a fact about that range of records, not about the connection, and
    the caller's answer to both is the same -- ask for fewer. Hammering a range
    that will never work also seems to provoke 400s of its own, so a failure
    here narrows the request instead of retrying it into the ground.
    """
    url = WFS + "?" + urllib.parse.urlencode(params)
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=240) as r:
                return r.read()
        except urllib.error.HTTPError:
            return None                      # the server has an opinion; respect it
        except Exception as exc:
            if attempt == tries - 1:
                print(f"    giving up on this page: {exc}", flush=True)
                return None
            time.sleep(4 * (attempt + 1))
    return None


def tenths(raw):
    """Animal units, in tenths, whichever way the year wrote the decimal point.

    The layers are not consistent: most write DE as 4.679, but CHR20 writes
    4,679 with a comma. A plain float() on that raises, and silently catching it
    put a zero in -- which cost 92% of 2020's animal units before the total for
    that year turned out to be a tenth of its neighbours'.
    """
    if raw in (None, ""):
        return 0
    try:
        return int(round(float(str(raw).replace(",", ".")) * 10))
    except (TypeError, ValueError):
        return 0


def herd_size(props):
    """Animals at this site, without double-counting a column that is a total."""
    sizes, labels = [], []
    for n in ("1", "2", "4"):
        raw = props.get("BSTR" + n)
        sizes.append(tenths(raw) // 10)
        labels.append((props.get("BSTRTEKST" + n) or "").lower())
    for i, label in enumerate(labels):
        if any(w in label for w in TOTAL_WORDS):
            return sizes[i]
    return sum(sizes)


def page(yy, start, count):
    """One page, or None if the server could not produce it.

    CHR19 holds at least one record with a corrupt geometry -- the server's own
    message is "Angle 141.777.288.611.771.376 is too high" -- and GeoServer does
    not fail cleanly on it. It answers 200, streams JSON until it reaches the bad
    record, then stops mid-object and appends an XML exception. So a truncated
    body is a fact about one row, not about the request.
    """
    raw = get({"service": "WFS", "version": "2.0.0", "request": "GetFeature",
               "typeNames": f"Jordbrugsanalyser:CHR{yy}",
               "outputFormat": "application/json", "srsName": "EPSG:4326",
               "count": count, "startIndex": start})
    if raw is None:
        return None
    try:
        # GeoServer's JSON writer says UTF-8 and emits ISO-8859-1.
        return json.loads(raw.decode("latin-1"))
    except json.JSONDecodeError:
        return None


def fetch_page_around_bad_rows(yy, start, count):
    """A page, narrowing on failure until the bad row is isolated and stepped over.

    Halving finds the offending record in a handful of requests rather than
    walking to it one at a time; when the window is down to a single row and
    still fails, that row is the bad one and is skipped.

    Returns the features, how far to advance, and the page size that worked --
    that last one matters: a short page only means the end of the layer if a
    full page was asked for. Treating a narrowed page as the end stopped 2019 at
    12,500 rows of 37,000.
    """
    got = page(yy, start, count)
    if got is not None:
        feats = got.get("features") or []
        return feats, len(feats), count

    width = count
    while width > 1:
        width //= 2
        got = page(yy, start, width)
        if got is not None:
            feats = got.get("features") or []
            return feats, max(len(feats), 1), width
    print(f"    skipping record {start:,}: the server cannot serialise it", flush=True)
    return [], 1, 1


def fetch_year(yy):
    """One annual layer, paged, as compact rows."""
    rows, start, blanks = [], 0, 0
    while True:
        feats, advance, asked = fetch_page_around_bad_rows(yy, start, PAGE)
        for f in feats:
            p = f.get("properties") or {}
            g = f.get("geometry") or {}
            c = (g.get("coordinates") or [None, None])
            if c[0] is None:
                continue
            rows.append([p.get("CHRNR"), p.get("CVRNR"), p.get("DYRKODE"),
                         herd_size(p), tenths(p.get("DE")),
                         round(c[0], 5), round(c[1], 5)])
        start += advance
        if len(rows) and start % PAGE < advance:
            print(f"    {start:,} rows", flush=True)
        if not feats:
            blanks += 1
            # One blank is a skipped bad row; several running is the end.
            if blanks > 3:
                break
        else:
            blanks = 0
            # Short only counts as finished when a full page was requested.
            if asked >= PAGE and len(feats) < PAGE:
                break
    return rows


def main():
    global PAGE
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--years", nargs="*", help="two-digit years; default 00-23")
    ap.add_argument("--force", action="store_true", help="re-fetch years already on disk")
    ap.add_argument("--page", type=int, default=PAGE,
                    help="records per request. The default halves its way around a "
                         "record the server cannot serialise, which works without "
                         "knowing in advance which year is broken but costs most of "
                         "a megabyte per failed probe -- CHR19 took an hour and three "
                         "quarters. For a year already known to be fragile, asking "
                         "for fewer up front is far quicker.")
    args = ap.parse_args()

    years = args.years or [f"{y:02d}" for y in range(0, 24)]
    OUT.mkdir(parents=True, exist_ok=True)

    PAGE = args.page

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
