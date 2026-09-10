#!/usr/bin/env python3
"""Publish the built datasets as a store a static host can serve.

serve.py hands the map its data over HTTP and does the live lookups itself. A
static host will do neither, so this writes a second copy of the same data in a
shape a page can read with no server at all: JavaScript shards loaded by script
tag, indexed so a click costs about twenty kilobytes.

The mechanism lives in its own repository, since nothing about it is specific to
Danish livestock:

    https://github.com/Jjokulian/static-async-data

Point SAD_PATH at a checkout, or keep one beside this project as
../static-async-data, and run:

    python3 scripts/build_static_store.py

Nothing existing is touched. data/ and site/map/data/ stay exactly as they were,
serve.py keeps serving them, and this adds site/map/static-async-data/ beside
them plus the loader the page needs. It is a second way out for the same data,
not a migration.
"""
import argparse
import os
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = ROOT / "static-stores.json"
LOADER_DEST = ROOT / "site" / "map" / "sad.js"


def find_library():
    """Locate the static-async-data checkout, in the order worth trying."""
    candidates = []
    if os.environ.get("SAD_PATH"):
        candidates.append(pathlib.Path(os.environ["SAD_PATH"]).expanduser())
    candidates += [ROOT.parent / "static-async-data", ROOT / "vendor" / "static-async-data"]
    for path in candidates:
        if (path / "sad" / "__init__.py").exists():
            return path
    sys.exit(
        "could not find the static-async-data library. Clone it beside this "
        "project:\n\n"
        "    git clone git@github.com:Jjokulian/static-async-data.git "
        f"{ROOT.parent / 'static-async-data'}\n\n"
        "or set SAD_PATH to an existing checkout.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    lib = find_library()
    sys.path.insert(0, str(lib))
    import sad

    print(f"using {lib}")
    rc = sad.main(["build", "--config", str(CONFIG), *(["--quiet"] if args.quiet else [])])
    if rc:
        return rc

    # The page needs the loader beside it; copying keeps site/map/ deployable on
    # its own, with no build step at the far end.
    shutil.copy2(lib / "sad" / "sad.js", LOADER_DEST)
    print(f"loader -> {LOADER_DEST.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
