#!/usr/bin/env python3
"""Resolve the cadastral parcel under every CHR livestock site.

DAWA's /jordstykker endpoint answers a point-in-parcel query unauthenticated,
and returns the matrikel number, the cadastral district (ejerlav), the
registered area, and the SFE/BFE property number the parcel belongs to. That is
the ownership unit — as opposed to data/land_by_cvr.json, which is land declared
for area support in one year and includes rented ground.

What DAWA does not carry is *who* owns it: Ejerfortegnelsen sits on Datafordeler
behind a login. So this gives the territory, not the owner's name.

Runs a small thread pool (8 by default) and checkpoints as it goes, so an
interrupted run resumes instead of starting over.

Usage:  python3 scripts/fetch_matrikel.py [--workers 8] [--limit N]
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
UA = {"User-Agent": "danish-livestock/1.0 (CHR x matrikel join; contact via repo)"}
FIELDS = ("matrikelnr", "registreretareal", "sfeejendomsnr", "bfenummer")


def lookup(lon, lat):
    url = DAWA + "?" + urllib.parse.urlencode({"x": lon, "y": lat, "srid": 4326})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=45) as r:
                rows = json.loads(r.read().decode("utf-8"))
            if not rows:
                return None
            row = rows[0]
            out = {k: row.get(k) for k in FIELDS}
            out["ejerlav"] = (row.get("ejerlav") or {}).get("navn")
            out["ejerlavkode"] = (row.get("ejerlav") or {}).get("kode")
            return out
        except Exception:
            if attempt == 2:
                return {"error": True}
            time.sleep(2 * (attempt + 1))


def main():
    root = pathlib.Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="stop after N sites (for testing)")
    ap.add_argument("-o", "--out")
    args = ap.parse_args()

    payload = json.loads((root / "data" / "map_sites.json").read_text())
    sites = payload["sites"]
    if args.limit:
        sites = sites[:args.limit]
    out = pathlib.Path(args.out or root / "data" / "matrikel_by_chr.json")

    done = json.loads(out.read_text()) if out.exists() else {}
    todo = [s for s in sites if str(s[8]) not in done]
    print(f"{len(sites):,} sites, {len(done):,} already resolved, {len(todo):,} to go")

    work = queue.Queue()
    for s in todo:
        work.put(s)
    lock = threading.Lock()
    counter = {"n": 0, "hit": 0}
    started = time.time()

    def worker():
        while True:
            try:
                site = work.get_nowait()
            except queue.Empty:
                return
            res = lookup(site[0] / 1e5, site[1] / 1e5)
            with lock:
                if res and not res.get("error"):
                    done[str(site[8])] = res
                    counter["hit"] += 1
                counter["n"] += 1
                n = counter["n"]
            if n % 2000 == 0:
                rate = n / max(1e-6, time.time() - started)
                with lock:
                    out.write_text(json.dumps(done, ensure_ascii=False, separators=(",", ":")))
                print(f"  {n:,}/{len(todo):,}  {counter['hit']:,} matched  "
                      f"{rate:.0f}/s  eta {(len(todo) - n) / max(rate, 1e-6) / 60:.0f} min")
            work.task_done()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(args.workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    out.write_text(json.dumps(done, ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {out} ({out.stat().st_size:,} bytes, {len(done):,} sites matched to a parcel)")


if __name__ == "__main__":
    main()
