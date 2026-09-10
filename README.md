# danish-livestock

A century of Danish livestock headcounts, pulled from the Statistics Denmark
StatBank API and rendered as a single self-contained report page.

Run it locally:

```sh
python3 serve.py            # http://127.0.0.1:8765
```

- `/` the farm map: 57,860 herd records, 150,382 cadastral parcels, and the
  company behind each one
- `/report` the 1920-2025 write-up

[docs/data-sources.md](docs/data-sources.md) records which Danish registers are
open, which are gated, and what each one actually contains.

## What's here

```
docs/data-sources.md      which registers are open, which are gated
scripts/fetch.py          national series, from the StatBank API
scripts/fetch_chr.py      every CHR livestock site, from the LandbrugsGIS WFS
scripts/fetch_marker.py   declared field parcels, aggregated to hectares per CVR
scripts/fetch_kommuner.py municipality outlines from DAWA, simplified for the web
scripts/build_map_data.py CHR + land -> the compact payload the map embeds
scripts/build.py          assemble site/index.html
scripts/build_map.py      assemble site/map.html
data/                     the built datasets
scripts/fetch_cvr.py      the company behind each CVR number, from CVR itself
scripts/fetch_regnskaber.py filed annual accounts, parsed out of XBRL
scripts/build_cvr_data.py CVR + accounts -> the panel's company payload
scripts/live_enrich.py    the on-click web lookup, also runnable on its own
scripts/fetch_parcels.py  parcel geometry for every property with livestock
scripts/build_tiles.py    cut the parcels into per-cell tiles for streaming
scripts/gzip_data.py      pre-compress what the server hands out
scripts/df_graphql.py     Datafordeler GraphQL client (--probe to see access)
serve.py                  local server: static files, gzip, WMS proxy
site/map/                 the map app - index.html, app.js, map.css, data/
site/                     the report's parts and its built page
```

## How the map streams

The cadastre is 150,382 parcels, far too much to hand a browser at once, so the
map works the way a level-of-detail renderer does: it asks for what the viewport
can see, at the detail the zoom justifies.

| Zoom | Drawn | Fetched |
|---|---|---|
| below 11 | farm points only | nothing |
| 11-12 | each property as one merged shape, ~25 m simplification | the visible cells |
| 13+ | every matrikel line, ~2 m | the visible cells |

Parcels live in 1,556 grid tiles of 0.1 deg x 0.05 deg, about 12-16 KB each; a
viewport pulls roughly 100 KB. Rings are Google-polyline encoded, about three
bytes a vertex against JSON's twenty-odd, and decoded once then cached.

Drawing and hit-testing are deliberately separate. What is *drawn* degrades with
zoom; what is *hit* never does. A click runs point-in-polygon against the
full-detail ring, fetching the cell if the viewer has never been close enough
for it to have loaded — so clicking a two-pixel dot at zoom 8 still resolves the
exact parcel the cursor was inside. Verified against 250 random farms: every one
lands inside its own property.

Merging at low zoom is not a computed union. Each property's parcels go into one
canvas path, so shared edges simply are not stroked. Nothing is invented.

## The company behind the farm

CHR gives every site a CVR number and nothing else about it. Two open registers
fill that in, and both are in the panel when you click a farm.

`scripts/fetch_cvr.py` pulls the company record for all 16,449 CVR numbers that
keep livestock — legal form, industry, purpose, capital, employees, production
units, and the partners, directors and board behind it. It reads
`datacvr.virk.dk`'s own gateway, which is first-party and unauthenticated; the
awkward part is a filter that starts answering 403 until you carry the session
cookie the ordinary company page hands out. With the cookie it runs clean.

`scripts/fetch_regnskaber.py` pulls filed annual accounts from
`distribution.virk.dk/offentliggoerelser`, which needs no credential at all, and
parses the **XBRL** rather than the PDF — so revenue, profit, equity and the book
value of land and buildings come out as numbers. 1,630 of the livestock
companies have readable accounts; the rest are sole traders and I/S with no
filing duty.

```sh
python3 scripts/fetch_cvr.py            # ~3 h, resumable, safe to stop
python3 scripts/fetch_regnskaber.py     # index in seconds, XBRL in minutes
python3 scripts/build_cvr_data.py       # -> site/map/data/cvr.json
python3 scripts/gzip_data.py
```

Neither register says who owns the *land*. CVR names the people behind the
*company*, which for a sole trader or an I/S is nearly the same thing and for an
A/S is not at all. The panel keeps the two apart.

## Live lookup, at the moment of the click

Everything above ships with the page. `/api/enrich` does not: when a farm is
clicked, the server goes out and fetches, streaming each answer back over
server-sent events so the panel fills in rather than waiting for the slowest
step — the CVR record, then the filed accounts, then a web search, then the top
results fetched and reduced to the sentences that mention the farm.

```sh
python3 scripts/live_enrich.py --cvr 29751455      # the same thing on a terminal
```

Results are cached on disk for a week, so a second click is instant and a room
full of people clicking is not a room full of requests to someone else's server.

**It works without any key.** Three keyless sources are wired in and the panel
degrades honestly rather than silently:

| | What it gives |
|---|---|
| **Wikipedia** (da, then en) | Always on, never blocks, always structured. Nothing for a family farm; the right answer for Arla, Danish Crown, DLG. |
| **Marginalia** | An independent index of the non-commercial web. Thin on Danish local news, good on an obscure page. |
| **DuckDuckGo** | Best coverage of the three, and the only one that refuses. |

The refusals are the design problem, and the fix is to be structurally incapable
of the behaviour that causes them. This project got itself blocked by DuckDuckGo
twice while being built, both times by a test loop, and unblocked both times
after a rest. Real use is one query per human click, which is nothing like that.
So:

* **One outbound search per engine per interval, process-wide** — six seconds
  for DuckDuckGo — whoever is asking.
* **A refusal is remembered for five minutes** and that engine is skipped
  meanwhile, because waiting out a rate limiter to be refused again is the worst
  thing this can do to a click.
* **"No results" and "blocked" are told apart.** An API answering with zero hits
  has genuinely searched; a scraped HTML endpoint returning nothing is the block
  page. Only the second counts against the engine.
* **The whole search phase has a nine-second budget.** A panel that says
  "nothing found" in five seconds beats one that says it in twenty.
* **Results are cached for a week**, so a second click costs nothing.

Worst case — every engine refusing — is now 6.6 s for the first click and 0.3 s
for the next, against 20 s before.

What comes back is filtered rather than dumped: duplicates collapsed across
engines and URL spellings, each hit classified, and the fifteen-odd sites that
are *themselves CVR mirrors* — proff, selskabsinfo, ownr, profiler, firmania,
cylex — marked as register hits and sunk to the bottom. A Danish company name on
the open web is mostly those, and the panel already has the register they are
copying. The fetch budget goes to news and reference instead.

**A key widens it, and is not needed.** Google CSE has by far the best coverage
of Danish local press, which is the thing worth searching for here:

```sh
export GOOGLE_CSE_KEY=... GOOGLE_CSE_CX=...   # 100/day; enable "search the entire web"
export BRAVE_SEARCH_API_KEY=...               # 2,000/month, easier signup
export MOJEEK_API_KEY=...
export YACY_PEER=default                      # or your own peer's URL
```

`python3 scripts/check_search_key.py` says whether a key actually works and,
when it does not, which of the usual misconfigurations it is — a Programmable
Search Engine that has not been set to search the whole web looks exactly like a
dead key otherwise.

`YACY_PEER` turns on peer-to-peer search, which needs no key and answers to
nobody — but measured against the public peer it took ten seconds to return one
irrelevant result, because its peers have not crawled the Danish web. Point it
at a peer you run and it becomes as good as what that peer has crawled. It is
off by default for the latency, not the principle.

## Publishing it as a static page

`serve.py` hands the map its data and does the live lookups itself. A static host
— GitHub Pages, S3, a USB stick — will do neither. So the same data is also
published in a shape a page can read with no server at all: JavaScript shards
loaded by script tag, indexed so a click costs about twenty kilobytes rather
than a 5.5 MB file.

```sh
git clone git@github.com:Jjokulian/static-async-data.git ../static-async-data
python3 scripts/build_static_store.py     # -> site/map/static-async-data/ + sad.js
```

The mechanism lives in [its own repository](https://github.com/Jjokulian/static-async-data),
since nothing about it is specific to Danish livestock.

**It is additive.** `data/` and `site/map/data/` are untouched, `serve.py` keeps
serving them, and this writes a separate tree beside them. Two ways out for the
same data, not a migration.

The map reads the sharded store in **both** modes, because there was no reason
for the served version to be the worse one. It used to pull the whole 6.5 MB
company table at boot whether or not anyone clicked a farm; now the boot loads
the store's index and the shards arrive a click at a time.

| | Before | Now |
|---|---|---|
| At boot | 4.09 MB gzipped | 2.34 MB gzipped |
| Per farm clicked | 0 | ~20 KB |

`site/map/data/cvr.json` is now only a build input for the store — nothing
fetches it. It is worth excluding from a static deploy, along with `*.json.gz`,
which is dead weight on a host that compresses for itself.

What a static deploy keeps and loses, tested rather than assumed:

| | Static host |
|---|---|
| Map, parcels, herd records | works — all paths are relative |
| Company panel, filed accounts | works — from the sharded store |
| Cadastre and address lookup | works — DAWA sends `Access-Control-Allow-Origin: *` |
| Annual accounts, live | works — Erhvervsstyrelsen's index reflects the origin |
| Wikipedia context | works — CORS `*` with `origin=*` |
| Basemap | works — falls back to OpenStreetMap, which needs no key |
| Klimadatastyrelsen topo and aerial | **server only** — the token has to stay server-side |
| Web search | **lost** — no engine sends CORS headers, and it wants a key anyway |

The app works out which it is on its own: `/api/config` 404s on a static host,
the catch sets an empty config, the basemap control offers OpenStreetMap instead
of the two Danish layers, and the live-lookup section does not appear.

So a static deploy loses one thing that matters — the Klimadatastyrelsen aerial
photos, which are markedly better over farmland than anything keyless. Everything
else is intact. That is not enough to be worth running a server for; if you want
those layers, run `serve.py` somewhere and it will use them automatically.

## Not done yet

**The layout assumes a desktop.** A 330 px sidebar beside a full-height map, a
panel that slides in from the right, and a slider whose mode changes on scroll
are all mouse-and-keyboard assumptions. Porting it to a phone is worth doing
once the data side has settled, and is deliberately not attempted before then.

## The token stays on the server

`serve.py` proxies the Klimadatastyrelsen basemaps at `/api/wms/topo` and
`/api/wms/orto`, adding the Dataforsyningen token server-side from
`~/.data-forsyningen-token` or `$DATAFORSYNINGEN_TOKEN`. The browser never sees
a credential, and deploying means setting the env var on the host.

Rebuild the report:

```sh
python3 scripts/fetch.py                 # national series 1920-2025
python3 scripts/build.py                 # -> site/index.html
```

Rebuild the map (the CHR and Marker pulls take a few minutes each):

```sh
python3 scripts/fetch_chr.py --year 24   # 57,860 livestock sites
python3 scripts/fetch_marker.py          # 616,411 parcels -> hectares per CVR
python3 scripts/fetch_kommuner.py --tolerance 0.0005
python3 scripts/build_map_data.py
python3 scripts/build_map.py             # -> site/map.html
```

The map carries no tile layer on purpose: an Artifact's CSP blocks external
images, so the basemap is the municipality geometry drawn as vectors, and
Leaflet's stylesheet is inlined rather than linked.

`fetch.py` is deterministic against a given API state: re-running it reproduces
`data/livestock.json` byte for byte until Statistics Denmark publishes a new year.

## Sources

| Table | What it gives | Span |
|---|---|---|
| `HDYR1920` | livestock, historic census (in 1,000 units) | 1920–1981 |
| `HDYR1` | farms with livestock: animal counts and holding counts | 1982–2025 |
| `PELS11` | fur animals (mink), discontinued after the 2020 cull | 2010–2020 |
| `ANI5` | pig slaughterings and exports of live pigs | 1990–2025 |
| `ANI7` | milk ex farm and dairy cow numbers | 1990–2025 |
| `HISB3` | population, for the per-capita figures | 1901–2026 |

## Reading the data

`data/livestock.json` holds `[year, value]` pairs throughout:

- `long` — the joined 1920–2025 series per species (`cattle`, `cows`, `pigs`,
  `sows`, `fowls`, `sheep`, `horses`)
- `modern` — 1982-onward detail the historic census doesn't have
  (`dairy_cows`, `suckler_cows`, `poultry_total`, `hens`, `broilers`, `turkeys`)
- `farms` — holdings keeping each species, 1982 onward
- `pigflow`, `milk`, `mink`, `population` — the supporting series

Three joins in `long` are not like-for-like, and the report says so on the page:

- **Sheep** include goats before 1982.
- **Horses** from 1982 count only horses on agricultural holdings, so the modern
  level understates the national horse population by a wide margin.
- Pre-1982 figures were published in thousands, so those years are rounded to
  the nearest thousand and the join shows as a small step in some series.

Every figure is a **census-day headcount of live animals**, not annual
production. Broilers turn over roughly eight times a year: 13.8 million standing
in 2024 against 106 million chickens slaughtered in 2025.
