#!/usr/bin/env python3
"""CVR records and filed accounts -> the compact payload the map loads.

data/cvr_raw.jsonl is one company per line, as fetched.  data/cvr_financials.json
is the parsed annual accounts.  Neither is a shape a browser wants: the raw file
repeats the same forty industry names sixteen thousand times, and the accounts
carry every figure for three years.

This folds them into site/map/data/cvr.json:

    {"industries": [...], "forms": [...], "roles": [...],   # shared strings
     "c": {"29751455": [name, form_i, industry_i, city, start, ...]}}

Run it whenever the fetchers have advanced; it works fine on a partial pull and
simply covers fewer companies.

    python3 scripts/build_cvr_data.py
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "site" / "map" / "data" / "cvr.json"
FIN = ROOT / "site" / "map" / "data" / "finance.json"

# Filtering the whole map by a figure needs that figure for every company at
# once, which is the one thing a sharded store deliberately will not do -- a
# shard is fetched on a click. So the detail stays sharded and the numbers worth
# filtering on ship as one small column index, loaded at boot. Four figures for
# 1,630 companies is tens of kilobytes; the records they came from are 6.5 MB.
FIN_FIELDS = ["year", "equity", "profit", "assets", "land_bldg", "revenue",
              "employees"]

# Record layout, mirrored in app.js.  Positional because 16,000 of these ship to
# every visitor and the key names would be most of the file.
FIELDS = ["name", "form", "industry", "city", "kommune", "start", "status",
          "employees", "purpose", "capital", "people", "owners", "units",
          "phone", "email", "accounts"]


def load_companies():
    path = DATA / "cvr_raw.jsonl"
    if not path.exists():
        sys.exit("no data/cvr_raw.jsonl — run scripts/fetch_cvr.py first")
    out = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("ok") and rec.get("d", {}).get("name"):
                out[rec["cvr"]] = rec["d"]        # a later line wins
    return out


def load_financials():
    path = DATA / "cvr_financials.json"
    if not path.exists():
        return {}
    return {int(k): v for k, v in json.loads(path.read_text()).items()}


# The gateway names roles in Danish, and a few slip past the mapping in
# fetch_cvr.py because the register uses more of them than are documented.
# Translating here rather than at fetch time means a new one can be handled
# without re-pulling sixteen thousand companies.
ROLE_EN = {
    "bestyrelsesmedlemmer": "board member",
    "direktoerer": "director",
    "adm dir": "managing director",
    "formand": "chair",
    "naestformand": "deputy chair",
    "baeredygtighedsrevision": "sustainability auditor",
    "revision": "auditor",
    "stiftere": "founder",
    "likvidator": "liquidator",
    "kurator": "trustee in bankruptcy",
    "ejere": "owner",
}


def shorten_name(name, kind):
    """Given names to initials, surname in full: Jørgen Holk Nielsen -> J. H. Nielsen.

    The register publishes these names and Danish CVR is deliberately open, but
    a full name is the key that joins a person to everything else about them on
    the open web.  An initial keeps the record recognisable to someone who
    already knows the farm -- which is who a farm map is for -- while making the
    published file useless as a list to look people up from.

    Companies keep their names. An auditor is a firm, not a person, and
    "P. STATSAUTORISERET REVISIONSPARTNERSELSKAB" helps nobody.
    """
    if (kind or "").upper() != "PERSON":
        return name
    parts = (name or "").split()
    if len(parts) < 2:
        return name
    return " ".join(p[0].upper() + "." for p in parts[:-1]) + " " + parts[-1]


def role_label(role):
    if not role:
        return None
    return ROLE_EN.get(role, role)


class Pool:
    """A string table, so a repeated industry name is stored once."""

    def __init__(self):
        self.items, self.index = [], {}

    def __call__(self, value):
        if value in (None, ""):
            return -1
        i = self.index.get(value)
        if i is None:
            i = self.index[value] = len(self.items)
            self.items.append(value)
        return i


def one_line(value):
    """CVR writes a two-part address with a newline in it; a table cell wants one."""
    return " ".join(str(value).split()) if value else value


def employee_count(rec):
    """One number for staff, from whichever series has the fresher figure.

    CVR reports a monthly count as a plain number and a quarterly one as a band
    ('20-49 medarbejdere').  The monthly figure is preferred; a band is kept as
    text, because rounding '20-49' to a number would invent precision.
    """
    monthly = rec.get("employees") or {}
    staff = (monthly.get("staff") or "").strip()
    if staff.isdigit():
        return int(staff)
    quarterly = rec.get("employees_q") or {}
    band = (quarterly.get("staff") or "").strip()
    return band or None


def build_finance_index(financials, on_map):
    """The filterable figures, newest year per company, keyed by CVR.

    Only companies that actually filed appear -- about a tenth of the register,
    since a sole trader or an I/S has no filing duty. The map has to say so
    rather than quietly dropping nine farms in ten when a filter is touched.
    """
    out = {}
    for cvr, years in financials.items():
        if cvr not in on_map or not years:
            continue
        latest = years[0]
        row = [int((latest.get("end") or "0")[:4]) or None]
        row += [latest.get(f) for f in FIN_FIELDS[1:]]
        while row and row[-1] is None:
            row.pop()
        if len(row) > 1:                      # a year alone is not worth shipping
            out[str(cvr)] = row
    return out


def main():
    companies = load_companies()
    financials = load_financials()
    print(f"{len(companies):,} companies, {len(financials):,} with filed accounts")

    forms, industries, roles = Pool(), Pool(), Pool()
    out = {}
    for cvr, rec in sorted(companies.items()):
        people = [[shorten_name(p["name"], p.get("type")),
                   roles(role_label(p.get("role")))]
                  for p in (rec.get("people") or [])][:12]
        owners = [[shorten_name(o.get("name"), "PERSON"), o.get("share")]
                  for o in (rec.get("legal_owners") or []) +
                           (rec.get("beneficial_owners") or []) if o.get("name")][:8]
        units = [[one_line(u.get("name")), one_line(u.get("address")),
                  one_line(u.get("city")), u.get("industry")]
                 for u in (rec.get("units") or [])][:8]
        row = [
            one_line(rec.get("name")),
            forms(rec.get("form")),
            industries(rec.get("industry")),
            one_line(rec.get("city")),
            rec.get("kommune"),
            (rec.get("start") or "")[:10] or None,
            rec.get("status"),
            employee_count(rec),
            (rec.get("purpose") or "")[:400] or None,
            rec.get("capital"),
            people,
            owners,
            units,
            rec.get("phone"),
            rec.get("email"),
        ]
        acc = financials.get(cvr)
        row.append(acc[:3] if acc else None)
        while row and row[-1] in (None, [], -1):
            row.pop()
        out[str(cvr)] = row

    payload = {"fields": FIELDS, "forms": forms.items, "industries": industries.items,
               "roles": roles.items, "c": out}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    size = OUT.stat().st_size
    with_accounts = sum(1 for r in out.values() if len(r) > 15 and r[15])
    print(f"{len(out):,} companies ({with_accounts:,} with accounts), "
          f"{len(industries.items)} industries, {len(forms.items)} legal forms")
    print(f"-> {OUT}  {size / 1e6:.2f} MB")

    fin_index = build_finance_index(financials, set(companies))
    FIN.write_text(json.dumps({"fields": FIN_FIELDS, "f": fin_index},
                              separators=(",", ":")), encoding="utf-8")
    counts = {f: sum(1 for r in fin_index.values()
                     if len(r) > i and r[i] is not None)
              for i, f in enumerate(FIN_FIELDS)}
    print(f"-> {FIN}  {FIN.stat().st_size / 1024:.0f} KB  "
          f"{len(fin_index):,} companies with figures")
    print("   coverage: " + ", ".join(f"{f} {counts[f]:,}" for f in FIN_FIELDS[1:]))


if __name__ == "__main__":
    sys.exit(main())
