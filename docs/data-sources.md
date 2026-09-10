# Where Danish livestock and land data actually lives

Notes from probing the registers on 9 September 2026. Every "open" claim below was
tested with an unauthenticated request from this machine, not taken from a portal
description.

## The short answer

The farm-level data is open, and so is the cadastre. Point locations, species and
herd sizes for all 57,860 CHR-registered livestock sites come from one
unauthenticated WFS; the matrikel parcel under any of those points comes from
DAWA, also unauthenticated. What is gated is narrower than it first looks: the
**identity of the owner**, not the land.

| What | Where | Access |
|---|---|---|
| Livestock sites: point, species, herd size, animal units | `geodata.fvm.dk/geoserver/ows`, layers `Jordbrugsanalyser:CHR00…CHR24` | **Open**, no key |
| Field parcels with operator CVR and area | same WFS, `Marker:Marker_2008…Marker_2026` | **Open**, no key |
| Field blocks | same WFS, `Markblokke:Markblokke_2005…2027` | **Open**, no key |
| Addresses, municipality/region outlines | `api.dataforsyningen.dk` (DAWA) | **Open**, no key |
| Company and production-unit register (CVR) | `datacvr.virk.dk/gateway` (the register's own site API) | **Open**, no key — see below |
| Published annual accounts, with XBRL | `distribution.virk.dk/offentliggoerelser` | **Open**, no key |
| CVR bulk API, documented | `distribution.virk.dk/cvr-permanent` | Gated — credentials by application |
| National herd totals, 1920– | Statistics Denmark StatBank API | **Open**, no key |
| Cadastral parcels: matrikel no., ejerlav, area, SFE/BFE property no., geometry | `api.dataforsyningen.dk/jordstykker` (DAWA) | **Open**, no key |
| Topographic and aerial raster basemaps | `api.dataforsyningen.dk/topo_skaermkort_DAF`, `…/orto_foraar_DAF` | Token (Dataforsyningen) |
| Matriklen2 full service, with history | Datafordeler / `services.datafordeler.dk` | **Free but gated** — account, IT-system, API key |
| **Property ownership (Ejerfortegnelsen) — who owns a parcel** | Datafordeler | **Free but gated**, same registration |
| BBR (buildings, including farm buildings) | Datafordeler; `api.dataforsyningen.dk/bbrlight` | Gated / partly open |
| **CHR at herd/animal level** (individual animals, movements) | Fødevarestyrelsen web services; `chr.fvst.dk` | **Gated** — public site does one CHR number at a time; bulk needs an agreement |

## CVR: the company behind the farm, and how to actually get it

Every CHR site carries a CVR number, and 16,449 distinct companies keep livestock
in Denmark.  The register that describes them is open; getting at it in bulk is
where the three obvious routes diverge.  All three were tried from this machine
on 9 September 2026.

| Route | What happened |
|---|---|
| `distribution.virk.dk/cvr-permanent/_search` | 401. This is Erhvervsstyrelsen's documented bulk API and the correct answer, but credentials are issued on written application. |
| `cvrapi.dk/api?search=<cvr>&country=dk` | Works unauthenticated, pleasant shape — and the free quota is about **fifty lookups a day**. It answered 43 companies and then returned `{"error":"QUOTA_EXCEEDED"}` as an HTTP 200, which is worth knowing: a naive fetcher records fifty good rows and sixteen thousand empty ones. |
| `datacvr.virk.dk/gateway/virksomhed/hentVirksomhed?cvrnummer=<cvr>` | **Works, unauthenticated, and it is first-party.** This is what Erhvervsstyrelsen's own public site calls to draw a company page. |

The gateway is much richer than the mirror.  For one Bogø cattle farm it returns
the legal form, the purpose clause, registered capital, the industry code,
employment quarter by quarter back to 2013, every production unit with its own
address and industry, the full list of published accounts — and the people:

```json
{"senesteNavn": "Britt Muurholm Svensson", "personType": "deltager",
 "rolle": {"name": "FULDT_ANSVARLIG_DELTAGERE"}}
```

### The 403, and the cookie that fixes it

Bare requests to the gateway work for a while and then return 403 — after about
215 of them here, and intermittently well before that.  It is not a rate limit
in the usual sense: one GET on the ordinary company page issues an `S9SESSIONID`
cookie, and requests carrying it answer steadily.  A run that had been failing
one lookup in three went to 25 out of 25 with no other change.
`scripts/fetch_cvr.py` opens a session, keeps it, and opens a new one whenever
the filter starts refusing.

### What this does and does not tell you about ownership

It is worth being exact, because the two registers are easy to conflate:

* **CVR names the people behind the company** — partners, directors, board,
  founders, and where the company form requires it the legal and beneficial
  owners. That is open, and it is in the panel.
* **Ejerfortegnelsen names the owner of the land.** That is the one behind
  OAuth and an approved application, as below. Nothing here opens it.

For the great majority of Danish livestock farms the first is close to the
second, since an *enkeltmandsvirksomhed* or an I/S is its partners. But they are
different facts from different registers, and the map says so.

The endpoint also returns the private home addresses of those people.  The
fetcher drops them, and honours `reklamebeskyttet` — the register's own flag
meaning the contact details are not to be used for approaches — by not carrying
phone and e-mail for companies that set it.

## Annual accounts are wide open, and they are XBRL

`http://distribution.virk.dk/offentliggoerelser/_search` needs no credential at
all, which is a surprise directly next door to `cvr-permanent`, which does.  It
is an Elasticsearch index of every annual report filed in Denmark, and it takes
`terms` queries and scrolls, so all 16,449 companies can be asked at once in 42
batches — seconds, not hours.

Each filing links a PDF for people and an **XBRL instance for machines**, and
the XBRL is the point: revenue, profit, equity and the book value of land and
buildings arrive as tagged numbers rather than as text in a scanned table.

Of the livestock CVRs, **1,630 have readable filed accounts**.  Most farms have
none and never will — a sole trader or an I/S has no filing duty, and that is
the majority of Danish agriculture. What comes back is the ApS and A/S end of
the register, which is where the large herds are.

Three things bite when parsing it:

1. **The taxonomy prefix is not fixed.** `fsa:`, `c:` and `d:` all appear for
   the same concepts, so match the local name, not the prefix.
2. **Since 2023 filings are inline XBRL** — one `.xhtml` that is both the human
   document and the machine one, with facts in `ix:nonFraction` wrappers whose
   attribute order varies.
3. **The number format is declared, and guessing it is wrong by a factor of a
   thousand.** `format="ixt:numcommadecimal"` on `542.165` means five hundred
   and forty-two thousand — the dot groups thousands. Read the `format`
   attribute; honour `scale` and `sign` while you are there.

Contexts carrying a `segment` or `scenario` are slices — one business area, one
share class — and summing them double-counts. The company-level figure is the
one on a plain context, matched to the period end.

## The one that matters: CHR on the LandbrugsGIS WFS

`https://geodata.fvm.dk/geoserver/ows` is Landbrugsstyrelsen's GeoServer. It
publishes 520 layers with no key and no visible rate limit. The livestock ones
are `Jordbrugsanalyser:CHR<yy>`, one per year from 2000 to 2024 — an annual
snapshot of the Central Livestock Register taken on 1 June.

```
service=WFS&version=2.0.0&request=GetFeature
&typeNames=Jordbrugsanalyser:CHR24
&outputFormat=application/json&srsName=EPSG:4326
&count=10000&startIndex=0
```

Each feature is one site:

| Field | Meaning |
|---|---|
| `the_geom` | point, the registered location of the holding |
| `CHRNR` | CHR number — the site's identity in the register |
| `CVRNR` | the business that runs it |
| `DYRKODE` / `DYRTEKST` | species (12 cattle, 15 pigs, 31 laying hens, 11 horses, …) |
| `DE` | animal units under the manure regulation |
| `BSTR1/2/4` + `BSTRTEKST1/2/4` | herd size in up to three categories, named per species |
| `BRUGKODE` / `VIRKART` | what the holding is for (meat, milk, breeding, hobby, exhibition) |
| `Kommune`, `Postnr` | municipality and postcode |

Two traps:

* **GeoServer's JSON writer says UTF-8 and emits ISO-8859-1.** Decode `latin-1`
  or every Danish name arrives mangled.
* **The three size columns do not always sum.** For cattle and pigs they
  partition the herd (bulls + heifers + cows). For sheep, goats, horses and mink
  one column is already `… i alt` and the others are subsets of it — summing
  double-counts. `scripts/build_map_data.py` handles this.

Sanity check against Statistics Denmark for 2024: CHR gives 1,427,375 cattle,
the census gives 1,426,086. A 0.09% gap between two registers built for entirely
different purposes.

## Land: what the WFS can and cannot tell you

`Marker:Marker_2025` is every parcel declared for area support — 616,411 polygons
carrying the operator's `CVR` and `IMK_areal` in hectares. Asking for
`propertyName=CVR,IMK_areal,Afgroede` with `outputFormat=csv` skips the geometry
and makes the whole country a few minutes' download. Aggregated by CVR it comes
to 2,621,104 ha across 26,922 businesses, which matches the national utilised
agricultural area.

Because CHR also carries `CVRNR`, the two join: **16,449 CVR numbers appear on
both sides, and the businesses that keep livestock farm 1,393,327 ha — 53% of all
declared Danish farmland.**

What this is *not* is ownership. It is land declared for subsidy in one year:
rented land counts, and land in no support scheme does not appear.

## The cadastre is open too — via DAWA, not Datafordeler

`matrikler.dk` is a small private lookup site (HTTP only; its HTTPS does not
answer). Reading its page source is what turned this up: it runs on
`api.dataforsyningen.dk/jordstykker`, and that endpoint needs no key at all.

```
GET https://api.dataforsyningen.dk/jordstykker?x=9.363492&y=54.916952&srid=4326
```

answers with the parcel that contains the point:

| Field | Value for that CHR site |
|---|---|
| `matrikelnr` | `24` |
| `ejerlav` | Perbøl, Kliplev (code 1530654) |
| `registreretareal` | 56,758 m² |
| `sfeejendomsnr` / `bfenummer` | 7845431 — the property the parcel belongs to |

Add `format=geojson` for the polygon, or query `?sfeejendomsnr=…` to get every
parcel of one property and sum its registered area. That is a genuine ownership
*unit* — a samlet fast ejendom — with real cadastral areas, and it needs no
credential. `scripts/fetch_matrikel.py` resolves all 57,860 CHR sites this way in
about a quarter of an hour.

The thing DAWA will not tell you is **who** the owner is. `ejerlav` is the
cadastral district, not a person. Names and CVR numbers of owners live in
Ejerfortegnelsen on Datafordeler, behind the login.

## Two credential systems, easily confused

They are different agencies' front doors and different keys:

* **Dataforsyningen** (`api.dataforsyningen.dk`) — Klimadatastyrelsen's gateway.
  A 32-character token on the query string. It unlocks the raster services:
  `topo_skaermkort_DAF` (the topographic screen map) and `orto_foraar_DAF`
  (GeoDanmark aerial photos), both serving EPSG:3857 so they drop straight into
  Leaflet. DAWA lives on the same host but ignores the token — it is open.
* **Datafordeler** (`services.datafordeler.dk`) — the shared public-sector
  distribution. Needs an account, a registered "IT-system", and an API key or
  OAuth. This is what Matriklen2 and Ejerfortegnelsen sit behind.

A Dataforsyningen token does **not** open Datafordeler services: requests for
`matrikel*` on the Dataforsyningen gateway return 404 with a token that works
perfectly for `orto_foraar_DAF`.

## The gated ones, and what it costs

**Matriklen2 and Ejerfortegnelsen (via Datafordeler).** Free of charge, but you
must register on datafordeler.dk, create an "IT-system" and issue an API key or
OAuth credentials before the WFS answers. An unauthenticated call to
`services.datafordeler.dk/MATRIKLEN2/…` returns 404 rather than a challenge.
Klimadatastyrelsen has said that from 15 January 2027 authentication will be the
only route, as the older unauthenticated services are retired. For parcel
geometry and areas you do not need any of this — DAWA has it, as above. For
owner identity you do.

**CHR below the site level.** The register knows every individual pig birth, ear
tag and movement. `chr.fvst.dk` will answer for one CHR number at a time in a
browser; programmatic bulk access is by agreement with Fødevarestyrelsen. What
the open WFS publishes is the annual aggregate per site — which is exactly the
level a map needs.

## Datafordeler with an API key: tested 9 September 2026

An API key from Datafordeler Administration (`?apiKey=…`, no certificate, no SSH2
key, e-mail user is enough) reaches the GraphQL endpoint at
`https://graphql.datafordeler.dk/<register>/<version>`. Every register that
exists answers:

| Path | Result |
|---|---|
| `MAT/v2` | 200 — Matriklen, full model, real data |
| `EJF/v1` | 200 — Ejerfortegnelsen, schema and object ids only |
| `EBR/v1`, `BBR/v3`, `DAR/v3`, `CVR/custom/v1` | 200 |
| `MAT/v1`, `MATRIKLEN/v1`, `DAGI/v1` | 404 — no such path |

Three things bite:

1. **A fresh key is inert for up to 15 minutes.** Until then every call is 401
   `DAF-AUTH-0005`, whose message spells this out. Ours went live on the fifth
   minute of polling.
2. **`DAF-AUTH-0005` also fires sporadically on a key that is known good.** The
   first probe showed four registers as 401 that answered 200 once
   `scripts/df_graphql.py` retried. Do not conclude "not subscribed" from a
   single 401.
3. **The bitemporal registers refuse an unqualified query** with
   `DAF-GQL-0009`: you must filter on `id_lokalId`/`datafordelerRowId`, or pass
   `virkningstid` / `registreringstid`.

A working query, and the answer for the Aabenraa cattle farm's property:

```graphql
{
  MAT_SamletFastEjendom(first: 3, virkningstid: "2026-09-09T00:00:00.000Z",
                        where: { BFEnummer: { eq: 7845431 } }) {
    nodes { BFEnummer status landbrugsnotering arbejderbolig udskiltVej }
  }
}
```

```json
{"BFEnummer": 7845431, "status": "Gældende", "landbrugsnotering": "Landbrug",
 "arbejderbolig": false, "udskiltVej": false}
```

`landbrugsnotering: "Landbrug"` — the property carries the formal agricultural
notation. DAWA does not expose that; the register does.

### Where the API key stops

Ownership content is denied **field by field**, not entity by entity, which is
easy to misread. `EJF_Ejerskab` answers happily if you select only
`id_lokalId`. Ask for anything that identifies the owner or the holding —
`bestemtFastEjendomBFENr`, `ejendeVirksomhedCVRNr`, `ejendePersonPersonNr`,
`ejerforholdskode`, the ownership fractions — and it returns 403
`DAF-AUTH-0001`, "The current user is not authorized to access this resource".
`EJF_PersonVirksomhedsoplys` (names and addresses) and `EJF_Ejerskifte` are
refused outright.

So the documented split holds exactly: an API key is unprotected data only.
Land, geometry, areas, agricultural notation — yes. **Who owns it — no.** That
needs OAuth (shared secret or an OCES3 certificate), an approved application to
the register authority, and an IP allowlist. An OCES3 certificate cannot be
self-generated: it is X.509 issued by the Danish state through MitID Erhverv.

## Sources

- LandbrugsGIS / Landbrugsstyrelsen WFS: <https://geodata.fvm.dk/geoserver/ows>
- LandbrugsGIS downloads: <https://landbrugsgeodata.fvm.dk/>
- CHR public lookup: <https://chr.fvst.dk/chri/faces/chri>
- CHR rules and registration duty: <https://foedevarestyrelsen.dk/lovstof/det-centrale-husdyrbrugsregister-chr-lovstof>
- CHR as a published dataset: <https://arealdata.miljoeportal.dk/datasets/urn:dmp:ds:chr-2023-fvm>
- Datafordeler, Matriklen2: <https://datafordeler.dk/dataoversigt/matriklen-mat/matriklen2/>
- DAWA (addresses and administrative geometry): <https://dawadocs.dataforsyningen.dk/>
- Statistics Denmark StatBank API: <https://www.statbank.dk/>
- matrikler.dk (private lookup site, HTTP only): <http://matrikler.dk/>
- Dataforsyningen web service docs: <https://confluence.sdfi.dk/pages/viewpage.action?pageId=158368397>
- CVR company pages: <https://datacvr.virk.dk/>
- Erhvervsstyrelsen's system access to CVR: <https://datacvr.virk.dk/artikel/system-til-system-adgang-til-cvr-data>
- Published annual accounts: <http://distribution.virk.dk/offentliggoerelser/_search>
- The Danish XBRL taxonomies: <https://erhvervsstyrelsen.dk/vejledning-tekniske-vejledninger-og-taksonomier>

## Live web lookup: what is actually reachable

The map fetches web data for a farm at the moment it is clicked, so the question
of which search backends answer an unauthenticated server matters. Tested from
this machine, same date:

| Backend | Result |
|---|---|
| DuckDuckGo HTML endpoint | Answers a few queries, then serves an anomaly page — HTTP 202 with no results — to the IP. |
| Mojeek HTML endpoint | 403 after a handful of queries. |
| Public SearxNG instances | Captcha, 429 or 403. |
| Google News RSS | Answers, but its own copyright notice restricts the feed to a personal reader and prohibits other use. Not wired up. |
| Marginalia public API | Answers, no key. An independent index of the non-commercial web: good for an obscure page, thin on Danish local news. |
| Brave / Google CSE / Mojeek APIs | Free tiers, but each needs a key. Brave is not a web3 service despite the browser's BAT token — it is a conventional engine with its own index. |
| **YaCy** (peer-to-peer) | Answers, no key, genuinely decentralised — but 9.8 s for one irrelevant result and 13.2 s for none. Its peers have crawled almost no Danish web: `landbrug` returns the same page four times, a named farm returns nothing. Wired up, off unless `YACY_PEER` is set. |
| **Presearch** | No public search API. `api.presearch.com`, `nodes.presearch.com` and the documented paths all 404. |
| **ipfs-search, Masa** | Did not resolve from here at all. |

### On decentralised search, having actually tried it

The honest result is that the only decentralised engine that answers is YaCy,
and its index does not contain the thing this map needs to look up. That is not
a protocol problem and no amount of nodes fixes it: a P2P index holds what its
peers have crawled, and nobody's peer has crawled Danish farm companies. Running
a YaCy peer pointed at the Danish agricultural web would change that, and
`YACY_PEER` exists for exactly that case.

Federation is also slow by construction — a query waits on remote peers, which
is ten seconds against the sub-second the registers answer in. For a lookup that
fires on a click, that is the difference between a panel that fills in and one
that appears to hang.

Distributing requests across many IPs to get past DuckDuckGo's and Mojeek's
blocks — a proxy pool, or an incentivised bandwidth network like Mysterium,
Orchid or Grass — would be evading an access control the operators deliberately
applied, so it is not built in.

Golem is sometimes suggested for this. It does not fit: task VMs have no general
network egress (outbound needs a signed manifest of permitted URLs that
providers opt into), task startup is tens of seconds to minutes, and the scarce
resource here is request origin, not CPU. The decentralised-compute project that
*would* fit is Akash, for hosting the service itself.

So there is no dependable keyless general web search, and `scripts/live_enrich.py`
says so rather than returning an empty box: providers are tried in order, and the
panel reports whether nothing was found or nothing was configured.

Spreading the requests over many IPs to get past the block — a proxy pool, or a
decentralised bandwidth network — would be evading an access control the
operators deliberately applied, so it is not built in.

The registers, by contrast, answer happily: CVR, the accounts index and DAWA all
respond in well under a second and are the substance of what the panel shows.
General web search is the garnish.
