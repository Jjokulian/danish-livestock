#!/usr/bin/env python3
"""Total the cadastral area of each property a livestock site sits on.

fetch_matrikel.py gives the parcel under the farmyard, which is only the yard.
A samlet fast ejendom (SFE) is the whole property — every parcel registered
together — and that is the closer answer to "how much land does this farm hold".
DAWA answers `?sfeejendomsnr=<no.>` with all of them, unauthenticated.

Note what this measures: parcels held as one cadastral property. Land the same
business rents, or owns under a different property number, is not in here — for
that see data/land_by_cvr.json, which counts declared area instead.

Usage:  python3 scripts/fetch_property.py [--workers 8]
"""
import argparse
import json
import pathlib
import queue
import threading
import time
import urllib.parse
import urllib.request

DAWA = "https://api.dataforsyningen.dk/jordstykker"
UA = {"User-Agent": "danish-livestock/1.0 (SFE property totals; contact via repo)"}


def fetch_property(sfe):
    url = DAWA + "?" + urllib.parse.urlencode({"sfeejendomsnr": sfe, "per_side": 500})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=45) as r:
                rows = json.loads(r.read().decode("utf-8"))
            area = sum(x.get("registreretareal") or 0 for x in rows)
            return [area, len(rows)]
        except Exception:
            if attempt == 2:
                return None
            time.sleep(2 * (attempt + 1))


def main():
    root = pathlib.Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("-o", "--out")
    args = ap.parse_args()

    parcels = json.loads((root / "data" / "matrikel_by_chr.json").read_text())
    out = pathlib.Path(args.out or root / "data" / "property_area.json")
    done = json.loads(out.read_text()) if out.exists() else {}

    wanted = {str(v["sfeejendomsnr"]) for v in parcels.values() if v.get("sfeejendomsnr")}
    todo = sorted(wanted - set(done))
    print(f"{len(wanted):,} distinct properties, {len(done):,} done, {len(todo):,} to go")

    work = queue.Queue()
    for sfe in todo:
        work.put(sfe)
    lock = threading.Lock()
    counter = {"n": 0}
    started = time.time()

    def worker():
        while True:
            try:
                sfe = work.get_nowait()
            except queue.Empty:
                return
            res = fetch_property(sfe)
            with lock:
                if res:
                    done[sfe] = res
                counter["n"] += 1
                n = counter["n"]
            if n % 2000 == 0:
                rate = n / max(1e-6, time.time() - started)
                with lock:
                    out.write_text(json.dumps(done, separators=(",", ":")))
                print(f"  {n:,}/{len(todo):,}  {rate:.0f}/s  "
                      f"eta {(len(todo) - n) / max(rate, 1e-6) / 60:.0f} min")
            work.task_done()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(args.workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    out.write_text(json.dumps(done, separators=(",", ":")))
    total_ha = sum(v[0] for v in done.values()) / 10000
    print(f"wrote {out} ({out.stat().st_size:,} bytes, {len(done):,} properties, "
          f"{total_ha:,.0f} ha)")


if __name__ == "__main__":
    main()
