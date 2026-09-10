#!/usr/bin/env python3
"""Download every cadastral parcel in Denmark, not just the livestock ones.

About 2.5 million jordstykker. Two things force the shape of this script:

*   DAWA refuses to paginate past the first 25,000 elements, so a large
    municipality cannot simply be paged through. When one hits that wall its
    bounding box is split into quadrants and each is fetched separately,
    recursively, until every query fits under the cap. Results are filtered back
    to the municipality by kommunekode, and a parcel straddling a split line
    arrives twice, so identical (ejerlav, matrikel) pairs are dropped.

*   `bbox` is silently ignored by DAWA -- it returns unfiltered results -- so the
    spatial filter has to be `polygon`.
*   This machine has a few hundred MB of RAM free, and one rural municipality is
    a 105 MB response. So pages are small, each parcel is thinned as it arrives,
    and results go straight to a JSONL file per municipality — nothing large is
    ever resident.

Output: data/all_parcels/<kommunekode>.jsonl, one parcel per line. A municipality
already on disk is skipped, so the run resumes.

Usage:  python3 scripts/fetch_all_parcels.py [--workers 4] [--per-side 1000]
"""
import argparse
import json
import math
import pathlib
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

DAWA = "https://api.dataforsyningen.dk/jordstykker"
UA = {"User-Agent": "danish-livestock/1.0 (full cadastre; contact via repo)"}


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
        norm = math.hypot(dx, dy)
        best, bi = -1.0, lo
        for i in range(lo + 1, hi):
            pxx, pyy = points[i]
            d = (math.hypot(pxx - ax, pyy - ay) if norm == 0
                 else abs((pxx - ax) * dy - (pyy - ay) * dx) / norm)
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
        for ring in poly:
            r = rdp([(round(x, 6), round(y, 6)) for x, y in ring], tol)
            if len(r) >= 4:
                if r[0] != r[-1]:
                    r.append(r[0])
                out.append([[x, y] for x, y in r])
    return out


class PagingCapped(Exception):
    """DAWA will not paginate past the first 25,000 elements of a result set."""


def get(url, tries=4):
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=240) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 400:
                body = e.read() or b""
                if b"25000" in body:
                    raise PagingCapped()
            if attempt == tries - 1:
                raise
            time.sleep(3 * (attempt + 1))
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(3 * (attempt + 1))


def fetch_page(params, per_side, side):
    q = dict(params)
    q.update({"format": "geojson", "srid": 4326, "per_side": per_side, "side": side})
    return get(DAWA + "?" + urllib.parse.urlencode(q)).get("features", [])


def walk(params, per_side, kode, emit, box=None, depth=0):
    """Page a query, splitting its box into quadrants when DAWA caps the paging."""
    side = 1
    try:
        while True:
            feats = fetch_page(params, per_side, side)
            for f in feats:
                if f["properties"].get("kommunekode") == kode:
                    emit(f)
            if len(feats) < per_side:
                return
            side += 1
    except PagingCapped:
        if box is None or depth > 6:
            raise
        w, s0, e, n = box
        mx, my = (w + e) / 2, (s0 + n) / 2
        for qw, qs, qe, qn in ((w, s0, mx, my), (mx, s0, e, my),
                               (w, my, mx, n), (mx, my, e, n)):
            ring = [[qw, qs], [qe, qs], [qe, qn], [qw, qn], [qw, qs]]
            sub = {"polygon": json.dumps([ring])}
            walk(sub, per_side, kode, emit, (qw, qs, qe, qn), depth + 1)


def kommune_boxes(root):
    """Bounding box per municipality, taken from the outlines already on disk."""
    path = root / "data" / "kommuner.json"
    if not path.exists():
        return {}
    boxes = {}
    for feat in json.loads(path.read_text())["features"]:
        xs, ys = [], []
        for poly in feat["geometry"]["coordinates"]:
            for ring in poly:
                for x, y in ring:
                    xs.append(x)
                    ys.append(y)
        if xs:
            boxes[str(feat["properties"]["kode"]).zfill(4)] = (
                min(xs) - 0.01, min(ys) - 0.01, max(xs) + 0.01, max(ys) + 0.01)
    return boxes


def main():
    root = pathlib.Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--per-side", type=int, default=1000)
    ap.add_argument("--tolerance", type=float, default=0.00002, help="degrees, ~2 m")
    args = ap.parse_args()

    out_dir = root / "data" / "all_parcels"
    out_dir.mkdir(parents=True, exist_ok=True)

    boxes = kommune_boxes(root)
    kommuner = get("https://api.dataforsyningen.dk/kommuner")
    todo = [k for k in kommuner if not (out_dir / f"{k['kode']}.jsonl").exists()]
    print(f"{len(kommuner)} municipalities, {len(todo)} to fetch")

    work = queue.Queue()
    for k in todo:
        work.put(k)
    lock = threading.Lock()
    counter = {"done": 0, "parcels": 0}
    started = time.time()

    def worker():
        while True:
            try:
                kom = work.get_nowait()
            except queue.Empty:
                return
            kode, navn = kom["kode"], kom["navn"]
            tmp = out_dir / f"{kode}.part"
            counts = {"n": 0}
            seen = set()
            try:
                with tmp.open("w") as fh:
                    def emit(f):
                        p = f["properties"]
                        ident = (p.get("ejerlavkode"), p.get("matrikelnr"))
                        if ident in seen:
                            return
                        seen.add(ident)
                        rings = thin(f["geometry"], args.tolerance)
                        if not rings:
                            return
                        fh.write(json.dumps({
                            "m": p.get("matrikelnr") or "",
                            "e": p.get("ejerlavnavn") or "",
                            "a": p.get("registreretareal") or 0,
                            "s": p.get("bfenummer") or 0,
                            "g": rings,
                        }, ensure_ascii=False, separators=(",", ":")) + "\n")
                        counts["n"] += 1

                    walk({"kommunekode": kode}, args.per_side, kode, emit, boxes.get(kode))
                tmp.rename(out_dir / f"{kode}.jsonl")
                n = counts["n"]
            except Exception as exc:
                if tmp.exists():
                    tmp.unlink()
                print(f"  !! {navn} ({kode}) failed: {exc}")
                work.task_done()
                continue
            with lock:
                counter["done"] += 1
                counter["parcels"] += n
                d, tot = counter["done"], counter["parcels"]
            rate = d / max(1e-6, (time.time() - started) / 60)
            print(f"  {d}/{len(todo)}  {navn} ({kode}): {n:,} parcels  "
                  f"[{tot:,} total, {rate:.1f} kom/min, "
                  f"eta {(len(todo) - d) / max(rate, 1e-6):.0f} min]")
            work.task_done()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(args.workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print(f"done: {counter['parcels']:,} parcels in {counter['done']} municipalities")


if __name__ == "__main__":
    main()
