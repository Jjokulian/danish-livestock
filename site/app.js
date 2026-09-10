(function(){
"use strict";
var D = JSON.parse(document.getElementById("dataset").textContent);
var NS = "http://www.w3.org/2000/svg";
var nf = new Intl.NumberFormat("en-GB");
var renderers = [];

function el(name, attrs){
  var e = document.createElementNS(NS, name);
  for (var k in attrs) e.setAttribute(k, attrs[k]);
  return e;
}
function abbr(v){
  if (v >= 1e6) return (v/1e6).toFixed(v >= 1e7 ? 1 : 2).replace(/\.?0+$/, "") + "m";
  if (v >= 1e3) return Math.round(v/1e3) + "k";
  return String(Math.round(v));
}
function niceMax(v){
  if (v <= 0) return 1;
  var p = Math.pow(10, Math.floor(Math.log10(v)));
  var steps = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10];
  for (var i = 0; i < steps.length; i++) if (v <= steps[i]*p) return steps[i]*p;
  return 10*p;
}
function pathFrom(pts, sx, sy){
  var d = "";
  for (var i = 0; i < pts.length; i++) d += (i ? "L" : "M") + sx(pts[i][0]).toFixed(2) + " " + sy(pts[i][1]).toFixed(2);
  return d;
}

/* ---------------- small multiples ---------------- */
var PANELS = [
  {k:"cattle",  title:"Cattle",   note:"Every bovine on the farm, calves included."},
  {k:"cows",    title:"Cows",     note:"Dairy cows plus cows kept for suckling."},
  {k:"pigs",    title:"Pigs",     note:"Sows, piglets, weaners and finishers."},
  {k:"sows",    title:"Sows",     note:"The breeding herd behind the pig count."},
  {k:"fowls",   title:"Chickens", note:"Laying hens, broilers and breeding stock."},
  {k:"sheep",   title:"Sheep",    note:"Counted with goats before 1982."},
  {k:"horses",  title:"Horses",   note:"Only those kept on agricultural holdings."}
];
var panelAPI = [];

function buildPanels(){
  var host = document.getElementById("panels");
  PANELS.forEach(function(spec){
    var pts = D.long[spec.k];
    var last = pts[pts.length - 1];
    var peak = pts.reduce(function(a,b){ return b[1] > a[1] ? b : a; });

    var wrap = document.createElement("div");
    wrap.className = "panel";
    wrap.innerHTML =
      '<div class="p-top"><h3></h3><div class="p-year mono"></div></div>' +
      '<div class="p-mid"><div class="hdr-v"></div><div class="p-peak mono"></div></div>' +
      '<div class="p-chart"></div>' +
      '<div class="p-note"></div>';
    wrap.querySelector("h3").textContent = spec.title;
    wrap.querySelector(".p-note").textContent = spec.note;
    wrap.querySelector(".p-peak").innerHTML = "peak " + abbr(peak[1]) + "<br>" + peak[0];
    var yearEl = wrap.querySelector(".p-year");
    var valEl = wrap.querySelector(".hdr-v");
    var chartEl = wrap.querySelector(".p-chart");
    host.appendChild(wrap);

    var lookup = {};
    pts.forEach(function(p){ lookup[p[0]] = p[1]; });
    var state = {};

    function setYear(y){
      if (y == null || lookup[y] == null){
        yearEl.textContent = last[0];
        valEl.textContent = nf.format(last[1]);
      } else {
        yearEl.textContent = y;
        valEl.textContent = nf.format(lookup[y]);
      }
      if (state.move) state.move(y);
    }
    setYear(null);

    function draw(){
      chartEl.textContent = "";
      var w = chartEl.clientWidth || 200, h = 108;
      var pad = {l:27, r:6, t:10, b:15};
      var iw = w - pad.l - pad.r, ih = h - pad.t - pad.b;
      var ymax = niceMax(peak[1]);
      var sx = function(y){ return pad.l + (y - 1920) / (2025 - 1920) * iw; };
      var sy = function(v){ return pad.t + ih - (v / ymax) * ih; };

      var svg = el("svg", {width:w, height:h, viewBox:"0 0 " + w + " " + h, role:"img"});
      svg.appendChild(el("title", {})).textContent = spec.title + " in Denmark, " + pts[0][0] + " to " + last[0];

      [0, ymax].forEach(function(t){
        svg.appendChild(el("line", {x1:pad.l, x2:pad.l + iw, y1:sy(t), y2:sy(t),
          stroke: t === 0 ? "var(--axis)" : "var(--grid)", "stroke-width":1}));
        var tx = el("text", {x:pad.l - 5, y:sy(t) + 3.2, "text-anchor":"end",
          fill:"var(--muted)", "font-size":9, "font-family":'"IBM Plex Mono", monospace'});
        tx.textContent = t === 0 ? "0" : abbr(t);
        svg.appendChild(tx);
      });
      [1920, 2025].forEach(function(y, i){
        var tx = el("text", {x:sx(y), y:h - 3, "text-anchor": i ? "end" : "start",
          fill:"var(--muted)", "font-size":9, "font-family":'"IBM Plex Mono", monospace'});
        tx.textContent = y;
        svg.appendChild(tx);
      });

      var d = pathFrom(pts, sx, sy);
      svg.appendChild(el("path", {d: d + "L" + sx(last[0]).toFixed(2) + " " + sy(0) + "L" + sx(pts[0][0]).toFixed(2) + " " + sy(0) + "Z",
        fill:"var(--s1-fill)", stroke:"none"}));
      svg.appendChild(el("path", {d:d, fill:"none", stroke:"var(--s1)", "stroke-width":1.9,
        "stroke-linejoin":"round", "stroke-linecap":"round"}));
      svg.appendChild(el("circle", {cx:sx(peak[0]), cy:sy(peak[1]), r:3.1,
        fill:"var(--s1)", stroke:"var(--surface)", "stroke-width":1.8}));

      var cross = el("line", {x1:0, x2:0, y1:pad.t - 3, y2:pad.t + ih, stroke:"var(--ink-2)",
        "stroke-width":1, opacity:0, "pointer-events":"none"});
      var dot = el("circle", {cx:0, cy:0, r:3.4, fill:"var(--s1)", stroke:"var(--surface)",
        "stroke-width":2, opacity:0, "pointer-events":"none"});
      svg.appendChild(cross); svg.appendChild(dot);

      var hit = el("rect", {x:pad.l, y:0, width:Math.max(iw,1), height:h, fill:"transparent"});
      svg.appendChild(hit);
      chartEl.appendChild(svg);

      state.move = function(y){
        if (y == null || lookup[y] == null){ cross.setAttribute("opacity", 0); dot.setAttribute("opacity", 0); return; }
        var px = sx(y);
        cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("opacity", 0.28);
        dot.setAttribute("cx", px); dot.setAttribute("cy", sy(lookup[y])); dot.setAttribute("opacity", 1);
      };

      function fromEvent(ev){
        var r = svg.getBoundingClientRect();
        var y = Math.round(1920 + (ev.clientX - r.left - pad.l) / iw * (2025 - 1920));
        return Math.min(2025, Math.max(1920, y));
      }
      hit.addEventListener("pointermove", function(ev){ broadcast(fromEvent(ev)); });
      hit.addEventListener("pointerdown", function(ev){ broadcast(fromEvent(ev)); });
      hit.addEventListener("pointerleave", function(){ broadcast(null); });
    }

    panelAPI.push({draw:draw, setYear:setYear});
    renderers.push(draw);
  });

  function broadcast(y){ panelAPI.forEach(function(p){ p.setYear(y); }); }
}

/* ---------------- wide line chart ---------------- */
function lineChart(host, opts){
  var block = host.closest(".chart-block");
  var tip = document.createElement("div");
  tip.className = "tip";
  block.appendChild(tip);

  function draw(){
    host.textContent = "";
    var w = host.clientWidth || 600, h = opts.height || 270;
    var pad = {l:46, r:12, t:16, b:26};
    var iw = w - pad.l - pad.r, ih = h - pad.t - pad.b;
    var x0 = opts.xMin, x1 = opts.xMax;
    var ymax = opts.yMax, ymin = opts.yMin || 0;
    var sx = function(y){ return pad.l + (y - x0) / (x1 - x0) * iw; };
    var sy = function(v){ return pad.t + ih - (v - ymin) / (ymax - ymin) * ih; };

    var svg = el("svg", {width:w, height:h, viewBox:"0 0 " + w + " " + h, role:"img"});
    svg.appendChild(el("title", {})).textContent = opts.alt || "";

    opts.yTicks.forEach(function(t){
      svg.appendChild(el("line", {x1:pad.l, x2:pad.l + iw, y1:sy(t), y2:sy(t),
        stroke: t === ymin ? "var(--axis)" : "var(--grid)", "stroke-width":1}));
      var tx = el("text", {x:pad.l - 7, y:sy(t) + 3.4, "text-anchor":"end", fill:"var(--muted)",
        "font-size":10.5, "font-family":'"IBM Plex Mono", monospace'});
      tx.textContent = opts.yFmt(t);
      svg.appendChild(tx);
    });
    opts.xTicks.forEach(function(t){
      var tx = el("text", {x:sx(t), y:h - 7, "text-anchor":"middle", fill:"var(--muted)",
        "font-size":10.5, "font-family":'"IBM Plex Mono", monospace'});
      tx.textContent = t;
      svg.appendChild(tx);
    });

    opts.series.forEach(function(s){
      if (s.fill) {
        var dd = pathFrom(s.pts, sx, sy);
        svg.appendChild(el("path", {d: dd + "L" + sx(s.pts[s.pts.length-1][0]).toFixed(2) + " " + sy(ymin) +
          "L" + sx(s.pts[0][0]).toFixed(2) + " " + sy(ymin) + "Z", fill:s.fill, stroke:"none"}));
      }
      svg.appendChild(el("path", {d:pathFrom(s.pts, sx, sy), fill:"none", stroke:s.color,
        "stroke-width":2, "stroke-linejoin":"round", "stroke-linecap":"round"}));
      var lastPt = s.pts[s.pts.length - 1];
      var ly = sy(lastPt[1]) + (s.side === "below" ? 15 : -9);
      var lab = el("text", {x:sx(lastPt[0]), y:ly, "text-anchor":"end", fill:s.color,
        "font-size":11.5, "font-family":'"IBM Plex Mono", monospace'});
      lab.textContent = s.label;
      svg.appendChild(lab);
    });

    (opts.annotations || []).forEach(function(a){
      svg.appendChild(el("circle", {cx:sx(a.year), cy:sy(a.value), r:3.4, fill:"var(--s1)",
        stroke:"var(--surface)", "stroke-width":2}));
      var ax = sx(a.textYear != null ? a.textYear : a.year);
      var ay = sy(a.textValue != null ? a.textValue : a.value);
      a.text.split("|").forEach(function(line, i){
        var t = el("text", {x:ax + (a.dx || 0), y:ay + (a.dy || 0) + i*13,
          "text-anchor":a.anchor || "end", fill:"var(--ink-2)", "font-size":11.5,
          "font-family":'"IBM Plex Mono", monospace'});
        t.textContent = line;
        svg.appendChild(t);
      });
    });

    var cross = el("line", {x1:0, x2:0, y1:pad.t, y2:pad.t + ih, stroke:"var(--ink-2)",
      "stroke-width":1, opacity:0, "pointer-events":"none"});
    svg.appendChild(cross);
    var dots = opts.series.map(function(s){
      var c = el("circle", {r:4, fill:s.color, stroke:"var(--surface)", "stroke-width":2,
        opacity:0, "pointer-events":"none"});
      svg.appendChild(c);
      return c;
    });

    var maps = opts.series.map(function(s){
      var m = {}; s.pts.forEach(function(p){ m[p[0]] = p[1]; }); return m;
    });

    var hit = el("rect", {x:pad.l, y:pad.t - 8, width:Math.max(iw,1), height:ih + 16, fill:"transparent"});
    svg.appendChild(hit);
    host.appendChild(svg);

    function show(ev){
      var r = svg.getBoundingClientRect();
      var yr = Math.round(x0 + (ev.clientX - r.left - pad.l) / iw * (x1 - x0));
      yr = Math.min(x1, Math.max(x0, yr));
      var px = sx(yr);
      cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("opacity", 0.3);
      var rows = "";
      opts.series.forEach(function(s, i){
        var v = maps[i][yr];
        if (v == null){ dots[i].setAttribute("opacity", 0); return; }
        dots[i].setAttribute("cx", px); dots[i].setAttribute("cy", sy(v)); dots[i].setAttribute("opacity", 1);
        rows += '<div class="tr"><span style="color:' + s.color + '">' + s.label + '</span><b class="mono">' +
          opts.tipFmt(v) + "</b></div>";
      });
      tip.innerHTML = '<div class="ty mono">' + yr + "</div>" + rows;
      tip.style.opacity = 1;
      var bb = block.getBoundingClientRect();
      var left = ev.clientX - bb.left + 14;
      if (left + tip.offsetWidth > bb.width - 8) left = ev.clientX - bb.left - tip.offsetWidth - 14;
      tip.style.left = Math.max(8, left) + "px";
      tip.style.top = (r.top - bb.top + pad.t) + "px";
    }
    function hide(){
      tip.style.opacity = 0;
      cross.setAttribute("opacity", 0);
      dots.forEach(function(d){ d.setAttribute("opacity", 0); });
    }
    hit.addEventListener("pointermove", show);
    hit.addEventListener("pointerdown", show);
    hit.addEventListener("pointerleave", hide);
  }
  renderers.push(draw);
  draw();
}

/* ---------------- charts ---------------- */
function buildPigflow(){
  var m = function(pts){ return pts.map(function(p){ return [p[0], p[1]/1e6]; }); };
  lineChart(document.getElementById("pigflow"), {
    xMin:1990, xMax:2025, yMax:25, yMin:0,
    yTicks:[0, 5, 10, 15, 20, 25],
    xTicks:[1990, 1995, 2000, 2005, 2010, 2015, 2020, 2025],
    yFmt:function(v){ return v + "m"; },
    tipFmt:function(v){ return v.toFixed(2) + "m"; },
    alt:"Pigs slaughtered in Denmark fell from 22 million in 2004 to 15 million in 2025, while live pig exports rose from near zero to 17 million.",
    series:[
      {label:"Slaughtered", color:"var(--s1)", pts:m(D.pigflow.slaughtered), side:"below"},
      {label:"Exported live", color:"var(--s2)", pts:m(D.pigflow.live_exports), side:"above"}
    ]
  });
}
function buildMilk(){
  function idx(pts){
    var base = null;
    pts.forEach(function(p){ if (p[0] === 1990) base = p[1]; });
    return pts.map(function(p){ return [p[0], p[1]/base*100]; });
  }
  lineChart(document.getElementById("milk"), {
    xMin:1990, xMax:2025, yMax:180, yMin:60,
    yTicks:[60, 80, 100, 120, 140, 160, 180],
    xTicks:[1990, 1995, 2000, 2005, 2010, 2015, 2020, 2025],
    yFmt:function(v){ return String(v); },
    tipFmt:function(v){ return v.toFixed(1); },
    alt:"Indexed to 1990, milk yield per cow rose to 169 and total milk to 123 while the number of dairy cows fell to 73.",
    series:[
      {label:"Milk per cow", color:"var(--s3)", pts:idx(D.milk.yield), side:"below"},
      {label:"Milk, total", color:"var(--s2)", pts:idx(D.milk.total_kg_m), side:"above"},
      {label:"Dairy cows", color:"var(--s1)", pts:idx(D.milk.cows), side:"above"}
    ]
  });
}
function buildMink(){
  var pts = D.mink.animals.map(function(p){ return [p[0], p[1]/1e6]; });
  lineChart(document.getElementById("minkchart"), {
    xMin:2010, xMax:2021, yMax:4, yMin:0, height:210,
    yTicks:[0, 1, 2, 3, 4],
    xTicks:[2010, 2012, 2014, 2016, 2018, 2020],
    yFmt:function(v){ return v + "m"; },
    tipFmt:function(v){ return v.toFixed(2) + "m"; },
    alt:"Fur animals on Danish farms peaked at 3.43 million in 2017 and fell to zero after the November 2020 cull.",
    series:[{label:"", color:"var(--s1)", pts:pts, fill:"var(--s1-fill)", side:"above"}],
    annotations:[{year:2021, value:0, textYear:2020, textValue:1.05, text:"herd culled|November 2020", dx:-8, dy:0, anchor:"end"}]
  });
}

/* ---------------- farm concentration rows ---------------- */
var FARMROWS = [
  {k:"pigs",       label:"Pigs",        animals:"pigs"},
  {k:"dairy_cows", label:"Dairy cows",  animals:"dairy_cows", modern:true},
  {k:"cattle",     label:"Cattle",      animals:"cattle"},
  {k:"sows",       label:"Sows",        animals:"sows"},
  {k:"poultry",    label:"Poultry",     animals:"poultry_total", modern:true},
  {k:"sheep",      label:"Sheep",       animals:"sheep"},
  {k:"horses",     label:"Horses",      animals:"horses"}
];
function animalSeries(key){
  if (D.long[key]) return D.long[key];
  return D.modern[key];
}
function buildFarmRows(){
  var host = document.getElementById("farmrows");
  FARMROWS.forEach(function(r){
    var farms = D.farms[r.k];
    var f0 = farms[0], f1 = farms[farms.length - 1];
    var an = animalSeries(r.animals);
    var amap = {}; an.forEach(function(p){ amap[p[0]] = p[1]; });
    var per0 = amap[f0[0]] ? Math.round(amap[f0[0]]/f0[1]) : null;
    var per1 = amap[f1[0]] ? Math.round(amap[f1[0]]/f1[1]) : null;

    var row = document.createElement("div");
    row.className = "row";
    row.innerHTML =
      '<div class="name"></div>' +
      '<div class="spark"></div>' +
      '<div class="num"></div>' +
      '<div class="num"></div>' +
      '<div class="chg"></div>';
    var cells = row.children;
    cells[0].innerHTML = "<span></span><em></em>";
    cells[0].firstChild.textContent = r.label;
    cells[0].querySelector("em").textContent = "holdings";
    cells[2].innerHTML = "<span></span><em>1982</em>";
    cells[2].firstChild.textContent = nf.format(f0[1]);
    cells[3].innerHTML = "<span></span><em>" + f1[0] + "</em>";
    cells[3].firstChild.textContent = nf.format(f1[1]);
    cells[4].innerHTML = (per0 != null && per1 != null)
      ? '<span>' + nf.format(per0) + " → " + nf.format(per1) + "</span><em>animals each</em>"
      : "<span>&mdash;</span>";
    host.appendChild(row);

    var sparkHost = cells[1];
    function draw(){
      sparkHost.textContent = "";
      var w = sparkHost.clientWidth || 180, h = 34;
      if (w < 40) return;
      var ymax = niceMax(f0[1]);
      var sx = function(y){ return 1 + (y - 1982)/(f1[0] - 1982) * (w - 2); };
      var sy = function(v){ return 4 + (h - 8) - v/ymax * (h - 8); };
      var svg = el("svg", {width:w, height:h, viewBox:"0 0 " + w + " " + h, "aria-hidden":"true"});
      svg.appendChild(el("line", {x1:0, x2:w, y1:sy(0), y2:sy(0), stroke:"var(--grid)", "stroke-width":1}));
      svg.appendChild(el("path", {d:pathFrom(farms, sx, sy), fill:"none", stroke:"var(--s1)",
        "stroke-width":1.6, "stroke-linejoin":"round", "stroke-linecap":"round"}));
      svg.appendChild(el("circle", {cx:sx(f1[0]), cy:sy(f1[1]), r:2.8, fill:"var(--s1)",
        stroke:"var(--surface)", "stroke-width":1.6}));
      sparkHost.appendChild(svg);
    }
    renderers.push(draw);
    draw();
  });
}

/* ---------------- data table ---------------- */
function buildTable(){
  var keys = ["cattle", "cows", "pigs", "sows", "fowls", "sheep", "horses"];
  var heads = ["Year", "Cattle", "Cows", "Pigs", "Sows", "Chickens", "Sheep", "Horses"];
  var t = document.getElementById("datatable");
  var thr = document.createElement("tr");
  heads.forEach(function(h){
    var th = document.createElement("th");
    th.textContent = h;
    th.setAttribute("scope", "col");
    thr.appendChild(th);
  });
  t.tHead.appendChild(thr);
  var maps = keys.map(function(k){
    var m = {}; D.long[k].forEach(function(p){ m[p[0]] = p[1]; }); return m;
  });
  var body = t.tBodies[0];
  var frag = document.createDocumentFragment();
  for (var y = 2025; y >= 1920; y--){
    var tr = document.createElement("tr");
    var th = document.createElement("th");
    th.setAttribute("scope", "row");
    th.textContent = y;
    tr.appendChild(th);
    maps.forEach(function(m){
      var td = document.createElement("td");
      td.textContent = m[y] == null ? "" : nf.format(m[y]);
      tr.appendChild(td);
    });
    frag.appendChild(tr);
  }
  body.appendChild(frag);
}

/* ---------------- boot ---------------- */
buildPanels();
panelAPI.forEach(function(p){ p.draw(); });
buildPigflow();
buildMilk();
buildMink();
buildFarmRows();
buildTable();

var tmr = null;
window.addEventListener("resize", function(){
  clearTimeout(tmr);
  tmr = setTimeout(function(){ renderers.forEach(function(f){ f(); }); }, 160);
});
})();
