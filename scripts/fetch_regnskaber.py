#!/usr/bin/env python3
"""Published annual accounts for the companies that keep livestock.

Erhvervsstyrelsen publishes every annual report filed in Denmark, and the index
answers without any credential at all -- unlike the company register next to it:

    POST http://distribution.virk.dk/offentliggoerelser/_search

Each hit names the accounting period and links the documents: a PDF for people
and an XBRL instance for machines.  The XBRL is the point.  It carries the
figures as tagged facts, so revenue, profit, equity and the book value of land
and buildings come out as numbers rather than as text in a scanned table.

    python3 scripts/fetch_regnskaber.py             # index + the last 3 years
    python3 scripts/fetch_regnskaber.py --years 1   # just the latest
    python3 scripts/fetch_regnskaber.py --index-only

Most farms will not appear.  A sole trader or an I/S has no filing duty, and
that is the majority of Danish agriculture; what comes back is the ApS and A/S
end of the register, which is where the large herds are.  The script says how
many of the CVR numbers it covered.

Two files come out of it:
    data/regnskab_index.json   every publication for these CVRs, from the index
    data/cvr_financials.json   parsed figures, latest first, per CVR
"""
import argparse
import gzip
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SEARCH = "http://distribution.virk.dk/offentliggoerelser/_search"
UA = {"User-Agent": "danish-livestock/1.0 (open register map)"}
BATCH = 400          # CVR numbers per index query
PAGE = 1000          # hits per scroll page

# The figures worth carrying, and the taxonomy tags that hold them.  The Danish
# tags changed over the years, so each figure lists its aliases newest first and
# the first one present wins.
FIGURES = [
    ("revenue",      ["Revenue"]),
    ("gross",        ["GrossResult", "GrossProfitLoss"]),
    ("staff_cost",   ["EmployeeBenefitsExpense"]),
    ("ebit",         ["ProfitLossFromOrdinaryOperatingActivities", "OperatingProfitLoss"]),
    ("pretax",       ["ProfitLossFromOrdinaryActivitiesBeforeTax", "ProfitLossBeforeTax"]),
    ("profit",       ["ProfitLoss"]),
    ("equity",       ["Equity"]),
    ("assets",       ["Assets"]),
    ("land_bldg",    ["LandAndBuildings"]),
    ("ppe",          ["PropertyPlantAndEquipment"]),
    ("biological",   ["BiologicalAssets", "NoncurrentBiologicalAssets"]),
    ("inventories",  ["Inventories"]),
    ("lt_debt",      ["LongtermLiabilitiesOtherThanProvisions"]),
    ("st_debt",      ["ShorttermLiabilitiesOtherThanProvisions"]),
    ("employees",    ["AverageNumberOfEmployees"]),
]


def cvrs_on_the_map():
    sites = json.loads((DATA / "map_sites.json").read_text())["sites"]
    return sorted({row[9] for row in sites if row[9]})


def post(url, body, timeout=90):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={**UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def scroll_batch(cvrs):
    """Every publication for one batch of CVR numbers, following the scroll."""
    out = []
    page = post(SEARCH + "?scroll=2m", {"query": {"terms": {"cvrNummer": cvrs}},
                                        "size": PAGE})
    while True:
        hits = page.get("hits", {}).get("hits", [])
        if not hits:
            break
        out.extend(h["_source"] for h in hits)
        sid = page.get("_scroll_id")
        if not sid or len(hits) < PAGE:
            break
        page = post("http://distribution.virk.dk/_search/scroll",
                    {"scroll": "2m", "scroll_id": sid})
    return out


def build_index(cvrs):
    """The publication index for every CVR on the map, newest period first."""
    by_cvr = {}
    batches = [cvrs[i:i + BATCH] for i in range(0, len(cvrs), BATCH)]
    for n, batch in enumerate(batches, 1):
        for _ in range(3):
            try:
                docs = scroll_batch(batch)
                break
            except Exception as exc:
                err = exc
                time.sleep(3)
        else:
            print(f"  batch {n} failed: {err}", file=sys.stderr)
            continue
        for src in docs:
            period = (src.get("regnskab") or {}).get("regnskabsperiode") or {}
            end = period.get("slutDato")
            if not end:
                continue
            # A filing arrives as some mix of PDF, XBRL and -- since 2023 -- a
            # single inline-XBRL .xhtml that is both at once.  Take the machine
            # readable one to parse and the nicest one to link a reader to.
            docs = [d["dokumentUrl"] for d in src.get("dokumenter") or []
                    if d.get("dokumentType") == "AARSRAPPORT" and d.get("dokumentUrl")]
            if not docs:
                continue
            # A filing arrives as some mix of PDF, XBRL and -- since 2023 -- a
            # single inline-XBRL .xhtml that is both at once.  Parse the machine
            # readable one; link a reader to the nicest one, PDF for preference.
            def first(*endings):
                return next((u for u in docs if u.lower().endswith(endings)), None)
            urls = {"xbrl": first(".xml", ".xhtml"),
                    "doc": first(".pdf") or first(".xhtml") or docs[0]}
            urls = {k: v for k, v in urls.items() if v}
            rec = {"start": period.get("startDato"), "end": end,
                   "published": src.get("offentliggoerelsesTidspunkt"), **urls}
            by_cvr.setdefault(src["cvrNummer"], []).append(rec)
        print(f"  index batch {n}/{len(batches)}  {len(by_cvr):,} companies so far",
              flush=True)

    for cvr, reports in by_cvr.items():
        # Newest period first; a re-filed year (omgørelse) keeps the later publication.
        reports.sort(key=lambda r: (r["end"], r.get("published") or ""), reverse=True)
        seen, uniq = set(), []
        for r in reports:
            if r["end"] in seen:
                continue
            seen.add(r["end"])
            uniq.append(r)
        by_cvr[cvr] = uniq
    return by_cvr


def get_xbrl(url, tries=3):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={**UA, "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            return raw.decode("utf-8", "replace")
        except Exception:
            if attempt == tries - 1:
                return None
            time.sleep(2 * (attempt + 1))


CTX_RE = re.compile(r"<(?:\w+:)?context[^>]*\bid=\"([^\"]+)\"(.*?)</(?:\w+:)?context>", re.S)
DATE_RE = re.compile(r"<(?:\w+:)?(startDate|endDate|instant)>([^<]+)<")
ATTR_RE = re.compile(r"([\w:.-]+)\s*=\s*\"([^\"]*)\"")
# Inline XBRL: the fact is an ix:nonFraction wrapper naming the concept.
INLINE_RE = re.compile(r"<(?:\w+:)?nonFraction\b([^>]*)>(.*?)</(?:\w+:)?nonFraction>", re.S)
# Plain XBRL: the element itself is the concept, under whatever prefix the
# filing agent chose -- fsa:, c: and d: all appear in the wild.
PLAIN_RE = re.compile(r"<(\w+):(\w+)\b([^>]*\bcontextRef=[^>]*)>([^<]*)</\1:\2>")

WANTED_TAGS = {alias for _, aliases in FIGURES for alias in aliases}
NOT_A_TAXONOMY = {"xbrli", "xbrldi", "link", "xlink", "ix", "xsi", "html", "xhtml"}


def clean_contexts(xml):
    """Context id -> its period end, for contexts with no dimension breakdown.

    A context carrying a segment or scenario is a slice -- one business area,
    one class of share -- and adding those up double-counts.  The company-level
    figure is the one on the plain context.
    """
    out = {}
    for cid, body in CTX_RE.findall(xml):
        if "segment" in body or "scenario" in body:
            continue
        dates = dict((k, v) for k, v in DATE_RE.findall(body))
        end = dates.get("endDate") or dates.get("instant")
        if end:
            out[cid] = end
    return out


def to_number(text, fmt=None, scale=None, sign=None):
    """One tagged amount, read the way the filing says to read it.

    Inline XBRL states the convention in a format attribute, and Danish filings
    use both of them.  ixt:numcommadecimal writes 542.165 for five hundred and
    forty-two thousand -- the dot groups thousands.  Guessing from the string
    alone gets that wrong by a factor of a thousand, so the format wins wherever
    it is given, and the heuristic is only for plain XBRL, which has no format
    attribute and is required to use a plain decimal point.
    """
    text = (text or "").strip()
    if not text or text in {"-", "\u2014"}:
        return None
    text = re.sub(r"[^\d,.\-]", "", text)
    if not text:
        return None
    fmt = (fmt or "").rsplit(":", 1)[-1].lower()
    comma_decimal = fmt in {"numcommadecimal", "numdotcomma", "numspacecomma", "numcomma"}
    dot_decimal = fmt in {"numdotdecimal", "numcommadot", "numspacedot", "numdot"}
    if comma_decimal:
        text = text.replace(".", "").replace(" ", "").replace(",", ".")
    elif dot_decimal:
        text = text.replace(",", "")
    elif "," in text and "." in text:            # plain XBRL never mixes; be safe
        text = (text.replace(".", "").replace(",", ".") if text.rindex(",") > text.rindex(".")
                else text.replace(",", ""))
    elif "," in text:
        text = text.replace(",", ".")
    try:
        v = float(text)
    except ValueError:
        return None
    if scale:
        try:
            v *= 10 ** int(scale)
        except ValueError:
            pass
    if sign == "-":
        v = -v
    return int(v) if v == int(v) else round(v, 2)


def facts_in(xml):
    """Every wanted fact as (local tag name, context id, value).

    Two encodings share the register.  A plain XBRL instance carries the
    concept as the element name; an inline one hides it in a name attribute on
    an ix:nonFraction that wraps the rendered text.  Attribute order is not
    fixed in either, so attributes get parsed rather than pattern-matched.
    """
    for attrs, inner in INLINE_RE.findall(xml):
        a = dict(ATTR_RE.findall(attrs))
        tag = a.get("name", "").rsplit(":", 1)[-1]
        cid = a.get("contextRef")
        if tag not in WANTED_TAGS or not cid:
            continue
        value = to_number(re.sub(r"<[^>]+>", "", inner), a.get("format"),
                          a.get("scale"), a.get("sign"))
        if value is not None:
            yield tag, cid, value

    for prefix, tag, attrs, text in PLAIN_RE.findall(xml):
        if prefix in NOT_A_TAXONOMY or tag not in WANTED_TAGS:
            continue
        a = dict(ATTR_RE.findall(attrs))
        value = to_number(text, None, a.get("scale"), a.get("sign"))
        if value is not None:
            yield tag, a["contextRef"], value


def parse_xbrl(xml, period_end):
    """The figures for one accounting period, keyed by the short names above."""
    contexts = clean_contexts(xml)
    wanted = {cid for cid, end in contexts.items() if end == period_end}
    if not wanted:                       # the index date and the instance can differ by a day
        near = sorted(set(contexts.values()))
        if not near:
            return {}
        period_end = max(near)
        wanted = {cid for cid, end in contexts.items() if end == period_end}

    facts = {}
    for tag, cid, value in facts_in(xml):
        if cid in wanted:
            facts.setdefault(tag, value)

    out = {}
    for name, aliases in FIGURES:
        for alias in aliases:
            if facts.get(alias) is not None:
                out[name] = facts[alias]
                break
    return out


def parse_one(job):
    cvr, rep = job
    xml = get_xbrl(rep["xbrl"])
    if not xml:
        return cvr, None
    try:
        figures = parse_xbrl(xml, rep["end"])
    except Exception:
        return cvr, None
    if not figures:
        return cvr, None
    return cvr, {"end": rep["end"], "start": rep.get("start"),
                 "doc": rep.get("doc"), **figures}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--years", type=int, default=3, help="accounts per company (newest first)")
    ap.add_argument("--index-only", action="store_true")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--reuse-index", action="store_true", help="skip the index pull")
    args = ap.parse_args()

    cvrs = cvrs_on_the_map()
    idx_path = DATA / "regnskab_index.json"
    if args.reuse_index and idx_path.exists():
        index = {int(k): v for k, v in json.loads(idx_path.read_text()).items()}
        print(f"reusing index: {len(index):,} companies")
    else:
        print(f"index: {len(cvrs):,} CVR numbers in {-(-len(cvrs) // BATCH)} batches")
        index = build_index(cvrs)
        idx_path.write_text(json.dumps({str(k): v for k, v in sorted(index.items())},
                                       ensure_ascii=False, indent=None))
        total = sum(len(v) for v in index.values())
        print(f"{total:,} annual reports for {len(index):,} of {len(cvrs):,} companies "
              f"({100 * len(index) / len(cvrs):.1f}%) -> {idx_path}")
    if args.index_only:
        return

    jobs = [(cvr, rep) for cvr, reps in sorted(index.items())
            for rep in reps[:args.years] if rep.get("xbrl")]
    print(f"parsing {len(jobs):,} XBRL instances with {args.workers} workers")

    out, failed = {}, 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for n, (cvr, rec) in enumerate(pool.map(parse_one, jobs), 1):
            if rec is None:
                failed += 1
            else:
                out.setdefault(cvr, []).append(rec)
            if n % 250 == 0 or n == len(jobs):
                print(f"  {n:,}/{len(jobs):,}  {len(out):,} companies  {failed:,} unreadable",
                      flush=True)

    for cvr in out:
        out[cvr].sort(key=lambda r: r["end"], reverse=True)
    path = DATA / "cvr_financials.json"
    path.write_text(json.dumps({str(k): v for k, v in sorted(out.items())},
                               ensure_ascii=False))
    print(f"{len(out):,} companies with readable accounts -> {path}")


if __name__ == "__main__":
    sys.exit(main())
