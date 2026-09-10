#!/usr/bin/env python3
"""Fetch Danish municipality outlines from DAWA and simplify them for the web.

The map in site/map.html has no tile layer — an Artifact's CSP blocks external
images — so the basemap is drawn from these polygons. DAWA serves them open and
unauthenticated, but at full resolution the 98 municipalities are 119 MB, so
each one is fetched separately and thinned with Ramer-Douglas-Peucker before the
next is requested. Nothing large is ever held in memory.

Usage:  python3 scripts/fetch_kommuner.py [--tolerance 0.0015]
"""
import argparse
import json
import pathlib
import time
import urllib.request

import numpy as np

DAWA = "https://api.dataforsyningen.dk/kommuner"
UA = {"User-Agent": "danish-livestock/1.0 (DAWA pull)"}


def get_json(url):
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=180) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as exc:
            if attempt == 3:
                raise
            print(f"  retry {attempt + 1} after {exc}")
            time.sleep(3 * (attempt + 1))


def rdp(points, tol):
    """Ramer-Douglas-Peucker, iterative so deep rings can't blow the stack."""
    pts = np.asarray(points, dtype=float)
    if len(pts) < 3:
        return pts
    keep = np.zeros(len(pts), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        lo, hi = stack.pop()
        if hi <= lo + 1:
            continue
        seg = pts[hi] - pts[lo]
        norm = float(np.hypot(*seg))
        rel = pts[lo + 1:hi] - pts[lo]
        if norm == 0:
            dist = np.hypot(rel[:, 0], rel[:, 1])
        else:
            dist = np.abs(rel[:, 0] * seg[1] - rel[:, 1] * seg[0]) / norm
        idx = int(np.argmax(dist))
        if dist[idx] > tol:
            split = lo + 1 + idx
            keep[split] = True
            stack.append((lo, split))
            stack.append((split, hi))
    return pts[keep]


def simplify_ring(ring, tol, decimals=4):
    thinned = rdp(ring, tol)
    if len(thinned) < 4:
        return None
    out = [[round(float(x), decimals), round(float(y), decimals)] for x, y in thinned]
    # Rounding can collapse the closing point; re-close explicitly.
    if out[0] != out[-1]:
        out.append(out[0])
    return out if len(out) >= 4 else None


def simplify_geometry(geom, tol):
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    kept = []
    for poly in polys:
        rings = [r for r in (simplify_ring(ring, tol) for ring in poly) if r]
        if rings:
            kept.append(rings)
    return {"type": "MultiPolygon", "coordinates": kept} if kept else None


def main():
    root = pathlib.Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tolerance", type=float, default=0.0015,
                    help="RDP tolerance in degrees (0.0015 is roughly 150 m)")
    ap.add_argument("-o", "--out")
    args = ap.parse_args()
    out = pathlib.Path(args.out or root / "data" / "kommuner.json")

    index = get_json(DAWA)
    print(f"{len(index)} municipalities")

    features = []
    for i, k in enumerate(index, 1):
        fc = get_json(f"{DAWA}/{k['kode']}?format=geojson")
        feature = fc["features"][0] if fc.get("type") == "FeatureCollection" else fc
        geom = simplify_geometry(feature["geometry"], args.tolerance)
        if not geom:
            continue
        features.append({
            "type": "Feature",
            "properties": {"kode": k["kode"], "navn": k["navn"],
                           "region": feature["properties"].get("regionsnavn", "")},
            "geometry": geom,
        })
        pts = sum(len(r) for poly in geom["coordinates"] for r in poly)
        print(f"  {i:>3}/{len(index)}  {k['navn']:<22} {pts:>6,} points")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"type": "FeatureCollection", "features": features},
                              ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {out} ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
