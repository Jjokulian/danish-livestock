#!/usr/bin/env python3
"""Minimal client for GraphQL on Datafordeleren.

    https://graphql.datafordeler.dk/<register>/<version>[/schema]

Authentication is the API key from Datafordeler Administration, passed as
?apiKey=. An API key reaches unprotected data only; access-restricted registers
need OAuth (shared secret or an OCES3 certificate) plus an approved application.
A freshly created key takes up to 15 minutes to activate, and until it does
every call returns 401 "Unrecognized Authentication key".

    python3 scripts/df_graphql.py --register MAT/v1 --schema
    python3 scripts/df_graphql.py --register MAT/v1 --query '{ ... }'
    python3 scripts/df_graphql.py --probe        # which registers answer

The key is read from ~/.datafordeler-apikey and never printed.
"""
import argparse
import json
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://graphql.datafordeler.dk"
KEYFILE = pathlib.Path.home() / ".datafordeler-apikey"
UA = {"User-Agent": "danish-livestock/1.0"}
PROBE = ["MAT/v1", "MAT/v2", "MATRIKLEN/v1", "EJF/v1", "EBR/v1",
         "BBR/v3", "DAR/v3", "CVR/custom/v1", "DAGI/v1"]


def api_key():
    if not KEYFILE.exists():
        sys.exit(f"no API key at {KEYFILE}")
    return KEYFILE.read_text().strip()


def call(path, key, body=None, retries=4):
    """One request, retrying the intermittent DAF-AUTH-0005 401 the gateway emits."""
    url = f"{BASE}/{path}?" + urllib.parse.urlencode({"apiKey": key})
    data = json.dumps({"query": body}).encode() if body else None
    headers = dict(UA)
    if data:
        headers["Content-Type"] = "application/json"
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            body_text = e.read().decode("utf-8", "replace")
            # DAF-AUTH-0005 comes back sporadically on keys that are known good.
            if e.code == 401 and "DAF-AUTH-0005" in body_text and attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            return e.code, body_text
        except Exception as exc:
            if attempt == retries - 1:
                return 0, str(exc)
            time.sleep(2 * (attempt + 1))
    return 0, "exhausted retries"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--register", default="MAT/v1", help="e.g. MAT/v1, BBR/v3")
    ap.add_argument("--schema", action="store_true", help="fetch the GraphQL schema")
    ap.add_argument("--query", help="a GraphQL query string")
    ap.add_argument("--probe", action="store_true", help="try every known register")
    ap.add_argument("-o", "--out", help="write the response body to this file")
    args = ap.parse_args()
    key = api_key()

    if args.probe:
        for reg in PROBE:
            status, body = call(f"{reg}/schema", key)
            note = ""
            if status != 200:
                try:
                    note = json.loads(body).get("detail", "")[:80]
                except Exception:
                    note = body[:80].replace("\n", " ")
            print(f"  {reg:<16} {status}  {note}")
        return

    path = f"{args.register}/schema" if args.schema else args.register
    status, body = call(path, key, args.query)
    print(f"HTTP {status}  ({len(body):,} bytes)")
    if args.out:
        pathlib.Path(args.out).write_text(body)
        print(f"wrote {args.out}")
    else:
        print(body[:4000])


if __name__ == "__main__":
    main()
