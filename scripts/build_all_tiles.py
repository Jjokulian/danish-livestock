#!/usr/bin/env python3
"""Tile the full cadastre from data/all_parcels/*.jsonl.

Same grid and encoding as build_tiles.py, but roughly sixteen times the parcels,
so it never holds the set in memory. Two passes:

    1. stream every parcel, encode it, append the line to a per-cell spool file
    2. turn each spool file into its tile, one at a time

Spooling keeps resident memory to a bounded buffer regardless of how large the
cadastre is.

Usage:  python3 scripts/build_all_tiles.py [--out site/map/data/tiles_all]
"""
import argparse
import json
import math
import pathlib
import shutil

CELL_LON = 0.1
CELL_LAT = 0.05
FLUSH_EVERY = 120_000


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
    ap.add_argument("--coarse", type=float, default=0.00025)
    ap.add_argument("--src", default=str(root / "data" / "all_parcels"))
    ap.add_argument("--out", default=str(root / "site" / "map" / "data" / "tiles_all"))
    args = ap.parse_args()

    src = pathlib.Path(args.src)
    out = pathlib.Path(args.out)
    spool = root / "data" / "_spool"
    for d in (out, spool):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)

    buffers = {}
    buffered = 0
    total = 0

    def flush():
        nonlocal buffered
        for key, lines in buffers.items():
            with (spool / f"{key}.jsonl").open("a") as fh:
                fh.write("".join(lines))
        buffers.clear()
        buffered = 0

    files = sorted(src.glob("*.jsonl"))
    print(f"pass 1: spooling {len(files)} municipalities")
    for n, path in enumerate(files, 1):
        with path.open() as fh:
            for line in fh:
                parcel = json.loads(line)
                for ring in parcel["g"]:
                    pts = [(x, y) for x, y in ring]
                    if len(pts) < 4:
                        continue
                    cx = int(math.floor(sum(p[0] for p in pts) / len(pts) / CELL_LON))
                    cy = int(math.floor(sum(p[1] for p in pts) / len(pts) / CELL_LAT))
                    coarse = rdp(pts, args.coarse)
                    if len(coarse) < 4:
                        coarse = pts[:: max(1, len(pts) // 4)]
                    rec = [parcel.get("m", ""), parcel.get("e", ""), parcel.get("a", 0),
                           parcel.get("s", 0), encode(pts), encode(coarse)]
                    buffers.setdefault(f"{cx}_{cy}", []).append(
                        json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
                    buffered += 1
                    total += 1
        if buffered >= FLUSH_EVERY:
            flush()
        if n % 10 == 0:
            print(f"  {n}/{len(files)} municipalities, {total:,} rings")
    flush()

    spooled = sorted(spool.glob("*.jsonl"))
    print(f"pass 2: writing {len(spooled):,} tiles")
    bytes_out = 0
    index = {}
    for path in spooled:
        ejerlav, lav_idx = [], {}
        meta, hi, lo = [], [], []
        with path.open() as fh:
            for line in fh:
                m, e, a, s, h, l = json.loads(line)
                if e not in lav_idx:
                    lav_idx[e] = len(ejerlav)
                    ejerlav.append(e)
                meta.append([m, lav_idx[e], a, s, []])
                hi.append(h)
                lo.append(l)
        tile = {"ejerlav": ejerlav, "meta": meta, "hi": hi, "lo": lo}
        dest = out / f"{path.stem}.json"
        dest.write_text(json.dumps(tile, ensure_ascii=False, separators=(",", ":")))
        bytes_out += dest.stat().st_size
        index[path.stem] = len(meta)

    (out.parent / "tile_all_index.json").write_text(json.dumps({
        "cellLon": CELL_LON, "cellLat": CELL_LAT, "cells": index}, separators=(",", ":")))
    shutil.rmtree(spool)
    print(f"wrote {len(index):,} tiles, {total:,} rings, {bytes_out / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
