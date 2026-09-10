#!/usr/bin/env python3
"""Put the farms that keep no animals on the map.

Livestock is a part of Danish farming, not the whole of it. 26,922 businesses
declare field parcels for area support and fewer than half of them keep animals;
the rest grow things. They are just as much farms, they simply do not appear in
a livestock register -- so there is no CHR point to draw them at.

What they do have is a registered address, all 15,271 of them, and DAWA turns a
Danish address into coordinates without a key:

    /adgangsadresser?q=<address>&struktur=mini   -> betegnelse, x, y

    python3 scripts/geocode_farms.py
    python3 scripts/geocode_farms.py --limit 200

Resumable: answers land in data/crop_farm_points.jsonl one line at a time.

An address is where a business is registered, which for a farm is usually the
farmyard and occasionally an accountant's office in town. The map says so rather
than implying these points are as exact as a CHR site, which is surveyed.
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
DATA = ROOT / "data"
OUT = DATA / "crop_farm_points.jsonl"
API = "https://api.dataforsyningen.dk/adgangsadresser"
UA = {"User-Agent": "danish-livestock/1.0 (farm map)"}


def crop_only():
    """Businesses with a CVR record and no livestock site."""
    sites = json.loads((DATA / "map_sites.json").read_text())["sites"]
    livestock = {row[9] for row in sites if row[9]}
    out = {}
    with (DATA / "cvr_raw.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            if not rec.get("ok") or rec["cvr"] in livestock:
                continue
            d = rec["d"]
            if d.get("address") and d.get("city"):
                out[rec["cvr"]] = d
    return out


def already_have():
    seen = set()
    if OUT.exists():
        with OUT.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    try:
                        seen.add(json.loads(line)["cvr"])
                    except (json.JSONDecodeError, KeyError):
                        pass
    return seen


def lookup(query, tries=3):
    url = API + "?" + urllib.parse.urlencode(
        {"q": query, "struktur": "mini", "per_side": 1})
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return []
            if attempt == tries - 1:
                return None
            time.sleep(2 * (attempt + 1))
        except Exception:
            if attempt == tries - 1:
                return None
            time.sleep(2 * (attempt + 1))
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--delay", type=float, default=0.05)
    args = ap.parse_args()

    farms = crop_only()
    seen = already_have()
    todo = [c for c in sorted(farms) if c not in seen]
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(farms):,} crop-only businesses, {len(seen):,} placed, "
          f"{len(todo):,} to geocode (~{len(todo) * (args.delay + 0.08) / 60:.0f} min)")
    if not todo:
        return

    hits = misses = errors = 0
    with OUT.open("a", encoding="utf-8") as fh:
        for i, cvr in enumerate(todo, 1):
            d = farms[cvr]
            q = " ".join(f"{d['address']}, {d['city']}".split())
            res = lookup(q)
            if res is None:
                errors += 1
                rec = {"cvr": cvr, "ok": False}
            elif not res:
                misses += 1
                rec = {"cvr": cvr, "ok": False}
            else:
                a = res[0]
                hits += 1
                rec = {"cvr": cvr, "ok": True,
                       "lon": round(a["x"], 5), "lat": round(a["y"], 5),
                       "at": a.get("betegnelse")}
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            if i % 500 == 0 or i == len(todo):
                print(f"  {i:,}/{len(todo):,}  {hits:,} placed  {misses:,} unmatched  "
                      f"{errors:,} failed", flush=True)
            time.sleep(args.delay)

    print(f"done: {hits:,} placed, {misses:,} unmatched, {errors:,} failed -> {OUT}")


if __name__ == "__main__":
    sys.exit(main())
