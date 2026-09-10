#!/usr/bin/env python3
"""Assemble the static deploy tree for GitHub Pages (or any file host).

site/map/ is what the server serves, and not all of it belongs on a static host:
the pre-compressed .gz siblings are dead weight where the host compresses for
itself, and data/cvr.json is now only a build input for the sharded store that
replaced it.

    python3 scripts/deploy_pages.py                 # -> build/pages/
    python3 scripts/deploy_pages.py --lean          # without the full cadastre
    python3 scripts/deploy_pages.py --out /tmp/x

Nothing is moved: this copies. site/map/ stays exactly as it is, and serve.py
keeps serving it.
"""
import argparse
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "site" / "map"

# Excluded always, with the reason, because a silent omission is a bug.
ALWAYS_SKIP = [
    ("*.json.gz", "the host compresses for itself; these double the repository"),
    ("data/cvr.json", "only a build input for static-async-data/cvr now"),
]
LEAN_SKIP = [
    ("data/tiles_all", "the full cadastre — the map's 'every parcel' checkbox "
                       "reports it as not built and carries on"),
    ("data/tile_all_index.json", "index for the above"),
]


def matches(rel, pattern):
    return rel.match(pattern) or str(rel) == pattern or str(rel).startswith(pattern + "/")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "build" / "pages"))
    ap.add_argument("--lean", action="store_true",
                    help="leave out the full-cadastre tiles (about 230 MB)")
    args = ap.parse_args()

    skip = list(ALWAYS_SKIP) + (LEAN_SKIP if args.lean else [])
    out = pathlib.Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    kept = skipped = kept_bytes = skipped_bytes = 0
    for f in sorted(SRC.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(SRC)
        if any(matches(rel, pat) for pat, _ in skip):
            skipped += 1
            skipped_bytes += f.stat().st_size
            continue
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dest)
        kept += 1
        kept_bytes += f.stat().st_size

    # Jekyll is on by default and quietly drops anything under a _ or . name.
    (out / ".nojekyll").write_text("", encoding="utf-8")

    print(f"{kept:,} files, {kept_bytes/1048576:.1f} MB -> {out}")
    print(f"left out {skipped:,} files, {skipped_bytes/1048576:.1f} MB:")
    for pat, why in skip:
        print(f"  {pat:<28} {why}")

    missing = [n for n in ("index.html", "app.js", "sad.js", "map.css",
                           "static-async-data/cvr/index.js", "data/sites.json")
               if not (out / n).exists()]
    if missing:
        print("\nMISSING, the page will not work:", ", ".join(missing))
        return 1
    print("\nverify it:  cd", out, "&& python3 -m http.server 8801")
    return 0


if __name__ == "__main__":
    sys.exit(main())
