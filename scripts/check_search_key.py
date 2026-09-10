#!/usr/bin/env python3
"""Check whichever search API keys are set, and say precisely what is wrong.

A search key that does not work usually fails silently: the chain moves on to
the next provider and the panel just says nothing was found. This asks each
configured provider directly and reports what actually came back.

    python3 scripts/check_search_key.py
    python3 scripts/check_search_key.py --query "LANGVANG A/S Skjern"

Nothing here needs the server to be running.
"""
import argparse
import json
import os
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
UA = {"User-Agent": "danish-livestock/1.0 (key check)"}


def fetch(url, headers=None):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def check_google(query):
    key, cx = os.environ.get("GOOGLE_CSE_KEY"), os.environ.get("GOOGLE_CSE_CX")
    if not (key or cx):
        return "google", None, "not set"
    if not key:
        return "google", False, "GOOGLE_CSE_CX is set but GOOGLE_CSE_KEY is not"
    if not cx:
        return "google", False, "GOOGLE_CSE_KEY is set but GOOGLE_CSE_CX is not"

    url = "https://www.googleapis.com/customsearch/v1?" + urllib.parse.urlencode(
        {"key": key, "cx": cx, "q": query, "num": 3})
    try:
        _, data = fetch(url)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            err = json.loads(body)["error"]
            reason = (err.get("errors") or [{}])[0].get("reason", "")
            msg = err.get("message", "")
        except Exception:
            reason, msg = "", body[:200]
        hints = {
            "accessNotConfigured":
                "the Custom Search API is not enabled on that Cloud project — "
                "enable it at console.cloud.google.com/apis/library/customsearch.googleapis.com",
            "keyInvalid": "the API key is wrong, or restricted to other APIs or referrers",
            "invalid": "the cx (search engine id) is not one this key may use",
            "dailyLimitExceeded": "the 100-a-day free quota is spent; it resets at "
                                  "midnight Pacific",
            "rateLimitExceeded": "too many requests just now — wait and retry",
        }
        return "google", False, f"HTTP {e.code} {reason or ''}: {hints.get(reason, msg)}"
    except Exception as exc:
        return "google", False, f"{type(exc).__name__}: {exc}"

    items = data.get("items") or []
    total = (data.get("searchInformation") or {}).get("totalResults", "0")
    if not items:
        # By far the most common misconfiguration, and it looks like a dead key.
        return "google", False, (
            "the key and cx work, but the search returned nothing. A new "
            "Programmable Search Engine only searches sites you list — turn on "
            '"Search the entire web" in its settings at '
            "programmablesearchengine.google.com/controlpanel/all")
    return "google", True, f"{len(items)} results (of about {total})", items


def check_brave(query):
    key = os.environ.get("BRAVE_SEARCH_API_KEY")
    if not key:
        return "brave", None, "not set"
    url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode(
        {"q": query, "count": 3, "country": "dk"})
    try:
        _, data = fetch(url, {"Accept": "application/json",
                              "X-Subscription-Token": key})
    except urllib.error.HTTPError as e:
        hints = {401: "the subscription token is wrong",
                 422: "the request was rejected — check the plan covers Web Search",
                 429: "rate or quota limit reached (free tier is 1/second, "
                      "2,000/month)"}
        return "brave", False, f"HTTP {e.code}: {hints.get(e.code, e.reason)}"
    except Exception as exc:
        return "brave", False, f"{type(exc).__name__}: {exc}"
    items = [{"title": w.get("title"), "link": w.get("url")}
             for w in (data.get("web") or {}).get("results", [])]
    if not items:
        return "brave", False, "the key works but the search returned nothing"
    return "brave", True, f"{len(items)} results", items


def check_mojeek(query):
    key = os.environ.get("MOJEEK_API_KEY")
    if not key:
        return "mojeek", None, "not set"
    url = "https://www.mojeek.com/search?" + urllib.parse.urlencode(
        {"q": query, "api_key": key, "fmt": "json", "t": 3})
    try:
        _, data = fetch(url)
    except Exception as exc:
        return "mojeek", False, f"{type(exc).__name__}: {exc}"
    items = [{"title": r.get("title"), "link": r.get("url")}
             for r in (data.get("response") or {}).get("results", [])]
    return ("mojeek", bool(items),
            f"{len(items)} results" if items else "the key works but returned nothing",
            items)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--query", default="LANGVANG A/S Skjern landbrug",
                    help="what to search for; the default is a real farm")
    args = ap.parse_args()

    print(f'checking search providers with: "{args.query}"\n')
    any_working = False
    for check in (check_google, check_brave, check_mojeek):
        result = check(args.query)
        name, ok, note = result[0], result[1], result[2]
        mark = {True: "ok  ", False: "FAIL", None: "--  "}[ok]
        print(f"  [{mark}] {name}: {note}")
        if ok:
            any_working = True
            for item in (result[3] if len(result) > 3 else [])[:3]:
                print(f"           {(item.get('title') or '')[:64]}")
                print(f"           {item.get('link', '')[:74]}")
        print()

    if any_working:
        print("Set the same variables where serve.py runs and restart it;\n"
              "/api/config will then list the provider under \"keyed\".")
    else:
        print("No provider is working. The map still runs: the registers, the\n"
              "filed accounts and Wikipedia need no key at all.")
    return 0 if any_working else 1


if __name__ == "__main__":
    sys.exit(main())
