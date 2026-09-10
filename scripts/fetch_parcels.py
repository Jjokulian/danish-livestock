#!/usr/bin/env python3
"""Download the parcel geometry for every property a livestock site sits on.

One request per property (`?sfeejendomsnr=…&format=geojson`) returns all of its
parcels with polygons, so 42,691 requests cover roughly 144,000 parcels.

Rings are thinned with Ramer-Douglas-Peucker as they arrive and stored as plain
coordinate lists; scripts/build_tiles.py turns them into the zoom pyramid the
map streams from. Checkpoints every 2,000 properties, so an interrupted run
resumes.

Usage:  python3 scripts/fetch_parcels.py [--workers 8] [--tolerance 0.00002]
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
UA = {"User-Agent": "danish-livestock/1.0 (parcel geometry; contact via repo)"}


def rdp(points, tol):
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        lo, hi = stack.pop()
        if hi <= lo + 1:
            continue
        ax, ay = points[lo]
        bx, by = points[hi]
        dx, dy = bx - ax, by - ay
        norm = (dx * dx + dy * dy) ** 0.5
        best, bi = -1.0, lo
        for i in range(lo + 1, hi):
            px, py = points[i]
            if norm == 0:
                d = ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
            else:
                d = abs((px - ax) * dy - (py - ay) * dx) / norm
            if d > best:
                best, bi = d, i
        if best > tol:
            keep[bi] = True
            stack.append((lo, bi))
            stack.append((bi, hi))
    return [p for p, k in zip(points, keep) if k]


def thin(geom, tol):
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    out = []
    for poly in polys:
        rings = []
        for ring in poly:
            r = rdp([(round(x, 6), round(y, 6)) for x, y in ring], tol)
            if len(r) >= 4:
                if r[0] != r[-1]:
                    r.append(r[0])
                rings.append([[x, y] for x, y in r])
        if rings:
            out.append(rings)
    return out


def main():
    root = pathlib.Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--tolerance", type=float, default=0.00002, help="degrees, ~2 m")
    ap.add_argument("-o", "--out")
    args = ap.parse_args()

    props = json.loads((root / "data" / "property_area.json").read_text())
    out = pathlib.Path(args.out or root / "data" / "parcels.json")
    done = json.loads(out.read_text()) if out.exists() else {}
    todo = [s for s in props if s not in done]
    print(f"{len(props):,} properties, {len(done):,} done, {len(todo):,} to go")

    work = queue.Queue()
    for sfe in todo:
        work.put(sfe)
    lock = threading.Lock()
    counter = {"n": 0, "parcels": 0, "points": 0}
    started = time.time()

    def worker():
        while True:
            try:
                sfe = work.get_nowait()
            except queue.Empty:
                return
            url = DAWA + "?" + urllib.parse.urlencode(
                {"sfeejendomsnr": sfe, "format": "geojson", "srid": 4326})
            rows = None
            for attempt in range(3):
                try:
                    req = urllib.request.Request(url, headers=UA)
                    with urllib.request.urlopen(req, timeout=60) as r:
                        rows = json.loads(r.read().decode("utf-8"))
                    break
                except Exception:
                    if attempt == 2:
                        rows = None
                    else:
                        time.sleep(2 * (attempt + 1))
            parcels = []
            for f in (rows or {}).get("features", []):
                p = f.get("properties", {})
                polys = thin(f["geometry"], args.tolerance)
                if not polys:
                    continue
                parcels.append({
                    "m": p.get("matrikelnr"),
                    "e": (p.get("ejerlavnavn") or ""),
                    "a": p.get("registreretareal") or 0,
                    "g": polys,
                })
            with lock:
                if parcels:
                    done[sfe] = parcels
                    counter["parcels"] += len(parcels)
                    counter["points"] += sum(len(r) for pc in parcels
                                             for poly in pc["g"] for r in poly)
                counter["n"] += 1
                n = counter["n"]
            if n % 2000 == 0:
                rate = n / max(1e-6, time.time() - started)
                with lock:
                    out.write_text(json.dumps(done, separators=(",", ":")))
                print(f"  {n:,}/{len(todo):,}  {counter['parcels']:,} parcels  "
                      f"{counter['points']:,} pts  {rate:.0f}/s  "
                      f"eta {(len(todo) - n) / max(rate, 1e-6) / 60:.0f} min")
            work.task_done()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(args.workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    out.write_text(json.dumps(done, separators=(",", ":")))
    print(f"wrote {out} ({out.stat().st_size:,} bytes, {counter['parcels']:,} parcels, "
          f"{counter['points']:,} points)")


if __name__ == "__main__":
    main()
