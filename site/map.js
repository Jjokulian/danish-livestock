(function(){
"use strict";

var D = JSON.parse(document.getElementById("sites").textContent);
var KOM = JSON.parse(document.getElementById("kommuner").textContent);
var pnode = document.getElementById("parcels");
var P = pnode ? JSON.parse(pnode.textContent) : null;
var S = D.sites;

/* record layout */
var LON = 0, LAT = 1, GRP = 2, SPC = 3, HEAD = 4, DE = 5, HA = 6, KOM_I = 7,
    CHR = 8, CVR = 9, CATS = 10, MAT = 11, LAV = 12, PAR_HA = 13, PROP_HA = 14,
    PROP_N = 15, SFE = 16, POST = 17, VIRK = 18, BRUG = 19, LPARC = 20, CROP = 21;

var nf = new Intl.NumberFormat("en-GB");
var GROUP_VAR = ["--s1", "--s2", "--s3", "--s0", "--s0", "--s0", "--s0"];
var STEPS = [0, 10, 50, 100, 500, 1000, 5000, 20000];
var STEP_LABELS = ["All sites", "10 animals or more", "50 or more", "100 or more",
                   "500 or more", "1,000 or more", "5,000 or more", "20,000 or more"];
var Z_PARCEL = 11;   // below this, parcels are not drawn at all
var Z_SHARP = 13;    // at and above this, full detail and individual matrikel lines

var state = {group: -1, mode: "head", min: 0, filtered: [], colors: {}, selected: null};

/* every herd record that shares a CHR number — one site can hold several */
var byChr = new Map();
for (var i0 = 0; i0 < S.length; i0++){
  var k0 = S[i0][CHR];
  var a0 = byChr.get(k0);
  if (!a0){ a0 = []; byChr.set(k0, a0); }
  a0.push(S[i0]);
}

function css(name){
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
function readColors(){
  state.colors = {
    "--s1": css("--s1"), "--s2": css("--s2"), "--s3": css("--s3"), "--s0": css("--s0"),
    land: css("--land"), coast: css("--coast"), surface: css("--surface"),
    parcelFill: css("--parcel-fill"), parcelFillStrong: css("--parcel-fill-strong"),
    parcelLine: css("--parcel-line"),
    selected: css("--selected")
  };
}
readColors();

function value(row){ return state.mode === "head" ? row[HEAD] : row[DE] / 10; }
function radius(v){
  if (v <= 0) return 1.3;
  return Math.max(1.3, Math.min(20, 0.8 * Math.pow(v, 0.33)));
}
function zoomFactor(z){ return Math.max(0.22, Math.min(2.6, Math.pow(1.42, z - 11))); }

/* ---------------- map ---------------- */
var map = L.map("map", {
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

var komLayer = L.geoJSON(KOM, {
  interactive: false,
  style: function(){
    return {color: state.colors.coast, weight: 0.7, fillColor: state.colors.land,
            fillOpacity: 1, opacity: 1};
  }
}).addTo(map);
map.fitBounds(komLayer.getBounds(), {padding: [12, 12]});

if (window.DF_BASEMAP_TOKEN){
  var tok = encodeURIComponent(window.DF_BASEMAP_TOKEN);
  var kds = 'Kort: <a href="https://dataforsyningen.dk">Klimadatastyrelsen</a>';
  var topo = L.tileLayer.wms("https://api.dataforsyningen.dk/topo_skaermkort_DAF?token=" + tok,
    {layers: "dtk_skaermkort", format: "image/png", version: "1.3.0", maxZoom: 18,
     attribution: kds});
  var orto = L.tileLayer.wms("https://api.dataforsyningen.dk/orto_foraar_DAF?token=" + tok,
    {layers: "orto_foraar", format: "image/jpeg", version: "1.3.0", maxZoom: 18,
     attribution: kds});
  var plain = L.layerGroup();
  L.control.layers({"Plain": plain, "Topographic": topo, "Aerial photo": orto},
                   null, {position: "topright", collapsed: false}).addTo(map);
  plain.addTo(map);
  map.on("baselayerchange", function(ev){
    var over = ev.name !== "Plain";
    komLayer.setStyle({fillOpacity: over ? 0 : 1, opacity: over ? 0.45 : 1});
    komLayer.bringToBack();
    scheduleDraw();
  });
}

/* ---------------- parcel geometry: decode on demand, cache forever ---------------- */
var ringCache = [new Map(), new Map()];   // [lo, hi]

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
function ringFor(pid, hi){
  var cache = ringCache[hi ? 1 : 0];
  var r = cache.get(pid);
  if (!r){ r = decodePoly(hi ? P.hi[pid] : P.lo[pid]); cache.set(pid, r); }
  return r;
}
function cellKeys(bounds){
  if (!P) return [];
  var keys = [];
  var x0 = Math.floor(bounds.getWest() / P.cellLon), x1 = Math.floor(bounds.getEast() / P.cellLon);
  var y0 = Math.floor(bounds.getSouth() / P.cellLat), y1 = Math.floor(bounds.getNorth() / P.cellLat);
  for (var x = x0; x <= x1; x++){
    for (var y = y0; y <= y1; y++){
      var k = x + "," + y;
      if (P.cells[k]) keys.push(k);
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
/* Always resolved against full-detail geometry, whatever is currently drawn. */
function parcelAt(latlng){
  if (!P) return -1;
  var lon = latlng.lng, lat = latlng.lat;
  var cx = Math.floor(lon / P.cellLon), cy = Math.floor(lat / P.cellLat);
  for (var dx = -1; dx <= 1; dx++){
    for (var dy = -1; dy <= 1; dy++){
      var ids = P.cells[(cx + dx) + "," + (cy + dy)];
      if (!ids) continue;
      for (var i = 0; i < ids.length; i++){
        if (pointInRing(ringFor(ids[i], true), lon, lat)) return ids[i];
      }
    }
  }
  return -1;
}

/* ---------------- canvas ---------------- */
map.createPane("farms");
var farmPane = map.getPane("farms");
farmPane.style.zIndex = 450;
farmPane.style.pointerEvents = "none";
var canvas = L.DomUtil.create("canvas", "leaflet-zoom-hide");
canvas.style.position = "absolute";
farmPane.appendChild(canvas);
var ctx = canvas.getContext("2d");
var frame = null;

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

function drawParcels(zoom){
  if (!P || zoom < Z_PARCEL) return;
  var hi = zoom >= Z_SHARP;
  var byProp = new Map();
  var keys = cellKeys(map.getBounds().pad(0.15));
  for (var k = 0; k < keys.length; k++){
    var ids = P.cells[keys[k]];
    for (var i = 0; i < ids.length; i++){
      var sfe = P.meta[ids[i]][3];
      var arr = byProp.get(sfe);
      if (!arr){ arr = []; byProp.set(sfe, arr); }
      arr.push(ids[i]);
    }
  }
  ctx.lineWidth = zoom >= 15 ? 1.1 : 0.8;
  ctx.strokeStyle = state.colors.parcelLine;
  // Below the sharp level the fill is the only thing carrying the parcels, so
  // it has to hold its own; above it, the lines do the work and the fill recedes.
  ctx.fillStyle = hi ? state.colors.parcelFill : state.colors.parcelFillStrong;
  byProp.forEach(function(ids){
    // One path per property: shared edges disappear on fill, so at low zoom the
    // parcels read as a single holding rather than a mesh of lines.
    var path = new Path2D();
    for (var i = 0; i < ids.length; i++){
      var ring = ringFor(ids[i], hi);
      for (var j = 0; j < ring.length; j++){
        var X = px(ring[j][0]), Y = py(ring[j][1]);
        if (j === 0) path.moveTo(X, Y); else path.lineTo(X, Y);
      }
      path.closePath();
    }
    ctx.fill(path);
    if (hi) ctx.stroke(path);
  });

  if (state.selected && state.selected.pid >= 0){
    var sel = new Path2D();
    var sring = ringFor(state.selected.pid, true);
    for (var m = 0; m < sring.length; m++){
      var sx = px(sring[m][0]), sy = py(sring[m][1]);
      if (m === 0) sel.moveTo(sx, sy); else sel.lineTo(sx, sy);
    }
    sel.closePath();
    ctx.lineWidth = 2;
    ctx.strokeStyle = state.colors.selected;
    ctx.stroke(sel);
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
map.on("move zoom", scheduleDraw);
map.on("resize", function(){ sizeCanvas(); scheduleDraw(); });
sizeCanvas();

/* ---------------- filtering ---------------- */
function applyFilter(){
  var min = STEPS[state.min];
  var out = [];
  for (var i = 0; i < S.length; i++){
    var row = S[i];
    if (state.group >= 0 && row[GRP] !== state.group) continue;
    if (min > 0 && row[HEAD] < min) continue;
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
document.getElementById("minHerd").addEventListener("input", function(){
  state.min = Number(this.value);
  document.getElementById("minHerdLabel").textContent = STEP_LABELS[state.min];
  applyFilter();
});

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
map.on("zoomend", buildLegend);

/* ---------------- selection & info panel ---------------- */
function esc(s){
  return String(s == null ? "" : s).replace(/[&<>"]/g, function(c){
    return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c];
  });
}
function nearestSite(latlng, containerPoint){
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
map.on("click", function(ev){
  refreshProjection();
  var pid = parcelAt(ev.latlng);
  var row = nearestSite(ev.latlng, ev.containerPoint);
  if (pid < 0 && !row){ closePanel(); return; }
  if (!row && pid >= 0){
    // Clicked bare land: show the farm(s) registered on that property.
    var chrs = P.meta[pid][4];
    if (chrs && chrs.length){
      var candidates = byChr.get(chrs[0]);
      if (candidates && candidates.length) row = candidates[0];
    }
  }
  if (row && pid < 0 && row[SFE]) pid = -1;
  openPanel(row, pid);
});

function row2(label, val){
  return '<div class="pr"><span>' + esc(label) + "</span><b>" + val + "</b></div>";
}
function openPanel(row, pid){
  state.selected = {row: row, pid: pid};
  var panel = document.getElementById("panel");
  var body = document.getElementById("panelBody");
  var title = document.getElementById("panelTitle");
  var sub = document.getElementById("panelSub");
  var html = "";

  var herds = row ? (byChr.get(row[CHR]) || [row]) : [];
  if (row){
    title.textContent = D.species[row[SPC]] + (herds.length > 1 ? " + " + (herds.length - 1) + " more" : "");
    sub.textContent = "CHR " + row[CHR] + " · " + (D.kommuner[row[KOM_I]] || "") +
                      (row[POST] ? " " + row[POST] : "");
  } else {
    title.textContent = "Cadastral parcel";
    sub.textContent = pid >= 0 ? "no livestock registered here" : "";
  }

  if (herds.length){
    html += '<section><h4>Herds at this site</h4>';
    herds.forEach(function(h){
      html += '<div class="herd"><div class="hh">' + esc(D.species[h[SPC]]) + "</div>";
      if (h[VIRK] >= 0) html += '<div class="hs">' + esc(D.virkart[h[VIRK]]) + "</div>";
      (h[CATS] || []).forEach(function(c){
        html += row2(D.labels[c[0]], nf.format(c[1]));
      });
      html += row2("Animals", nf.format(h[HEAD]));
      if (h[DE]) html += row2("Animal units (DE)", (h[DE] / 10).toFixed(1));
      if (h[BRUG] >= 0) html += row2("Purpose", esc(D.brug[h[BRUG]]));
      html += "</div>";
    });
    html += "</section>";
  }

  var m = pid >= 0 ? P.meta[pid] : null;
  if (m || (row && row[MAT])){
    html += '<section><h4>Cadastre</h4>';
    if (m){
      html += row2("Matrikel", esc(m[0]));
      html += row2("Ejerlav", esc(P.ejerlav[m[1]]));
      html += row2("Parcel area", (m[2] / 10000).toFixed(2) + " ha");
      if (m[3]) html += row2("Property (BFE)", m[3]);
    } else {
      html += row2("Matrikel", esc(row[MAT]));
      if (row[LAV] >= 0) html += row2("Ejerlav", esc(D.ejerlav[row[LAV]]));
      html += row2("Parcel area", row[PAR_HA].toFixed(2) + " ha");
      if (row[SFE]) html += row2("Property (BFE)", row[SFE]);
    }
    if (row && row[PROP_HA]){
      html += row2("Property area", nf.format(row[PROP_HA]) + " ha");
      html += row2("Parcels in property", row[PROP_N]);
    }
    html += "</section>";
  }

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

  html += '<p class="pfoot">Herd figures from CHR, 1 June 2024. Cadastre from DAWA. ' +
          'Declared land from Marker 2025. No owner name is published in any of them.</p>';
  body.innerHTML = html;
  panel.classList.add("open");
  scheduleDraw();
}
function closePanel(){
  state.selected = null;
  document.getElementById("panel").classList.remove("open");
  scheduleDraw();
}
document.getElementById("panelClose").addEventListener("click", closePanel);
document.addEventListener("keydown", function(e){ if (e.key === "Escape") closePanel(); });

/* ---------------- theme ---------------- */
if (window.matchMedia){
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function(){
    readColors();
    komLayer.setStyle({color: state.colors.coast, fillColor: state.colors.land});
    scheduleDraw();
  });
}

/* ---------------- boot ---------------- */
document.getElementById("siteTotal").textContent = nf.format(S.length);
var chrEl = document.getElementById("chrTotal");
if (chrEl) chrEl.textContent = nf.format(byChr.size);
var pc = document.getElementById("parcelCount");
if (pc) pc.textContent = P ? nf.format(P.meta.length) : "—";
buildSpeciesList();
buildLegend();
applyFilter();
})();
