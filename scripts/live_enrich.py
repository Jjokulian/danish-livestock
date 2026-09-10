#!/usr/bin/env python3
"""What the open registers cannot say: live web lookup for a clicked farm.

The map knows a farm's CHR number, its CVR number and its parcels because those
are in registers.  It does not know that the company was in the paper last month,
that it has a website, or that it sits under a co-operative.  That is on the web,
and the web has no bulk export -- so this fetches it at the moment of the click.

    python3 scripts/live_enrich.py --cvr 29751455
    python3 scripts/live_enrich.py --cvr 29751455 --json

serve.py mounts the same code at /api/enrich as a server-sent-event stream, so
the panel fills in progressively rather than waiting for the slowest fetch.  Work
happens in this order, cheapest first, and each result is emitted the moment it
lands:

    registers   the CVR record and the filed annual accounts   ~0.5 s, exact
    links       register pages that need no search to find     instant
    search      a web search for the company and its place     ~1 s
    pages       the top results, fetched and reduced to text   ~2 s

Everything is cached on disk under data/cache/live/ so a second click on the
same farm is instant, and so that a room full of people clicking does not turn
into a room full of requests to someone else's server.
"""
import argparse
import gzip
import hashlib
import html
import io
import json
import os
import pathlib
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import build_cvr_data
import fetch_cvr

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / "cache" / "live"
TTL = 7 * 24 * 3600          # a company's web presence does not change hourly
PAGE_BYTES = 400_000         # stop reading a page after this much
SEARCH_BUDGET = 9.0          # seconds the whole search phase may take
TIMEOUT = 12

BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0")
BOT_UA = "danish-livestock/1.0 (open register map)"

# Domains worth telling apart in the panel, because a hit on Ritzau's press
# release archive is a different kind of fact from a hit on the company's own
# front page.
KINDS = [
    # A search for a Danish company name comes back dominated by sites that are
    # themselves CVR mirrors -- they restate the register and nothing more, so
    # they are marked as register hits and drop to the back of the fetch queue.
    # Otherwise they eat the whole budget and the panel learns nothing it did
    # not already ship with.
    ("register", ("datacvr.virk.dk", "cvrapi.dk", "virk.dk", "regnskaber.virk.dk",
                  "cvr.dk", "proff.dk", "nnmarkedsdata.dk", "biq.dk",
                  "chr.fvst.dk", "landbrugsinfo.dk", "husdyrgodkendelse.dk",
                  "selskabsinfo.dk", "ownr.dk", "profiler.dk", "firmania.dk",
                  "cylex.dk", "virksomhedsdata", "cvrapi", "bizzdata",
                  "companyhouse", "nordicnet", "soliditet", "bisnode",
                  "regnskabstal", "cvr-nummer", "findvirksomhed")),
    ("news", ("dr.dk", "tv2", "landbrugsavisen.dk", "maskinbladet.dk", "agriwatch.dk",
              "effektivtlandbrug.dk", "food-supply.dk", "finans.dk", "borsen.dk",
              "jyllands-posten.dk", "politiken.dk", "berlingske.dk", "avisen",
              "stiften.dk", "amtsavisen.dk", "jv.dk", "fyens.dk", "nordjyske.dk")),
    ("official", ("mst.dk", "miljoestyrelsen.dk", "fvst.dk", "foedevarestyrelsen.dk",
                  "lbst.dk", "naturstyrelsen.dk", ".kommune.dk", "retsinformation.dk",
                  "domstol.dk", "statstidende.dk", "mfvm.dk")),
    ("reference", ("wikipedia.org", "wikidata.org", "wikimedia.org",
                   "denstoredanske.lex.dk", "lex.dk")),
    ("social", ("facebook.com", "linkedin.com", "instagram.com", "youtube.com",
                "x.com", "twitter.com")),
    ("directory", ("krak.dk", "degulesider.dk", "findsmiley.dk", "trustpilot",
                   "yelp", "google.com/maps")),
]


# What is worth spending a fetch on, best first. Registers and directories
# restate what the panel already shipped; a reference article or a news story
# is the only thing here that can say something new.
PAGE_RANK = {"news": 0, "reference": 1, "official": 2, "web": 3,
             "social": 4, "directory": 5, "register": 6}


def kind_of(url):
    host = urllib.parse.urlparse(url).netloc.lower()
    for name, needles in KINDS:
        if any(n in host for n in needles):
            return name
    return "web"


# ---------------------------------------------------------------- politeness

# The keyless engines block a client that behaves like a benchmark, and that is
# exactly what a test loop looks like: this project got itself blocked by
# DuckDuckGo twice during development, and unblocked both times after a rest.
# Real use is one query per human click, which is nothing like that -- so the
# server is made structurally incapable of the fast pattern. One outbound search
# per engine per interval, process-wide, whoever is asking.
_LAST_CALL = {}
_RATE_LOCK = threading.Lock()
MIN_INTERVAL = {"duckduckgo": 6.0, "marginalia": 3.0, "yacy": 3.0,
                "wikipedia": 1.0}


# An engine that just refused will refuse the next query too, and waiting out
# the rate limiter to hear it again is the worst thing this can do to a click.
# A refusal is remembered briefly and the engine is skipped until it expires.
_COOLDOWN = {}
COOLDOWN_AFTER_FAILURE = 300.0


def is_cold(engine):
    with _RATE_LOCK:
        return _COOLDOWN.get(engine, 0.0) > time.monotonic()


def mark_cold(engine):
    with _RATE_LOCK:
        _COOLDOWN[engine] = time.monotonic() + COOLDOWN_AFTER_FAILURE


def throttle(engine):
    """Block until this engine may be called again. Returns the wait it imposed."""
    floor = MIN_INTERVAL.get(engine, 1.0)
    with _RATE_LOCK:
        now = time.monotonic()
        earliest = _LAST_CALL.get(engine, 0.0) + floor
        wait = max(0.0, earliest - now)
        _LAST_CALL[engine] = now + wait
    if wait:
        time.sleep(wait)
    return wait


# ---------------------------------------------------------------- cache

def cache_get(key, ttl=TTL):
    path = CACHE / (hashlib.sha1(key.encode()).hexdigest() + ".json")
    if not path.exists() or time.time() - path.stat().st_mtime > ttl:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def cache_put(key, value):
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / (hashlib.sha1(key.encode()).hexdigest() + ".json")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return value


# ---------------------------------------------------------------- http

def get(url, ua=BROWSER_UA, data=None, timeout=TIMEOUT, limit=PAGE_BYTES):
    """One request, decompressed, truncated, and decoded with the stated charset.

    GeoServer is not the only Danish service that lies about its encoding, so the
    charset from the header is trusted first and UTF-8 with replacement is the
    floor -- a mangled character is better than an exception halfway up a stream.
    """
    headers = {"User-Agent": ua, "Accept-Encoding": "gzip",
               "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
               "Accept-Language": "da,en;q=0.8"}
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(limit)
        if r.headers.get("Content-Encoding") == "gzip":
            try:
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read(limit)
            except Exception:
                pass
        charset = r.headers.get_content_charset() or "utf-8"
        ctype = r.headers.get("Content-Type", "")
        final = r.geturl()
    try:
        text = raw.decode(charset, "replace")
    except LookupError:
        text = raw.decode("utf-8", "replace")
    return text, ctype, final


# ---------------------------------------------------------------- registers

def cvr_record(cvr):
    """The company as CVR has it.

    The bulk cache from fetch_cvr.py answers instantly and covers every company
    on the map.  A click on something the bulk run has not reached yet -- or has
    not run at all -- falls through to the same first-party endpoint live, and to
    the cvrapi.dk mirror only if that fails, since its free quota is tiny.
    """
    cached = local_cvr(cvr)
    if cached:
        return {"source": "cvr (bulk)", **cached}

    key = f"cvr:{cvr}"
    hit = cache_get(key)
    if hit is not None:
        return hit

    status, payload = fetch_cvr.gateway_fetch(cvr, tries=2)
    if status == 200 and payload:
        try:
            rec = fetch_cvr.slim_gateway(payload)
            # The same tidying the build step does, so a live lookup and a bulk
            # one read identically in the panel.
            for person in rec.get("people") or []:
                person["role"] = build_cvr_data.role_label(person.get("role"))
            for field in ("address", "city", "name"):
                if rec.get(field):
                    rec[field] = " ".join(str(rec[field]).split())
            return cache_put(key, {"source": "datacvr.virk.dk", **rec})
        except Exception:
            pass

    status, payload = fetch_cvr.cvrapi_fetch(cvr, tries=1)
    if status == 200 and payload:
        return cache_put(key, {"source": "cvrapi.dk", **fetch_cvr.slim_cvrapi(payload)})
    return {"error": "no CVR record could be fetched"}


_LOCAL_CVR = None


def local_cvr(cvr):
    """The bulk pull from fetch_cvr.py, loaded once and kept in memory."""
    global _LOCAL_CVR
    if _LOCAL_CVR is None:
        _LOCAL_CVR = {}
        raw = DATA / "cvr_raw.jsonl"
        if raw.exists():
            for line in raw.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("ok"):
                    _LOCAL_CVR[rec["cvr"]] = rec["d"]
    return _LOCAL_CVR.get(int(cvr))


def accounts(cvr):
    """Annual reports filed with Erhvervsstyrelsen.  Open index, no credential."""
    key = f"regnskab:{cvr}"
    hit = cache_get(key, ttl=30 * 24 * 3600)
    if hit is not None:
        return hit
    body = {"query": {"term": {"cvrNummer": int(cvr)}},
            "size": 8,
            "sort": [{"offentliggoerelsesTidspunkt": {"order": "desc"}}]}
    req = urllib.request.Request(
        "http://distribution.virk.dk/offentliggoerelser/_search",
        data=json.dumps(body).encode(),
        headers={"User-Agent": BOT_UA, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            res = json.loads(r.read().decode("utf-8"))
    except Exception as exc:
        return {"error": str(exc)}
    out = []
    for h in res.get("hits", {}).get("hits", []):
        src = h["_source"]
        period = (src.get("regnskab") or {}).get("regnskabsperiode") or {}
        docs = [d["dokumentUrl"] for d in src.get("dokumenter") or []
                if d.get("dokumentType") == "AARSRAPPORT"]
        pdf = next((u for u in docs if u.endswith(".pdf")), None)
        out.append({"end": period.get("slutDato"), "start": period.get("startDato"),
                    "published": src.get("offentliggoerelsesTidspunkt"),
                    "type": src.get("offentliggoerelsestype"),
                    "doc": pdf or (docs[0] if docs else None)})
    out.sort(key=lambda r: r.get("end") or "", reverse=True)
    return cache_put(key, {"reports": out})


def known_links(cvr, chr_no, name, bfe):
    """Register pages that are addressable, so no search is needed to find them.

    These are constructed, not discovered: every one is a documented URL pattern
    on a public register.  They cost nothing and they are the links a person
    actually wants after clicking a farm.
    """
    out = []
    if cvr:
        q = urllib.parse.quote(str(cvr))
        out += [
            {"kind": "register", "title": "CVR — the company register entry",
             "url": f"https://datacvr.virk.dk/enhed/virksomhed/{q}"},
            {"kind": "register", "title": "Annual reports filed",
             "url": f"https://datacvr.virk.dk/enhed/virksomhed/{q}?fane=regnskaber"},
            {"kind": "register", "title": "Proff — company profile and figures",
             "url": f"https://www.proff.dk/segmentering?q={q}"},
        ]
    if chr_no:
        out.append({"kind": "register", "title": f"CHR {chr_no} — the herd register",
                    "url": "https://chr.fvst.dk/chri/faces/frontpage"})
    if bfe:
        out.append({"kind": "official", "title": f"Property {bfe} on the cadastre",
                    "url": f"https://www.ois.dk/ejendom/{bfe}"})
    if name:
        n = urllib.parse.quote(name)
        out.append({"kind": "official", "title": "Environmental permits (husdyrgodkendelse)",
                    "url": f"https://husdyrgodkendelse.dk/Sog?query={n}"})
    return out


# ---------------------------------------------------------------- search

RESULT_RE = re.compile(
    r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
SNIPPET_RE = re.compile(
    r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', re.S)


def strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def unwrap_ddg(href):
    """DuckDuckGo hands back its own redirect; the real URL is a query parameter."""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urllib.parse.urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        qs = urllib.parse.parse_qs(parsed.query)
        if qs.get("uddg"):
            return qs["uddg"][0]
    return href


# --- search providers ---------------------------------------------------
#
# There is no dependable keyless web search, and pretending otherwise would make
# this silently return nothing.  Tested from this machine on 9 September 2026:
#
#   DuckDuckGo's HTML endpoint  answers a few queries, then serves an anomaly
#                               page (HTTP 202, no results) to the IP.  Kept as
#                               a best-effort last resort, never relied on.
#   Mojeek's HTML endpoint      403 after a handful of queries.
#   public SearxNG instances    captcha, 429 or 403.
#   Google News RSS             answers, but its own copyright notice limits the
#                               feed to a personal reader and prohibits any other
#                               use, so it is not wired up.
#   Marginalia's public API     answers, no key.  An independent index of the
#                               non-commercial web -- excellent for an obscure
#                               page, thin on Danish local news.
#   YaCy (peer-to-peer)         answers, no key, genuinely decentralised.  Its
#                               peers have crawled almost no Danish local web:
#                               "landbrug" returns the same page four times and
#                               a named farm returns nothing.  Wired up anyway,
#                               last in the chain, because it costs nothing and
#                               a peer you run yourself can be pointed at
#                               whatever you want it to have crawled.
#   Presearch                   no public search API; every documented path 404s.
#   ipfs-search, Masa           did not resolve from here at all.
#
# So the providers that actually hold up want a key, and each is read from the
# environment.  With none set the panel says so rather than showing an empty box.

def _brave(query, limit):
    key = os.environ.get("BRAVE_SEARCH_API_KEY")
    if not key:
        return None
    url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode(
        {"q": query, "count": limit, "country": "dk", "search_lang": "da"})
    req = urllib.request.Request(url, headers={
        "Accept": "application/json", "X-Subscription-Token": key,
        "User-Agent": BOT_UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = json.loads(r.read().decode("utf-8"))
    return [{"url": w["url"], "title": strip_tags(w.get("title")),
             "snippet": strip_tags(w.get("description")), "engine": "brave"}
            for w in (data.get("web") or {}).get("results", [])[:limit]]


def _google_cse(query, limit):
    key, cx = os.environ.get("GOOGLE_CSE_KEY"), os.environ.get("GOOGLE_CSE_CX")
    if not (key and cx):
        return None
    url = "https://www.googleapis.com/customsearch/v1?" + urllib.parse.urlencode(
        {"key": key, "cx": cx, "q": query, "num": min(limit, 10), "gl": "dk", "lr": "lang_da"})
    text, _, _ = get(url, ua=BOT_UA)
    data = json.loads(text)
    return [{"url": i["link"], "title": strip_tags(i.get("title")),
             "snippet": strip_tags(i.get("snippet")), "engine": "google"}
            for i in data.get("items", [])[:limit]]


def _mojeek(query, limit):
    key = os.environ.get("MOJEEK_API_KEY")
    if not key:
        return None
    url = "https://www.mojeek.com/search?" + urllib.parse.urlencode(
        {"q": query, "api_key": key, "fmt": "json", "t": limit})
    text, _, _ = get(url, ua=BOT_UA)
    data = json.loads(text)
    return [{"url": r["url"], "title": strip_tags(r.get("title")),
             "snippet": strip_tags(r.get("desc")), "engine": "mojeek"}
            for r in (data.get("response") or {}).get("results", [])[:limit]]


def _marginalia(query, limit):
    """Keyless, and an index of the small web -- worth asking, rarely the answer."""
    url = "https://api.marginalia.nu/public/search/" + urllib.parse.quote(query)
    text, _, _ = get(url, ua=BOT_UA)
    data = json.loads(text)
    return [{"url": r["url"], "title": strip_tags(r.get("title")),
             "snippet": strip_tags(r.get("description")), "engine": "marginalia"}
            for r in data.get("results", [])[:limit]]


def _yacy(query, limit):
    """YaCy: peer-to-peer search, no key, no central index.

    The decentralised option that actually answers.  Each peer crawls what its
    operator points it at and the network queries across peers, so there is no
    company to ask for an API key and nobody to block this server.  The cost is
    coverage: the network has crawled very little Danish local web, so a named
    farm usually returns nothing.  It sits below the keyed engines for that
    reason, not for any other.

    Off unless YACY_PEER is set, because measured against the public peer it
    took 9.8 s to return one irrelevant result and 13.2 s to return none, and
    that is not a wait to put in front of every click by default.  Set it to
    "default" for the public peer, or to your own peer's URL -- pointing a peer
    you run at the Danish agricultural web is the only thing that makes the
    coverage anything other than what it is.
    """
    peer = os.environ.get("YACY_PEER")
    if not peer:
        return None
    if peer == "default":
        peer = "https://yacy.searchlab.eu"
    url = peer.rstrip("/") + "/yacysearch.json?" + urllib.parse.urlencode(
        {"query": query, "maximumRecords": limit, "resource": "global"})
    # A federated query waits on remote peers, so it is slow by construction --
    # seconds, not the fraction of a second the registers take.  It is last in
    # the chain and gets a longer rope than the others, but not an unlimited one.
    text, _, _ = get(url, ua=BOT_UA, timeout=25)
    data = json.loads(text)
    channels = data.get("channels") or [{}]
    out, seen = [], set()
    for item in (channels[0].get("items") or [])[:limit * 2]:
        link = item.get("link")
        # The network returns the same page from several peers; collapse those.
        if not link or link in seen:
            continue
        seen.add(link)
        out.append({"url": link, "title": strip_tags(item.get("title")),
                    "snippet": strip_tags(item.get("description")),
                    "engine": "yacy"})
        if len(out) >= limit:
            break
    return out


def _wikipedia(query, limit):
    """Danish Wikipedia, then English. Keyless, never blocks, always structured.

    Useless for a family farm and exactly right for the co-operatives at the top
    of the industry -- Arla, Danish Crown, DLG -- which is where a click is most
    likely to want context the registers do not carry.
    """
    out = []
    for lang in ("da", "en"):
        url = f"https://{lang}.wikipedia.org/w/api.php?" + urllib.parse.urlencode({
            "action": "query", "list": "search", "srsearch": query,
            "srlimit": limit, "format": "json", "srprop": "snippet"})
        try:
            text, _, _ = get(url, ua=BOT_UA, timeout=15)
            data = json.loads(text)
        except Exception:
            continue
        for r in (data.get("query") or {}).get("search", []):
            title = r["title"]
            out.append({
                "url": f"https://{lang}.wikipedia.org/wiki/" +
                       urllib.parse.quote(title.replace(" ", "_")),
                "title": title,
                "snippet": strip_tags(r.get("snippet")),
                "engine": f"wikipedia:{lang}"})
        if out:
            break
    return out[:limit]


def _duckduckgo(query, limit):
    """Best effort.  Returns nothing rather than raising once the IP is flagged."""
    page, _, _ = get("https://html.duckduckgo.com/html/?" +
                     urllib.parse.urlencode({"q": query, "kl": "dk-da"}))
    hrefs = RESULT_RE.findall(page)
    snips = SNIPPET_RE.findall(page)
    out = []
    for i, (href, title) in enumerate(hrefs[:limit]):
        out.append({"url": unwrap_ddg(href), "title": strip_tags(title),
                    "snippet": strip_tags(snips[i]) if i < len(snips) else "",
                    "engine": "duckduckgo"})
    return out


PROVIDERS = [("brave", _brave), ("google", _google_cse), ("mojeek", _mojeek),
             ("duckduckgo", _duckduckgo), ("marginalia", _marginalia),
             ("yacy", _yacy)]
# Wikipedia is not an alternative to the others -- it answers a different
# question -- so it runs alongside whichever of them replied, not instead.
ALWAYS = [("wikipedia", _wikipedia)]


def providers_available():
    """Which providers are actually configured, for the panel to say so honestly.

    Computed from the environment rather than hardcoded, so the message a viewer
    sees matches what the chain will really try.
    """
    keys = {"brave": ["BRAVE_SEARCH_API_KEY"],
            "google": ["GOOGLE_CSE_KEY", "GOOGLE_CSE_CX"],
            "mojeek": ["MOJEEK_API_KEY"]}
    keyed = [name for name, needed in keys.items()
             if all(os.environ.get(k) for k in needed)]
    keyless = ["duckduckgo", "marginalia"]
    if os.environ.get("YACY_PEER"):
        keyless.append("yacy")
    # Wikipedia runs alongside the chain rather than in it, and never blocks,
    # so it is always on -- listed separately so the panel does not imply the
    # web search is working when only Wikipedia answered.
    return {"keyed": keyed, "keyless": keyless, "always": ["wikipedia"]}


# An empty answer means different things depending on who gave it. An API that
# replies with zero hits has genuinely searched and found nothing -- most farm
# names are not in Wikipedia, and that is not a fault. A scraped HTML endpoint
# returning nothing is almost always the block page, since a real search page
# for a company name is never empty. Only the second is worth cooling off.
SCRAPERS = {"duckduckgo"}


def _run(name, fn, query, limit):
    """One provider, never raising. Returns (results, status, healthy)."""
    try:
        got = fn(query, limit)
    except Exception as exc:
        return [], type(exc).__name__, False
    if got is None:
        return [], "no key configured", True
    if not got:
        return ([], "blocked", False) if name in SCRAPERS else ([], "no results", True)
    return got, f"{len(got)} results", True


def search(query, limit=8):
    """A web search from the first provider that answers, plus Wikipedia.

    Providers are tried in order of how much they can be trusted to answer at
    all, and the result says which one replied and what the others did -- an
    empty set from a blocked scraper and an empty set from a working index mean
    completely different things, and the panel says which.
    """
    key = f"search3:{query}"
    hit = cache_get(key)
    if hit is not None:
        return hit

    tried, results, used = [], [], None
    for name, fn in PROVIDERS:
        if is_cold(name):
            tried.append({"engine": name, "status": "cooling off after refusal"})
            continue
        # A configured key costs nothing to check and should not wait on a
        # rate limiter meant for the scrapers.
        if name in MIN_INTERVAL:
            throttle(name)
        got, status, healthy = _run(name, fn, query, limit)
        tried.append({"engine": name, "status": status})
        if got:
            results, used = got, name
            break
        if not healthy:
            mark_cold(name)

    for name, fn in ALWAYS:
        if is_cold(name):
            tried.append({"engine": name, "status": "cooling off after refusal"})
            continue
        throttle(name)
        got, status, healthy = _run(name, fn, query, max(2, limit // 2))
        tried.append({"engine": name, "status": status})
        results = results + got
        if not healthy:
            mark_cold(name)

    return cache_put(key, {"query": query, "results": clean(results),
                           "engine": used, "tried": tried})


def clean(results):
    """Turn whatever the engines returned into one filtered, structured list.

    Three things happen here. The same page arrives from several engines and
    under several URLs, so it is collapsed to one. Sites that are themselves CVR
    mirrors are marked as such, because a farm's name on the open web is mostly
    those and the panel already has the register they are copying. And anything
    with no title worth showing is dropped rather than rendered as a bare URL.
    """
    seen, out = set(), []
    for r in results:
        url = (r.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        parsed = urllib.parse.urlparse(url)
        # Collapse http/https, www, trailing slash, query and fragment.
        host = parsed.netloc.lower().removeprefix("www.")
        path = parsed.path.rstrip("/").lower()
        ident = host + path
        if ident in seen:
            continue
        seen.add(ident)
        title = " ".join((r.get("title") or "").split())
        if not title:
            continue
        out.append({"url": url, "title": title[:180],
                    "snippet": " ".join((r.get("snippet") or "").split())[:300],
                    "host": host, "kind": kind_of(url),
                    "engine": r.get("engine")})
    out.sort(key=lambda r: PAGE_RANK.get(r["kind"], 9))
    return out


def queries_for(name, city, cvr, chr_no):
    """What to actually ask.  A farm's name alone is far too generic to search."""
    out = []
    if name:
        quoted = f'"{name}"'
        out.append(f"{quoted} {city}".strip() if city else quoted)
        out.append(f"{quoted} landbrug OR gård OR besætning")
    if cvr:
        out.append(f"{cvr} CVR {name}".strip())
    return [q for q in dict.fromkeys(out) if q]


# ---------------------------------------------------------------- page text

BLOCK_RE = re.compile(r"<(script|style|noscript|svg|nav|footer|header|form)\b.*?</\1>",
                      re.S | re.I)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
DESC_RE = re.compile(
    r'<meta[^>]+(?:name|property)="(?:description|og:description)"[^>]+content="([^"]*)"',
    re.I)


def read_page(url, want=None, chars=900):
    """Fetch one result and reduce it to something a panel can show.

    Not a summary -- nothing here is generated.  It is the page's own title, its
    own description, and the sentences that actually mention what was clicked,
    which is the part a reader would have scrolled to anyway.
    """
    key = f"page:{url}"
    hit = cache_get(key)
    if hit is not None:
        return hit
    try:
        raw, ctype, final = get(url, timeout=TIMEOUT)
    except Exception as exc:
        return cache_put(key, {"url": url, "error": type(exc).__name__})
    if "html" not in ctype and "text" not in ctype:
        return cache_put(key, {"url": url, "error": "not a page"})

    title = strip_tags(TITLE_RE.search(raw).group(1)) if TITLE_RE.search(raw) else ""
    desc_m = DESC_RE.search(raw)
    desc = html.unescape(desc_m.group(1)).strip() if desc_m else ""

    body = BLOCK_RE.sub(" ", raw)
    body = re.sub(r"<[^>]+>", " ", body)
    body = html.unescape(body)
    body = re.sub(r"[ \t\xa0]+", " ", body)
    body = re.sub(r"\s*\n\s*", "\n", body).strip()

    excerpt = ""
    if want:
        needles = [w for w in want if w and len(w) > 3]
        sentences = re.split(r"(?<=[.!?])\s+|\n", body)
        keep = [s.strip() for s in sentences
                if len(s.strip()) > 40 and any(n.lower() in s.lower() for n in needles)]
        excerpt = " ".join(keep)[:chars]
    if not excerpt:
        excerpt = body[:chars]

    return cache_put(key, {"url": final, "title": title, "description": desc[:400],
                           "excerpt": excerpt, "kind": kind_of(final),
                           "chars": len(body)})


# ---------------------------------------------------------------- orchestration

def enrich(cvr=None, chr_no=None, name=None, city=None, bfe=None, pages=4,
           emit=None, fetch_pages=True):
    """Everything known about one clicked farm, emitted as it arrives.

    `emit(event, payload)` is called for each stage.  The return value is the
    same thing assembled, for callers that would rather have it all at once.
    """
    def send(event, payload):
        if emit:
            emit(event, payload)
        return payload

    out = {"cvr": cvr, "chr": chr_no}
    send("stage", {"doing": "registers"})

    if cvr:
        rec = cvr_record(cvr)
        out["company"] = rec
        if not name and isinstance(rec, dict):
            name = rec.get("name")
        if not city and isinstance(rec, dict):
            city = rec.get("city")
        send("company", rec)

    out["name"] = name
    out["links"] = known_links(cvr, chr_no, name, bfe)
    send("links", {"links": out["links"]})

    if cvr:
        out["accounts"] = accounts(cvr)
        send("accounts", out["accounts"])

    if not name:
        send("done", {"note": "no company name to search for"})
        return out

    send("stage", {"doing": "search"})
    found, seen, tried, engine = [], set(), [], None
    # However many phrasings there are, the whole search phase gets one budget.
    # A panel that says "nothing found" in five seconds is worth more than one
    # that says it in twenty.
    deadline = time.monotonic() + SEARCH_BUDGET
    for q in queries_for(name, city, cvr, chr_no):
        if time.monotonic() > deadline:
            tried.append({"engine": "(budget)", "status": "search time budget spent"})
            break
        res = search(q)
        engine = engine or res.get("engine")
        if not tried:
            tried = res.get("tried") or []
        for r in res["results"]:
            if r["url"] in seen:
                continue
            seen.add(r["url"])
            found.append(r)
        # Each further query costs another turn of the rate limiter, so the
        # extra phrasings are a fallback for a name that found nothing, not a
        # way to pad a result set that is already useful.
        if len(found) >= 4:
            break
    out["search"] = found
    out["engine"] = engine
    # An empty result set from a blocked scraper and an empty one from a working
    # index mean different things, so the panel is told which happened.
    send("search", {"results": found, "engine": engine, "tried": tried,
                    "providers": providers_available()})

    if not fetch_pages or not found:
        send("done", {"pages": 0})
        return out

    send("stage", {"doing": "reading pages"})
    # Registers and directories say the same thing the map already knows, so the
    # fetch budget goes to the pages that might say something new.
    targets = sorted(found, key=lambda r: PAGE_RANK.get(r["kind"], 9))[:pages]
    want = [name, str(cvr) if cvr else "", city or ""]
    out["pages"] = []
    with ThreadPoolExecutor(max_workers=min(4, len(targets))) as pool:
        futures = {pool.submit(read_page, t["url"], want): t for t in targets}
        for fut in as_completed(futures):
            try:
                page = fut.result()
            except Exception as exc:
                page = {"url": futures[fut]["url"], "error": str(exc)}
            out["pages"].append(page)
            send("page", page)

    send("done", {"pages": len(out["pages"])})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cvr", type=int)
    ap.add_argument("--chr", dest="chr_no")
    ap.add_argument("--name")
    ap.add_argument("--city")
    ap.add_argument("--bfe")
    ap.add_argument("--pages", type=int, default=4)
    ap.add_argument("--json", action="store_true", help="dump the whole result")
    args = ap.parse_args()

    if not (args.cvr or args.name):
        sys.exit("need --cvr or --name")

    t0 = time.time()

    def show(event, payload):
        if args.json:
            return
        if event == "stage":
            print(f"[{time.time() - t0:5.1f}s] {payload['doing']}…")
        elif event == "company":
            bits = [payload.get("form"), payload.get("city"), payload.get("status")]
            print(f"  {payload.get('name', '?')} — {payload.get('industry', '')}")
            print(f"    {' · '.join(b for b in bits if b)}")
            for person in (payload.get("people") or [])[:6]:
                print(f"    {person['name']}  ({person.get('role', '')})")
        elif event == "accounts":
            reps = payload.get("reports") or []
            print(f"  {len(reps)} filings" +
                  (f", latest {reps[0]['end']}" if reps else ""))
        elif event == "links":
            for l in payload["links"]:
                print(f"  · {l['title']}")
        elif event == "search":
            for r in payload["results"]:
                print(f"  [{r['kind']:9}] {r['title'][:78]}")
                print(f"              {r['url'][:100]}")
        elif event == "page":
            if payload.get("error"):
                print(f"  ! {payload['url'][:70]} — {payload['error']}")
            else:
                print(f"  > {payload['title'][:80]}")
                print(f"    {payload['excerpt'][:300]}")
        elif event == "done":
            print(f"[{time.time() - t0:5.1f}s] done")

    result = enrich(args.cvr, args.chr_no, args.name, args.city, args.bfe,
                    pages=args.pages, emit=show)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
