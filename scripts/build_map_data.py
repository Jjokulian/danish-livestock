#!/usr/bin/env python3
"""Turn the raw CHR pull into the compact payload the map page embeds.

Reads data/chr_<year>.json (and data/land_by_cvr.json if present) and writes
data/map_sites.json: one array per site, plus the lookup tables the page needs.

Two things are worth knowing about the source numbers:

*   Herd size lives in up to three category columns whose meaning changes by
    species. For cattle and pigs the categories partition the herd, so they sum.
    For sheep, goats, horses and mink one category is already the total ("Dyr i
    alt", "Heste i alt"), and the others are subsets of it — summing there would
    double-count, so the total column wins.
*   DE (dyreenheder, animal units) is a manure-regulation measure and is left at
    zero for species outside that regulation, horses included. Head count is the
    only measure that covers every site.

Usage:  python3 scripts/build_map_data.py [--year 2024]
"""
import argparse
import collections
import json
import pathlib

GROUPS = [
    ("cattle", "Cattle", {12}),
    ("pigs", "Pigs", {15}),
    ("poultry", "Poultry", {30, 31, 32, 33, 34, 35, 40, 41, 42, 43, 44, 45, 46, 47}),
    ("sheepgoats", "Sheep & goats", {13, 14}),
    ("horses", "Horses", {11}),
    ("fish", "Fish & shellfish", set(range(50, 72))),
    ("other", "Other", set()),  # deer, camelids, fur animals, rabbits, bees
]
GROUP_OF = {code: i for i, (_, _, codes) in enumerate(GROUPS) for code in codes}


def as_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def herd_size(rec):
    """Head count, honouring the 'i alt' total columns instead of summing them."""
    cats = [((rec.get(f"BSTRTEKST{i}") or "").strip(), as_int(rec.get(f"BSTR{i}")))
            for i in (1, 2, 4)]
    cats = [(label, n) for label, n in cats if label]
    totals = [n for label, n in cats if "i alt" in label.lower()]
    head = totals[0] if totals else sum(n for _, n in cats)
    return head, cats


def main():
    root = pathlib.Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--year", default="2024")
    ap.add_argument("-o", "--out")
    args = ap.parse_args()

    sites_in = json.loads((root / "data" / f"chr_{args.year}.json").read_text())
    land_path = root / "data" / "land_by_cvr.json"
    land = json.loads(land_path.read_text()) if land_path.exists() else {}
    mat_path = root / "data" / "matrikel_by_chr.json"
    matrikel = json.loads(mat_path.read_text()) if mat_path.exists() else {}
    prop_path = root / "data" / "property_area.json"
    props = json.loads(prop_path.read_text()) if prop_path.exists() else {}
    print(f"{len(sites_in):,} CHR sites, {len(land):,} CVR land records, "
          f"{len(matrikel):,} parcels, {len(props):,} properties")

    kommuner, kommune_idx = [], {}
    ejerlav, ejerlav_idx = [], {}
    virkart, virkart_idx = [], {}
    brug, brug_idx = [], {}
    crops, crop_idx = [], {}
    labels, label_idx = [], {}
    species, species_idx = [], {}
    rows = []

    for rec in sites_in:
        kom = rec.get("Kommune") or ""
        if kom not in kommune_idx:
            kommune_idx[kom] = len(kommuner)
            kommuner.append(kom)
        kind = rec.get("DYRTEKST") or ""
        if kind not in species_idx:
            species_idx[kind] = len(species)
            species.append(kind)

        def intern(value, table, index):
            value = (value or "").strip()
            if not value:
                return -1
            if value not in index:
                index[value] = len(table)
                table.append(value)
            return index[value]

        virkart_i = intern(rec.get("VIRKART"), virkart, virkart_idx)
        brug_i = intern(rec.get("BRUGTEKST"), brug, brug_idx)

        head, cats = herd_size(rec)
        packed = []
        for label, n in cats:
            if label not in label_idx:
                label_idx[label] = len(labels)
                labels.append(label)
            packed.append([label_idx[label], n])

        cvr = (rec.get("CVRNR") or "").strip()
        land_rec = land.get(cvr) if cvr else None
        ha = land_rec[0] if land_rec else 0
        land_parcels = land_rec[1] if land_rec else 0
        crop_i = intern(land_rec[2], crops, crop_idx) if land_rec else -1

        # The parcel the farmyard stands on, and the property it belongs to.
        par = matrikel.get(str(as_int(rec.get("CHRNR"))))
        mat_no, lav_i, parcel_ha, prop_ha, prop_n, sfe_no = "", -1, 0, 0, 0, 0
        if par:
            mat_no = par.get("matrikelnr") or ""
            lav = par.get("ejerlav")
            if lav:
                if lav not in ejerlav_idx:
                    ejerlav_idx[lav] = len(ejerlav)
                    ejerlav.append(lav)
                lav_i = ejerlav_idx[lav]
            parcel_ha = round((par.get("registreretareal") or 0) / 10000, 1)
            sfe = str(par.get("sfeejendomsnr") or "")
            sfe_no = as_int(sfe)
            if sfe in props:
                prop_ha = round(props[sfe][0] / 10000, 1)
                prop_n = props[sfe][1]

        rows.append([
            int(round(rec["lon"] * 1e5)),
            int(round(rec["lat"] * 1e5)),
            GROUP_OF.get(rec.get("DYRKODE"), len(GROUPS) - 1),
            species_idx[kind],
            head,
            int(round((rec.get("DE") or 0) * 10)),
            int(round(ha)),
            kommune_idx[kom],
            as_int(rec.get("CHRNR")),
            as_int(cvr),
            packed,
            mat_no,
            lav_i,
            parcel_ha,
            prop_ha,
            prop_n,
            sfe_no,
            as_int(rec.get("Postnr")),
            virkart_i,
            brug_i,
            land_parcels,
            crop_i,
        ])

    payload = {
        "year": int(args.year),
        "groups": [[key, name] for key, name, _ in GROUPS],
        "species": species,
        "labels": labels,
        "kommuner": kommuner,
        "ejerlav": ejerlav,
        "virkart": virkart,
        "brug": brug,
        "crops": crops,
        "sites": rows,
    }
    out = pathlib.Path(args.out or root / "data" / "map_sites.json")
    out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {out} ({out.stat().st_size:,} bytes)")

    by_group = collections.Counter()
    head_by_group = collections.Counter()
    for r in rows:
        by_group[r[2]] += 1
        head_by_group[r[2]] += r[4]
    for i, (_, name, _) in enumerate(GROUPS):
        print(f"  {name:<18} {by_group[i]:>7,} sites  {head_by_group[i]:>12,} head")


if __name__ == "__main__":
    main()
