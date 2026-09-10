(function(){
"use strict";

var D, KOM, S, CFG = null;
var STORE = null, CVRMETA = null;   // the sharded company store

var LAYERS = {
  farm: {dir: "data/tiles/",     idxUrl: "data/tile_index.json",
         index: null, cache: new Map(), fails: new Set()},
  all:  {dir: "data/tiles_all/", idxUrl: "data/tile_all_index.json",
         index: null, cache: new Map(), fails: new Set()}
};

/* record layout */
var LON = 0, LAT = 1, GRP = 2, SPC = 3, HEAD = 4, DE = 5, HA = 6, KOM_I = 7,
    CHR = 8, CVR = 9, CATS = 10, MAT = 11, LAV = 12, PAR_HA = 13, PROP_HA = 14,
    PROP_N = 15, SFE = 16, POST = 17, VIRK = 18, BRUG = 19, LPARC = 20, CROP = 21;

var nf = new Intl.NumberFormat("en-GB");
var GROUP_VAR = ["--s1", "--s2", "--s3", "--s0", "--s0", "--s0", "--s0"];
var Z_PARCEL = 11;   // below this, parcels are not drawn at all
var Z_SHARP = 13;    // at and above this, full detail and individual matrikel lines

var HERD_POS_MAX = 1000;   // slider positions; the threshold is derived, not stepped
var herdCeiling = 1;       // largest herd among the species currently shown

var state = {group: -1, mode: "head", min: 0,
             herdMode: "min", herdWidth: 0.10,   // the slider's own mode

             filtered: [], colors: {},
             selected: null, showAll: false};
var byChr = new Map();   // CHR number -> its herd records; one site can hold several
var map, komLayer, canvas, ctx, farmPane, frame = null;
var CFG = null;    // what the server says it can do, from /api/config

var liveStream = null;   // the open /api/enrich EventSource, if any

function css(name){
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
function readColors(){
  state.colors = {
    "--s1": css("--s1"), "--s2": css("--s2"), "--s3": css("--s3"), "--s0": css("--s0"),
    land: css("--land"), coast: css("--coast"), surface: css("--surface"),
    parcelFill: css("--parcel-fill"), parcelFillStrong: css("--parcel-fill-strong"),
    parcelLine: css("--parcel-line"), otherFill: css("--other-fill"),
    otherLine: css("--other-line"),
    selected: css("--selected")
  };
}
readColors();

/* The slider spans whatever the selected species actually reaches, so the whole
   track stays useful whichever kind is chosen. Cattle top out at 4,731 head and
   laying hens at 300,000; a scale fixed to the larger would jam every cattle
   holding into the first twentieth of the track. Recomputed when the species
   selection changes. */
function herdScale(){
  var top = 1;
  for (var i = 0; i < S.length; i++){
    if (state.group >= 0 && S[i][GRP] !== state.group) continue;
    if (S[i][HEAD] > top) top = S[i][HEAD];
  }
  herdCeiling = top;
}

/* Position to a headcount, logarithmically. Herd sizes are distributed that way
   -- the median cattle holding is 10 animals and the largest is 4,731 -- so a
   linear scale would spend nine tenths of its length on holdings that barely
   differ from each other. */
function herdFromPos(pos){
  if (pos <= 0) return 0;
  return Math.round(Math.pow(herdCeiling, pos / HERD_POS_MAX));
}

/* The slider carries one position; the mode decides what it selects. Two of
   the three are open-ended -- everything above the handle, or everything below
   it. The third is a band around it, and its width comes from dragging the same
   handle up and down rather than from a second one to fight over. */
function herdRange(){
  var v = herdFromPos(state.min);
  if (state.herdMode === "max") return [0, v || Infinity];
  if (state.herdMode === "band"){
    var w = state.herdWidth * HERD_POS_MAX;
    return [herdFromPos(Math.max(0, state.min - w)),
            herdFromPos(Math.min(HERD_POS_MAX, state.min + w))];
  }
  return [v, Infinity];
}

function herdLabel(pos){
  var r = herdRange();
  // At rest at either end the slider constrains nothing, whichever way it is
  // pointing, and should say so rather than quote an infinity at the reader.
  if (!isFinite(r[1]) && r[0] <= 0) return "All sites";
  if (state.herdMode === "min") return nf.format(r[0]) + " animals or more";
  if (state.herdMode === "max") return nf.format(r[1]) + " animals or fewer";
  return nf.format(r[0]) + " to " + nf.format(r[1]) + " animals";
}

/* Paint the kept part of the track, so the mode is visible without reading. */
function paintHerdBand(){
  var band = document.getElementById("herdBand");
  if (!band) return;
  var fill = band.querySelector(".fill");
  var wrap = band.parentNode;
  var lo = document.getElementById("herdEdgeLo");
  var hi = document.getElementById("herdEdgeHi");
  var at = state.min / HERD_POS_MAX * 100;

  if (state.herdMode === "min"){ fill.style.left = at + "%"; fill.style.right = "0"; }
  else if (state.herdMode === "max"){ fill.style.left = "0"; fill.style.right = (100 - at) + "%"; }
  else {
    var w = state.herdWidth * 100;
    var l = Math.max(0, at - w), r = Math.min(100, at + w);
    fill.style.left = l + "%";
    fill.style.right = (100 - r) + "%";
    // The edges sit on the same inset track the fill does, so they line up with
    // where the thumb would have been rather than with the element's full width.
    if (lo && hi){
      lo.style.left = "calc(8px + " + l + "% - " + (l / 100 * 16) + "px - 1px)";
      hi.style.left = "calc(8px + " + r + "% - " + (r / 100 * 16) + "px - 1px)";
    }
  }
  if (wrap) wrap.classList.toggle("band", state.herdMode === "band");
}

function value(row){ return state.mode === "head" ? row[HEAD] : row[DE] / 10; }
function radius(v){
  if (v <= 0) return 1.3;
  return Math.max(1.3, Math.min(20, 0.8 * Math.pow(v, 0.33)));
}
function zoomFactor(z){ return Math.max(0.22, Math.min(2.6, Math.pow(1.42, z - 11))); }

/* ---------------- map ---------------- */
function buildMap(){
map = L.map("map", {
  center: [56.15, 10.6], zoom: 7, minZoom: 6, maxZoom: 18,
  maxBounds: [[54.0, 7.0], [58.3, 16.0]], maxBoundsViscosity: 0.7,
  zoomControl: true, attributionControl: true
});
L.control.scale({metric: true, imperial: false, position: "bottomleft"}).addTo(map);
map.attributionControl.setPrefix("");
map.attributionControl.addAttribution(
  'CHR 2024 &middot; <a href="https://geodata.fvm.dk/geoserver/ows">Landbrugsstyrelsen</a>' +
  ' &middot; matrikel &amp; outlines <a href="https://dawadocs.dataforsyningen.dk/">DAWA</a>'
);

komLayer = L.geoJSON(KOM, {
  interactive: false,
  style: function(){
    return {color: state.colors.coast, weight: 0.7, fillColor: state.colors.land,
            fillOpacity: 1, opacity: 1};
  }
}).addTo(map);
// #zoom/lat/lon keeps the view in the URL, so a place can be linked to.
var fromHash = /^#(\d+(?:\.\d+)?)\/(-?\d+\.?\d*)\/(-?\d+\.?\d*)$/.exec(location.hash);
if (fromHash){
  map.setView([parseFloat(fromHash[2]), parseFloat(fromHash[3])], parseFloat(fromHash[1]));
} else {
  map.fitBounds(komLayer.getBounds(), {padding: [12, 12]});
}
map.on("moveend", function(){
  var c = map.getCenter();
  history.replaceState(null, "", "#" + map.getZoom() + "/" +
    c.lat.toFixed(5) + "/" + c.lng.toFixed(5));
});
}

function addBasemaps(){
  // Two ways to get a basemap, and which one is available depends on whether
  // there is a server in front of this page.
  //
  // With serve.py, tiles come through it and it adds the Dataforsyningen token,
  // so the browser never sees a credential -- and Klimadatastyrelsen's maps are
  // the better ones over Denmark by some margin, especially the aerial photos.
  //
  // On a static host there is nothing to hold the token, and putting it in the
  // page would hand out someone else's quota. So the fallback is OpenStreetMap,
  // which needs no key at all. Fewer layers, coarser over farmland, but a map
  // with a basemap rather than one without.
  var layers = {"Plain": L.layerGroup()};

  if (CFG && CFG.basemaps){
    var kds = 'Kort: <a href="https://dataforsyningen.dk">Klimadatastyrelsen</a>';
    layers["Topographic"] = L.tileLayer.wms("/api/wms/topo",
      {format: "image/png", version: "1.3.0", maxZoom: 18, attribution: kds});
    layers["Aerial photo"] = L.tileLayer.wms("/api/wms/orto",
      {format: "image/jpeg", version: "1.3.0", maxZoom: 18, attribution: kds});
  } else {
    layers["Map"] = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">' +
                   "OpenStreetMap</a> contributors"
    });
  }

  var plain = layers["Plain"];
  L.control.layers(layers, null, {position: "topright", collapsed: false}).addTo(map);
  plain.addTo(map);
  map.on("baselayerchange", function(ev){
    var over = ev.name !== "Plain";
    komLayer.setStyle({fillOpacity: over ? 0 : 1, opacity: over ? 0.45 : 1});
    komLayer.bringToBack();
    scheduleDraw();
  });
}

function tileKey(cx, cy){ return cx + "_" + cy; }

function decodePoly(str){
  var pts = [], i = 0, x = 0, y = 0, b, shift, result;
  while (i < str.length){
    shift = 0; result = 0;
    do { b = str.charCodeAt(i++) - 63; result |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
    x += (result & 1) ? ~(result >> 1) : (result >> 1);
    shift = 0; result = 0;
    do { b = str.charCodeAt(i++) - 63; result |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
    y += (result & 1) ? ~(result >> 1) : (result >> 1);
    pts.push([x / 1e5, y / 1e5]);
  }
  return pts;
}

function loadTile(layer, key){
  if (!layer.index || !layer.index.cells[key]) return Promise.resolve(null);
  var have = layer.cache.get(key);
  if (have && have !== "pending") return Promise.resolve(have);
  if (have === "pending" || layer.fails.has(key)) return Promise.resolve(null);
  layer.cache.set(key, "pending");
  return fetch(layer.dir + key + ".json")
    .then(function(r){ if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(function(t){
      t._hi = new Array(t.meta.length);
      t._lo = new Array(t.meta.length);
      layer.cache.set(key, t);
      updateTileCounter();
      scheduleDraw();
      return t;
    })
    .catch(function(){ layer.cache.delete(key); layer.fails.add(key); return null; });
}

function ringOf(tile, i, hi){
  var cache = hi ? tile._hi : tile._lo;
  if (!cache[i]) cache[i] = decodePoly(hi ? tile.hi[i] : tile.lo[i]);
  return cache[i];
}

function cellRange(layer, bounds){
  if (!layer.index) return [];
  var keys = [];
  var x0 = Math.floor(bounds.getWest() / layer.index.cellLon);
  var x1 = Math.floor(bounds.getEast() / layer.index.cellLon);
  var y0 = Math.floor(bounds.getSouth() / layer.index.cellLat);
  var y1 = Math.floor(bounds.getNorth() / layer.index.cellLat);
  for (var x = x0; x <= x1; x++){
    for (var y = y0; y <= y1; y++){
      var k = tileKey(x, y);
      if (layer.index.cells[k]) keys.push(k);
    }
  }
  return keys;
}

function pointInRing(ring, lon, lat){
  var inside = false;
  for (var i = 0, j = ring.length - 1; i < ring.length; j = i++){
    var xi = ring[i][0], yi = ring[i][1], xj = ring[j][0], yj = ring[j][1];
    if ((yi > lat) !== (yj > lat) &&
        lon < (xj - xi) * (lat - yi) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

function searchLayer(layer, lon, lat){
  if (!layer.index) return Promise.resolve(null);
  var cx = Math.floor(lon / layer.index.cellLon), cy = Math.floor(lat / layer.index.cellLat);
  var wanted = [];
  for (var dx = -1; dx <= 1; dx++){
    for (var dy = -1; dy <= 1; dy++){
      var k = tileKey(cx + dx, cy + dy);
      if (layer.index.cells[k]) wanted.push(k);
    }
  }
  return Promise.all(wanted.map(function(k){ return loadTile(layer, k); })).then(function(loaded){
    for (var t = 0; t < loaded.length; t++){
      var tile = loaded[t];
      if (!tile) continue;
      for (var i = 0; i < tile.meta.length; i++){
        if (pointInRing(ringOf(tile, i, true), lon, lat)) return {tile: tile, i: i};
      }
    }
    return null;
  });
}

/* Resolved against full-detail geometry at any zoom, fetching the cell if the
   viewer has never been close enough for it to have loaded. Livestock parcels
   win ties, so a click on a farm gives the farm rather than a neighbour. */
function parcelAt(latlng){
  var lon = latlng.lng, lat = latlng.lat;
  return searchLayer(LAYERS.farm, lon, lat).then(function(found){
    if (found || !state.showAll) return found;
    return searchLayer(LAYERS.all, lon, lat);
  });
}

function updateTileCounter(){
  var el = document.getElementById("parcelCount");
  if (!el) return;
  var n = 0;
  [LAYERS.farm, LAYERS.all].forEach(function(layer){
    layer.cache.forEach(function(t){ if (t && t !== "pending") n += t.meta.length; });
  });
  el.textContent = nf.format(n);
}

/* ---------------- canvas ---------------- */
function sizeCanvas(){
  var size = map.getSize();
  var dpr = window.devicePixelRatio || 1;
  canvas.width = size.x * dpr;
  canvas.height = size.y * dpr;
  canvas.style.width = size.x + "px";
  canvas.style.height = size.y + "px";
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

/* Direct web-mercator projection — far cheaper than a call per vertex. */
var proj = {scale: 0, ox: 0, oy: 0};
function refreshProjection(){
  var b = map.getPixelBounds();
  proj.scale = 256 * Math.pow(2, map.getZoom());
  proj.ox = b.min.x;
  proj.oy = b.min.y;
}
function px(lon){ return (lon + 180) / 360 * proj.scale - proj.ox; }
function py(lat){
  var s = Math.sin(lat * Math.PI / 180);
  return (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * proj.scale - proj.oy;
}

function collectByProperty(layer, bounds){
  var byProp = new Map();
  var keys = cellRange(layer, bounds);
  for (var k = 0; k < keys.length; k++){
    var tile = layer.cache.get(keys[k]);
    if (!tile || tile === "pending"){ loadTile(layer, keys[k]); continue; }
    for (var i = 0; i < tile.meta.length; i++){
      var sfe = tile.meta[i][3];
      var arr = byProp.get(sfe);
      if (!arr){ arr = []; byProp.set(sfe, arr); }
      arr.push([tile, i]);
    }
  }
  return byProp;
}

function paintProperties(byProp, hi, fill, line){
  ctx.fillStyle = fill;
  ctx.strokeStyle = line;
  byProp.forEach(function(items){
    // One path per property: shared edges vanish on fill, so at the coarse level
    // a holding reads as a single shape rather than a mesh of lines.
    var path = new Path2D();
    for (var n = 0; n < items.length; n++){
      var ring = ringOf(items[n][0], items[n][1], hi);
      for (var j = 0; j < ring.length; j++){
        var X = px(ring[j][0]), Y = py(ring[j][1]);
        if (j === 0) path.moveTo(X, Y); else path.lineTo(X, Y);
      }
      path.closePath();
    }
    if (fill) ctx.fill(path);
    if (hi && line) ctx.stroke(path);
  });
}

function drawParcels(zoom){
  if (zoom < Z_PARCEL) return;
  var hi = zoom >= Z_SHARP;
  var bounds = map.getBounds().pad(0.15);
  ctx.lineWidth = zoom >= 15 ? 1.1 : 0.8;

  // Everyone else first and fainter, so the livestock holdings stay legible.
  if (state.showAll && LAYERS.all.index){
    paintProperties(collectByProperty(LAYERS.all, bounds), hi,
                    hi ? "" : state.colors.otherFill, state.colors.otherLine);
  }
  paintProperties(collectByProperty(LAYERS.farm, bounds), hi,
                  hi ? state.colors.parcelFill : state.colors.parcelFillStrong,
                  state.colors.parcelLine);

  var sel = state.selected && state.selected.parcel;
  if (sel){
    var ring = ringOf(sel.tile, sel.i, true);
    var sp = new Path2D();
    for (var m = 0; m < ring.length; m++){
      var sx = px(ring[m][0]), sy = py(ring[m][1]);
      if (m === 0) sp.moveTo(sx, sy); else sp.lineTo(sx, sy);
    }
    sp.closePath();
    ctx.lineWidth = 2;
    ctx.strokeStyle = state.colors.selected;
    ctx.stroke(sp);
  }
}

function draw(){
  frame = null;
  L.DomUtil.setPosition(canvas, map.containerPointToLayerPoint([0, 0]));
  var size = map.getSize();
  ctx.clearRect(0, 0, size.x, size.y);
  refreshProjection();

  var zoom = map.getZoom();
  drawParcels(zoom);

  var b = map.getBounds().pad(0.12);
  var south = b.getSouth(), north = b.getNorth(), west = b.getWest(), east = b.getEast();
  var zf = zoomFactor(zoom);
  var alpha = Math.max(0.4, Math.min(0.72, 0.4 + (zoom - 7) * 0.06));
  var surface = state.colors.surface;

  for (var i = 0; i < state.filtered.length; i++){
    var row = state.filtered[i];
    var lat = row[LAT] / 1e5, lon = row[LON] / 1e5;
    if (lat < south || lat > north || lon < west || lon > east) continue;
    var X = px(lon), Y = py(lat);
    var r = Math.max(0.6, radius(value(row)) * zf);
    ctx.beginPath();
    ctx.arc(X, Y, r, 0, 6.283185);
    ctx.globalAlpha = alpha;
    ctx.fillStyle = state.colors[GROUP_VAR[row[GRP]]];
    ctx.fill();
    if (r > 3.6){
      ctx.globalAlpha = 0.9;
      ctx.lineWidth = 1;
      ctx.strokeStyle = surface;
      ctx.stroke();
    }
  }
  ctx.globalAlpha = 1;

  if (state.selected && state.selected.row){
    var sr = state.selected.row;
    ctx.beginPath();
    ctx.arc(px(sr[LON] / 1e5), py(sr[LAT] / 1e5),
            Math.max(6, radius(value(sr)) * zf + 4), 0, 6.283185);
    ctx.lineWidth = 2;
    ctx.strokeStyle = state.colors.selected;
    ctx.stroke();
  }
}
function scheduleDraw(){ if (frame == null) frame = requestAnimationFrame(draw); }

function buildCanvas(){
map.createPane("farms");
farmPane = map.getPane("farms");
farmPane.style.zIndex = 450;
farmPane.style.pointerEvents = "none";
canvas = L.DomUtil.create("canvas", "leaflet-zoom-hide");
canvas.style.position = "absolute";
farmPane.appendChild(canvas);
ctx = canvas.getContext("2d");

map.on("move zoom", scheduleDraw);
map.on("resize", function(){ sizeCanvas(); scheduleDraw(); });
map.on("zoomend", buildLegend);
sizeCanvas();
}

/* ---------------- filtering ---------------- */
function applyFilter(){
  var range = herdRange();
  var out = [];
  for (var i = 0; i < S.length; i++){
    var row = S[i];
    if (state.group >= 0 && row[GRP] !== state.group) continue;
    if (row[HEAD] < range[0] || row[HEAD] > range[1]) continue;
    out.push(row);
  }
  out.sort(function(a, b){ return value(b) - value(a); });
  state.filtered = out;
  updateStats();
  scheduleDraw();
}

/* ---------------- sidebar ---------------- */
function groupCounts(){
  var counts = new Array(D.groups.length).fill(0);
  for (var i = 0; i < S.length; i++) counts[S[i][GRP]]++;
  return counts;
}
function buildSpeciesList(){
  var host = document.getElementById("speciesList");
  var counts = groupCounts();
  var rows = [{name: "All registered sites", idx: -1, n: S.length, colorVar: null}];
  D.groups.forEach(function(g, i){
    rows.push({name: g[1], idx: i, n: counts[i], colorVar: GROUP_VAR[i]});
  });
  rows.forEach(function(r){
    var b = document.createElement("button");
    b.type = "button";
    b.className = "sp";
    b.setAttribute("aria-pressed", r.idx === state.group ? "true" : "false");
    b.dataset.idx = r.idx;
    var chip = r.colorVar
      ? '<span class="chip" style="background:var(' + r.colorVar + ')"></span>'
      : '<span class="chip" style="background:linear-gradient(135deg,var(--s1) 0 34%,' +
        'var(--s2) 34% 67%,var(--s3) 67% 100%)"></span>';
    b.innerHTML = chip + '<span class="nm"></span><span class="ct">' + nf.format(r.n) + "</span>";
    b.querySelector(".nm").textContent = r.name;
    b.addEventListener("click", function(){
      state.group = r.idx;
      host.querySelectorAll(".sp").forEach(function(el){
        el.setAttribute("aria-pressed", Number(el.dataset.idx) === state.group ? "true" : "false");
      });
      // The slider spans the selected species' own range, so changing species
      // rescales it. The handle keeps its position, which keeps its meaning:
      // two thirds along is two thirds of the way up whatever is shown now.
      herdScale();
      document.getElementById("minHerdLabel").textContent = herdLabel(state.min);
      applyFilter();
    });
    host.appendChild(b);
  });
}

function compact(v){
  if (v >= 1e6) return (v / 1e6).toFixed(v >= 1e7 ? 0 : 1) + "m";
  if (v >= 1e4) return Math.round(v / 1e3) + "k";
  return nf.format(v);
}
function updateStats(){
  var sites = state.filtered.length, head = 0, de = 0, land = 0, prop = 0;
  var seen = new Set(), seenProp = new Set();
  var perKom = new Map();
  for (var i = 0; i < state.filtered.length; i++){
    var row = state.filtered[i];
    head += row[HEAD];
    de += row[DE] / 10;
    if (row[CVR] && !seen.has(row[CVR])){ seen.add(row[CVR]); land += row[HA]; }
    if (row[PROP_HA] && row[SFE] && !seenProp.has(row[SFE])){
      seenProp.add(row[SFE]);
      prop += row[PROP_HA];
    }
    var cur = perKom.get(row[KOM_I]) || [0, 0, 0];
    cur[0] += row[HEAD]; cur[1] += row[DE] / 10; cur[2] += 1;
    perKom.set(row[KOM_I], cur);
  }
  document.getElementById("stSites").textContent = nf.format(sites);
  document.getElementById("stHead").textContent = compact(head);
  document.getElementById("stDE").textContent = compact(Math.round(de));
  document.getElementById("stLand").textContent = land ? compact(Math.round(land)) : "—";
  var pe = document.getElementById("stProp");
  if (pe) pe.textContent = prop ? compact(Math.round(prop)) : "—";
  document.getElementById("shownCount").textContent = nf.format(sites) + " shown";


  var metric = state.mode === "head" ? 0 : 1;
  var list = Array.from(perKom.entries());
  if (list.reduce(function(a, e){ return a + e[1][metric]; }, 0) === 0) metric = 2;
  list.sort(function(a, b){ return b[1][metric] - a[1][metric]; });
  var top = list.slice(0, 8);
  var max = top.length ? top[0][1][metric] : 1;
  var host = document.getElementById("komList");
  host.textContent = "";
  top.forEach(function(entry){
    var b = document.createElement("button");
    b.type = "button";
    b.className = "kom";
    var pct = max ? Math.max(2, entry[1][metric] / max * 100) : 0;
    b.innerHTML = '<div class="barfill" style="width:' + pct.toFixed(1) + '%"></div>' +
      '<span></span><span class="v">' +
      (metric === 2 ? nf.format(entry[1][2]) + " sites" : compact(Math.round(entry[1][metric]))) +
      "</span>";
    b.querySelector("span").textContent = D.kommuner[entry[0]] || "—";
    b.addEventListener("click", function(){ zoomToKommune(entry[0]); });
    host.appendChild(b);
  });
}
function zoomToKommune(idx){
  var name = D.kommuner[idx], bounds = null;
  komLayer.eachLayer(function(l){ if (l.feature.properties.navn === name) bounds = l.getBounds(); });
  if (bounds) map.fitBounds(bounds, {padding: [20, 20]});
}

/* ---------------- controls ---------------- */
document.getElementById("sizeMode").addEventListener("click", function(ev){
  var b = ev.target.closest("button");
  if (!b) return;
  state.mode = b.dataset.mode;
  this.querySelectorAll("button").forEach(function(el){
    el.setAttribute("aria-pressed", el.dataset.mode === state.mode ? "true" : "false");
  });
  document.getElementById("sizeNote").textContent = state.mode === "head"
    ? "Area is proportional to the herd recorded at the site."
    : "Animal units weight species by manure output. Horses and hobby holdings sit at zero.";
  document.getElementById("legendTitle").textContent = state.mode === "head"
    ? "Animals per site" : "Animal units per site";
  applyFilter();
  buildLegend();
});
var minHerd = document.getElementById("minHerd");
minHerd.addEventListener("input", function(){
  state.min = Number(this.value);
  document.getElementById("minHerdLabel").textContent = herdLabel(state.min);
  applyFilter();
});
telescope(minHerd, {near: 0.25, gamma: 3});

/* ---------------- legend ---------------- */
function buildLegend(){
  var vals = state.mode === "head" ? [10, 1000, 100000] : [10, 100, 1000];
  var zf = zoomFactor(map.getZoom());
  var host = document.getElementById("legendSizes");
  host.textContent = "";
  vals.forEach(function(v){
    var d = Math.max(4, radius(v) * zf * 2);
    var wrap = document.createElement("div");
    wrap.className = "sz";
    wrap.innerHTML = '<div class="dot" style="width:' + d.toFixed(1) + "px;height:" +
      d.toFixed(1) + 'px"></div><div class="cap">' + compact(v) + "</div>";
    host.appendChild(wrap);
  });
  var note = document.getElementById("lodNote");
  if (note){
    var z = map.getZoom();
    note.textContent = z < Z_PARCEL
      ? "Zoom in for matrikel boundaries"
      : (z < Z_SHARP ? "Matrikler merged by property" : "Full matrikel detail");
  }
}

/* ---------------- selection & info panel ---------------- */
function esc(s){
  return String(s == null ? "" : s).replace(/[&<>"]/g, function(c){
    return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c];
  });
}
function nearestSite(containerPoint){
  var best = null, bestD = Infinity;
  var zf = zoomFactor(map.getZoom());
  for (var i = state.filtered.length - 1; i >= 0; i--){
    var row = state.filtered[i];
    var dx = px(row[LON] / 1e5) - containerPoint.x;
    var dy = py(row[LAT] / 1e5) - containerPoint.y;
    var d = Math.sqrt(dx * dx + dy * dy);
    var hit = Math.max(9, radius(value(row)) * zf);
    if (d <= hit && d < bestD){ bestD = d; best = row; }
  }
  return best;
}
function row2(label, val){
  return '<div class="pr"><span>' + esc(label) + "</span><b>" + val + "</b></div>";
}
function openPanel(row, parcel){
  state.selected = {row: row, parcel: parcel};
  var body = document.getElementById("panelBody");
  var html = "";
  var herds = row ? (byChr.get(row[CHR]) || [row]) : [];

  if (row){
    document.getElementById("panelTitle").textContent =
      D.species[row[SPC]] + (herds.length > 1 ? " +" + (herds.length - 1) : "");
    document.getElementById("panelSub").textContent =
      "CHR " + row[CHR] + " · " + (D.kommuner[row[KOM_I]] || "") + (row[POST] ? " " + row[POST] : "");
  } else {
    document.getElementById("panelTitle").textContent = "Cadastral parcel";
    document.getElementById("panelSub").textContent = "no livestock registered here";
  }

  if (herds.length){
    html += '<section><h4>Herds at this site</h4>';
    herds.forEach(function(h){
      html += '<div class="herd"><div class="hh">' + esc(D.species[h[SPC]]) + "</div>";
      if (h[VIRK] >= 0) html += '<div class="hs">' + esc(D.virkart[h[VIRK]]) + "</div>";
      (h[CATS] || []).forEach(function(c){ html += row2(D.labels[c[0]], nf.format(c[1])); });
      html += row2("Animals", nf.format(h[HEAD]));
      if (h[DE]) html += row2("Animal units (DE)", (h[DE] / 10).toFixed(1));
      if (h[BRUG] >= 0) html += row2("Purpose", esc(D.brug[h[BRUG]]));
      html += "</div>";
    });
    html += "</section>";
  }

  if (parcel || (row && row[MAT])){
    html += '<section><h4>Cadastre</h4>';
    if (parcel){
      var m = parcel.tile.meta[parcel.i];
      html += row2("Matrikel", esc(m[0]));
      html += row2("Ejerlav", esc(parcel.tile.ejerlav[m[1]]));
      html += row2("Parcel area", (m[2] / 10000).toFixed(2) + " ha");
      if (m[3]) html += row2("Property (BFE)", m[3]);
    } else {
      html += row2("Matrikel", esc(row[MAT]));
      if (row[LAV] >= 0) html += row2("Ejerlav", esc(D.ejerlav[row[LAV]]));
      if (row[PAR_HA]) html += row2("Parcel area", row[PAR_HA].toFixed(2) + " ha");
      if (row[SFE]) html += row2("Property (BFE)", row[SFE]);
    }
    if (row && row[PROP_HA]){
      html += row2("Property area", nf.format(row[PROP_HA]) + " ha");
      html += row2("Parcels in property", row[PROP_N]);
    }
    html += "</section>";
  }

  // The company lives in a shard that has to be fetched, so the panel opens
  // on what is already in memory and this fills in a moment later.
  if (row && row[CVR]) html += '<section id="companySlot"></section>';

  if (row && (row[CVR] || row[HA])){
    html += '<section><h4>Operation</h4>';
    if (row[CVR]) html += row2("CVR", row[CVR]);
    if (row[HA]) html += row2("Land declared", nf.format(row[HA]) + " ha");
    if (row[LPARC]) html += row2("Fields declared", nf.format(row[LPARC]));
    if (row[CROP] >= 0) html += row2("Largest crop", esc(D.crops[row[CROP]]));
    html += "</section>";
  }

  if (row){
    html += '<section><h4>Location</h4>' +
      row2("Coordinates", (row[LAT] / 1e5).toFixed(5) + ", " + (row[LON] / 1e5).toFixed(5)) +
      row2("Municipality", esc(D.kommuner[row[KOM_I]] || "—")) +
      (row[POST] ? row2("Postcode", row[POST]) : "") + "</section>";
  }

  if (row && row[CVR] && CFG && CFG.enrich) html += liveSection();

  html += '<p class="pfoot">Herds from CHR, 1 June 2024. Cadastre from DAWA. Declared ' +
          'land from Marker 2025. Company, people and accounts from CVR and ' +
          'Erhvervsstyrelsen. No register here says who owns the land.</p>';
  body.innerHTML = html;
  document.getElementById("panel").classList.add("open");

  if (row && row[CVR]){
    var opened = state.selected;
    company(row[CVR]).then(function(c){
      // A quick second click can land while this is in flight; only paint if
      // the panel still belongs to the farm that asked.
      if (state.selected !== opened) return;
      var slot = document.getElementById("companySlot");
      if (slot) slot.outerHTML = companySections(c) || "";
      if (CFG && CFG.enrich) startLive(row, c);
    });
  }
  scheduleDraw();
}
function closePanel(){
  state.selected = null;
  closeStream();
  document.getElementById("panel").classList.remove("open");
  scheduleDraw();
}


/* ---------------- company register, accounts, live web ---------------- */

/* The company table is string-pooled -- a row holds indexes into shared industry,
   form and role lists rather than repeating those strings 16,000 times -- and
   sharded, so a click fetches about twenty kilobytes instead of the six and a
   half megabytes the whole table weighs. The pools ride in the store's index and
   are loaded once; this turns one row back into something readable. */
var CF = {NAME:0, FORM:1, IND:2, CITY:3, KOM:4, START:5, STATUS:6, EMP:7,
          PURPOSE:8, CAPITAL:9, PEOPLE:10, OWNERS:11, UNITS:12, PHONE:13,
          EMAIL:14, ACCOUNTS:15};

function inflate(row){
  if (!row) return null;
  var m = CVRMETA || {};
  function pool(list, i){
    return (list && i >= 0 && list[i] != null) ? list[i] : null;
  }
  return {
    name: row[CF.NAME],
    form: pool(m.forms, row[CF.FORM]),
    industry: pool(m.industries, row[CF.IND]),
    city: row[CF.CITY], kommune: row[CF.KOM], start: row[CF.START],
    status: row[CF.STATUS], employees: row[CF.EMP], purpose: row[CF.PURPOSE],
    capital: row[CF.CAPITAL],
    people: (row[CF.PEOPLE] || []).map(function(p){
      return {name: p[0], role: pool(m.roles, p[1])};
    }),
    owners: row[CF.OWNERS] || [],
    units: row[CF.UNITS] || [],
    phone: row[CF.PHONE], email: row[CF.EMAIL],
    accounts: row[CF.ACCOUNTS] || null
  };
}

/* One company and its filed accounts, from the sharded store. Both come from
   the same shard neighbourhood and are asked for together, so a click costs two
   small files rather than one enormous one. */
function company(cvr){
  if (!STORE || !cvr) return Promise.resolve(null);
  return Promise.all([
    STORE.get("cvr", String(cvr)),
    STORE.get("financials", String(cvr)).catch(function(){ return null; })
  ]).then(function(r){
    var c = inflate(r[0]);
    if (c && r[1]) c.accounts = r[1];      // the store has more years than the row
    return c;
  }).catch(function(){ return null; });
}

function danishYear(iso){ return iso ? String(iso).slice(0, 4) : "—"; }

/* Accounts are in kroner and run to eight figures, which is unreadable in a
   330px panel.  Millions to one decimal keeps the magnitude and the sign, and
   thousands below that; nothing is rounded away that the eye would have used. */
function kr(v){
  if (v == null) return "—";
  var a = Math.abs(v);
  // Selection totals reach the tens of billions once the co-operatives are in,
  // so the ladder has to go further than a single farm's accounts need.
  if (a >= 1e9) return (v / 1e9).toFixed(a >= 1e10 ? 0 : 1) + "bn";
  if (a >= 1e6) return (v / 1e6).toFixed(a >= 1e8 ? 0 : 1) + "m";
  if (a >= 1e4) return Math.round(v / 1e3) + "k";
  return nf.format(Math.round(v));
}

var ACCT_ROWS = [
  ["revenue", "Revenue", false],
  ["gross", "Gross result", false],
  ["staff_cost", "Staff cost", false],
  ["ebit", "Operating result", true],
  ["profit", "Profit after tax", true],
  ["equity", "Equity", true],
  ["assets", "Balance sheet total", false],
  ["land_bldg", "Land and buildings", false],
  ["biological", "Livestock at book value", false],
  ["lt_debt", "Long-term debt", false],
  ["employees", "Employees", false]
];

function accountsTable(years){
  var cols = years.slice(0, 3);
  var present = ACCT_ROWS.filter(function(r){
    return cols.some(function(y){ return y[r[0]] != null; });
  });
  if (!present.length) return "";
  var html = '<table class="acct"><thead><tr><th>Figures in DKK</th>';
  cols.forEach(function(y){ html += "<th>" + danishYear(y.end) + "</th>"; });
  html += "</tr></thead><tbody>";
  present.forEach(function(r){
    var neg = cols.some(function(y){ return y[r[0]] < 0; });
    var cls = (r[2] ? "rule " : "") + (neg ? "neg" : "");
    html += (cls ? '<tr class="' + cls.trim() + '">' : "<tr>") +
            "<td>" + esc(r[1]) + "</td>";
    cols.forEach(function(y){
      var v = y[r[0]];
      html += "<td>" + (v == null ? "—"
                        : (r[0] === "employees" ? nf.format(v) : kr(v))) + "</td>";
    });
    html += "</tr>";
  });
  html += "</tbody></table>";
  var doc = cols[0].doc;
  if (doc) html += '<p class="pnote"><a href="' + esc(doc) +
    '" target="_blank" rel="noopener">The filed annual report for ' +
    danishYear(cols[0].end) + "</a>, from Erhvervsstyrelsen.</p>";
  return html;
}

function companySections(c){
  if (!c) return "";
  var html = '<section><h4>The company</h4>';
  html += '<div class="ptext"><b>' + esc(c.name) + "</b>" +
          (c.form ? " · " + esc(c.form) : "") + "</div>";
  if (c.industry) html += row2("Industry", esc(c.industry));
  if (c.status) html += row2("Status", esc(c.status));
  if (c.start) html += row2("Registered", esc(c.start));
  if (c.employees != null) html += row2("Employees",
    typeof c.employees === "number" ? nf.format(c.employees) : esc(c.employees));
  if (c.capital) html += row2("Capital", esc(c.capital));
  if (c.units && c.units.length > 1)
    html += row2("Production units", nf.format(c.units.length));
  if (c.purpose) html += '<p class="pnote">' + esc(c.purpose) + "</p>";
  html += "</section>";

  if (c.people.length || c.owners.length){
    html += '<section><h4>Behind the company</h4><div class="people">';
    c.people.forEach(function(p){
      html += '<div class="person"><b>' + esc(p.name) + "</b><span>" +
              esc(p.role || "") + "</span></div>";
    });
    c.owners.forEach(function(o){
      html += '<div class="person"><b>' + esc(o[0]) + "</b><span>" +
              (o[1] ? esc(o[1]) : "owner") + "</span></div>";
    });
    html += '</div><p class="pnote">Names and roles as registered in CVR. ' +
            'This is the company, not the land &mdash; who owns the ' +
            'parcels is in Ejerfortegnelsen, which needs a login.</p></section>';
  }

  if (c.accounts && c.accounts.length){
    html += '<section><h4>Filed accounts</h4>' + accountsTable(c.accounts) + "</section>";
  }
  return html;
}

/* The bulk company table covers whatever fetch_cvr.py has reached. For anything
   it has not, the live stream carries the same record from the same register, so
   the panel renders that instead rather than showing a farm with no company. */
function liveCompanyHtml(d){
  if (!d || d.error || !d.name) return "";
  var html = '<div class="ptext"><b>' + esc(d.name) + "</b>" +
             (d.form ? " · " + esc(d.form) : "") + "</div>";
  if (d.industry) html += row2("Industry", esc(d.industry));
  if (d.status) html += row2("Status", esc(d.status));
  if (d.start) html += row2("Registered", esc(d.start));
  if (d.city) html += row2("Address",
    esc(((d.address ? d.address + ", " : "") + d.city).replace(/\s+/g, " ")));
  var emp = d.employees || d.employees_q;
  if (emp && emp.staff) html += row2("Employees", esc(emp.staff) +
    (emp.period ? ' <span style="color:var(--muted)">' + esc(emp.period) + "</span>" : ""));
  if (d.capital) html += row2("Capital", esc(d.capital));
  var people = (d.people || []).concat(
    (d.legal_owners || []).map(function(o){ return {name: o.name, role: "owner"}; }));
  if (people.length){
    html += '<div class="people" style="margin-top:8px">';
    people.slice(0, 10).forEach(function(p){
      html += '<div class="person"><b>' + esc(p.name) + "</b><span>" +
              esc(p.role || "") + "</span></div>";
    });
    html += "</div>";
  }
  if (d.purpose) html += '<p class="pnote">' + esc(d.purpose) + "</p>";
  return html;
}

/* ---- live lookup ------------------------------------------------------
   Everything above is already in the browser.  This part is not: it goes out
   to the registers and the web at the moment of the click, and each answer is
   written into the panel as it lands.  A new click closes the old stream, so a
   viewer clicking down a row of farms does not leave ten of them running. */

function closeStream(){
  if (liveStream){ liveStream.close(); liveStream = null; }
}

function liveSection(){
  return '<section id="liveCompany"></section>' +
         '<section id="liveSec"><div class="livehead"><h4>From the web</h4>' +
         '<span class="spin" id="liveSpin"></span></div>' +
         '<div id="liveBody"><p class="pnote" id="liveStatus">Looking…</p></div>' +
         "</section>";
}

function hit(item){
  var tag = item.kind && item.kind !== "web"
    ? '<span class="tag ' + esc(item.kind) + '">' + esc(item.kind) + "</span>" : "";
  var host = "";
  try { host = new URL(item.url).hostname.replace(/^www\./, ""); } catch (e) { host = item.url; }
  return '<a class="hit" href="' + esc(item.url) + '" target="_blank" rel="noopener">' +
         '<div class="ht">' + tag + esc(item.title || host) + "</div>" +
         '<div class="hu">' + esc(host) + "</div>" +
         (item.excerpt || item.snippet || item.description
           ? '<div class="hx">' + esc((item.excerpt || item.snippet ||
                                       item.description).slice(0, 240)) + "</div>"
           : "") + "</a>";
}

function startLive(row, c){
  closeStream();
  var cvr = row && row[CVR];
  var chrNo = row && row[CHR];
  if (!cvr && !chrNo) return;
  var q = new URLSearchParams();
  if (cvr) q.set("cvr", cvr);
  if (chrNo) q.set("chr", chrNo);
  if (c && c.name) q.set("name", c.name);
  if (c && c.city) q.set("city", c.city);
  if (row && row[SFE]) q.set("bfe", row[SFE]);

  var sec = document.getElementById("liveSec");
  if (!sec) return;
  var body = document.getElementById("liveBody");
  var spin = document.getElementById("liveSpin");
  var status = document.getElementById("liveStatus");

  // Results are keyed by URL because the same page arrives twice: once as a
  // search snippet, then again as fetched text.  The second is better, so it
  // replaces the first in place rather than appearing under it.
  var found = new Map(), links = [], note = "";
  var haveBulk = !!c;   // the store already answered; do not draw it twice

  function paint(){
    var items = Array.from(found.values());
    body.innerHTML =
      (note ? '<p class="pnote">' + note + "</p>" : "") +
      items.map(hit).join("") +
      (links.length ? "<h4 style='margin-top:14px'>Look it up</h4>" +
                      links.map(hit).join("") : "");
  }
  function stop(){
    if (spin && spin.parentNode) spin.remove();
    closeStream();
  }

  var es = new EventSource("/api/enrich?" + q.toString());
  liveStream = es;

  es.addEventListener("stage", function(ev){
    var d = JSON.parse(ev.data);
    if (status && status.parentNode) status.textContent =
      d.doing === "registers" ? "Asking the registers…"
      : d.doing === "search" ? "Searching the web…"
      : "Reading what it found…";
  });

  es.addEventListener("company", function(ev){
    if (haveBulk) return;              // already drawn from the shipped table
    var html = liveCompanyHtml(JSON.parse(ev.data));
    if (!html) return;
    var slot = document.getElementById("liveCompany");
    if (slot){ slot.innerHTML = "<h4>The company</h4>" + html; }
  });

  es.addEventListener("accounts", function(ev){
    if (haveBulk) return;
    var reps = (JSON.parse(ev.data).reports || []).filter(function(r){ return r.end; });
    if (!reps.length) return;
    var slot = document.getElementById("liveCompany");
    if (!slot) return;
    // Only the index is live here; the parsed figures are a build-time job, so
    // this links the filings rather than pretending to have read them.
    slot.innerHTML += "<h4 style='margin-top:12px'>Filed accounts</h4>" +
      reps.slice(0, 4).map(function(r){
        return row2(danishYear(r.end), r.doc
          ? '<a href="' + esc(r.doc) + '" target="_blank" rel="noopener">annual report</a>'
          : esc(r.type || "filed"));
      }).join("");
  });

  es.addEventListener("links", function(ev){
    links = JSON.parse(ev.data).links || [];
    paint();
  });

  es.addEventListener("search", function(ev){
    var d = JSON.parse(ev.data);
    (d.results || []).forEach(function(r){ found.set(r.url, r); });
    var keyed = (d.providers && d.providers.keyed) || [];
    // Three different situations, and they are worth telling apart: a real
    // search ran, only the reference sources answered, or nothing did.
    if (!found.size){
      note = keyed.length
        ? "The search engines returned nothing for this name."
        : "No web search answered. The registers above are the whole of what " +
          "is known here.";
    } else if (!d.engine){
      note = "No web search engine answered &mdash; these are reference " +
             "sources only. A search API key would widen this.";
    }
    paint();
  });

  es.addEventListener("page", function(ev){
    var d = JSON.parse(ev.data);
    if (d.error) return;
    var prior = found.get(d.url) || {};
    // Keep the search engine's title if the page itself has none worth showing.
    found.set(d.url, {url: d.url, kind: d.kind || prior.kind,
                      title: d.title || prior.title,
                      excerpt: d.excerpt || d.description || prior.snippet});
    paint();
  });

  es.addEventListener("done", function(){ stop(); paint(); });
  es.addEventListener("error", function(){ stop(); });
  es.onerror = function(){ stop(); };
}


/* ---------------- telescoping sliders ----------------
   A range input moves the same amount of value per pixel wherever you click,
   which is wrong when the interesting part is a thousandth of the range. This
   makes the value-per-pixel depend on how far the click is from the handle:
   far away it behaves exactly like an ordinary slider, and close in it
   magnifies, so the last few pixels either side of the handle resolve detail
   the whole track could not otherwise reach.

   The two regimes are joined at `near` and match exactly there, so there is no
   jump as you cross the boundary -- the response just gets progressively finer
   as you approach the handle. */
function telescope(el, opts){
  if (!el) return;
  opts = opts || {};
  var near = opts.near || 0.25;      // the fraction of the track that magnifies
  var gamma = opts.gamma || 3;       // how hard it magnifies inside that
  var floor = opts.floor || 0.02;    // finest gain on a drag: 50x, never zero
  // Dragging up and down is a second axis for the same thumb, which is how a
  // range gets a width without a second handle to fight over.
  var onVertical = opts.onVertical || null;
  var vScale = opts.vScale || 160;   // pixels for the full width of the range
  /* What counts as "on the handle". Grabbing the handle should move it with the
     pointer rather than snapping its value to where the pointer happens to be,
     which is what every other draggable thing does. A caller can widen this --
     in band mode the whole interval is the handle, so grabbing anywhere inside
     it picks the band up where you took hold of it. */
  var grab = opts.grab || function(frac){ return Math.abs(frac - handleFraction()) < 0.02; };
  var dragging = false, grabbed = false, grabOffset = 0, lastX = 0, lastY = 0;
  var moved = false, downX = 0;

  function geom(){
    var r = el.getBoundingClientRect();
    // The thumb is inset by half its width at each end, so the usable track is
    // shorter than the element and the handle never reaches the very edge.
    var thumb = opts.thumb || 16;
    return {left: r.left + thumb / 2, width: Math.max(1, r.width - thumb)};
  }

  function span(){ return (Number(el.max) || 100) - (Number(el.min) || 0); }

  function handleFraction(){
    var min = Number(el.min) || 0;
    return (Number(el.value) - min) / Math.max(1e-9, span());
  }

  function pointerFraction(clientX){
    var g = geom();
    return Math.max(0, Math.min(1, (clientX - g.left) / g.width));
  }

  function commit(v){
    var min = Number(el.min) || 0, max = Number(el.max) || 100;
    var step = Number(el.step) || 1;
    v = Math.max(min, Math.min(max, Math.round(v / step) * step));
    if (String(v) === el.value) return;
    el.value = v;
    el.dispatchEvent(new Event("input", {bubbles: true}));
  }

  /* A click is absolute: it jumps by an amount that falls away cubically as the
     click approaches the handle. Far out it lands exactly where an ordinary
     slider would -- the two branches meet at `near` -- and close in it nudges by
     a fraction of a step, which is the only way to separate one holding in ten
     thousand from the next. */
  function click(clientX){
    var d = pointerFraction(clientX) - handleFraction();
    var mag = Math.abs(d);
    var delta = mag >= near
      ? d * span()
      : Math.sign(d) * Math.pow(mag / near, gamma) * near * span();
    commit(Number(el.value) + delta);
  }

  /* A drag follows the cursor exactly, like any other slider. Magnifying a drag
     as well was a mistake: the thumb then lags behind the finger holding it,
     which reads as a broken control however principled the curve behind it is.
     Precision comes from clicking, which is a discrete act with nothing to lag.
     The vertical axis still applies, since that is a separate gesture. */
  function drag(clientX, clientY){
    if (onVertical && clientY != null){
      // Up widens, down narrows -- screen coordinates run the other way.
      var dy = (lastY - clientY) / vScale;
      if (dy) onVertical(dy);
      lastY = clientY;
    }
    var min = Number(el.min) || 0;
    // A grabbed handle keeps the point you took hold of under the pointer; an
    // ungrabbed drag is the ordinary "value follows cursor".
    var frac = pointerFraction(clientX) - (grabbed ? grabOffset : 0);
    commit(min + Math.max(0, Math.min(1, frac)) * span());
    lastX = clientX;
  }

  el.addEventListener("pointerdown", function(ev){
    if (el.disabled) return;
    dragging = true;
    lastX = ev.clientX;
    lastY = ev.clientY;
    var frac = pointerFraction(ev.clientX);
    grabbed = grab(frac);
    grabOffset = frac - handleFraction();
    moved = false;
    downX = ev.clientX;
    el.setPointerCapture(ev.pointerId);
    if (!grabbed) click(ev.clientX);   // a click elsewhere still jumps, telescoped
    ev.preventDefault();   // the browser's own jump-to-click would fight this
  });
  el.addEventListener("pointermove", function(ev){
    if (!dragging) return;
    if (Math.abs(ev.clientX - downX) > 2) moved = true;
    drag(ev.clientX, ev.clientY);
  });
  function stop(ev){
    if (!dragging) return;
    // Press and hold drags; a tap that never moved is a click, and lands where
    // it was aimed even if the handle was sitting on top of that spot. Without
    // this the handle is a dead zone, which is the one place a reader is most
    // likely to press.
    if (grabbed && !moved) click(ev.clientX);
    dragging = false;
    grabbed = false;
    try { el.releasePointerCapture(ev.pointerId); } catch (e) {}
  }
  el.addEventListener("pointerup", stop);
  el.addEventListener("pointercancel", stop);
  // Arrow keys, Home and End are untouched: the native input still handles
  // those, and they remain the accessible way to reach an exact value.
}

/* ---------------- boot ---------------- */
function wireControls(){
  document.getElementById("sizeMode").addEventListener("click", function(ev){
    var b = ev.target.closest("button");
    if (!b) return;
    state.mode = b.dataset.mode;
    this.querySelectorAll("button").forEach(function(el){
      el.setAttribute("aria-pressed", el.dataset.mode === state.mode ? "true" : "false");
    });
    document.getElementById("sizeNote").textContent = state.mode === "head"
      ? "Area is proportional to the herd recorded at the site."
      : "Animal units weight species by manure output. Horses and hobby holdings sit at zero.";
    document.getElementById("legendTitle").textContent = state.mode === "head"
      ? "Animals per site" : "Animal units per site";
    applyFilter();
    buildLegend();
  });
  var minHerd = document.getElementById("minHerd");
  var HERD_MODES = ["min", "band", "max"];

  function refreshHerd(){
    document.getElementById("minHerdLabel").textContent = herdLabel(state.min);
    paintHerdBand();
    applyFilter();
  }

  function setHerdMode(m){
    state.herdMode = m;
    var seg = document.getElementById("herdMode");
    if (seg) seg.querySelectorAll("button").forEach(function(el){
      el.setAttribute("aria-pressed", el.dataset.hmode === m ? "true" : "false");
    });
    refreshHerd();
  }

  minHerd.addEventListener("input", function(){
    state.min = Number(this.value);
    refreshHerd();
  });

  /* Scrolling over the slider cycles the mode. It is the one gesture a range
     input does not already use, it needs no extra chrome, and the painted track
     shows immediately what it did. The buttons below do the same thing for
     anyone who would rather see the choices. */
  minHerd.addEventListener("wheel", function(ev){
    ev.preventDefault();
    var i = HERD_MODES.indexOf(state.herdMode);
    setHerdMode(HERD_MODES[(i + (ev.deltaY > 0 ? 1 : HERD_MODES.length - 1)) % HERD_MODES.length]);
  }, {passive: false});

  var herdSeg = document.getElementById("herdMode");
  if (herdSeg) herdSeg.addEventListener("click", function(ev){
    var b = ev.target.closest("button");
    if (b) setHerdMode(b.dataset.hmode);
  });

  telescope(minHerd, {
    near: 0.25, gamma: 3,
    // In band mode the whole interval is the handle, so it can be picked up
    // anywhere inside it and carried; elsewhere it is just the thumb.
    grab: function(frac){
      var at = state.min / HERD_POS_MAX;
      var reach = state.herdMode === "band" ? state.herdWidth : 0.02;
      return Math.abs(frac - at) <= reach;
    },
    // Only the band has a width to change, so the vertical axis is inert in the
    // other two rather than quietly editing something invisible.
    onVertical: function(dy){
      if (state.herdMode !== "band") return;
      state.herdWidth = Math.max(0.01, Math.min(0.5, state.herdWidth + dy));
      refreshHerd();
    }
  });
  paintHerdBand();
  var allBox = document.getElementById("showAll");
  if (allBox){
    allBox.addEventListener("change", function(){
      state.showAll = this.checked;
      var note = document.getElementById("showAllNote");
      if (state.showAll && !LAYERS.all.index){
        if (note) note.textContent = "Loading the full cadastre index…";
        getJSON("data/tile_all_index.json").then(function(idx){
          LAYERS.all.index = idx;
          if (note){
            note.textContent = nf.format(Object.keys(idx.cells).length) +
              " tiles of every Danish parcel available.";
          }
          scheduleDraw();
        }).catch(function(){
          if (note) note.textContent = "Not built yet — run scripts/build_all_tiles.py.";
          state.showAll = false;
          allBox.checked = false;
        });
      }
      scheduleDraw();
    });
  }
  document.getElementById("panelClose").addEventListener("click", closePanel);
  document.addEventListener("keydown", function(e){ if (e.key === "Escape") closePanel(); });
  if (window.matchMedia){
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function(){
      readColors();
      komLayer.setStyle({color: state.colors.coast, fillColor: state.colors.land});
      scheduleDraw();
    });
  }
}

function initData(){
  for (var i = 0; i < S.length; i++){
    var key = S[i][CHR];
    var arr = byChr.get(key);
    if (!arr){ arr = []; byChr.set(key, arr); }
    arr.push(S[i]);
  }
  map.on("click", function(ev){
    refreshProjection();
    var row = nearestSite(ev.containerPoint);
    parcelAt(ev.latlng).then(function(parcel){
      var target = row;
      if (!target && parcel){
        var chrs = parcel.tile.meta[parcel.i][4];
        if (chrs && chrs.length){
          var cand = byChr.get(chrs[0]);
          if (cand && cand.length) target = cand[0];
        }
      }
      if (!target && !parcel){ closePanel(); return; }
      openPanel(target, parcel);
    });
  });

  document.getElementById("siteTotal").textContent = nf.format(S.length);
  var chrEl = document.getElementById("chrTotal");
  if (chrEl) chrEl.textContent = nf.format(byChr.size);
  herdScale();
  buildSpeciesList();
  buildLegend();
  applyFilter();
  var boot = document.getElementById("boot");
  if (boot) boot.remove();
}

function getJSON(url){ return fetch(url).then(function(r){ return r.json(); }); }

// Outlines are small: draw the country first so nothing stares at a blank
// screen while the 57,860 herd records arrive.
getJSON("data/kommuner.json").then(function(k){
  KOM = k;
  buildMap();
  buildCanvas();
  wireControls();
  getJSON("/api/config")
    .then(function(cfg){ CFG = cfg || {}; })
    .catch(function(){ CFG = {}; })          // no server: a static host
    .then(function(){ addBasemaps(); });
  var boot = document.getElementById("boot");
  if (boot) boot.textContent = "Loading 57,860 herd records…";
  // The company store is opened, not loaded: its index carries the shared string
  // pools and says which shard holds what, and the shards themselves arrive a
  // click at a time. A missing or half-built store costs the panel a section and
  // nothing else.
  var opening = false;
  if (typeof StaticStore === "function"){
    var store = new StaticStore("static-async-data");
    opening = store.meta("cvr").then(function(meta){
      CVRMETA = meta;
      STORE = store;
      return true;
    }).catch(function(){ return false; });
  }

  return Promise.all([
    getJSON("data/sites.json"),
    getJSON("data/tile_index.json").catch(function(){ return null; }),
    opening
  ]);
}).then(function(res){
  D = res[0]; LAYERS.farm.index = res[1]; S = D.sites;
  initData();
}).catch(function(err){
  var boot = document.getElementById("boot");
  if (boot) boot.textContent = "Could not load the data: " + err.message;
});
})();
