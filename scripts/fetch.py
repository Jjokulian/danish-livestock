#!/usr/bin/env python3
"""Rebuild data/livestock.json from the Statistics Denmark StatBank API.

Joins two livestock registers into one 1920-2025 series per species, and pulls
the supporting tables used by the report in site/.

  HDYR1920  livestock, 1920-1981 (published in 1,000 units)
  HDYR1     farms with livestock, 1982-  (animal counts and holding counts)
  PELS11    fur animals, 2010-2020 (discontinued after the 2020 cull)
  ANI5      pig slaughterings and live exports, 1990-
  ANI7      milk production and dairy cow numbers, 1990-
  HISB3     population, for per-capita figures

Usage:  python3 scripts/fetch.py [-o data/livestock.json]
"""
import argparse
import collections
import csv
import io
import json
import pathlib
import urllib.parse
import urllib.request

API = "https://api.statbank.dk/v1/data/{table}/CSV"
UA = {"User-Agent": "danish-livestock/1.0 (StatBank CSV pull)"}


def fetch(table, **params):
    """Return the table as a list of dicts, one per CSV row."""
    params.setdefault("lang", "en")
    url = API.format(table=table) + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
        text = r.read().decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text), delimiter=";"))


def series(rows, key_col, value_col="INDHOLD", scale=1, where=None):
    """{label: {year: value}} for the rows that pass `where`, dropping '..'."""
    out = collections.defaultdict(dict)
    for row in rows:
        if where and not where(row):
            continue
        raw = row[value_col].strip()
        if raw == "..":
            continue
        value = float(raw.replace(",", ".")) * scale
        out[row[key_col]][int(row["TID"])] = value
    return out


def pairs(mapping):
    return [[year, int(round(v))] for year, v in sorted(mapping.items())]


def build():
    # 1920-1981: historic census, published in thousands.
    hist = series(
        fetch("HDYR1920", HUSDYRTYPE="1035,1020,1130,1115,1080,1060,1175", Tid="*"),
        "HUSDYRTYPE", scale=1000,
    )
    # 1982-: farm register, split into animal counts and holding counts.
    modern_rows = fetch(
        "HDYR1",
        AREAL1="AIALT",
        ENHED="*",
        ART="D1,KIALT,MK50,AKIALT,KKIALT,SIALT,SOIALT,FIALT,D366,D35,D33,D328,D36",
        Tid="*",
    )
    animals = series(modern_rows, "ART", where=lambda r: r["ENHED"].startswith("Animals"))
    holdings = series(modern_rows, "ART", where=lambda r: r["ENHED"].startswith("Farms"))

    # The historic and modern labels for the same species.
    long_spec = {
        "cattle": ("Cattle, total", "All farms with cattle"),
        "cows": ("Cows", "Cows total"),
        "pigs": ("Pigs, total", "All farms with pigs"),
        "sows": ("Sows", "All farms with sows"),
        "fowls": ("Fowls, total", "Fowls, total"),
        # Sheep are counted with goats before 1982.
        "sheep": ("Sheep and goats, total", "All farms with sheep"),
        # Horses from 1982 cover agricultural holdings only.
        "horses": ("Horses, total", "Horses"),
    }
    long = {}
    for key, (old, new) in long_spec.items():
        joined = dict(hist[old])
        joined.update(animals[new])
        long[key] = pairs(joined)

    modern = {
        key: pairs(animals[label])
        for key, label in [
            ("dairy_cows", "All farms with dairy cows"),
            ("suckler_cows", "Cows kept for suckling total"),
            ("poultry_total", "Poultry,total"),
            ("hens", "Hens"),
            ("broilers", "Chickens for slaughtering"),
            ("turkeys", "Turkeys"),
        ]
    }
    farms = {
        key: pairs(holdings[label])
        for key, label in [
            ("cattle", "All farms with cattle"),
            ("dairy_cows", "All farms with dairy cows"),
            ("pigs", "All farms with pigs"),
            ("sows", "All farms with sows"),
            ("sheep", "All farms with sheep"),
            ("poultry", "Poultry,total"),
            ("horses", "Horses"),
        ]
    }

    # Pig flows: the table reports thousands of head.
    flow = series(
        fetch("ANI5", DYRKAT="SLAGTSVIN,EKSPORT1,SVINIALT", ENHED="SLAGEKS", Tid="*"),
        "DYRKAT", scale=1000,
    )
    pigflow = {
        "slaughtered": pairs(flow["Pigs for slaughtering"]),
        "live_exports": pairs(flow["Exports of live pigs, total"]),
        "total": pairs(flow["Pigs, total"]),
    }

    # Milk: cow numbers in thousands, milk ex farm in million kg.
    milk_rows = series(fetch("ANI7", ENHED="MALKEKOER,MAELK1", Tid="*"), "ENHED")
    cows = milk_rows["Dairy cows, number (1000 units)"]
    milk_m_kg = milk_rows["Milk ex farm, total (m kg)"]
    years = sorted(set(cows) & set(milk_m_kg))
    milk = {
        "cows": [[y, int(round(cows[y] * 1000))] for y in years],
        "total_kg_m": [[y, milk_m_kg[y]] for y in years],
        "yield": [[y, int(round(milk_m_kg[y] * 1e6 / (cows[y] * 1000)))] for y in years],
    }

    # Fur animals. The July 2020 census predates the November 2020 cull; the
    # series was discontinued, so 2021 is carried as an explicit zero.
    pels = series(
        fetch("PELS11", BEDRIFTOPL="1500,1555", BESAET="1000", Tid="*"), "BEDRIFTOPL"
    )
    mink = {
        "animals": pairs(pels["Number of furred animals"]) + [[2021, 0]],
        "farms": pairs(pels["Number of farms"]) + [[2021, 0]],
    }

    # HISB3 labels this row "Population January 1 (in 1,000)"; it is the only
    # series the request asks for, so take it without hard-coding the label.
    pop = series(fetch("HISB3", **{"BEVÆGELSE": "M+K", "Tid": "*"}), "BEVÆGELSE", scale=1000)
    population = [[y, int(v)] for y, v in sorted(next(iter(pop.values())).items()) if y >= 1920]

    return {
        "long": long,
        "modern": modern,
        "farms": farms,
        "pigflow": pigflow,
        "population": population,
        "mink": mink,
        "milk": milk,
    }


def main():
    root = pathlib.Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--out", default=str(root / "data" / "livestock.json"))
    args = ap.parse_args()

    data = build()
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, separators=(",", ":")))
    print(f"wrote {out} ({out.stat().st_size:,} bytes)")
    for key, points in data["long"].items():
        print(f"  {key:8} {points[0][0]}-{points[-1][0]}  {len(points):3} years  latest {points[-1][1]:,}")


if __name__ == "__main__":
    main()
