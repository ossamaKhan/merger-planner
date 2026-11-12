/* MergeCo Franchise Planner — planner page */
(() => {
  "use strict";

  const app = document.getElementById("app");
  const SC = app.dataset.scenario;
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const csrf = () => ($("input[name=csrfmiddlewaretoken]") || {}).value || "";
  const LS_KEY = `planner:v3:${SC}`;
  const ALL = "__all__";

  const S = {
    filters: null, res: null,
    months: [], selMonths: new Set(), agg: "avg",
    km: parseFloat(app.dataset.km) || 1, bydist: app.dataset.bydist === "1", clip: "district",
    method: app.dataset.method, real: parseFloat(app.dataset.real),
    region: ALL, level: "arm", unit: "", reviews: {},
    franchises: {}, newBuckets: new Set(), undo: [],
    dive: null, paint: { target: "", mode: "click" }, pend: new Map(),
    treeSide: "post", tblSide: "post", kpi: null, cols: null, expanded: new Set(),
    sort: { key: "sites", dir: -1 }, fit: true,
  };

  // ------------------------------------------------------------------ utils
  function fmt(v, dec) {
    if (v === null || v === undefined || Number.isNaN(v)) return "—";
    const a = Math.abs(v);
    if (a >= 1e9) return (v / 1e9).toFixed(2) + "B";
    if (a >= 1e6) return (v / 1e6).toFixed(2) + "M";
    if (a >= 1e4) return (v / 1e3).toFixed(1) + "K";
    return v.toLocaleString(undefined, { maximumFractionDigits: dec ?? (a < 10 ? 2 : a < 100 ? 1 : 0) });
  }
  const pct = (a, b) => (b ? ((a - b) / Math.abs(b)) * 100 : null);
  const pctTxt = (p) => (p === null || !isFinite(p) ? "" : `${p >= 0 ? "▲" : "▼"} ${Math.abs(p).toFixed(1)}%`);
  const colorOf = (code) => (S.franchises[code] && S.franchises[code].color) || "#999";
  function status(t, busy) { const el = $("#status"); el.textContent = t || ""; el.classList.toggle("busy", !!busy); }
  let toastT;
  function toast(t) { const el = $("#toast"); el.innerHTML = t; el.classList.remove("hidden"); clearTimeout(toastT); toastT = setTimeout(() => el.classList.add("hidden"), 4500); }
  function save() { try { localStorage.setItem(LS_KEY, JSON.stringify({ months: [...S.selMonths], agg: S.agg, clip: S.clip, region: S.region, level: S.level, unit: S.unit })); } catch (e) { /* storage unavailable */ } }
  function load() { try { return JSON.parse(localStorage.getItem(LS_KEY) || "null"); } catch (e) { return null; } }

  async function api(url, body) {
    const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() }, body: JSON.stringify(body) };
    const r = await fetch(url, opt);
    const txt = await r.text();
    if (!r.ok) throw new Error(`${r.status}: ${txt.replace(/<[^>]+>/g, " ").slice(0, 200)}`);
    return JSON.parse(txt);
  }
  function query() {
    const p = new URLSearchParams();
    p.set("months", [...S.selMonths].join("|"));
    p.set("agg", S.agg);
    p.set("km", S.km);
    p.set("bydist", S.bydist ? "1" : "0");
    p.set("clip", S.clip);
    scopeParams().forEach(([k, v]) => p.set(k, v));
    if (OPS.company() !== "both") p.set("company", OPS.company());
    return p.toString();
  }
  const resolve = (code) => {
    const mp = S.res.mapping; let cur = code; const seen = new Set([code]);
    while (mp[cur] && mp[cur] !== cur) { cur = mp[cur]; if (seen.has(cur)) break; seen.add(cur); }
    return cur;
  };
  // ------------------------------------------------------------------ level navigator (ASM -> BM -> City -> District -> Region)
  const LEVELS = { arm: { i: 4, name: "ASM", plural: "ASMs" }, bm: { i: 3, name: "BM", plural: "BMs" }, tehsil: { i: 2, name: "City", plural: "cities" },
    district: { i: 1, name: "District", plural: "districts" }, region: { i: 0, name: "Region", plural: "regions" } };
  function regionList() { return Object.keys(S.filters.regions).sort(); }
  function unitsFor(level) {
    const L_ = LEVELS[level], out = {};
    S.filters.combos.forEach((c) => {
      if (S.region !== ALL && isBig(S.region) ? c[6] !== bigName(S.region) : (level !== "region" && S.region !== ALL && c[0] !== S.region)) return;
      const v = level === "tehsil" ? `${c[1]} / ${c[2]}` : c[L_.i];
      if (!v) return;
      out[v] = (out[v] || 0) + c[5];
    });
    return Object.keys(out).sort().map((v) => ({ v, n: out[v] }));
  }
  function scopeParams() {
    const p = [];
    const u = S.unit;
    if (S.level === "region" && u) p.push(["regions", u]);
    else if (S.region !== ALL && isBig(S.region)) p.push(["bigs", bigName(S.region)]);
    else if (S.region !== ALL) p.push(["regions", S.region]);
    if (!u || S.level === "region") return p;
    if (S.level === "district") p.push(["districts", u]);
    if (S.level === "tehsil") { const [d, t] = u.split(" / "); p.push(["districts", d], ["tehsils", t]); }
    if (S.level === "bm") p.push(["bms", u]);
    if (S.level === "arm") p.push(["arms", u]);
    return p;
  }
  // Big Region (first column of the file, e.g. "Central B") sits above Region; values look like "big::Central B"
  const isBig = (v) => typeof v === "string" && v.startsWith("big::");
  const bigName = (v) => v.slice(5);
  const bigList = () => (S.filters && S.filters.bigs) || [];
  const regionLabel = (v) => (isBig(v) ? bigName(v) + " (Big Region)" : v);
  const isPartialView = () => !!S.unit || S.region !== ALL;
  const circle = () => (S.filters && S.filters.circle) || "Central B";
  const viewName = () => (S.unit ? `${LEVELS[S.level].name}: ${S.unit}` : S.region !== ALL ? regionLabel(S.region) : `${circle()} (all franchises)`)
    + ({ ufone: " · Ufone only", tp: " · TP only" }[OPS.company()] || "");
  const reviewed = (lvl, v) => (S.reviews[lvl] || []).includes(v);
  function drawNav() {
    const regs = regionList();
    const total = (r) => Object.values(S.filters.regions[r]).reduce((a, b) => a + b, 0);
    const bigs = bigList(), regBig = {};
    S.filters.combos.forEach((c) => { (regBig[c[0]] ||= new Set()).add(c[6]); });
    const bigN = (b) => S.filters.combos.filter((c) => c[6] === b).reduce((a, c) => a + c[5], 0);
    const regOpt = (r) => `<option value="${esc(r)}">   ${esc(r)} (${total(r)})</option>`;
    $("#selRegion").innerHTML = `<option value="${ALL}">${esc(circle())} — all franchises</option>` + (bigs.length > 1
      ? bigs.map((b) => `<optgroup label="Big Region: ${esc(b)}"><option value="big::${esc(b)}">${esc(b)} — all franchises (${bigN(b)})</option>`
          + regs.filter((r) => regBig[r] && regBig[r].has(b)).map(regOpt).join("") + "</optgroup>").join("")
      : `<optgroup label="Regions in ${esc(circle())}">` + regs.map(regOpt).join("") + "</optgroup>");
    $("#selRegion").value = S.region;
    $("#selRegion").disabled = S.level === "region" && !(bigList().length > 1);
    $$("#levelSeg button").forEach((b) => b.classList.toggle("on", b.dataset.v === S.level));
    const units = unitsFor(S.level);
    if (S.unit && !units.some((u) => u.v === S.unit)) S.unit = "";
    const L_ = LEVELS[S.level];
    $("#selUnit").innerHTML = (S.level === "region" ? `<option value="">${esc(circle())} — all franchises (${units.length} regions)</option>`
      : `<option value="">All ${L_.plural} in ${S.region !== ALL ? esc(regionLabel(S.region)) : esc(circle())} (${units.length})</option>`) +
      units.map((u) => `<option value="${esc(u.v)}">${reviewed(S.level, u.v) ? "✓ " : "  "}${esc(u.v)} · ${u.n} sites</option>`).join("");
    $("#selUnit").value = S.unit;
    const done = units.filter((u) => reviewed(S.level, u.v)).length;
    $("#progress").innerHTML = `<span class="pbar"><span style="width:${units.length ? (done / units.length) * 100 : 0}%"></span></span>${done}/${units.length} ${L_.plural} reviewed`;
    const isDone = S.unit && reviewed(S.level, S.unit);
    $("#btnDone").textContent = !S.unit ? "✓ Reviewed, next" : isDone ? "Reviewed ✓ · undo" : "✓ Reviewed, next";
    $("#btnDone").classList.toggle("btn-ghost", !!isDone);
    $("#btnDone").classList.toggle("btn", !isDone);
    $("#btnDone").disabled = !S.unit;
  }
  function goUnit(v) { S.unit = v; S.fit = true; save(); drawNav(); run(); }
  function stepUnit(dir) {
    const units = unitsFor(S.level).map((u) => u.v);
    if (!units.length) return;
    const i = units.indexOf(S.unit);
    goUnit(units[(i + dir + units.length) % units.length]);
  }
  $("#selRegion").addEventListener("change", (e) => { S.region = e.target.value; S.unit = ""; S.fit = true; save(); drawNav(); run(); });
  $("#levelSeg").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b || b.dataset.v === S.level) return;
    S.level = b.dataset.v;
    const first = unitsFor(S.level).find((u) => !reviewed(S.level, u.v));
    goUnit(first ? first.v : "");
  });
  $("#selUnit").addEventListener("change", (e) => goUnit(e.target.value));
  $("#uPrev").addEventListener("click", () => stepUnit(-1));
  $("#uNext").addEventListener("click", () => stepUnit(1));
  $("#btnDone").addEventListener("click", async () => {
    if (!S.unit) return;
    const wasDone = reviewed(S.level, S.unit);
    const r = await api(`/api/scenario/${SC}/review/`, { level: S.level, value: S.unit, done: !wasDone });
    S.reviews = r.reviews;
    if (wasDone) { drawNav(); toast(`${esc(S.unit)} marked as not reviewed.`); return; }
    const units = unitsFor(S.level).map((u) => u.v);
    const i = units.indexOf(S.unit);
    const next = [...units.slice(i + 1), ...units.slice(0, i)].find((v) => !reviewed(S.level, v));
    if (next) { toast(`✓ ${esc(S.unit)} reviewed. Next: <b>${esc(next)}</b>`); goUnit(next); }
    else {
      drawNav();
      const order = ["arm", "bm", "tehsil", "district", "region"];
      const nl = order[order.indexOf(S.level) + 1];
      toast(`All ${LEVELS[S.level].plural} reviewed.` + (nl ? ` Move up to <b>${LEVELS[nl].name}</b> level.` : " Open <b>Report &amp; KML</b> for the complete KML."));
    }
  });

  // ------------------------------------------------------------------ months
  function drawMonthChips() {
    $("#monthChips").innerHTML = S.months.slice().reverse().map((m) => `<label class="chip ${S.selMonths.has(m.key) ? "on" : ""}"><input type="checkbox" value="${m.key}" ${S.selMonths.has(m.key) ? "checked" : ""}>${esc(m.label)}</label>`).join("");
  }
  $("#monthChips").addEventListener("change", (e) => {
    e.target.checked ? S.selMonths.add(e.target.value) : S.selMonths.delete(e.target.value);
    if (!S.selMonths.size) { S.selMonths.add(e.target.value); toast("At least one month must stay selected."); }
    drawMonthChips(); save(); run();
  });
  $("#mAll").addEventListener("click", () => { S.selMonths = new Set(S.months.map((m) => m.key)); drawMonthChips(); save(); run(); });
  $("#mLatest").addEventListener("click", () => { S.selMonths = new Set([S.months[0].key]); drawMonthChips(); save(); run(); });

  // ------------------------------------------------------------------ settings
  $("#btnSettings").addEventListener("click", () => $("#settings").classList.toggle("hidden"));
  function fillSettings() { $("#km").value = S.km; $("#bydist").value = S.bydist ? "1" : "0"; $("#clip").value = S.clip; $("#method").value = S.method; $("#real").value = S.real; }
  $("#btnApplySettings").addEventListener("click", async () => {
    S.km = Math.max(0.1, parseFloat($("#km").value) || 1);
    S.bydist = $("#bydist").value === "1"; S.clip = $("#clip").value;
    S.method = $("#method").value; S.real = parseFloat($("#real").value) || 0;
    try { await api(`/api/scenario/${SC}/settings/`, { coverage_km: S.km, fill_within_district: S.bydist, projection_method: S.method, realization_pct: S.real }); } catch (e) { /* non-fatal */ }
    $("#settings").classList.add("hidden"); save(); run();
  });
  $("#scName").addEventListener("change", (e) => api(`/api/scenario/${SC}/settings/`, { name: e.target.value }).then(() => { document.title = e.target.value; toast("Renamed"); }));
  function segInit(id, getter, setter) {
    const el = $(id);
    const mark = () => $$("button", el).forEach((b) => b.classList.toggle("on", b.dataset.v === String(getter())));
    el.addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; setter(b.dataset.v); mark(); });
    mark();
    return mark;
  }
  const markAgg = segInit("#aggSeg", () => S.agg, (v) => { S.agg = v; save(); run(); });
  segInit("#treeSide", () => S.treeSide, (v) => { S.treeSide = v; drawHierarchy(); });
  segInit("#tblSide", () => S.tblSide, (v) => { S.tblSide = v; drawTable(); });
  segInit("#paintMode", () => S.paint.mode, (v) => { S.paint.mode = v; updatePaint(); });

  function showTab(t) {
    $$("#sideTabs button").forEach((x) => x.classList.toggle("on", x.dataset.t === t));
    $$(".tab").forEach((x) => x.classList.toggle("hidden", x.id !== "tab-" + t));
    const wide = t === "dive";
    if (app.classList.contains("wide-side") !== wide) {
      app.classList.toggle("wide-side", wide);
      setTimeout(() => { M.pre.map.invalidateSize(); M.post.map.invalidateSize(); }, 50);
    }
  }
  $$("#sideTabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.t)));

  // ------------------------------------------------------------------ maps
  const tiles = () => L.tileLayer("https://{s}.basemap.cartocdn.com/light_nolabels/{z}/{x}/{y}{r}.png", { attribution: "© OpenStreetMap © CARTO", subdomains: "abcd", maxZoom: 19 });
  // Place names (cities, towns, neighbourhoods such as Cantt, Gulgasht) drawn ABOVE the franchise colours
  const nameTiles = () => L.tileLayer("https://{s}.basemap.cartocdn.com/light_only_labels/{z}/{x}/{y}{r}.png", { subdomains: "abcd", maxZoom: 19, pane: "names" });
  const M = {};
  ["pre", "post"].forEach((side) => {
    const map = L.map(side === "pre" ? "mapPre" : "mapPost", { preferCanvas: true, zoomSnap: 0.5 }).setView([30.5, 72.0], 7);
    tiles().addTo(map);
    map.createPane("names"); map.getPane("names").style.zIndex = 450; map.getPane("names").style.pointerEvents = "none";
    map.createPane("areas"); map.getPane("areas").style.zIndex = 640;
    const areaGroup = L.layerGroup();
    M[side] = { map, layers: { names: L.layerGroup([nameTiles(), areaGroup]) }, markers: {}, areaGroup };
    map.on("moveend", () => side === "post" && loadAreaLabels());
    map.on("click", (ev) => { if (S.addingArea) addAreaAt(ev.latlng); });
    map.on("zoomend", () => map.getContainer().classList.toggle("z-low", map.getZoom() < 9));
  });
  // ---- area / neighbourhood names served by the app itself (built-in Punjab list + your own)
  let areaReq = 0;
  async function loadAreaLabels() {
    if (!layerOn("names")) return;
    const map = M.post.map, z = map.getZoom(), b = map.getBounds().pad(0.15), my = ++areaReq;
    let res;
    try {
      const resp = await fetch(`/api/areas/?bbox=${b.getSouth()},${b.getWest()},${b.getNorth()},${b.getEast()}&z=${z}`);
      if (!resp.ok) throw new Error(`server said ${resp.status}`);
      res = await resp.json();
    } catch (e) {
      if (!S.areaErr) { S.areaErr = true; toast(`Area names could not load (${esc(e.message)}). Check the black server window for the error.`); }
      return;
    }
    if (my !== areaReq) return;
    // de-clutter: keep a label only if its box does not overlap one already placed
    const placed = [], keep = [];
    const big = z <= 9 ? 1 : 0;
    res.labels.forEach((a) => {
      const pt = map.latLngToContainerPoint([a.lat, a.lon]);
      const w = a.name.length * 6.4 + 10, h = 15, x0 = pt.x - w / 2, y0 = pt.y - h / 2;
      const hit = placed.some((r) => x0 < r[2] && x0 + w > r[0] && y0 < r[3] && y0 + h > r[1]);
      if ((hit || keep.length >= 140) && a.t !== "yours") return;
      placed.push([x0 - 14, y0 - 7, x0 + w + 14, y0 + h + 7]);
      keep.push({ ...a, major: big || (a.pop || 0) >= 50000 });
    });
    ["pre", "post"].forEach((side) => {
      const g = M[side].areaGroup; g.clearLayers();
      keep.forEach((a) => {
        const cls = a.t === "yours" ? "arealbl yours" : (a.t === "neighbourhood" ? "arealbl nb" : "arealbl town") + (a.major && (a.pop || 0) >= 50000 ? " major" : "");
        if (a.poly) g.addLayer(L.geoJSON(a.poly, { interactive: false, style: { color: "#1f2a44", weight: 1.5, dashArray: "2 4", fill: false } }));
        const m = L.marker([a.lat, a.lon], { pane: "areas", interactive: a.t === "yours", keyboard: false,
          icon: L.divIcon({ className: cls, html: `<span>${esc(a.name)}</span>`, iconSize: null }) });
        if (a.t === "yours") m.bindPopup(`<div class="pp"><b>${esc(a.name)}</b>${a.city ? " · " + esc(a.city) : ""}<br><span class="muted small">your area name</span>
          <div class="pp-move"><button class="btn-ghost xs" data-arename="${a.id}" data-n="${esc(a.name)}" data-c="${esc(a.city || "")}">Rename</button>
          <button class="btn-ghost xs danger" data-adel="${a.id}">Delete</button></div></div>`);
        g.addLayer(m);
      });
    });
  }
  async function areaApi(body) {
    await api("/api/areas/", body);
    M.post.map.closePopup(); M.pre.map.closePopup();
    loadAreaLabels(); run(); // sites get re-labelled
  }
  function startAddArea() {
    S.addingArea = !S.addingArea;
    $("#btnAddArea").classList.toggle("on", S.addingArea);
    $("#maps").classList.toggle("adding-area", S.addingArea);
    if (S.addingArea) toast("Click the spot on either map where the area is (e.g. the middle of Gulgasht).");
  }
  async function addAreaAt(ll) {
    S.addingArea = false; $("#btnAddArea").classList.remove("on"); $("#maps").classList.remove("adding-area");
    const v = await choose(`<h3>Name this area</h3>
      <p class="small muted">Sites within 2 km (that are not closer to another of your names) will show this name.</p>
      <label class="small">Area / neighbourhood</label><input id="areaN" class="search" placeholder="e.g. Gulgasht" style="width:100%">
      <label class="small">City (optional)</label><input id="areaC" class="search" placeholder="e.g. Multan" style="width:100%">`,
      [{ label: "Save", v: "ok", cls: "btn" }, { label: "Cancel", v: "", cls: "btn-ghost" }]);
    if (v !== "ok") return;
    const name = ($("#areaN") || {}).value, city = ($("#areaC") || {}).value;
    if (!name || !name.trim()) return;
    await areaApi({ action: "add", name, city, lat: ll.lat, lon: ll.lng });
    toast(`Added area <b>${esc(name)}</b>.`);
  }
  document.addEventListener("click", async (e) => {
    const r = e.target.closest("[data-arename]");
    if (r) {
      const v = await choose(`<h3>Rename area</h3><label class="small">Area</label><input id="areaN" class="search" value="${r.dataset.n}" style="width:100%">
        <label class="small">City</label><input id="areaC" class="search" value="${r.dataset.c}" style="width:100%">`, [{ label: "Save", v: "ok", cls: "btn" }, { label: "Cancel", v: "", cls: "btn-ghost" }]);
      if (v === "ok") areaApi({ action: "rename", id: +r.dataset.arename, name: $("#areaN").value, city: $("#areaC").value });
    }
    const d = e.target.closest("[data-adel]");
    if (d && confirm("Delete this area name?")) areaApi({ action: "delete", id: +d.dataset.adel });
  });
  let findT = null;
  function initAreaFind() {
    const inp = $("#areaFind"), box = $("#areaHits");
    inp.addEventListener("input", () => {
      clearTimeout(findT);
      findT = setTimeout(async () => {
        if (inp.value.trim().length < 2) { box.classList.add("hidden"); return; }
        const res = await fetch(`/api/areas/?q=${encodeURIComponent(inp.value.trim())}`).then((x) => x.json());
        box.innerHTML = res.results.length ? res.results.map((a, i) => `<div class="hit" data-i="${i}"><b>${esc(a.name)}</b> <span class="muted small">${esc(a.city || "")} ${a.t === "yours" ? "· yours" : a.t === "town" ? "· town/village" : "· neighbourhood"}</span></div>`).join("") : `<div class="muted small hit">No match — add it with ＋ Area name</div>`;
        box.classList.remove("hidden");
        box.onclick = (ev) => { const h = ev.target.closest("[data-i]"); if (!h) return; const a = res.results[+h.dataset.i]; M.post.map.setView([a.lat, a.lon], 14); box.classList.add("hidden"); };
      }, 250);
    });
    inp.addEventListener("blur", () => setTimeout(() => box.classList.add("hidden"), 250));
  }
  let syncing = false;
  const sync = (a, b) => a.on("move", () => { if (syncing) return; syncing = true; b.setView(a.getCenter(), a.getZoom(), { animate: false }); syncing = false; });
  sync(M.pre.map, M.post.map); sync(M.post.map, M.pre.map);

  const boundary = {};
  async function loadBoundaries() {
    for (const lvl of ["districts", "tehsils"]) {
      try { boundary[lvl] = await fetch(`/api/boundaries/${lvl}/`).then((r) => r.json()); } catch (e) { boundary[lvl] = null; }
    }
    ["pre", "post"].forEach((side) => {
      const L_ = M[side].layers;
      ["districts", "tehsils"].forEach((lvl) => {
        if (!boundary[lvl]) return;
        L_[lvl] = L.geoJSON(boundary[lvl], {
          interactive: false,
          style: lvl === "districts" ? { color: "#1f2a44", weight: 1.4, fill: false, opacity: 0.7, dashArray: "6 4" } : { color: "#5b6478", weight: 0.6, fill: false, opacity: 0.55 },
        });
      });
    });
    applyToggles();
  }
  const layerOn = (name) => { const cb = $(`#layerToggles input[data-l="${name}"]`); return cb && cb.checked; };
  function applyToggles() {
    ["pre", "post"].forEach((side) => {
      const { map, layers } = M[side];
      Object.entries(layers).forEach(([name, lyr]) => {
        if (!lyr) return;
        if (layerOn(name)) { if (!map.hasLayer(lyr)) lyr.addTo(map); } else if (map.hasLayer(lyr)) map.removeLayer(lyr);
      });
    });
  }
  $$("#layerToggles input").forEach((cb) => cb.addEventListener("change", () => { applyToggles(); if (cb.dataset.l === "names") loadAreaLabels(); }));
  $("#btnAddArea").addEventListener("click", startAddArea);
  initAreaFind();
  $("#btnMapSize").addEventListener("click", () => {
    const big = $("#maps").classList.toggle("big");
    $("#btnMapSize").textContent = big ? "⤡ Smaller maps" : "⤢ Bigger maps";
    if (big) $("#maps").scrollIntoView({ behavior: "smooth", block: "start" });
    [50, 250, 450].forEach((t) => setTimeout(() => { M.pre.map.invalidateSize(); M.post.map.invalidateSize(); }, t));
  });

  function territoryPopup(p, side) {
    const row = side === "post" ? postByCode[p.code] : preByCode[p.code];
    const k = S.res.meta.primary;
    let extra = "";
    if (row && side === "post") extra = `<div class="pp-mem">${row.members.map((m) => `<span class="sw" style="background:${m.color}"></span>${esc(m.code)} (${fmt(m.sites)})`).join("<br>")}</div><button class="btn sm" data-dive="${esc(p.code)}">Deep dive ›</button>`;
    if (row && side === "pre") extra = `<div class="pp-mem">planned into: ${Object.entries(row.mapped_to).map(([c, n]) => `${esc(c)} (${n})`).join(", ")}</div>`;
    return `<div class="pp"><b>${esc(p.code)}</b><br>Sites: <b>${p.sites}</b> · Area ${fmt(p.area_km2)} km²<br>
      Covered ${fmt(p.covered_km2)} km² · Island-filled ${fmt(p.filled_km2)} km²<br>
      ${p.fragments > 1 ? `<span class="warn">${p.fragments} separate pieces</span><br>` : ""}
      ${row && k ? `${esc(k)}: <b>${fmt(row.kpis[k])}</b>` : ""}${extra}</div>`;
  }
  // every franchise code in the data (current + planned + tag targets), not just this view
  function allCodes() {
    return [...new Set([...Object.keys(S.filters.fr_sites || {}), ...Object.keys(S.franchises), ...Object.values(S.res ? S.res.mapping : {}), ...S.newBuckets])].filter(Boolean).sort();
  }
  function codeOptions(selected, inView, label) {
    const here = new Set(inView);
    const rest = allCodes().filter((c) => !here.has(c));
    const opt = (c) => {
      const n = S.filters.fr_sites[c], to = S.res && resolve(c) !== c ? ` → ${resolve(c)}` : "";
      return `<option value="${esc(c)}" ${c === selected ? "selected" : ""}>${esc(c)}${n ? ` · ${n} sites` : ""}${esc(to)}</option>`;
    };
    return `<optgroup label="${esc(label || "In this view")}">${inView.map(opt).join("")}</optgroup>` +
      (rest.length ? `<optgroup label="All other franchises (anywhere)">${rest.map(opt).join("")}</optgroup>` : "");
  }
  function targetOptions(selected, withBack) {
    return (withBack ? `<option value="__back__">↩ back to its own franchise</option>` : "") + codeOptions(selected, boardCodes());
  }
  function sitePopup(s) {
    const k = S.res.meta.primary;
    return `<div class="pp"><b>${esc(s.site_id)}</b> · ${esc(s.operator)} · ${esc(s.site_type || "")}<br>
      ${s.area ? `Area: <b>${esc(s.area)}</b>${s.area_src === "near town" ? ' <span class="muted small">(nearest town/village)</span>' : ""}<br>` : ""}
      ${esc(s.district)} / ${esc(s.tehsil)}<br>
      Current: <b style="color:${colorOf(s.pre)}">${esc(s.pre)}</b> → Plan: <b style="color:${colorOf(s.post)}">${esc(s.post)}</b>${s.overridden ? ' <span class="tag">moved</span>' : ""}<br>
      ${s.franchise_after_mocan ? `After MOCAN (file): ${esc(s.franchise_after_mocan)}<br>` : ""}
      ${k ? `${esc(k)}: ${fmt(s.kpi)}` : ""}
      <div class="pp-move"><select data-site="${esc(s.site_id)}">${targetOptions(s.post, s.overridden)}</select><button class="btn sm" data-move="${esc(s.site_id)}">Move</button></div></div>`;
  }
  document.addEventListener("click", (e) => {
    const mv = e.target.closest("[data-move]");
    if (mv) { const v = mv.parentElement.querySelector("select").value; moveSites([mv.dataset.move], v === "__back__" ? null : v); }
    const dv = e.target.closest("[data-dive]");
    if (dv) { e.preventDefault(); openDive(dv.dataset.dive); }
  });

  let preByCode = {}, postByCode = {};
  function drawMaps() {
    const r = S.res, g = r.geo;
    ["pre", "post"].forEach((side) => {
      const { layers } = M[side];
      ["terr", "fill", "islands", "strays", "circles", "sites", "labels", "context", "u_arm", "u_bm", "u_tehsil", "u_district", "u_region"].forEach((n) => { if (layers[n]) layers[n].remove(); layers[n] = null; });
      M[side].markers = {};
      layers.terr = L.geoJSON(g[side], {
        style: (f) => ({ color: f.properties.color, weight: 1.2, opacity: 0.9, fillColor: f.properties.color, fillOpacity: 0.42 }),
        onEachFeature: (f, l) => {
          l.bindTooltip(f.properties.code, { sticky: true, className: "tt" });
          l.on("click", (ev) => {
            if (side === "post" && S.paint.target) return; // painting: ignore territory clicks
            if (side === "post") { openDive(f.properties.code, false); L.popup().setLatLng(ev.latlng).setContent(territoryPopup(f.properties, side)).openOn(M.post.map); }
            else { highlight(f.properties.code, side, false); L.popup().setLatLng(ev.latlng).setContent(territoryPopup(f.properties, side)).openOn(M.pre.map); }
          });
        },
      });
      layers.fill = L.geoJSON(g["fill_" + side], { interactive: false, style: { stroke: false, fillColor: "#ffffff", fillOpacity: 0.5 } });
      layers.islands = L.geoJSON(g.islands, {
        style: { color: "#c81e1e", weight: 1.6, dashArray: "4 3", fillColor: "#c81e1e", fillOpacity: 0.28 },
        onEachFeature: (f, l) => l.bindPopup(`<b>Uncovered pocket</b><br>${f.properties.km2} km²<br>Nearest site ${esc(f.properties.nearest_site)}<br>Filled by <b>${esc(f.properties.filled_by)}</b>`),
      });
      const key = side === "post" ? "post" : "pre";
      const circles = [], dots = [];
      r.sites.forEach((s) => {
        if (s.latitude == null || s.longitude == null || !OPS.visible(s.operator)) return;
        const c = colorOf(s[key]);
        circles.push(L.circle([s.latitude, s.longitude], { radius: S.km * 1000, color: c, weight: 0.6, fillOpacity: 0.06, interactive: false }));
        const m = OPS.marker([s.latitude, s.longitude], s.operator, markerStyle(s, side));
        m.bindTooltip(`${s.site_id} · ${OPS.NAME[OPS.kind(s.operator)]} · ${s[key]}${s.area ? "<br>" + esc(s.area) : ""}`, { className: "tt" });
        m.on("click", () => {
          if (side === "post" && S.paint.target && S.paint.mode === "click") { queueMoves([s.site_id]); return; }
          L.popup().setLatLng(m.getLatLng()).setContent(sitePopup(s)).openOn(M[side].map);
        });
        M[side].markers[s.site_id] = m;
        dots.push(m);
      });
      layers.circles = L.layerGroup(circles);
      // island (stray) sites: red rings, on the POST map (and PRE so you can compare)
      layers.strays = L.layerGroup((r.strays || []).filter((x) => OPS.visible(x.operator)).map((x) =>
        L.circleMarker([x.latitude, x.longitude], { radius: 11, color: "#c81e1e", weight: 2.5, dashArray: "3 2", fill: false })
          .bindTooltip(`<b>Island:</b> ${esc(x.site_id)} (${esc(x.post)})<br>inside ${esc(x.suggest)}'s area · ${x.suggest_n}/${x.k} nearest sites`, { className: "tt" })
          .on("click", () => openStray(x.site_id))));
      layers.sites = L.layerGroup(dots);
      layers.labels = L.layerGroup(g[side].features.filter((f) => f.properties.ly != null).map((f) => L.marker([f.properties.ly, f.properties.lx], {
        interactive: false, icon: L.divIcon({ className: "frlabel", html: `<span style="border-color:${f.properties.color}">${esc(f.properties.code)}</span>`, iconSize: null }),
      })));
      // grey surroundings: what lies outside the current filter
      const ctxDots = (g.context_sites || []).filter((c) => OPS.visible(c[6])).map((c) => OPS.marker([c[1], c[2]], c[6], { radius: 2.6, fillColor: "#8a93a6", fillOpacity: 0.9, color: "#fff", weight: 0.5 })
        .bindTooltip(`${esc(c[0])} · ${esc(OPS.NAME[OPS.kind(c[6])])} · ${esc(c[3])}<br>ASM ${esc(c[4])} · BM ${esc(c[5])}<br><i>outside this view</i>`, { className: "tt" }));
      layers.context = L.layerGroup([L.geoJSON(g.context, {
        style: { color: "#6b7385", weight: 1, dashArray: "3 3", fillColor: "#b8bfcc", fillOpacity: 0.35 },
        onEachFeature: (f, l) => { const p = f.properties; l.bindTooltip(outsideTip(p), { sticky: true, className: "tt" }); },
      }), ...ctxDots]);
      // hierarchy borders of the view
      Object.entries((g.units || {})).forEach(([lvl, fc]) => {
        if (lvl === "district") return; // admin district polygons already shown
        const st = { arm: { color: "#111", weight: 2.6, dashArray: "8 5" }, bm: { color: "#7a1fa2", weight: 3.2, dashArray: "12 6" },
          tehsil: { color: "#0b6e4f", weight: 2, dashArray: "2 4" }, region: { color: "#c81e1e", weight: 4 } }[lvl];
        layers["u_" + lvl] = L.geoJSON(fc, { style: { ...st, fill: false, opacity: 0.9 }, interactive: true,
          onEachFeature: (f, l) => l.bindTooltip(`<b>${esc({ arm: "ASM", bm: "BM", tehsil: "City", region: "Region" }[lvl])}: ${esc(f.properties.name)}</b><br>${f.properties.sites} sites · ${f.properties.pre.length} → ${f.properties.post.length} franchises`, { sticky: true, className: "tt" }) });
      });
    });
    applyToggles();
    drawOutside();
    OPS.counts(r.sites);
    $("#preCount").textContent = `${r.summary.pre_franchises} franchises`;
    $("#postCount").textContent = `${r.summary.post_franchises} franchises`;
    if (S.fit) { fitScope(); S.fit = false; }
    if (S.dive && postByCode[S.dive]) highlight(S.dive, "post", false);
  }
  function outsideTip(p) {
    return `<b>Outside this view</b><br>${esc(p.code)} · ${p.sites} sites<br>ASM: ${esc(p.arms.join(", "))}<br>BM: ${esc(p.bms.join(", "))}<br>City: ${esc(p.tehsils.join(", "))}`;
  }
  function drawOutside() {
    const cs = S.res.geo.context_summary || [];
    const el = $("#outside");
    el.classList.toggle("hidden", !cs.length);
    if (!cs.length) return;
    const tot = cs.reduce((a, c) => a + c.sites, 0);
    const arms = {};
    cs.forEach((c) => c.arms.forEach((a) => { arms[a] = (arms[a] || 0) + 1; }));
    el.innerHTML = `<div class="o-head"><span class="sw grey"></span><b>Grey area = outside ${esc(viewName())}</b> · ${tot} neighbouring sites in ${cs.length} planned franchise(s). Their territories are drawn so you see the full picture; they don't count in this view's numbers.</div>
      <div class="o-list">${cs.slice(0, 12).map((c) => `<div class="o-item"><span class="sw" style="background:${c.color}"></span><b>${esc(c.code)}</b> ${c.sites} sites
        <span class="muted">· ${c.arms.map((a) => `<a href="#" data-goarm="${esc(a)}">ASM ${esc(a)}</a>`).join(", ")} · BM ${esc(c.bms.join(", "))} · ${esc(c.tehsils.join(", "))}</span></div>`).join("")}
        ${cs.length > 12 ? `<div class="muted small">+ ${cs.length - 12} more</div>` : ""}</div>`;
  }
  $("#outside").addEventListener("click", (e) => {
    const a = e.target.closest("[data-goarm]"); if (!a) return;
    e.preventDefault(); S.level = "arm"; S.region = ALL; goUnit(a.dataset.goarm);
  });
  function markerStyle(s, side) {
    const pendingTo = side === "post" ? S.pend.get(s.site_id) : undefined;
    if (pendingTo !== undefined) {
      const c = pendingTo === "__back__" ? colorOf(s.pre) : colorOf(pendingTo);
      return { radius: 6, fillColor: OPS.fill(s.operator, c), fillOpacity: 1, color: "#ff0080", weight: 2.5 };
    }
    const c = colorOf(side === "post" ? s.post : s.pre);
    const ring = side === "post" && s.overridden;
    return { radius: 4, fillColor: OPS.fill(s.operator, c), fillOpacity: 0.95, color: ring ? "#111" : "#fff", weight: ring ? 2 : 0.8 };
  }
  function fitScope() {
    const pts = S.res.sites.filter((s) => s.latitude != null).map((s) => [s.latitude, s.longitude]);
    if (pts.length) M.post.map.fitBounds(pts, { padding: [20, 20], maxZoom: 13 });
  }
  function highlight(code, side, zoom = true) {
    const key = side === "post" ? "post" : "pre";
    ["pre", "post"].forEach((sd) => {
      const t = M[sd].layers.terr; if (!t) return;
      t.eachLayer((l) => {
        const hit = l.feature.properties.code === code && sd === side;
        l.setStyle({ weight: hit ? 3.5 : 1.2, fillOpacity: hit ? 0.65 : (sd === side ? 0.25 : 0.42) });
        if (hit) l.bringToFront();
      });
    });
    if (zoom) {
      const pts = S.res.sites.filter((s) => s[key] === code && s.latitude != null).map((s) => [s.latitude, s.longitude]);
      if (pts.length) M.post.map.fitBounds(pts, { padding: [40, 40], maxZoom: 13 });
    }
  }
  function clearHighlight() {
    ["pre", "post"].forEach((sd) => { const t = M[sd].layers.terr; if (t) t.eachLayer((l) => l.setStyle({ weight: 1.2, fillOpacity: 0.42 })); });
  }

  // ------------------------------------------------------------------ site moves (paint on POST map)
  function updatePaint() {
    const on = !!S.paint.target;
    const m = M.post.map;
    m.getContainer().classList.toggle("painting", on);
    if (on && S.paint.mode === "box") m.dragging.disable(); else m.dragging.enable();
    $("#paintHint").textContent = !on ? "Pick a target, then click sites on the POST map (or drag a box)."
      : S.paint.mode === "click" ? `Click sites on the POST map to give them to ${S.paint.target === "__back__" ? "their own franchise" : S.paint.target}.`
        : "Drag a box on the POST map — every site inside is moved.";
    $("#assignBar").classList.toggle("active", on);
  }
  $("#paintTarget").addEventListener("change", (e) => { S.paint.target = e.target.value; updatePaint(); });
  function queueMoves(ids) {
    const t = S.paint.target;
    ids.forEach((id) => {
      const s = S.res.sites.find((x) => x.site_id === id);
      if (!s) return;
      if ((t === "__back__" && !s.overridden) || t === s.post) S.pend.delete(id); else S.pend.set(id, t);
      const mk = M.post.markers[id]; if (mk) mk.setStyle(markerStyle(s, "post"));
    });
    drawPending();
  }
  function drawPending() {
    const n = S.pend.size;
    $("#pendingN").textContent = `${n} site move${n === 1 ? "" : "s"} not saved`;
    ["#pendingN", "#btnPendSave", "#btnPendDiscard"].forEach((x) => $(x).classList.toggle("hidden", !n));
  }
  $("#btnPendDiscard").addEventListener("click", () => { S.pend.clear(); drawPending(); drawMaps(); });
  $("#btnPendSave").addEventListener("click", async () => {
    const groups = {};
    S.pend.forEach((t, id) => (groups[t] ||= []).push(id));
    status("Saving site moves…", true);
    try {
      for (const [t, ids] of Object.entries(groups)) await api(`/api/scenario/${SC}/override/`, { site_ids: ids, target: t === "__back__" ? null : t });
      toast(`Saved ${S.pend.size} site move(s).`);
      S.pend.clear(); drawPending(); run();
    } catch (e) { status("Save failed: " + e.message); }
  });
  let boxStart = null, boxRect = null;
  M.post.map.on("mousedown", (e) => { if (!(S.paint.target && S.paint.mode === "box")) return; boxStart = e.latlng; boxRect = L.rectangle([boxStart, boxStart], { color: "#ff0080", weight: 1.5, dashArray: "4 3", fillOpacity: 0.08 }).addTo(M.post.map); });
  M.post.map.on("mousemove", (e) => { if (boxStart && boxRect) boxRect.setBounds([boxStart, e.latlng]); });
  M.post.map.on("mouseup", () => {
    if (!boxStart) return;
    const b = boxRect.getBounds(); boxRect.remove(); boxStart = null;
    queueMoves(S.res.sites.filter((s) => s.latitude != null && OPS.visible(s.operator) && b.contains([s.latitude, s.longitude])).map((s) => s.site_id));
  });
  async function moveSites(ids, target) {
    status(`Moving ${ids.length} site(s)…`, true);
    try {
      const byId = Object.fromEntries(S.res.sites.map((s) => [s.site_id, s]));
      const items = ids.map((id) => [id, byId[id] && byId[id].overridden ? byId[id].post : null]);
      await api(`/api/scenario/${SC}/override/`, { site_ids: ids, target });
      S.undo.push({ kind: "ov", items });
      M.post.map.closePopup();
      toast(`${ids.length} site(s) ${target ? "moved to " + esc(target) : "returned to their own franchise"}.`);
      await run();
    } catch (e) { status("Move failed: " + e.message); }
  }

  // ------------------------------------------------------------------ merge board
  function boardCodes() {
    return [...new Set([...S.newBuckets, ...S.res.post.map((p) => p.code)])].sort();
  }
  function buildBuckets() {
    const b = {};
    S.res.post.forEach((p) => { b[p.code] = { code: p.code, row: p, chips: [] }; });
    S.res.pre.forEach((pr) => {
      const total = Object.values(pr.mapped_to).reduce((a, x) => a + x, 0);
      Object.entries(pr.mapped_to).forEach(([post, n]) => { if (b[post]) b[post].chips.push({ code: pr.code, n, total, color: pr.color, op: Object.keys(pr.operators)[0] || "" }); });
    });
    S.newBuckets.forEach((c) => { if (!b[c]) b[c] = { code: c, row: null, chips: [] }; });
    const here = new Set(S.res.pre.map((p) => p.code));
    Object.keys(S.res.mapping).forEach((c) => {  // merged in from outside this view
      const t = resolve(c);
      if (b[t] && !here.has(c) && c !== t) { const n = S.filters.fr_sites[c] || 0; b[t].chips.push({ code: c, n, total: n, color: colorOf(c), op: "", away: true }); }
    });
    Object.values(b).forEach((x) => x.chips.sort((p, q) => (p.code === x.code ? -1 : q.code === x.code ? 1 : q.n - p.n)));
    return b;
  }
  function drawBoard() {
    if (!S.res) return;
    const k = S.res.meta.primary;
    const q = $("#boardSearch").value.trim().toLowerCase();
    const list = Object.values(buildBuckets()).filter((b) => {
      if (q && !(b.code.toLowerCase().includes(q) || b.chips.some((c) => c.code.toLowerCase().includes(q)))) return false;
      return true;
    }).sort((a, b) => (!a.row ? -1 : !b.row ? 1 : b.row.sites - a.row.sites));
    $("#board").innerHTML = list.map((b) => {
      const r = b.row;
      const proj = r && k ? pct(r.projected[k], r.kpis[k]) : null;
      const chips = b.chips.map((c) => {
        const head = c.code === b.code;
        const g = S.filters.fr_sites[c.code] || c.total;
        const split = c.n !== g ? `${c.n}<span class="of">/${g}</span>` : `${c.n}`;
        if (c.away) return `<span class="fchip away" draggable="true" data-chip="${esc(c.code)}" data-bucket="${esc(b.code)}" title="Whole franchise merged here from outside this view (${c.n} sites). × to undo">
          <span class="sw" style="background:${c.color}"></span>${esc(c.code)}<small>${c.n} · outside view</small><button class="x" data-untag="${esc(c.code)}" title="Undo">×</button></span>`;
        return `<span class="fchip ${head ? "head" : ""}" draggable="true" data-chip="${esc(c.code)}" data-bucket="${esc(b.code)}" title="${c.n} site(s) here${c.n !== g ? ` of ${g} in total` : ""}. ${head ? "Drag onto another box to merge it there" : "Merged here — drag elsewhere or × to undo"}">
          <span class="sw" style="background:${c.color}"></span>${esc(c.code)}<span class="op op-${esc(c.op.toLowerCase())}">${esc(c.op.slice(0, 2).toUpperCase())}</span><small>${split}</small>
          ${head ? "" : `<button class="x" data-untag="${esc(c.code)}" title="Undo">×</button>`}</span>`;
      }).join("");
      return `<div class="bucket ${r ? "" : "empty"} ${S.dive === b.code ? "diving" : ""}" data-b="${esc(b.code)}" style="--c:${r ? r.color : colorOf(b.code)}">
        <div class="bhead"><span class="sw" style="background:${r ? r.color : colorOf(b.code)}"></span><a href="#" class="bcode" data-dive="${esc(b.code)}">${esc(b.code)}</a>
          ${r && r.is_new || !r ? '<span class="tag">new</span>' : ""}
          <span class="spacer"></span>
        </div>
        ${r ? `<div class="bstat">${fmt(r.sites)} sites · ${b.chips.length} franchise${b.chips.length > 1 ? "s" : ""}${k ? ` · ${esc(k)} ${fmt(r.kpis[k])}` : ""}${proj > 0.05 ? ` <span class="up">${pctTxt(proj)}</span>` : ""}</div>` : '<div class="bstat muted">Empty — drop franchises here</div>'}
        <div class="bchips">${chips}</div></div>`;
    }).join("") || '<div class="muted pad">No franchises match.</div>';
    $("#btnUndo").disabled = !S.undo.length;
  }
  const board = $("#board");
  board.addEventListener("dragstart", (e) => {
    const c = e.target.closest("[data-chip]"); if (!c) return;
    e.dataTransfer.setData("text/plain", JSON.stringify([c.dataset.chip]));
    e.dataTransfer.effectAllowed = "move";
    board.classList.add("dragging");
  });
  board.addEventListener("dragend", () => { board.classList.remove("dragging"); $$(".bucket.over").forEach((x) => x.classList.remove("over")); });
  board.addEventListener("dragover", (e) => { const b = e.target.closest(".bucket"); if (!b) return; e.preventDefault(); $$(".bucket.over").forEach((x) => x !== b && x.classList.remove("over")); b.classList.add("over"); });
  board.addEventListener("drop", (e) => {
    const b = e.target.closest(".bucket"); if (!b) return;
    e.preventDefault(); b.classList.remove("over");
    let codes; try { codes = JSON.parse(e.dataTransfer.getData("text/plain")); } catch (x) { return; }
    if (codes.length === 1 && b.querySelector(`[data-chip="${CSS.escape(codes[0])}"]`)) return; // dropped back on its own box
    tagWithChoice(codes[0], b.dataset.b);
  });
  board.addEventListener("click", (e) => {
    const x = e.target.closest("[data-untag]");
    if (x) { e.stopPropagation(); untag(x.dataset.untag, x.closest(".bucket").dataset.b); return; }
    if (e.target.closest("[data-dive]")) return; // handled globally
    const c = e.target.closest("[data-chip]");
    if (c) { $("#tfFrom").value = c.dataset.chip; drawTagScope(); $("#tfTo").focus(); }
  });
  $("#boardSearch").addEventListener("input", drawBoard);

  async function merge(codes, target) {
    const changes = {}, inverse = {};
    codes.forEach((c) => {
      const cur = S.res.mapping[c] || null;
      const nv = c === target ? null : target;
      if (cur === nv) return;
      changes[c] = nv; inverse[c] = cur;
    });
    if (!Object.keys(changes).length) return;
    const followers = S.res.pre.filter((p) => !codes.includes(p.code) && codes.some((c) => resolve(p.code) === c && p.code !== c)).map((p) => p.code);
    status("Saving…", true);
    try { await api(`/api/scenario/${SC}/mapping/`, { changes }); } catch (e) { status("Save failed: " + e.message); return; }
    S.undo.push({ kind: "map", inverse }); S.newBuckets.delete(target);
    toast(`${codes.map(esc).join(", ")} → <b>${esc(target)}</b>` + (followers.length ? `<br><span class="muted">also moved (were merged into it): ${followers.map(esc).join(", ")}</span>` : ""));
    run();
  }
  async function untag(code, bucket) {
    if (resolve(code) === bucket && S.res.mapping[code]) {
      await api(`/api/scenario/${SC}/mapping/`, { changes: { [code]: null } });
      S.undo.push({ kind: "map", inverse: { [code]: S.res.mapping[code] } });
      toast(`${esc(code)} is its own franchise again.`);
      run();
    } else {
      const ids = S.res.sites.filter((s) => s.pre === code && s.post === bucket && s.overridden).map((s) => s.site_id);
      if (ids.length) moveSites(ids, null);
    }
  }
  // ---- tagging: whole franchise vs only its sites in this view
  function sitesInView(code) { return S.res.sites.filter((s) => s.pre === code); }
  async function tagSites(code, target) {
    const sites = sitesInView(code);
    const natural = resolve(code);
    const items = sites.map((s) => [s.site_id, s.overridden ? s.post : null]);
    const back = target === natural;
    await api(`/api/scenario/${SC}/override/`, { site_ids: sites.map((s) => s.site_id), target: back ? null : target });
    S.undo.push({ kind: "ov", items }); S.newBuckets.delete(target);
    toast(`${sites.length} site(s) of ${esc(code)} in ${esc(viewName())} → <b>${esc(target)}</b><br><span class="muted">its other sites stay with ${esc(natural)}</span>`);
    run();
  }
  function choose(html, buttons) {
    return new Promise((resolve_) => {
      $("#modalBody").innerHTML = html;
      $("#modalBtns").innerHTML = buttons.map((b, i) => `<button class="${b.cls || "btn"}" data-i="${i}">${b.label}</button>`).join("");
      $("#modal").classList.remove("hidden");
      $("#modalBtns").onclick = (e) => { const b = e.target.closest("button"); if (!b) return; $("#modal").classList.add("hidden"); resolve_(buttons[+b.dataset.i].v); };
      $("#modal").onclick = (e) => { if (e.target.id === "modal") { $("#modal").classList.add("hidden"); resolve_(null); } };
    });
  }
  async function tagWithChoice(code, target, mode) {
    if (!code || !target || code === target && !S.res.mapping[code] && !sitesInView(code).some((s) => s.overridden)) return;
    const here = sitesInView(code).length;
    const all = S.filters.fr_sites[code] || here;
    if (!mode) mode = !here || here >= all || !isPartialView() ? "whole" : await choose(
      `<h3>Tag <b>${esc(code)}</b> → <b>${esc(target)}</b></h3>
       <p>${esc(code)} has <b>${here}</b> of its <b>${all}</b> sites in ${esc(viewName())}.</p>`,
      [{ label: `Whole franchise<br><small>all ${all} sites, every area</small>`, v: "whole" },
       { label: `Only these ${here} sites<br><small>just ${esc(viewName())}</small>`, v: "view", cls: "btn alt" },
       { label: "Cancel", v: null, cls: "btn-ghost" }]);
    if (mode === "whole") return merge([code], target);
    if (mode === "view") return here ? tagSites(code, target) : merge([code], target);
  }
  function fillTagForm() {
    const from = $("#tfFrom").value, to = $("#tfTo").value;
    const preHere = [...S.res.pre].map((p) => p.code).sort();
    $("#tfFrom").innerHTML = `<option value="">franchise…</option>` + codeOptions(null, preHere);
    $("#tfTo").innerHTML = `<option value="">into…</option>` + codeOptions(null, boardCodes()) + `<option value="__new__">＋ new franchise code…</option>`;
    if ([...$("#tfFrom").options].some((o) => o.value === from)) $("#tfFrom").value = from;
    if ([...$("#tfTo").options].some((o) => o.value === to)) $("#tfTo").value = to;
    drawTagScope();
  }
  function drawTagScope() {
    const code = $("#tfFrom").value;
    if (!code) { $("#tfScope").innerHTML = '<span class="muted small">Tip: click a chip to fill this in.</span>'; return; }
    const here = sitesInView(code).length, all = S.filters.fr_sites[code] || here;
    if (!here) { $("#tfScope").innerHTML = `<span class="muted small">${esc(code)} is outside this view — the whole franchise (${all} sites) is tagged.</span>`; return; }
    $("#tfScope").innerHTML = here >= all || !isPartialView() ? `<span class="muted small">Moves all ${all} sites of ${esc(code)}.</span>` :
      `<label><input type="radio" name="tfMode" value="whole" checked> whole franchise (${all} sites)</label>
       <label><input type="radio" name="tfMode" value="view"> only ${here} site(s) in ${esc(viewName())}</label>`;
  }
  $("#tfFrom").addEventListener("change", drawTagScope);
  $("#tfTo").addEventListener("change", async (e) => {
    if (e.target.value !== "__new__") return;
    const code = await newFranchise();
    fillTagForm(); $("#tfTo").value = code || "";
  });
  $("#tfGo").addEventListener("click", () => {
    const code = $("#tfFrom").value, to = $("#tfTo").value;
    if (!code || !to || to === "__new__") { toast("Choose both franchises."); return; }
    const m = $("input[name=tfMode]:checked");
    tagWithChoice(code, to, m ? m.value : "whole");
  });

  $("#btnUndo").addEventListener("click", async () => {
    const u = S.undo.pop(); if (!u) return;
    if (u.kind === "map") await api(`/api/scenario/${SC}/mapping/`, { changes: u.inverse });
    else {
      const groups = {};
      u.items.forEach(([id, prev]) => (groups[prev || ""] ||= []).push(id));
      for (const [t, ids] of Object.entries(groups)) await api(`/api/scenario/${SC}/override/`, { site_ids: ids, target: t || null });
    }
    toast("Undone."); run();
  });
  async function newFranchise() {
    const code = (prompt("New franchise code (e.g. MUL-NEW-01)") || "").trim().toUpperCase(); if (!code) return null;
    const name = prompt("Display name (optional)", code) || code;
    const r = await api("/api/franchise/", { code, name });
    S.franchises[r.franchise.code] = { ...r.franchise };
    S.newBuckets.add(code); drawBoard(); fillTargets();
    toast(`Created <b>${esc(code)}</b> — drag franchises into it.`);
    return code;
  }
  $("#btnNewFr").addEventListener("click", newFranchise);
  $("#btnSeed").addEventListener("click", async () => {
    if (!confirm("Tag every franchise to the 'Franchise ID After MOCAN' most of its sites carry (latest month)? Existing tags for those franchises are overwritten.")) return;
    status("Seeding…", true);
    const r = await api(`/api/scenario/${SC}/seed/`, {});
    toast(`Seeded ${r.mappings} tags, ${r.overrides} site moves.`); run();
  });
  $("#btnResetAll").addEventListener("click", async () => {
    if (!confirm("Remove ALL franchise tags and site moves from this scenario?")) return;
    await api(`/api/scenario/${SC}/reset/`, { what: "all" }); S.undo = []; run();
  });

  // ------------------------------------------------------------------ deep dive
  function openDive(code, switchTab = true) {
    S.dive = code;
    if (switchTab) showTab("dive");
    drawDive(); drawBoard();
    if (postByCode[code]) highlight(code, "post", switchTab);
  }
  $("#diveSel").addEventListener("change", (e) => { if (e.target.value) openDive(e.target.value); else { S.dive = null; clearHighlight(); drawDive(); } });
  function drawDive() {
    const r = S.res; if (!r) return;
    $("#diveSel").innerHTML = `<option value="">— choose a planned franchise —</option>` + [...r.post].sort((a, b) => b.sites - a.sites).map((p) => `<option value="${esc(p.code)}" ${p.code === S.dive ? "selected" : ""}>${esc(p.code)} · ${fmt(p.sites)} sites · ${p.members.length} member(s)</option>`).join("");
    const p = postByCode[S.dive];
    if (!p) { $("#dive").innerHTML = '<div class="muted pad">Pick a planned franchise above, or click one on the POST map, the board or the table.</div>'; return; }
    const k = r.meta.primary;
    const mem = p.members.slice(0, 4);
    const rest = p.members.slice(4);
    const kpiRows = [...r.meta.kpis.map((x) => ({ label: x.label, get: (o) => o.kpis[x.key], proj: p.projected[x.key], tr: p.trend_next[x.key], lb: x.lower_better })),
      ...r.meta.derived.map((d) => ({ label: d.label, get: (o) => o.derived[d.label], proj: p.projected_derived[d.label], tr: null, lb: d.lower_better, der: true }))];
    const sites = r.sites.filter((s) => s.post === p.code);
    const teh = {}; sites.forEach((s) => { teh[`${s.district} / ${s.tehsil}`] = (teh[`${s.district} / ${s.tehsil}`] || 0) + 1; });
    const ops = p.operators;
    $("#dive").innerHTML = `
      <div class="dhead" style="--c:${p.color}"><span class="sw lg" style="background:${p.color}"></span><div><div class="dcode">${esc(p.code)} ${p.is_new ? '<span class="tag">new</span>' : ""}</div>
        <div class="muted small">${esc(p.districts.join(", "))} · ${esc(p.regions.join(", "))}</div></div>
        <span class="spacer"></span><button class="btn-ghost xs" id="diveZoom">⌖ Zoom</button></div>
      <div class="dstats">
        <div><b>${fmt(p.sites)}</b><span>sites</span></div>
        <div><b>${p.members.length}</b><span>franchises merged</span></div>
        <div><b>${ops.Telenor || 0} / ${ops.Ufone || 0}</b><span>TP / Ufone sites</span></div>
        ${k ? `<div><b>${fmt(p.kpis[k])}</b><span>${esc(k)}</span></div><div><b class="up">${fmt(p.projected[k])}</b><span>projected ${pctTxt(pct(p.projected[k], p.kpis[k]))}</span></div>` : ""}
      </div>
      <h4>Pre → post by KPI</h4>
      <div class="tblwrap dive-tbl"><table class="tbl compact"><thead><tr><th>KPI</th>${mem.map((m) => `<th class="r"><span class="sw" style="background:${m.color}"></span>${esc(m.code)}</th>`).join("")}${rest.length ? `<th class="r">+${rest.length} more</th>` : ""}
        <th class="r">Combined</th><th class="r">Projected</th>${Object.keys(p.trend_next).length ? '<th class="r">Next month (trend)</th>' : ""}</tr></thead><tbody>
        ${kpiRows.map((x) => {
          const restV = rest.reduce((a, m) => a + (x.get(m) || 0), 0);
          const up = pct(x.proj, x.get(p));
          return `<tr><td>${esc(x.label)}</td>${mem.map((m) => `<td class="r">${fmt(x.get(m))}</td>`).join("")}${rest.length ? `<td class="r">${x.der ? "—" : fmt(restV)}</td>` : ""}
          <td class="r"><b>${fmt(x.get(p))}</b></td><td class="r ${up > 0.01 ? (x.lb ? "down" : "up") : ""}">${fmt(x.proj)} ${up > 0.01 ? `<small>${pctTxt(up)}</small>` : ""}</td>
          ${Object.keys(p.trend_next).length ? `<td class="r">${x.tr == null ? "—" : fmt(x.tr)}</td>` : ""}</tr>`;
        }).join("")}</tbody></table></div>
      <h4>Who brings what</h4>
      ${p.members.map((m) => `<div class="share"><span class="sw" style="background:${m.color}"></span><span class="sname">${esc(m.code)}</span>
        <div class="bar"><span style="width:${(m.sites / p.sites) * 100}%;background:${m.color}"></span></div><span class="sv">${fmt((m.sites / p.sites) * 100, 0)}% sites</span>
        ${k ? `<span class="sv">${fmt((m.kpis[k] / (p.kpis[k] || 1)) * 100, 0)}% ${esc(k)}</span>` : ""}</div>`).join("")}
      <h4>Where (district / tehsil)</h4>
      <div class="tehs">${Object.entries(teh).sort((a, b) => b[1] - a[1]).map(([t, n]) => `<span class="tchip">${esc(t)} <b>${n}</b></span>`).join("")}</div>
      ${p.bms.length ? `<h4>BMs / ARMs</h4><div class="small">${esc(p.bms.join(", "))}<br><span class="muted">${esc(p.arms.join(", "))}</span></div>` : ""}
      <h4>Sites <span class="pill">${sites.length}</span></h4>
      <div class="dmove"><label><input type="checkbox" id="dAll"> all</label>
        <select id="dTarget">${targetOptions(null, true)}</select><button class="btn sm" id="dMove">Move ticked sites</button></div>
      <div class="tblwrap dive-sites"><table class="tbl compact" id="dSites"><thead><tr><th></th><th>Site</th><th>Op</th><th>From</th><th>Tehsil</th>${k ? `<th class="r">${esc(k)}</th>` : ""}</tr></thead><tbody>
        ${sites.sort((a, b) => (b.kpi || 0) - (a.kpi || 0)).map((s) => `<tr><td><input type="checkbox" data-sid="${esc(s.site_id)}"></td>
          <td><a href="#" data-site-zoom="${esc(s.site_id)}">${esc(s.site_id)}</a>${s.overridden ? ' <span class="tag">moved</span>' : ""}</td>
          <td>${esc((s.operator || "").slice(0, 2).toUpperCase())}</td><td><span class="sw" style="background:${colorOf(s.pre)}"></span>${esc(s.pre)}</td>
          <td class="small">${esc(s.tehsil)}</td>${k ? `<td class="r">${fmt(s.kpi)}</td>` : ""}</tr>`).join("")}</tbody></table></div>`;
    $("#dTarget").value = boardCodes().find((c) => c !== p.code) || "__back__";
  }
  $("#dive").addEventListener("click", (e) => {
    if (e.target.id === "diveZoom") highlight(S.dive, "post", true);
    if (e.target.id === "dAll") $$("#dSites input[data-sid]").forEach((c) => { c.checked = e.target.checked; });
    if (e.target.id === "dMove") {
      const ids = $$("#dSites input[data-sid]:checked").map((c) => c.dataset.sid);
      const t = $("#dTarget").value;
      if (!ids.length) { toast("Tick the sites to move first."); return; }
      moveSites(ids, t === "__back__" ? null : t);
    }
    const z = e.target.closest("[data-site-zoom]");
    if (z) {
      e.preventDefault();
      const s = S.res.sites.find((x) => x.site_id === z.dataset.siteZoom);
      if (s && s.latitude != null) { M.post.map.setView([s.latitude, s.longitude], 14); const mk = M.post.markers[s.site_id]; if (mk) L.popup().setLatLng(mk.getLatLng()).setContent(sitePopup(s)).openOn(M.post.map); }
    }
  });

  // ------------------------------------------------------------------ cards
  function card(title, pre, post, sub, cls = "") {
    return `<div class="kcard ${cls}"><div class="kt">${title}</div><div class="kval">${pre !== null ? `<span class="pre">${pre}</span><span class="arr">→</span>` : ""}<span class="post">${post}</span></div><div class="ks">${sub || ""}</div></div>`;
  }
  function drawCards() {
    const s = S.res.summary, g = S.res.geo.stats || {}, k = s.primary;
    const ops = Object.entries(s.operators).map(([o, n]) => `${esc(o)} ${n}`).join(" · ");
    const proj = s.projected_totals[k], base = s.totals[k];
    const cvTxt = (v) => (v === null || v === undefined ? "—" : v.toFixed(2));
    $("#cards").innerHTML = [
      card("Franchises", s.pre_franchises, s.post_franchises, `${s.post_franchises - s.pre_franchises >= 0 ? "+" : ""}${s.post_franchises - s.pre_franchises} franchises`, "accent"),
      card("Sites in scope", null, fmt(s.sites), ops),
      card("Avg sites / franchise", fmt(s.pre_avg_sites), fmt(s.post_avg_sites), `Plan range ${fmt(s.post_min_sites)}–${fmt(s.post_max_sites)}`),
      card(`${esc(k)} / franchise`, fmt(s.pre_per_fr), fmt(s.post_per_fr), S.agg === "avg" ? "monthly average" : "period total"),
      card("Balance (CV)", cvTxt(s.pre_cv), cvTxt(s.post_cv), `spread of ${esc(k)} across franchises — lower is more even`),
      card(`Projected ${esc(k)}`, fmt(base), fmt(proj), `<b class="${proj >= base ? "up" : "down"}">${pctTxt(pct(proj, base))}</b>`, "accent"),
      card("Coverage", null, g.coverage_pct != null ? g.coverage_pct + "%" : "—", `${fmt(g.covered_km2)} of ${fmt(g.planning_area_km2)} km² within ${S.km} km`),
      card("Enclosed islands", null, g.enclosed_islands ?? "—", `${fmt(g.enclosed_km2)} km², given to nearest franchise`),
      card("Territory pieces", g.pre_fragments ?? "—", g.post_fragments ?? "—", `detached fragments · ${S.res.enclaves.length} enclaves`),
    ].join("");
  }

  // ------------------------------------------------------------------ hierarchy
  function drawHierarchy() {
    const key = S.treeSide, other = key === "post" ? "pre" : "post";
    const tree = {};
    S.res.sites.forEach((s) => {
      const t = (((tree[s.region || "—"] ||= {})[s.district] ||= {})[s.tehsil] ||= {});
      const f = (t[s[key]] ||= { n: 0, sub: {} });
      f.n++; f.sub[s[other]] = (f.sub[s[other]] || 0) + 1;
    });
    const count = (o) => (o.n !== undefined ? o.n : Object.values(o).reduce((a, v) => a + count(v), 0));
    const frs = (o) => { const set = new Set(); Object.values(o).forEach((t) => Object.keys(t).forEach((f) => set.add(f))); return set.size; };
    $("#hierTree").innerHTML = Object.entries(tree).sort().map(([reg, ds]) => `<details open><summary><b>${esc(reg)}</b><span class="muted">${count(ds)} sites</span></summary>
      ${Object.entries(ds).sort().map(([d, ts]) => `<details open><summary>${esc(d)}<span class="muted">${count(ts)} sites · ${frs(ts)} fr</span></summary>
        ${Object.entries(ts).sort().map(([t, fs]) => `<details><summary>${esc(t)}<span class="muted">${count(fs)}</span></summary>
          ${Object.entries(fs).sort((a, b) => b[1].n - a[1].n).map(([f, v]) => `<details class="fr"><summary><span class="sw" style="background:${colorOf(f)}"></span><a href="#" data-hl="${esc(f)}">${esc(f)}</a><span class="muted">${v.n}</span></summary>
            ${Object.entries(v.sub).sort((a, b) => b[1] - a[1]).map(([m, n]) => `<div class="leaf"><span class="sw" style="background:${colorOf(m)}"></span>${key === "post" ? "" : "→ "}${esc(m)} <span class="muted">${n}</span></div>`).join("")}
          </details>`).join("")}</details>`).join("")}</details>`).join("")}</details>`).join("");
  }
  $("#hierTree").addEventListener("click", (e) => { const a = e.target.closest("[data-hl]"); if (!a) return; e.preventDefault(); if (S.treeSide === "post") openDive(a.dataset.hl); else highlight(a.dataset.hl, "pre"); });

  // ------------------------------------------------------------------ issues
  // ------------------------------------------------------------------ islands (stray sites)
  S.strayBy = "post";
  function strayHtml(x, i) {
    const opts = [x.suggest, ...x.alts];
    return `<div class="stray" id="stray-${i}" data-i="${i}">
      <div class="st-h">${OPS.sym(OPS.kind(x.operator), 11)} <b>${esc(x.site_id)}</b>
        <span class="code-chip"><span class="sw" style="background:${colorOf(x.post)}"></span>${esc(x.post)}</span>
        <span class="muted small">${x.area ? "📍 " + esc(x.area) + " · " : ""}${esc(x.tehsil || "")} · ${esc(x.arm || "")}</span></div>
      <div class="st-why">Sits inside <b>${esc(x.suggest)}</b>'s area: ${x.suggest_n} of its ${x.k} nearest sites are ${esc(x.suggest)}${x.same_n ? `, only ${x.same_n} is its own` : ", none are its own"}.
        ${x.nearest_same_km != null ? `Nearest ${esc(x.post)} site is <b>${x.nearest_same_km} km</b> away.` : ""}</div>
      <div class="st-act">
        <button class="btn sm" data-stag="${i}" data-to="${esc(x.suggest)}" title="Suggested: the franchise that surrounds it">→ ${esc(x.suggest)}</button>
        <select data-spick="${i}"><option value="">other franchise…</option>${codeOptions("", opts.slice(1).concat(boardCodes().filter((c) => !opts.includes(c) && c !== x.post)), "Nearby / in this view")}</select>
        <button class="btn-ghost sm" data-sgo="${i}">Tag</button>
        <button class="btn-ghost xs" data-sshow="${i}" title="Zoom the maps to this site">📍 Show</button>
        <button class="btn-ghost xs" data-sskip="${i}" title="Keep it where it is (hide from this list)">Keep</button>
      </div></div>`;
  }
  function drawStrays() {
    const all = (S.res.strays || []).filter((x) => OPS.visible(x.operator));
    let skip = new Set(); try { skip = new Set(JSON.parse(localStorage.getItem(`planner:keep:${SC}`) || "[]")); } catch (e) { /* ignore */ }
    const list = all.filter((x) => !skip.has(x.site_id));
    S.strayList = list;
    const groups = {};
    list.forEach((x, i) => (groups[x[S.strayBy]] ||= []).push([x, i]));
    $("#strays").innerHTML = `
      <h4>Islands — sites inside another franchise's area</h4>
      <div class="muted small">A site is an island when at least 6 of its 8 nearest sites belong to one other planned franchise and at most 1 is its own (from the whole circle, not only this view)
        Ufone and TP franchises that are not merged yet are treated as separate networks. Red rings on the maps. Suggestion = the franchise that surrounds it.</div>
      <div class="st-tools">
        <b>${list.length}</b> island site(s) in this view${skip.size ? ` · <a href="#" id="strayUnkeep">${skip.size} kept as-is (show again)</a>` : ""}
        <span class="spacer"></span>
        <span class="seg small-seg" id="strayBy"><button data-v="post" class="${S.strayBy === "post" ? "on" : ""}">by franchise</button><button data-v="suggest" class="${S.strayBy === "suggest" ? "on" : ""}">by suggested area</button><button data-v="arm" class="${S.strayBy === "arm" ? "on" : ""}">by ASM</button></span>
      </div>
      ${list.length ? `<button class="btn sm" id="strayAll">Tag all ${list.length} to their suggestion</button>` : `<div class="issue ok">No islands here 👍</div>`}
      ${Object.entries(groups).sort((a, b) => b[1].length - a[1].length).map(([g, xs]) => `<h4 class="st-g">${esc(g || "—")} <span class="pill">${xs.length}</span></h4>${xs.map(([x, i]) => strayHtml(x, i)).join("")}`).join("")}`;
    $("#strayCount").textContent = list.length || "";
  }
  function openStray(id) {
    showTab("islands");
    const i = (S.strayList || []).findIndex((x) => x.site_id === id);
    const el = $(`#stray-${i}`);
    if (el) { el.scrollIntoView({ block: "center", behavior: "smooth" }); el.style.outline = "2px solid #f37021"; setTimeout(() => (el.style.outline = ""), 1800); }
  }
  $("#strays").addEventListener("click", (e) => {
    const L_ = S.strayList || [];
    const t = e.target.closest("[data-stag]");
    if (t) { moveSites([L_[+t.dataset.stag].site_id], t.dataset.to); return; }
    const g = e.target.closest("[data-sgo]");
    if (g) { const v = $(`[data-spick="${g.dataset.sgo}"]`).value; if (!v) { toast("Pick a franchise in the list first."); return; } moveSites([L_[+g.dataset.sgo].site_id], v); return; }
    const sh = e.target.closest("[data-sshow]");
    if (sh) { const x = L_[+sh.dataset.sshow]; M.post.map.setView([x.latitude, x.longitude], 14); const mk = M.post.markers[x.site_id]; if (mk) L.popup().setLatLng(mk.getLatLng()).setContent(sitePopup(S.res.sites.find((s) => s.site_id === x.site_id) || x)).openOn(M.post.map); return; }
    const k = e.target.closest("[data-sskip]");
    if (k) { const x = L_[+k.dataset.sskip]; let a = []; try { a = JSON.parse(localStorage.getItem(`planner:keep:${SC}`) || "[]"); } catch (er) { /* ignore */ } a.push(x.site_id); try { localStorage.setItem(`planner:keep:${SC}`, JSON.stringify(a)); } catch (er) { /* ignore */ } drawStrays(); return; }
    if (e.target.id === "strayUnkeep") { e.preventDefault(); try { localStorage.removeItem(`planner:keep:${SC}`); } catch (er) { /* ignore */ } drawStrays(); return; }
    const by = e.target.closest("#strayBy button");
    if (by) { S.strayBy = by.dataset.v; drawStrays(); return; }
    if (e.target.id === "strayAll") {
      choose(`<h3>Tag ${L_.length} island site(s)?</h3><p>Each site goes to the franchise that surrounds it (its suggestion). You can undo from <b>Issues › Site moves</b> or the ↶ Undo button.</p>`,
        [{ label: "Tag all", v: "go", cls: "btn" }, { label: "Cancel", v: "", cls: "btn-ghost" }]).then(async (v) => {
        if (v !== "go") return;
        const groups = {}; L_.forEach((x) => (groups[x.suggest] ||= []).push(x.site_id));
        status("Tagging islands…", true);
        for (const [to, ids] of Object.entries(groups)) await api(`/api/scenario/${SC}/override/`, { site_ids: ids, target: to });
        toast(`${L_.length} island site(s) tagged to the surrounding franchise.`); run();
      });
    }
  });

  // ------------------------------------------------------------------ consolidated IDs (live)
  S.consBy = "arm";
  const CONS_LV = { region: ["region", "Region"], bm: ["bm", "BM"], arm: ["arm", "ASM"], district: ["district", "District"], tehsil: ["tehsil", "City"], none: [null, ""] };
  function consRows() {
    const r = S.res, k = r.meta.primary, col = CONS_LV[S.consBy][0];
    const sitesBy = {};
    r.sites.forEach((s) => (sitesBy[s.post] ||= []).push(s));
    return r.post.filter((p) => $("#consAll").checked || p.members.length > 1).map((p) => {
      const ss = sitesBy[p.code] || [], cnt = {};
      if (col) ss.forEach((s) => { const v = s[col] || "Unknown"; cnt[v] = (cnt[v] || 0) + 1; });
      const parts = Object.entries(cnt).sort((a, b) => b[1] - a[1]);
      const ops = { ufone: 0, tp: 0, other: 0 }; ss.forEach((s) => ops[OPS.kind(s.operator)]++);
      const ac = {}; ss.forEach((s) => { if (s.area) ac[s.area] = (ac[s.area] || 0) + 1; });
      const areasTop = Object.entries(ac).sort((a, b) => b[1] - a[1]).slice(0, 4);
      return { p, group: col ? (parts[0] ? parts[0][0] : "Unknown") : "", parts, ops, areasTop,
        cur: k ? p.kpis[k] : null, proj: k ? p.projected[k] : null };
    });
  }
  function drawCons() {
    const r = S.res, k = r.meta.primary, rows = consRows(), lvName = CONS_LV[S.consBy][1];
    const merged = r.post.filter((p) => p.members.length > 1).length;
    $("#consNote").textContent = `${merged} consolidated ID(s) from ${r.post.filter((p) => p.members.length > 1).reduce((a, p) => a + p.members.length, 0)} current franchises · updates as you tag`;
    const pct = (a, b) => (b ? ((a - b) / Math.abs(b)) * 100 : 0);
    const groups = {};
    rows.forEach((x) => (groups[x.group] ||= []).push(x));
    const head = `<thead><tr><th>${lvName ? lvName + " › " : ""}Consolidated ID</th><th>Merged from (current IDs)</th><th>Main areas</th><th class="num">Sites</th><th class="num">Ufone / TP</th>
      ${k ? `<th class="num">${esc(k)} now</th><th class="num">Projected</th><th class="num">Uplift</th><th class="num">${esc(k)} / site</th>` : ""}
      ${lvName ? `<th>Also in other ${esc(lvName)}s</th>` : ""}</tr></thead>`;
    const frRow = (x) => {
      const p = x.p, up = pct(x.proj, x.cur);
      return `<tr class="fr" data-code="${esc(p.code)}"><td><span class="code-chip"><span class="sw" style="background:${p.color}"></span>${esc(p.code)}</span>${p.is_new ? ' <span class="pill">new</span>' : ""}</td>
        <td><div class="mem">${p.members.map((m) => `<span class="code-chip" title="${fmt(m.sites)} sites"><span class="sw" style="background:${m.color}"></span>${esc(m.code)} <span class="muted">${fmt(m.sites)}</span></span>`).join("")}</div></td>
        <td class="small">${x.areasTop.map(([a, n]) => `${esc(a)} <span class="muted">${n}</span>`).join(", ") || '<span class="muted">—</span>'}</td>
        <td class="num">${fmt(p.sites)}</td><td class="num">${x.ops.ufone} / ${x.ops.tp}</td>
        ${k ? `<td class="num">${fmt(x.cur)}</td><td class="num">${fmt(x.proj)}</td><td class="num ${up > 0.05 ? "up" : ""}">${up > 0.05 ? "+" + up.toFixed(1) + "%" : "—"}</td><td class="num">${fmt(p.sites ? x.cur / p.sites : 0)}</td>` : ""}
        ${lvName ? `<td class="small">${x.parts.slice(1).map(([v, n]) => `${esc(v)} (${n})`).join(", ") || '<span class="muted">—</span>'}</td>` : ""}</tr>`;
    };
    const sum = (xs, f) => xs.reduce((a, x) => a + (f(x) || 0), 0);
    const ncol = 5 + (k ? 4 : 0) + (lvName ? 1 : 0);
    let body = "";
    Object.entries(groups).sort((a, b) => sum(b[1], (x) => x.p.sites) - sum(a[1], (x) => x.p.sites)).forEach(([g, xs]) => {
      xs.sort((a, b) => b.p.sites - a.p.sites);
      if (lvName) {
        const c = sum(xs, (x) => x.cur), pj = sum(xs, (x) => x.proj);
        body += `<tr class="grp"><td>${esc(lvName)}: ${esc(g)}</td><td><span class="muted">${xs.length} consolidated ID(s) · ${sum(xs, (x) => x.p.members.length)} merged</span></td><td></td>
          <td class="num">${fmt(sum(xs, (x) => x.p.sites))}</td><td class="num">${sum(xs, (x) => x.ops.ufone)} / ${sum(xs, (x) => x.ops.tp)}</td>
          ${k ? `<td class="num">${fmt(c)}</td><td class="num">${fmt(pj)}</td><td class="num">${pct(pj, c) > 0.05 ? "+" + pct(pj, c).toFixed(1) + "%" : "—"}</td><td></td>` : ""}<td></td></tr>`;
      }
      body += xs.map(frRow).join("");
    });
    if (rows.length) {
      const c = sum(rows, (x) => x.cur), pj = sum(rows, (x) => x.proj);
      body += `<tr class="tot"><td>Total</td><td>${rows.length} ID(s)</td><td></td><td class="num">${fmt(sum(rows, (x) => x.p.sites))}</td><td class="num">${sum(rows, (x) => x.ops.ufone)} / ${sum(rows, (x) => x.ops.tp)}</td>
        ${k ? `<td class="num">${fmt(c)}</td><td class="num">${fmt(pj)}</td><td class="num">${pct(pj, c) > 0.05 ? "+" + pct(pj, c).toFixed(1) + "%" : "—"}</td><td></td>` : ""}${lvName ? "<td></td>" : ""}</tr>`;
    } else body = `<tr><td colspan="${ncol}" class="muted">No consolidated IDs in this view yet — drag a franchise onto another on the Merge board (or tick “also single IDs”).</td></tr>`;
    $("#consTable").innerHTML = head + "<tbody>" + body + "</tbody>";
  }
  segInit("#consBy", () => S.consBy, (v) => { S.consBy = v; drawCons(); });
  $("#consAll").addEventListener("change", drawCons);
  $("#consTable").addEventListener("click", (e) => { const tr = e.target.closest("tr.fr"); if (tr) openDive(tr.dataset.code, true); });
  $("#consCsv").addEventListener("click", () => {
    const k = S.res.meta.primary, lv = CONS_LV[S.consBy][1] || "Group";
    const lines = [[lv, "Consolidated ID", "Merged from", "Main areas", "Sites", "Ufone sites", "TP sites", k ? k + " now" : "", k ? "Projected" : ""].join(",")];
    consRows().forEach((x) => lines.push([x.group, x.p.code, x.p.members.map((m) => m.code).join(" + "), x.areasTop.map(([a, n]) => `${a} (${n})`).join("; "), x.p.sites, x.ops.ufone, x.ops.tp, x.cur ?? "", x.proj ?? ""].map((v) => `"${String(v).replace(/"/g, '""')}"`).join(",")));
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" })); a.download = "consolidated_ids.csv"; a.click();
  });

  function drawIssues() {
    const r = S.res, g = r.geo.stats || {};
    const w = r.warnings.map((x) => `<div class="issue ${x.level}">${esc(x.text)}</div>`).join("");
    const enc = r.enclaves.slice(0, 150).map((e, i) => `<div class="enc">
      <div><span class="sw" style="background:${colorOf(e.franchise)}"></span><b>${esc(e.franchise)}</b> detached piece · ${e.n_sites} site(s) · ${e.km2} km² · ${esc(e.district)}</div>
      <div class="muted small">${esc(e.sites.slice(0, 6).join(", "))}${e.sites.length > 6 ? "…" : ""}</div>
      <div class="encbtns">${e.suggest ? `<button class="btn sm" data-absorb="${i}">Give to ${esc(e.suggest)}</button> <span class="muted small">${e.shared_border_pct}% shared border</span>` : ""}
        <button class="btn-ghost xs" data-enczoom="${i}">⌖</button></div></div>`).join("");
    const ovs = Object.entries(r.overrides);
    $("#issues").innerHTML = `
      <h4>Coverage</h4>
      <div class="small">Planning area ${fmt(g.planning_area_km2)} km², ${g.coverage_pct ?? "—"}% within ${S.km} km of a site.
        ${g.enclosed_islands || 0} enclosed uncovered pockets (${fmt(g.enclosed_km2)} km²), drawn in red, each handed to the franchise of its nearest site.</div>
      <h4>Planning warnings <span class="pill">${r.warnings.length}</span></h4>${w || '<div class="muted small">None.</div>'}
      <h4>Enclaves in the plan <span class="pill">${r.enclaves.length}</span></h4>
      <div class="muted small">Pieces of a planned franchise cut off from its main territory.</div>
      ${r.enclaves.length ? `<button class="btn-ghost sm" id="absorbAll">Give all 1–2 site enclaves to suggested neighbour</button>` : ""}
      ${enc}
      <h4>Site moves <span class="pill">${ovs.length}</span></h4>
      ${ovs.length ? `<button class="btn-ghost sm danger" id="clearOv">Undo all site moves</button>` : ""}
      <div class="ovlist">${ovs.slice(0, 300).map(([s, t]) => `<div class="ov"><span>${esc(s)} → <b>${esc(t)}</b></span><button class="btn-ghost xs" data-unmove="${esc(s)}">undo</button></div>`).join("")}</div>`;
    $("#issueCount").textContent = r.warnings.filter((x) => x.level === "warn").length + r.enclaves.length || "";
  }
  $("#issues").addEventListener("click", (e) => {
    const a = e.target.closest("[data-absorb]");
    if (a) { const en = S.res.enclaves[+a.dataset.absorb]; moveSites(en.sites, en.suggest); }
    const um = e.target.closest("[data-unmove]");
    if (um) moveSites([um.dataset.unmove], null);
    const z = e.target.closest("[data-enczoom]");
    if (z) { const en = S.res.enclaves[+z.dataset.enczoom]; const pts = S.res.sites.filter((s) => en.sites.includes(s.site_id) && s.latitude != null).map((s) => [s.latitude, s.longitude]); if (pts.length) M.post.map.fitBounds(pts, { padding: [80, 80], maxZoom: 13 }); }
    if (e.target.id === "absorbAll") {
      const groups = {};
      S.res.enclaves.filter((en) => en.n_sites <= 2 && en.suggest).forEach((en) => (groups[en.suggest] ||= []).push(...en.sites));
      (async () => { for (const [t, ids] of Object.entries(groups)) await api(`/api/scenario/${SC}/override/`, { site_ids: ids, target: t }); run(); })();
    }
    if (e.target.id === "clearOv" && confirm("Undo every site move?")) api(`/api/scenario/${SC}/reset/`, { what: "overrides" }).then(run);
  });

  // ------------------------------------------------------------------ franchise table
  function allColumns() {
    const m = S.res.meta;
    return [...m.kpis.map((k) => ({ id: "k:" + k.key, label: k.label, kind: "kpi", key: k.key, lb: k.lower_better })),
      ...m.derived.map((d) => ({ id: "d:" + d.label, label: d.label, kind: "der", key: d.label, lb: d.lower_better }))];
  }
  function defaultCols() {
    const keys = S.res.meta.kpis.map((k) => k.key);
    const want = [S.res.meta.primary, "FCA.", "Act 90D", "Gross Churn", "Net Adds", "Total Recharge"].filter((k) => keys.includes(k));
    return new Set([...new Set(want)].map((k) => "k:" + k).concat(S.res.meta.derived.slice(0, 2).map((d) => "d:" + d.label)));
  }
  function drawColPicker() {
    $("#colPicker").innerHTML = allColumns().map((c) => `<label><input type="checkbox" data-col="${esc(c.id)}" ${S.cols.has(c.id) ? "checked" : ""}> ${esc(c.label)}</label>`).join("");
  }
  $("#btnCols").addEventListener("click", () => $("#colPicker").classList.toggle("hidden"));
  $("#colPicker").addEventListener("change", (e) => { const id = e.target.dataset.col; e.target.checked ? S.cols.add(id) : S.cols.delete(id); drawTable(); });
  const cellVal = (row, c) => (c.kind === "kpi" ? row.kpis[c.key] : row.derived[c.key]);
  const projVal = (row, c) => (c.kind === "kpi" ? row.projected[c.key] : row.projected_derived[c.key]);
  function drawTable() {
    const cols = allColumns().filter((c) => S.cols.has(c.id));
    const post = S.tblSide === "post";
    const rows = [...(post ? S.res.post : S.res.pre)];
    const sk = S.sort.key;
    const getSort = (r) => (sk === "sites" ? r.sites : sk === "code" ? r.code : cellVal(r, cols.find((c) => c.id === sk) || cols[0]) ?? -Infinity);
    rows.sort((a, b) => { const x = getSort(a), y = getSort(b); return (x > y ? 1 : x < y ? -1 : 0) * S.sort.dir; });
    const head = `<thead><tr><th data-s="code">Franchise</th><th data-s="sites" class="r">Sites</th><th>TP/Ufone</th><th>${post ? "Members" : "Planned into"}</th>
      ${cols.map((c) => `<th data-s="${esc(c.id)}" class="r">${esc(c.label)}${post && c.kind === "kpi" ? '<div class="sub">base · proj · trend</div>' : ""}</th>`).join("")}</tr></thead>`;
    const mix = (ops) => { const tp = ops.Telenor || 0, uf = ops.Ufone || 0, t = tp + uf || 1; return `<div class="mix" title="Telenor ${tp} · Ufone ${uf}"><span class="tp" style="width:${(tp / t) * 100}%"></span><span class="uf" style="width:${(uf / t) * 100}%"></span></div>`; };
    const body = rows.map((r) => {
      const open = S.expanded.has(r.code);
      const main = `<tr class="frrow ${post && r.is_new ? "isnew" : ""}" data-code="${esc(r.code)}">
        <td>${post ? `<button class="exp">${open ? "▾" : "▸"}</button>` : ""}<span class="sw" style="background:${r.color}"></span>${post ? `<a href="#" data-dive="${esc(r.code)}">${esc(r.code)}</a>` : `<a href="#" data-hl="${esc(r.code)}">${esc(r.code)}</a>`}${post && r.is_new ? ' <span class="tag">new</span>' : ""}
          <div class="muted small">${esc(r.districts.slice(0, 3).join(", "))}${r.districts.length > 3 ? "…" : ""}</div></td>
        <td class="r">${fmt(r.sites)}</td><td>${mix(r.operators)}</td>
        <td class="small">${post ? r.members.map((m) => `<span class="mem"><span class="sw" style="background:${m.color}"></span>${esc(m.code)}</span>`).join("") : Object.keys(r.mapped_to).map(esc).join(", ")}</td>
        ${cols.map((c) => {
          const v = cellVal(r, c);
          if (!post) return `<td class="r">${fmt(v)}</td>`;
          const p = projVal(r, c), tn = c.kind === "kpi" ? r.trend_next[c.key] : null, up = pct(p, v);
          return `<td class="r">${fmt(v)}<div class="sub">${p !== v && p != null ? `<span class="${(up >= 0) !== !!c.lb ? "up" : "down"}">${fmt(p)}</span>` : "·"}${tn != null ? ` · <span class="tr">${fmt(tn)}</span>` : ""}</div></td>`;
        }).join("")}</tr>`;
      const members = post && open ? r.members.map((m) => `<tr class="memrow"><td><span class="indent">↳</span><span class="sw" style="background:${m.color}"></span>${esc(m.code)}</td><td class="r">${fmt(m.sites)}</td><td></td><td class="small muted">${fmt((m.sites / r.sites) * 100, 0)}% of sites</td>
        ${cols.map((c) => `<td class="r">${fmt(c.kind === "kpi" ? m.kpis[c.key] : m.derived[c.key])}</td>`).join("")}</tr>`).join("") : "";
      return main + members;
    }).join("");
    $("#frTable").innerHTML = head + `<tbody>${body}</tbody>`;
  }
  $("#frTable").addEventListener("click", (e) => {
    const th = e.target.closest("th[data-s]");
    if (th) { const k = th.dataset.s; S.sort = { key: k, dir: S.sort.key === k ? -S.sort.dir : -1 }; drawTable(); return; }
    const ex = e.target.closest(".exp");
    if (ex) { const c = ex.closest("tr").dataset.code; S.expanded.has(c) ? S.expanded.delete(c) : S.expanded.add(c); drawTable(); return; }
    const hl = e.target.closest("[data-hl]");
    if (hl) { e.preventDefault(); highlight(hl.dataset.hl, "pre"); }
  });

  function drawTotals() {
    const s = S.res.summary, m = S.res.meta;
    const trendSum = {};
    S.res.post.forEach((r) => Object.entries(r.trend_next || {}).forEach(([k, v]) => { trendSum[k] = (trendSum[k] || 0) + v; }));
    const hasTrend = Object.keys(trendSum).length > 0;
    const row = (label, b, p, t, lb) => { const u = pct(p, b); return `<tr><td>${esc(label)}</td><td class="r">${fmt(b)}</td><td class="r">${fmt(p)}</td><td class="r ${u > 0.001 ? (lb ? "down" : "up") : ""}">${u && Math.abs(u) > 0.001 ? pctTxt(u) : "—"}</td>${hasTrend ? `<td class="r">${fmt(t)}</td>` : ""}</tr>`; };
    $("#totTable").innerHTML = `<thead><tr><th>KPI</th><th class="r">Baseline</th><th class="r">Projected</th><th class="r">Δ</th>${hasTrend ? '<th class="r">Trend next month</th>' : ""}</tr></thead><tbody>
      ${m.kpis.map((k) => row(k.label, s.totals[k.key], s.projected_totals[k.key], trendSum[k.key], k.lower_better)).join("")}
      ${m.derived.map((d) => row(d.label, s.derived_totals[d.label], s.projected_derived_totals[d.label], null, d.lower_better)).join("")}</tbody>`;
  }

  // ------------------------------------------------------------------ charts
  const charts = {};
  Chart.defaults.font.family = "Inter, system-ui, sans-serif";
  Chart.defaults.font.size = 11;
  const tickFmt = { callback: (v) => fmt(v) };
  function mkChart(id, cfg) { if (charts[id]) charts[id].destroy(); charts[id] = new Chart(document.getElementById(id), cfg); }
  function drawCharts() {
    const k = S.kpi, r = S.res;
    const posts = [...r.post].sort((a, b) => (b.kpis[k] || 0) - (a.kpis[k] || 0)).slice(0, 40);
    const memberCodes = [...new Set(posts.flatMap((p) => p.members.map((m) => m.code)))];
    $("#chMix").parentElement.style.height = Math.max(260, posts.length * 22 + 60) + "px";
    $("#chMixTitle").textContent = `What merges into what — ${k}`;
    mkChart("chMix", {
      type: "bar",
      data: { labels: posts.map((p) => p.code), datasets: memberCodes.map((mc) => ({ label: mc, backgroundColor: colorOf(mc), borderWidth: 0, data: posts.map((p) => { const m = p.members.find((x) => x.code === mc); return m ? m.kpis[k] : 0; }) })) },
      options: { indexAxis: "y", maintainAspectRatio: false, onClick: (e, els) => { if (els.length) openDive(posts[els[0].index].code); },
        plugins: { legend: { display: false }, tooltip: { filter: (i) => i.raw > 0, callbacks: { label: (c) => `${c.dataset.label}: ${fmt(c.raw)}` } } }, scales: { x: { stacked: true, ticks: tickFmt }, y: { stacked: true, ticks: { autoSkip: false } } } },
    });
    const preS = r.pre.map((p) => p.sites).sort((a, b) => b - a), postS = r.post.map((p) => p.sites).sort((a, b) => b - a);
    const n = Math.max(preS.length, postS.length);
    mkChart("chDist", {
      type: "bar",
      data: { labels: Array.from({ length: n }, (_, i) => i + 1), datasets: [
        { label: `Current (${preS.length})`, data: preS, backgroundColor: "#9aa3b5" },
        { label: `Plan (${postS.length})`, data: postS, backgroundColor: "#ff7a00" }] },
      options: { maintainAspectRatio: false, scales: { x: { title: { display: true, text: "franchise rank by sites" }, ticks: { maxTicksLimit: 20 } }, y: { title: { display: true, text: "sites" } } } },
    });
    const ser = r.series;
    const top = [...r.post].sort((a, b) => (b.kpis[k] || 0) - (a.kpis[k] || 0)).slice(0, 6);
    $("#chTrendTitle").textContent = `Monthly trend — ${k}` + (ser.months.length < 2 ? " (select 2+ months)" : "");
    mkChart("chTrend", {
      type: "line",
      data: { labels: ser.months, datasets: [
        { label: "Scope total", data: ser.total[k], borderColor: "#1f2a44", backgroundColor: "#1f2a44", borderWidth: 2.5, yAxisID: "y1", tension: 0.25 },
        ...top.map((p) => ({ label: p.code, data: ser.post[p.code][k], borderColor: p.color, backgroundColor: p.color, borderWidth: 1.5, tension: 0.25 }))] },
      options: { maintainAspectRatio: false, interaction: { mode: "index", intersect: false }, plugins: { tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmt(c.raw)}` } } },
        scales: { y: { ticks: tickFmt, title: { display: true, text: "top planned franchises" } }, y1: { position: "right", ticks: tickFmt, grid: { drawOnChartArea: false }, title: { display: true, text: "scope total" } } } },
    });
  }
  $("#kpiSel").addEventListener("change", (e) => { S.kpi = e.target.value; drawCharts(); });

  function drawLegend() {
    $("#legend").innerHTML = `<span class="muted small">Plan:</span> ` + S.res.post.map((p) => `<a href="#" class="lg" data-dive="${esc(p.code)}"><span class="sw" style="background:${p.color}"></span>${esc(p.code)}</a>`).join("") +
      `<span class="lg-note"><span class="sw grey"></span> outside this view · uncovered areas are filled by the nearest franchise · black ring = moved site · pink ring = unsaved move</span>`;
  }
  function fillTargets() {
    const cur = $("#paintTarget").value;
    $("#paintTarget").innerHTML = `<option value="">— pick target franchise —</option><option value="__back__">↩ back to their own franchise</option>` + codeOptions(null, boardCodes());
    $("#paintTarget").value = [...$("#paintTarget").options].some((o) => o.value === cur) ? cur : "";
    S.paint.target = $("#paintTarget").value; updatePaint();
    fillTagForm();
  }


  // ------------------------------------------------------------------ run
  let runSeq = 0;
  async function run() {
    const my = ++runSeq;
    status("Calculating…", true);
    $("#btnXlsx").href = `/scenario/${SC}/export.xlsx?${query()}`;
    $("#btnKml").href = `/scenario/${SC}/export.kml?${query()}`;
    $("#btnReport").href = `/scenario/${SC}/report/?` + new URLSearchParams({ view: S.unit ? `${S.level}::${S.unit}` : S.region !== ALL ? (isBig(S.region) ? S.region : `region::${S.region}`) : "",
      months: [...S.selMonths].join("|"), agg: S.agg, company: OPS.company() }).toString();
    let res;
    try { res = await api(`/api/scenario/${SC}/run/?${query()}`); } catch (e) { if (my === runSeq) status("Error: " + e.message); return; }
    if (my !== runSeq) return;
    if (res.empty) {
      const ms = S.months.filter((m) => S.selMonths.has(m.key)).map((m) => m.label).join(", ");
      status(`No sites for ${viewName()} in ${ms}. That region may not be uploaded for these months — tick more months (All) or check Upload data.`);
      toast(`No data for <b>${esc(viewName())}</b> in ${esc(ms)}.<br><span class="muted">Tick <b>All</b> months or see the coverage table on Upload data.</span>`);
      return;
    }
    S.res = res;
    S.reviews = res.reviews || {};
    [...res.pre, ...res.post].forEach((r) => { S.franchises[r.code] = { ...(S.franchises[r.code] || { code: r.code }), color: r.color, name: r.name }; });
    preByCode = Object.fromEntries(res.pre.map((r) => [r.code, r]));
    postByCode = Object.fromEntries(res.post.map((r) => [r.code, r]));
    S.newBuckets.forEach((c) => { if (postByCode[c]) S.newBuckets.delete(c); });
    if (S.dive && !postByCode[S.dive]) S.dive = null;
    [...S.pend.keys()].forEach((id) => { if (!res.sites.some((s) => s.site_id === id)) S.pend.delete(id); });
    const keys = res.meta.kpis.map((x) => x.key);
    if (!S.kpi || !keys.includes(S.kpi)) S.kpi = res.meta.primary;
    $("#kpiSel").innerHTML = keys.map((x) => `<option ${x === S.kpi ? "selected" : ""}>${esc(x)}</option>`).join("");
    if (!S.cols) S.cols = defaultCols();
    drawColPicker(); drawNav(); fillTargets(); drawPending();
    drawCards(); drawMaps(); drawLegend(); drawBoard(); drawDive(); drawHierarchy(); drawIssues(); drawStrays(); drawCons(); drawTable(); drawTotals(); drawCharts();
    const noXY = res.sites.filter((s) => s.latitude == null).length;
    status(`${esc(viewName())} · ${res.meta.months.length} month(s) · ${fmt(res.summary.sites)} sites · ${res.summary.pre_franchises} → ${res.summary.post_franchises} franchises${noXY ? ` · ${noXY} without coordinates` : ""}`);
  }

  // comparison layers (e.g. Zong) loaded on the Boundaries page
  async function initCompareLayers() {
    let ls = [];
    try { ls = (await api("/api/layers/")).layers.filter((l) => l.role === "compare"); } catch (e) { return; }
    if (!ls.length) return;
    $("#cmpToggles").innerHTML = `<span class="tg-sep">Compare:</span>` + ls.map((l) => `<label><input type="checkbox" data-l="cmp_${l.id}" data-cmp="${l.id}"> ${esc(l.label)}</label>`).join("");
    $$("#cmpToggles input").forEach((cb) => cb.addEventListener("change", async () => {
      const key = "cmp_" + cb.dataset.cmp;
      if (cb.checked && !M.post.layers[key]) {
        const gj = await api(`/api/layers/${cb.dataset.cmp}/zones/`);
        ["pre", "post"].forEach((side) => {
          M[side].layers[key] = L.geoJSON(gj, { style: { color: "#6b2fbf", weight: 2.4, fill: false, opacity: 0.9 },
            onEachFeature: (f, l) => l.bindTooltip(`<b>${esc(f.properties.layer)}</b>: ${esc(f.properties.name)}`, { sticky: true, className: "tt" }) });
        });
      }
      applyToggles();
    }));
  }

  OPS.control($("#opCtl"));
  OPS.companyControl($("#companyCtl"));
  document.addEventListener("ops-change", () => { if (S.res) drawMaps(); });
  document.addEventListener("company-change", () => { S.fit = true; run(); });

  async function init() {
    initCompareLayers();
    status("Loading…", true);
    fillSettings();
    S.filters = await api("/api/filters/");
    S.months = S.filters.months;
    S.filters.franchises.forEach((f) => { S.franchises[f.code] = f; });
    const saved = load();
    const valid = new Set(S.months.map((m) => m.key));
    const regs = regionList();
    if (saved) {
      (saved.months || []).filter((k) => valid.has(k)).forEach((k) => S.selMonths.add(k));
      S.agg = saved.agg || "avg"; S.clip = saved.clip || "district";
      if (saved.region === ALL || regs.includes(saved.region) || (isBig(saved.region) && bigList().includes(bigName(saved.region)))) S.region = saved.region;
      if (LEVELS[saved.level]) S.level = saved.level;
      S.unit = saved.unit || "";
      markAgg(); fillSettings();
    } else if (regs.length) {
      S.region = regs[0];
      S.level = "arm"; // start at ASM level, first ASM of the first region
      const first = unitsFor("arm")[0];
      S.unit = first ? first.v : "";
    }
    if (!S.selMonths.size) S.months.forEach((m) => S.selMonths.add(m.key)); // all months by default
    drawMonthChips(); drawNav();
    if (!S.months.length) { status("No data uploaded yet."); return; }
    loadBoundaries();
    await run();
  }
  init();
})();
