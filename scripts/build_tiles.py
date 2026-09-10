#!/usr/bin/env python3
"""Cut parcels.json into per-cell tiles the map fetches on demand.

Served from a real HTTP server there is no reason to ship the whole cadastre to
the browser: the page asks for the cells it can see, at the detail the zoom
justifies, and nothing else crosses the wire.

Grid cells are 0.1 deg x 0.05 deg, roughly 6 x 5.5 km. Each tile carries its
parcels twice:

    lo   ~25 m simplification, drawn at zoom 11-12 as one merged shape per property
    hi   as fetched (~2 m),    drawn at zoom 13+, and used for every hit test

Rings are Google-polyline encoded at precision 5, about three bytes a vertex
against the twenty-odd JSON costs.

Usage:  python3 scripts/build_tiles.py [--out site/map/data/tiles]
"""
import argparse
import collections
import json
import math
import pathlib
import shutil

CELL_LON = 0.1
CELL_LAT = 0.05


def encode(points):
    out = []
    prev_x = prev_y = 0
    for lon, lat in points:
        x, y = int(round(lon * 1e5)), int(round(lat * 1e5))
        for delta in (x - prev_x, y - prev_y):
            v = ~(delta << 1) if delta < 0 else (delta << 1)
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1f)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        prev_x, prev_y = x, y
    return "".join(out)


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
            px, py = points[i]
            d = (math.hypot(px - ax, py - ay) if norm == 0
                 else abs((px - ax) * dy - (py - ay) * dx) / norm)
            if d > best:
                best, bi = d, i
        if best > tol:
            keep[bi] = True
            stack.append((lo, bi))
            stack.append((bi, hi))
    return [p for p, k in zip(points, keep) if k]


def main():
    root = pathlib.Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--coarse", type=float, default=0.00025, help="lo tolerance, ~25 m")
    ap.add_argument("--out", default=str(root / "site" / "map" / "data" / "tiles"))
    args = ap.parse_args()

    parcels = json.loads((root / "data" / "parcels.json").read_text())
    matrikel = json.loads((root / "data" / "matrikel_by_chr.json").read_text())

    sites_by_sfe = collections.defaultdict(list)
    for chrnr, par in matrikel.items():
        sfe = str(par.get("sfeejendomsnr") or "")
        if sfe:
            sites_by_sfe[sfe].append(int(chrnr))

    tiles = collections.defaultdict(lambda: {"ejerlav": [], "meta": [], "hi": [], "lo": []})
    lav_per_tile = {}
    n_rings = 0

    for sfe, plist in parcels.items():
        for parcel in plist:
            for poly in parcel["g"]:
                for ring in poly:
                    pts = [(x, y) for x, y in ring]
                    if len(pts) < 4:
                        continue
                    cx = int(math.floor(sum(p[0] for p in pts) / len(pts) / CELL_LON))
                    cy = int(math.floor(sum(p[1] for p in pts) / len(pts) / CELL_LAT))
                    key = f"{cx}_{cy}"
                    tile = tiles[key]
                    index = lav_per_tile.setdefault(key, {})
                    lav = parcel.get("e") or ""
                    if lav not in index:
                        index[lav] = len(tile["ejerlav"])
                        tile["ejerlav"].append(lav)
                    coarse = rdp(pts, args.coarse)
                    if len(coarse) < 4:
                        coarse = pts[:: max(1, len(pts) // 4)]
                    tile["hi"].append(encode(pts))
                    tile["lo"].append(encode(coarse))
                    tile["meta"].append([
                        parcel.get("m") or "",
                        index[lav],
                        parcel.get("a") or 0,
                        int(sfe) if sfe.isdigit() else 0,
                        sites_by_sfe.get(sfe, [])[:6],
                    ])
                    n_rings += 1

    out = pathlib.Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    index = {}
    total = 0
    for key, tile in tiles.items():
        path = out / f"{key}.json"
        path.write_text(json.dumps(tile, ensure_ascii=False, separators=(",", ":")))
        size = path.stat().st_size
        total += size
        index[key] = len(tile["meta"])

    (out.parent / "tile_index.json").write_text(json.dumps({
        "cellLon": CELL_LON, "cellLat": CELL_LAT, "cells": index,
    }, separators=(",", ":")))

    print(f"wrote {len(tiles):,} tiles to {out}")
    print(f"  {n_rings:,} rings, {total / 1e6:.1f} MB total, "
          f"{total / max(1, len(tiles)) / 1024:.1f} KB median-ish per tile")


if __name__ == "__main__":
    main()
