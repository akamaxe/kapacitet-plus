/* The embeddable capacity map of a Kapacitet+ publication (study/embed.py).
 *
 * Reads ../capacity.geojson and ../manifest.json and draws the published cells as SVG in a Web
 * Mercator projection fitted to their extent. No library and no request outside this folder, unless
 * the manifest names a raster tile layer (study.toml [publication] map_tiles), drawn under the cells.
 * Everything a reader needs is also in the page's table, which works without this script.
 */
(function () {
  "use strict";

  var SVGNS = "http://www.w3.org/2000/svg";
  var LANG = new URLSearchParams(window.location.search).get("lang") === "en" ? "en" : "sv";
  var T = {
    sv: {
      title: "Ledig kapacitet i elnätet", show: "Visa", mapLabel: "Karta: varje cell kan väljas för att visa sina värden.",
      zoomIn: "Zooma in", zoomOut: "Zooma ut", zoomFit: "Visa alla celler", panelEmpty: "Välj en cell på kartan",
      tableTitle: "Alla celler", showTable: "Visa som tabell", hideTable: "Dölj tabellen",
      basis: "Grunder för beräkningen", download: "Ladda ner (CSV)",
      draftNote: "UTKAST: inte signerat, får inte användas som fastställt underlag.",
      generation_kw: "Produktion", consumption_kw: "Förbrukning", conditional_generation_kw: "Villkorad produktion",
      three_phase_generation_kw: "Produktion, trefas", three_phase_consumption_kw: "Förbrukning, trefas",
      legendTitle: "Ledig kapacitet, {layer} (kW)", noValue: "Inget värde (inte beräknat eller dolt)",
      cell: "Cell", level: "Nivå", customers: "Kunder", period: "Period", method: "Metodversion", name: "Namn",
      quarter: "Kvartal", month: "Månad", signedBy: "signerad av {role}", draft: "UTKAST", methodShort: "metodversion",
      published: "Publicerad {date}.", unplaced: "{n} celler har ingen plats på kartan (område eller hela studien); de finns i tabellen.",
      caption: "Ledig kapacitet per cell, {period}. {n} celler; – betyder inget värde (inte beräknat eller dolt).",
      failed: "Publiceringen kunde inte läsas. Värdena finns i tabellen nedan.",
      station: "nätstation", grid: "ruta", zone: "område", study: "hela studien", kw: "kW",
      cellLabel: "{name}: {layer} {value}"
    },
    en: {
      title: "Available grid capacity", show: "Show", mapLabel: "Map: each cell can be selected to show its figures.",
      zoomIn: "Zoom in", zoomOut: "Zoom out", zoomFit: "Show every cell", panelEmpty: "Select a cell on the map",
      tableTitle: "Every cell", showTable: "Show as a table", hideTable: "Hide the table",
      basis: "Basis of calculation (Swedish)", download: "Download (CSV)",
      draftNote: "DRAFT (UTKAST): not signed, not to be used as an established basis.",
      generation_kw: "Generation", consumption_kw: "Consumption", conditional_generation_kw: "Conditional generation",
      three_phase_generation_kw: "Generation, three-phase", three_phase_consumption_kw: "Consumption, three-phase",
      legendTitle: "Available capacity, {layer} (kW)", noValue: "No value (not computed or hidden)",
      cell: "Cell", level: "Level", customers: "Customers", period: "Period", method: "Method version", name: "Name",
      quarter: "Quarter", month: "Month", signedBy: "signed ({role})", draft: "DRAFT (UTKAST)", methodShort: "method version",
      published: "Published {date}.", unplaced: "{n} cells have no place on the map (a zone or the whole study); they are in the table.",
      caption: "Available capacity per cell, {period}. {n} cells; – means no value (not computed or hidden).",
      failed: "The publication could not be read. The figures are in the table below.",
      station: "substation", grid: "square", zone: "zone", study: "whole study", kw: "kW",
      cellLabel: "{name}: {layer} {value}"
    }
  };
  // sequential ramps: more capacity stands out more from the page, so it is darker by day and lighter
  // at night; "no value" is a hatch, never a step of a ramp
  var RAMPS = {
    light: {
      generation: ["#c6d5ea", "#a9c0dd", "#6f93c2", "#3d68a3", "#1f467c"],
      consumption: ["#ecd7a6", "#e2c27f", "#c99a3e", "#a0701a", "#6e4c0f"]
    },
    dark: {
      generation: ["#3b5578", "#4f73a6", "#6f96cc", "#9dbbe3", "#d3e1f4"],
      consumption: ["#6b5226", "#8f6a24", "#b58637", "#d7ab5f", "#f2deb3"]
    }
  };
  var DARK = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  var METRES_PER_WORLD = 40075016.686;     // the Earth's circumference: one Web Mercator world across
  var LAYERS = [
    { key: "generation_kw", ramp: "generation" },
    { key: "consumption_kw", ramp: "consumption" },
    { key: "three_phase_generation_kw", ramp: "generation" },
    { key: "three_phase_consumption_kw", ramp: "consumption" }
  ];
  var OPTIONAL = ["conditional_generation_kw", "three_phase_generation_kw", "three_phase_consumption_kw"];
  var NUMERIC = ["customers", "generation_kw", "consumption_kw"].concat(OPTIONAL);
  var NO_VALUE = "–";
  var MAX_K = 256 * Math.pow(2, 20);
  var PAD = 28;
  var PAD_RIGHT = 60;                      // the zoom buttons' column

  function t(key, params) {
    var s = (T[LANG][key] !== undefined ? T[LANG][key] : T.sv[key]) || key;
    if (params) {
      Object.keys(params).forEach(function (k) { s = s.split("{" + k + "}").join(String(params[k])); });
    }
    return s;
  }
  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function svg(tag, attrs) {
    var e = document.createElementNS(SVGNS, tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    return e;
  }
  function num(x) {
    if (x === null || x === undefined || x === "" || !isFinite(Number(x))) return NO_VALUE;
    x = Number(x);
    var d = Math.abs(x) < 10 && x !== Math.round(x) ? 1 : 0;
    var s = new Intl.NumberFormat(LANG === "en" ? "en-GB" : "sv-SE",
      { minimumFractionDigits: d, maximumFractionDigits: d }).format(x);
    return LANG === "sv" ? s.replace(/[  ]/g, " ").replace("-", "−") : s;
  }
  function kw(x) { var s = num(x); return s === NO_VALUE ? s : s + " " + t("kw"); }
  function value(p, key) { var v = p[key]; return v === null || v === undefined || v === "" ? null : Number(v); }

  // ------------------------------------------------------------ projection

  function mercator(lon, lat) {
    var s = Math.sin(lat * Math.PI / 180);
    s = Math.min(Math.max(s, -0.9999), 0.9999);
    return [(lon + 180) / 360, 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)];
  }

  var state = { cells: [], unplaced: 0, layer: LAYERS[0], view: { k: 1, x: 0, y: 0 }, bbox: null, selected: null,
                manifest: null, tiles: null, tileImgs: {}, breaks: [], colors: [] };

  function rings(geom) {
    if (!geom) return [];
    if (geom.type === "Polygon") return geom.coordinates;
    if (geom.type === "MultiPolygon") return geom.coordinates.reduce(function (a, p) { return a.concat(p); }, []);
    return [];
  }

  function prepare(features) {
    var minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
    function grow(p) { minx = Math.min(minx, p[0]); maxx = Math.max(maxx, p[0]); miny = Math.min(miny, p[1]); maxy = Math.max(maxy, p[1]); }
    features.forEach(function (f, i) {
      var g = f.geometry, cell = { i: i, props: f.properties || {}, point: null, rings: null };
      if (g && g.type === "Point" && g.coordinates && isFinite(g.coordinates[0]) && isFinite(g.coordinates[1])) {
        cell.point = mercator(g.coordinates[0], g.coordinates[1]);
        grow(cell.point);
      } else if (rings(g).length) {
        cell.rings = rings(g).map(function (r) { return r.map(function (c) { var p = mercator(c[0], c[1]); grow(p); return p; }); });
      } else {
        state.unplaced += 1;
        return;
      }
      state.cells.push(cell);
    });
    state.bbox = state.cells.length ? [minx, miny, maxx, maxy] : null;
  }

  function size() { var m = $("embed-map"); return [m.clientWidth || 600, m.clientHeight || 420]; }

  function fit() {
    if (!state.bbox) return;
    var wh = size(), b = state.bbox;
    var span = Math.max(b[2] - b[0], b[3] - b[1], 0);
    var w = wh[0] - PAD - PAD_RIGHT, h = wh[1] - 2 * PAD;
    var k = span > 0 ? Math.min(w / Math.max(b[2] - b[0], 1e-12), h / Math.max(b[3] - b[1], 1e-12))
                     : Math.min(w, h) / 0.0001;      // one cell: about two kilometres across
    k = Math.min(Math.max(k, 256), MAX_K);
    state.view = { k: k, x: PAD + w / 2 - k * (b[0] + b[2]) / 2, y: wh[1] / 2 - k * (b[1] + b[3]) / 2 };
    draw();
  }

  function zoomAt(factor, sx, sy) {
    var v = state.view, k = Math.min(Math.max(v.k * factor, 256), MAX_K), r = k / v.k;
    state.view = { k: k, x: sx - (sx - v.x) * r, y: sy - (sy - v.y) * r };
    draw();
  }

  function pan(dx, dy) { state.view.x += dx; state.view.y += dy; draw(); }

  // ------------------------------------------------------------ colours and the legend

  function nice(v) {
    if (!(v > 0)) return 0;
    var p = Math.pow(10, Math.floor(Math.log10(v)) - 1);
    return Math.round(v / p) * p;
  }

  function classify() {
    var key = state.layer.key;
    var vals = state.cells.map(function (c) { return value(c.props, key); })
      .filter(function (v) { return v !== null && isFinite(v); }).sort(function (a, b) { return a - b; });
    var distinct = vals.filter(function (v, i) { return i === 0 || v !== vals[i - 1]; });
    var n = Math.min(5, distinct.length), breaks = [];
    for (var i = 1; i < n; i++) {
      var b = nice(vals[Math.min(vals.length - 1, Math.floor(i * vals.length / n))]);
      if (b > (breaks.length ? breaks[breaks.length - 1] : -Infinity) && b > vals[0]) breaks.push(b);
    }
    var ramp = RAMPS[DARK && DARK.matches ? "dark" : "light"][state.layer.ramp], m = breaks.length + 1;
    state.breaks = breaks;
    state.colors = distinct.length ? Array.apply(null, Array(m)).map(function (_, j) {
      return ramp[m === 1 ? 3 : Math.round(j * (ramp.length - 1) / (m - 1))];
    }) : [];
  }

  function colour(v) {
    if (v === null || !isFinite(v) || !state.colors.length) return null;
    var j = 0;
    while (j < state.breaks.length && v >= state.breaks[j]) j++;
    return state.colors[j];
  }

  function swatch(fill) {
    var s = svg("svg", { width: "18", height: "14", viewBox: "0 0 18 14", "aria-hidden": "true", focusable: "false" });
    s.appendChild(svg("rect", { x: "0.5", y: "0.5", width: "17", height: "13", rx: "2", fill: fill, class: "swatch" }));
    return s;
  }

  function legend() {
    var box = $("embed-legend");
    box.textContent = "";
    box.appendChild(el("p", "legend-title", t("legendTitle", { layer: t(state.layer.key).toLowerCase() })));
    var list = el("ul", "legend-list");
    var vals = state.cells.map(function (c2) { return value(c2.props, state.layer.key); })
      .filter(function (v) { return v !== null && isFinite(v); });
    var lowest = Math.min.apply(null, vals), highest = Math.max.apply(null, vals);
    state.colors.forEach(function (c, j) {
      var lo = j === 0 ? null : state.breaks[j - 1], hi = j < state.breaks.length ? state.breaks[j] : null, text;
      if (lo === null && hi !== null) text = "< " + num(hi);
      else if (lo !== null && hi !== null) text = num(lo) + "–" + num(hi);
      else if (lo !== null) text = "≥ " + num(lo);
      else text = lowest === highest ? num(lowest) : num(lowest) + "–" + num(highest);     // one class only
      var li = el("li");
      li.appendChild(swatch(c));
      li.appendChild(el("span", "", text));
      list.appendChild(li);
    });
    var none = el("li");
    none.appendChild(swatch("url(#embed-nodata)"));
    none.appendChild(el("span", "", t("noValue")));
    list.appendChild(none);
    box.appendChild(list);
  }

  // ------------------------------------------------------------ drawing

  function defs(root) {
    var d = svg("defs");
    var p = svg("pattern", { id: "embed-nodata", width: "6", height: "6", patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" });
    p.appendChild(svg("rect", { width: "6", height: "6", class: "nodata-ground" }));
    p.appendChild(svg("line", { x1: "0", y1: "0", x2: "0", y2: "6", class: "nodata-line" }));
    d.appendChild(p);
    root.appendChild(d);
  }

  function label(c) {
    var p = c.props;
    return t("cellLabel", { name: p.name || p.cell_id, layer: t(state.layer.key).toLowerCase(), value: kw(value(p, state.layer.key)) });
  }

  function build() {
    var root = $("embed-svg");
    root.textContent = "";
    defs(root);
    var gPoly = svg("g", { class: "polys" }), gPts = svg("g", { class: "points" });
    root.appendChild(gPoly);
    root.appendChild(gPts);
    state.cells.forEach(function (c) {
      var shape = c.point ? svg("circle", { r: "9" }) : svg("path", { "fill-rule": "evenodd" });
      shape.setAttribute("class", "cell");
      shape.setAttribute("tabindex", "0");
      shape.setAttribute("role", "button");
      shape.setAttribute("data-i", String(c.i));
      shape.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(c); }
      });
      c.node = shape;
      (c.point ? gPts : gPoly).appendChild(shape);
    });
  }

  function paint() {
    classify();
    state.cells.forEach(function (c) {
      var col = colour(value(c.props, state.layer.key));
      c.node.setAttribute("fill", col || "url(#embed-nodata)");
      c.node.setAttribute("aria-label", label(c));
    });
    legend();
  }

  function draw() {
    var v = state.view;
    state.cells.forEach(function (c) {
      if (c.point) {
        c.node.setAttribute("cx", (c.point[0] * v.k + v.x).toFixed(1));
        c.node.setAttribute("cy", (c.point[1] * v.k + v.y).toFixed(1));
      } else {
        c.node.setAttribute("d", c.rings.map(function (r) {
          return "M" + r.map(function (p) { return (p[0] * v.k + v.x).toFixed(1) + " " + (p[1] * v.k + v.y).toFixed(1); }).join("L") + "Z";
        }).join(""));
      }
    });
    scale();
    tiles();
  }

  function scale() {
    // the ground distance of a bar at the map's centre latitude, rounded to 1, 2 or 5 times a power of ten
    if (!state.bbox) return;
    var wh = size(), v = state.view, y = (wh[1] / 2 - v.y) / v.k;
    var lat = Math.atan(Math.sinh(Math.PI * (1 - 2 * y)));
    var perPx = METRES_PER_WORLD * Math.cos(lat) / v.k, target = 96 * perPx;
    var p = Math.pow(10, Math.floor(Math.log10(target)));
    var metres = [5, 2, 1].map(function (f) { return f * p; }).filter(function (m) { return m <= target; })[0] || p;
    $("embed-scale").hidden = false;
    $("embed-scale-bar").style.width = Math.round(metres / perPx) + "px";
    $("embed-scale-text").textContent = metres >= 1000 ? num(metres / 1000) + " km" : num(metres) + " m";
  }

  function tiles() {
    if (!state.tiles) return;
    var box = $("embed-tiles"), wh = size(), v = state.view;
    var z = Math.min(Math.max(Math.round(Math.log2(v.k / 256)), 0), 19), n = Math.pow(2, z), s = v.k / n;
    var x0 = Math.floor(-v.x / s), x1 = Math.floor((wh[0] - v.x) / s), y0 = Math.max(0, Math.floor(-v.y / s)),
        y1 = Math.min(n - 1, Math.floor((wh[1] - v.y) / s)), keep = {};
    if ((x1 - x0 + 1) * (y1 - y0 + 1) > 120) return;
    for (var x = x0; x <= x1; x++) {
      for (var y = y0; y <= y1; y++) {
        var key = z + "/" + x + "/" + y, img = state.tileImgs[key];
        if (!img) {
          img = el("img", "tile");
          img.alt = "";
          img.decoding = "async";
          img.draggable = false;
          img.referrerPolicy = "no-referrer";
          img.src = state.tiles.split("{z}").join(String(z)).split("{x}").join(String(((x % n) + n) % n)).split("{y}").join(String(y));
          state.tileImgs[key] = img;
          box.appendChild(img);
        }
        img.style.width = img.style.height = Math.ceil(s) + 1 + "px";
        img.style.transform = "translate(" + (x * s + v.x).toFixed(1) + "px," + (y * s + v.y).toFixed(1) + "px)";
        keep[key] = true;
      }
    }
    Object.keys(state.tileImgs).forEach(function (k) {
      if (!keep[k]) { box.removeChild(state.tileImgs[k]); delete state.tileImgs[k]; }
    });
  }

  // ------------------------------------------------------------ the panel, the switch, the table

  function periodWord(m) {
    var month = m && ((m.period && m.period.kind === "month") || /^\d{4}-\d{2}$/.test(m.quarter || ""));
    return t(month ? "month" : "quarter");
  }

  function periodText(m) { return m && m.quarter ? periodWord(m) + " " + m.quarter : ""; }

  function role(m) {
    var s = m && m.signoff;
    if (!s || !s.valid || m.status !== "signerad") return null;
    var r = String(s.by || "").split(",").slice(1).join(",").trim();
    return r || null;
  }

  function row(dl, term, val) {
    dl.appendChild(el("dt", "", term));
    dl.appendChild(el("dd", "", val));
  }

  function select(c) {
    if (state.selected && state.selected.node) state.selected.node.classList.remove("selected");
    state.selected = c;
    c.node.classList.add("selected");
    var p = c.props, m = state.manifest || {};
    $("embed-panel-title").textContent = p.name || p.cell_id;
    var dl = $("embed-figures");
    dl.textContent = "";
    row(dl, t("generation_kw"), kw(value(p, "generation_kw")));
    row(dl, t("consumption_kw"), kw(value(p, "consumption_kw")));
    OPTIONAL.forEach(function (k) { if (k in p) row(dl, t(k), kw(value(p, k))); });
    row(dl, t("cell"), String(p.cell_id || NO_VALUE));
    row(dl, t("level"), t(p.level || "") || NO_VALUE);
    row(dl, t("customers"), num(p.customers));
    row(dl, t("period"), periodText(m) || String(p.quarter || NO_VALUE));
    row(dl, t("method"), String(m.method_version || NO_VALUE));
    if (m.status && m.status !== "signerad") row(dl, "", t("draft"));
  }

  function switcher(present) {
    var box = $("embed-switch");
    box.textContent = "";
    LAYERS.filter(function (l) { return present[l.key]; }).forEach(function (l, j) {
      var id = "embed-layer-" + l.key, input = el("input"), lab = el("label");
      input.type = "radio";
      input.name = "embed-layer";
      input.id = id;
      input.value = l.key;
      input.checked = j === 0;
      input.addEventListener("change", function () {
        state.layer = l;
        paint();
        if (state.selected) select(state.selected);
      });
      lab.htmlFor = id;
      lab.textContent = t(l.key);
      box.appendChild(input);
      box.appendChild(lab);
    });
    $("embed-controls").hidden = false;
  }

  function table(features, present) {
    var cols = ["name", "cell_id", "level", "customers", "generation_kw", "consumption_kw"]
      .concat(OPTIONAL.filter(function (k) { return present[k]; }));
    var tableEl = el("table"), cap = el("caption", "", t("caption", { period: periodWord(state.manifest).toLowerCase() + " " + ((state.manifest || {}).quarter || ""), n: features.length }));
    var head = el("thead"), tr = el("tr"), body = el("tbody");
    cols.forEach(function (c) {
      var th = el("th", NUMERIC.indexOf(c) >= 0 ? "num" : "",
        c === "cell_id" ? t("cell") : c === "level" ? t("level") : c === "name" ? t("name") : c === "customers" ? t("customers") : t(c) + " (" + t("kw") + ")");
      th.scope = "col";
      tr.appendChild(th);
    });
    head.appendChild(tr);
    features.forEach(function (f) {
      var p = f.properties || {}, r = el("tr");
      cols.forEach(function (c) {
        var isNum = NUMERIC.indexOf(c) >= 0;
        var text = isNum ? num(p[c]) : c === "level" ? t(p.level || "") : String(p[c] === null || p[c] === undefined || p[c] === "" ? NO_VALUE : p[c]);
        var td = el(c === "name" ? "th" : "td", isNum ? "num" : "", text);
        if (c === "name") td.scope = "row";
        r.appendChild(td);
      });
      body.appendChild(r);
    });
    tableEl.appendChild(cap);
    tableEl.appendChild(head);
    tableEl.appendChild(body);
    var box = $("embed-table");
    box.textContent = "";
    box.appendChild(tableEl);
  }

  function tableToggle() {
    var btn = $("embed-table-toggle"), box = $("embed-table");
    function set(open) {
      btn.setAttribute("aria-expanded", open ? "true" : "false");
      btn.textContent = open ? t("hideTable") : t("showTable");
      box.classList.toggle("sr-only", !open);
    }
    btn.hidden = false;
    set(false);
    btn.addEventListener("click", function () { set(btn.getAttribute("aria-expanded") !== "true"); });
  }

  function translate() {
    document.documentElement.lang = LANG;
    Array.prototype.forEach.call(document.querySelectorAll("[data-i18n]"), function (n) { n.textContent = t(n.getAttribute("data-i18n")); });
    Array.prototype.forEach.call(document.querySelectorAll("[data-i18n-label]"), function (n) { n.setAttribute("aria-label", t(n.getAttribute("data-i18n-label"))); });
  }

  function header(m) {
    var parts = [periodText(m), t("methodShort") + " " + (m.method_version || NO_VALUE)];
    var r = role(m);
    parts.push(r ? t("signedBy", { role: r }) : t("draft"));
    $("embed-meta").textContent = parts.join(" · ");
    var draft = m.status !== "signerad";
    document.body.classList.toggle("is-draft", draft);
    $("embed-draft-mark").hidden = !draft;
    $("embed-draft-note").hidden = !draft;
    var e = m.embed || {};
    if (e.map_tiles && /^https:\/\//.test(e.map_tiles) && e.map_attribution) {
      state.tiles = e.map_tiles;
      $("embed-attribution").textContent = e.map_attribution;
      $("embed-attribution").hidden = false;
    }
  }

  // ------------------------------------------------------------ input

  function interactions() {
    var map = $("embed-map"), drag = null;
    function local(e) { var r = map.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
    map.addEventListener("wheel", function (e) {
      e.preventDefault();
      var dy = e.deltaY * (e.deltaMode === 1 ? 40 : e.deltaMode === 2 ? 400 : 1), p = local(e);
      zoomAt(Math.exp(-dy * 0.0015), p[0], p[1]);
    }, { passive: false });
    map.addEventListener("pointerdown", function (e) {
      if (e.button !== 0 || (e.target.closest && e.target.closest("button"))) return;
      drag = { id: e.pointerId, x: e.clientX, y: e.clientY, moved: false, target: e.target };
      map.setPointerCapture(e.pointerId);
    });
    map.addEventListener("pointermove", function (e) {
      if (!drag || e.pointerId !== drag.id) return;
      var dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (!drag.moved && Math.abs(dx) + Math.abs(dy) < 4) return;
      drag.moved = true;
      map.classList.add("dragging");
      drag.x = e.clientX;
      drag.y = e.clientY;
      pan(dx, dy);
    });
    function end(e) {
      if (!drag || e.pointerId !== drag.id) return;
      var d = drag;
      drag = null;
      map.classList.remove("dragging");
      if (!d.moved && e.type === "pointerup") {
        var hit = d.target && d.target.closest ? d.target.closest("[data-i]") : null;
        if (hit) {
          var c = state.cells.filter(function (x) { return String(x.i) === hit.getAttribute("data-i"); })[0];
          if (c) { select(c); hit.focus({ preventScroll: true }); }
        }
      }
    }
    map.addEventListener("pointerup", end);
    map.addEventListener("pointercancel", end);
    map.addEventListener("keydown", function (e) {
      var wh = size();
      if (e.key === "+" || e.key === "=") zoomAt(1.5, wh[0] / 2, wh[1] / 2);
      else if (e.key === "-" || e.key === "_") zoomAt(1 / 1.5, wh[0] / 2, wh[1] / 2);
      else if (e.key === "ArrowLeft") pan(60, 0);
      else if (e.key === "ArrowRight") pan(-60, 0);
      else if (e.key === "ArrowUp") pan(0, 60);
      else if (e.key === "ArrowDown") pan(0, -60);
      else return;
      e.preventDefault();
    });
    $("embed-zoom-in").addEventListener("click", function () { var wh = size(); zoomAt(1.5, wh[0] / 2, wh[1] / 2); });
    $("embed-zoom-out").addEventListener("click", function () { var wh = size(); zoomAt(1 / 1.5, wh[0] / 2, wh[1] / 2); });
    $("embed-zoom-fit").addEventListener("click", fit);
    if (window.ResizeObserver) {
      var last = size();
      new ResizeObserver(function () {
        var now = size();
        if (now[0] === last[0] && now[1] === last[1]) return;
        pan((now[0] - last[0]) / 2, (now[1] - last[1]) / 2);
        last = now;
      }).observe(map);
    }
  }

  // ------------------------------------------------------------ start

  function getJSON(url) {
    return fetch(url, { credentials: "same-origin" }).then(function (r) {
      if (!r.ok) throw new Error(url + ": " + r.status);
      return r.json();
    });
  }

  function start() {
    translate();
    Promise.all([getJSON("../manifest.json"), getJSON("../capacity.geojson")]).then(function (res) {
      var m = res[0], gj = res[1], features = (gj && gj.features) || [];
      state.manifest = m;
      header(m);
      var present = {};
      features.forEach(function (f) { Object.keys(f.properties || {}).forEach(function (k) { present[k] = true; }); });
      present.generation_kw = present.consumption_kw = true;
      prepare(features);
      table(features, present);
      tableToggle();
      $("embed-stage").hidden = false;
      if (state.unplaced) {
        $("embed-unplaced").textContent = t("unplaced", { n: state.unplaced });
        $("embed-unplaced").hidden = false;
      }
      switcher(present);
      build();
      paint();
      if (DARK && DARK.addEventListener) DARK.addEventListener("change", function () { paint(); });
      interactions();
      fit();
      document.body.classList.add("is-ready");
    }).catch(function () {
      var note = el("p", "failed", t("failed"));
      var stage = $("embed-stage");
      stage.parentNode.insertBefore(note, stage);
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
