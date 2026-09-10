#!/usr/bin/env python3
"""Company master data for every CVR number that keeps livestock.

The CHR register names the business that runs each site but says nothing about
it -- not the legal form, not the industry code, not who runs it.  All of that
is in CVR, and getting at it in bulk is the awkward part.

Three routes were tried against this machine on 9 September 2026:

* `distribution.virk.dk/cvr-permanent` is Erhvervsstyrelsen's documented bulk
  API and the right answer, but it returns 401 without credentials, and those
  are issued by e-mail on application.  Nothing to do about that in an afternoon.
* `cvrapi.dk` is a free mirror.  It answers unauthenticated and its shape is
  pleasant, but the free quota is around fifty lookups a day -- a bulk pull of
  16,449 companies would take a year, so it is the fallback, not the source.
* `datacvr.virk.dk/gateway` is what Erhvervsstyrelsen's own public site calls to
  draw a company page.  It answers unauthenticated with a Referer header, it is
  first-party data rather than a mirror, and it carries a good deal more:
  purpose, capital, employees quarter by quarter, production units, the board
  and the registered owners.  That is what this uses.

That last one is the site's own endpoint, not a published contract, so it is
used the way a browser would use it -- one company at a time, slowly, cached to
disk, and never re-fetched.

    python3 scripts/fetch_cvr.py               # all CVRs on the map, resumable
    python3 scripts/fetch_cvr.py --limit 200   # a taste
    python3 scripts/fetch_cvr.py --source cvrapi   # the mirror instead

16,449 companies takes about three hours.  Answers land in data/cvr_raw.jsonl a
line at a time, so the file is usable while the run is going and stopping it
costs nothing -- starting again skips whatever is already there.

Personal data: CVR publishes the names and roles of the people behind a company,
and this keeps them, because for a sole trader or an I/S the partners *are* the
farm.  It does not keep their private addresses, which the endpoint also hands
out.  Where the register sets `reklamebeskyttet`, the contact details are
dropped -- that flag is the register's own instruction not to use them for
approaches, and honouring it costs nothing.
"""
import argparse
import gzip
import http.cookiejar
import json
import pathlib
import queue
import random
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "cvr_raw.jsonl"
BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
GATEWAY = "https://datacvr.virk.dk/gateway/virksomhed/hentVirksomhed"
CVRAPI = "https://cvrapi.dk/api"


def cvrs_on_the_map():
    """Every distinct CVR number carried by a CHR site, in numeric order."""
    sites = json.loads((DATA / "map_sites.json").read_text())["sites"]
    return sorted({row[9] for row in sites if row[9]})


def cvrs_from(path):
    """An explicit list of CVR numbers, for the farms CHR does not name.

    Livestock is only part of Danish farming: 26,922 businesses declare field
    parcels for area support and fewer than half of them keep animals. The rest
    are croppers, and they are just as much farms -- they simply do not appear
    in a livestock register. Their CVR numbers come from Marker instead.
    """
    nums = json.loads(pathlib.Path(path).read_text())
    return sorted({int(n) for n in nums})


def already_have():
    """CVR numbers already in the cache."""
    seen = set()
    if not RAW.exists():
        return seen
    with RAW.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                seen.add(json.loads(line)["cvr"])
            except (json.JSONDecodeError, KeyError):
                continue
    return seen


class Session:
    """A browser-shaped session against datacvr.virk.dk.

    The gateway sits behind a filter that lets a few bare requests through and
    then answers 403 to anything that has not picked up a session cookie.  One
    hit on a company page issues the cookie; after that the endpoint answers
    steadily.  So this opens a session, keeps it, and opens a new one when the
    filter starts refusing again -- which is what a browser would do anyway, and
    it is the difference between 25 lookups in 25 and 25 in 8.
    """

    def __init__(self):
        self.opener = None
        self.opened = 0.0

    def open(self, cvr=1):
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar))
        req = urllib.request.Request(
            f"https://datacvr.virk.dk/enhed/virksomhed/{cvr}",
            headers={"User-Agent": BROWSER_UA, "Accept": "text/html",
                     "Accept-Language": "da,en;q=0.9", "Accept-Encoding": "gzip"})
        try:
            self.opener.open(req, timeout=25).read(2048)
        except Exception:
            pass
        self.opened = time.time()

    def json(self, url, headers, timeout=30):
        if self.opener is None:
            self.open()
        req = urllib.request.Request(url, headers={**headers, "Accept-Encoding": "gzip"})
        with self.opener.open(req, timeout=timeout) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return json.loads(raw.decode("utf-8"))


def fetch_json(url, headers, timeout=30):
    """A one-off request with no session, for the endpoints that do not need one."""
    req = urllib.request.Request(url, headers={**headers, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8"))


# ------------------------------------------------------------------ datacvr

def gateway_fetch(cvr, tries=4, session=None):
    """One company page.  403 and 429 mean the session went stale, not that the
    company is missing, so those buy a new session and another go."""
    own = session is None
    session = session or Session()
    url = f"{GATEWAY}?" + urllib.parse.urlencode({"cvrnummer": cvr, "locale": "da"})
    headers = {"User-Agent": BROWSER_UA,
               "Accept": "application/json, text/plain, */*",
               "Accept-Language": "da,en;q=0.9",
               "Referer": f"https://datacvr.virk.dk/enhed/virksomhed/{cvr}",
               "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors",
               "Sec-Fetch-Dest": "empty"}
    for attempt in range(tries):
        try:
            return 200, session.json(url, headers)
        except urllib.error.HTTPError as e:
            if e.code in (404, 400):
                return e.code, None
            if e.code in (403, 429, 503) and attempt < tries - 1:
                time.sleep(2 * (attempt + 1) + random.random())
                session.open(cvr)
                continue
            if attempt == tries - 1:
                return e.code, None
            time.sleep(3 * (attempt + 1))
        except Exception:
            if attempt == tries - 1:
                return 0, None
            time.sleep(3 * (attempt + 1))
    return 0, None


def latest(series):
    """The most recent employment figure, monthly for preference."""
    if not series:
        return None
    def key(row):
        return (row.get("aar") or "", row.get("maaned") or 0, row.get("kvartal") or "")
    row = max(series, key=key)
    return {"period": row.get("periode"), "staff": row.get("antalAnsatte"),
            "fte": row.get("antalAarsvaerk")}


ROLE_LABELS = {
    "FULDT_ANSVARLIG_DELTAGERE": "fully liable partner",
    "DIREKTION": "management",
    "BESTYRELSE": "board",
    "STIFTERE": "founder",
    "REVISION": "auditor",
    "LEDELSESORGAN": "governing body",
}


def slim_gateway(d):
    """The parts of a company page worth keeping, in a flat shape."""
    stam = d.get("stamdata") or {}
    ext = d.get("udvidedeOplysninger") or {}
    own = d.get("ejerforhold") or {}
    protected = bool(stam.get("reklamebeskyttet"))

    people = []
    for group in (d.get("personkreds") or {}).get("personkredser") or []:
        role = (group.get("rolle") or {}).get("name") or ""
        label = ROLE_LABELS.get(role, role.replace("_", " ").lower())
        for person in group.get("personRoller") or []:
            name = person.get("senesteNavn")
            if not name:
                continue
            # The endpoint hands out private home addresses too; those stay here.
            people.append({"name": name, "role": label,
                           "type": person.get("enhedstype")})

    def owners(rows):
        out = []
        for o in rows or []:
            out.append({k: v for k, v in
                        {"name": o.get("senesteNavn") or o.get("navn"),
                         "share": o.get("ejerandelProcent") or o.get("vaerdi"),
                         "votes": o.get("stemmerettighedProcent")}.items() if v})
        return out

    units = []
    for pu in (d.get("produktionsenheder") or {}).get("aktiveProduktionsenheder") or []:
        s = pu.get("stamdata") or {}
        unit = {"pno": s.get("pnummer"), "name": s.get("navn"),
                "address": s.get("adresse"), "city": s.get("postnummerOgBy"),
                "industry": (s.get("hovedbranche") or {}).get("titel"),
                "industrycode": (s.get("hovedbranche") or {}).get("branchekode"),
                "start": s.get("startdato")}
        if not s.get("reklamebeskyttet"):
            unit["phone"] = s.get("telefon")
            unit["email"] = s.get("email")
        units.append({k: v for k, v in unit.items() if v})

    out = {
        "name": stam.get("navn"),
        "form": stam.get("virksomhedsform"),
        "formcode": stam.get("virksomhedsformKode"),
        "status": stam.get("status"),
        "address": stam.get("adresse"),
        "city": stam.get("postnummerOgBy"),
        "kommune": ext.get("kommune"),
        "start": stam.get("startdato"),
        "end": stam.get("ophoersdato"),
        "industry": (ext.get("hovedbranche") or {}).get("titel"),
        "industrycode": (ext.get("hovedbranche") or {}).get("branchekode"),
        "purpose": ext.get("formaal"),
        "capital": ext.get("registreretKapital"),
        "protected": protected,
        "employees": latest((d.get("antalAnsatte") or {}).get("maanedsbeskaeftigelse")),
        "employees_q": latest((d.get("antalAnsatte") or {}).get("kvartalsbeskaeftigelse")),
        "people": people,
        "legal_owners": owners(own.get("aktiveLegaleEjere")),
        "beneficial_owners": owners(own.get("aktiveReelleEjere")),
        "units": units,
        "accounts": len(d.get("sammenhaengendeRegnskaber") or []),
    }
    if not protected:
        out["phone"] = ext.get("telefon")
        out["email"] = ext.get("email")
    return {k: v for k, v in out.items() if v not in (None, "", [], {})}


# ------------------------------------------------------------------ cvrapi

def cvrapi_fetch(cvr, tries=3):
    url = f"{CVRAPI}?" + urllib.parse.urlencode({"search": cvr, "country": "dk"})
    headers = {"User-Agent": "danish-livestock - open register map"}
    for attempt in range(tries):
        try:
            d = fetch_json(url, headers, timeout=25)
            if isinstance(d, dict) and d.get("error"):
                return 429, d          # QUOTA_EXCEEDED arrives as a 200 with a body
            return 200, d
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return 404, None
            if attempt == tries - 1:
                return e.code, None
            time.sleep(3 * (attempt + 1))
        except Exception:
            if attempt == tries - 1:
                return 0, None
            time.sleep(3 * (attempt + 1))
    return 0, None


def slim_cvrapi(d):
    keep = ("name", "address", "zipcode", "city", "phone", "email", "startdate",
            "enddate", "employees", "industrycode", "industrydesc", "companydesc",
            "creditbankrupt")
    out = {k: d.get(k) for k in keep if d.get(k) is not None}
    out["units"] = [{"pno": u.get("pno"), "name": u.get("name"),
                     "address": u.get("address"), "city": u.get("city")}
                    for u in (d.get("productionunits") or [])]
    return out


SOURCES = {
    "datacvr": (gateway_fetch, slim_gateway),
    "cvrapi": (cvrapi_fetch, slim_cvrapi),
}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, help="stop after this many new lookups")
    ap.add_argument("--source", choices=sorted(SOURCES), default="datacvr")
    ap.add_argument("--workers", type=int, default=1,
                    help="parallel lookups; keep it small, this is someone's website")
    ap.add_argument("--delay", type=float, default=0.8,
                    help="seconds each worker pauses between lookups")
    ap.add_argument("--refresh", action="store_true", help="re-fetch CVRs already cached")
    ap.add_argument("--targets", help="JSON file holding a list of CVR numbers to "
                                      "fetch, instead of the ones on the map")
    args = ap.parse_args()

    fetch, slim = SOURCES[args.source]
    targets = cvrs_from(args.targets) if args.targets else cvrs_on_the_map()
    seen = set() if args.refresh else already_have()
    todo = [c for c in targets if c not in seen]
    if args.limit:
        todo = todo[:args.limit]

    rate = args.workers / max(args.delay + 0.5, 0.1)
    print(f"{len(targets):,} CVR numbers wanted, {len(seen):,} cached, "
          f"{len(todo):,} to fetch from {args.source} "
          f"(~{len(todo) / rate / 3600:.1f} h at ~{rate:.1f}/s)")
    if not todo:
        return

    work = queue.Queue()
    for cvr in todo:
        work.put(cvr)
    results = queue.Queue()
    counts = {"hit": 0, "miss": 0, "err": 0}
    stop = threading.Event()

    def worker():
        session = Session() if args.source == "datacvr" else None
        if session:
            session.open()
        misses_in_a_row = 0
        while not stop.is_set():
            try:
                cvr = work.get_nowait()
            except queue.Empty:
                return
            status, payload = (fetch(cvr, session=session) if session else fetch(cvr))
            if status == 200 and payload:
                try:
                    rec = {"cvr": cvr, "ok": True, "d": slim(payload)}
                except Exception as exc:
                    rec = {"cvr": cvr, "ok": False, "why": f"parse: {exc}"}
            elif status in (404, 400):
                rec = {"cvr": cvr, "ok": False, "why": "not found"}
            else:
                rec = {"cvr": cvr, "ok": False, "why": f"http {status}"}
            # A run that has stopped being answered should stop asking, rather
            # than fill the cache with failures for every company left.
            misses_in_a_row = 0 if rec["ok"] else misses_in_a_row + 1
            if misses_in_a_row >= 25:
                print(f"  25 refusals in a row (last: {rec['why']}) — stopping",
                      file=sys.stderr)
                stop.set()
                return
            results.put(rec)
            time.sleep(args.delay + random.uniform(0, 0.4))

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(args.workers)]
    for t in threads:
        t.start()

    written = 0
    with RAW.open("a", encoding="utf-8") as fh:
        while any(t.is_alive() for t in threads) or not results.empty():
            try:
                rec = results.get(timeout=0.5)
            except queue.Empty:
                continue
            if rec["ok"]:
                counts["hit"] += 1
            elif rec["why"] == "not found":
                counts["miss"] += 1
            else:
                counts["err"] += 1
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            written += 1
            if written % 200 == 0:
                print(f"  {written:,}/{len(todo):,}  {counts['hit']:,} found  "
                      f"{counts['miss']:,} missing  {counts['err']:,} failed", flush=True)

    print(f"done: {counts['hit']:,} found, {counts['miss']:,} missing, "
          f"{counts['err']:,} failed -> {RAW}")


if __name__ == "__main__":
    sys.exit(main())
