/* Maps-only page: as many maps as you tick, as big as the screen allows.
   Filters: hierarchy (Big Region › Region › BM › ASM, District › City) + operator (Company). */
(function () {
  const $ = (s) => document.querySelector(s);
  const grid = $("#grid"), SC = grid.dataset.scenario, BASE = grid.dataset.base, CIRCLE = grid.dataset.circle || "Central B";
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmt = (v) => (v == null || isNaN(v) ? "—" : Math.abs(v) >= 1e6 ? (v / 1e6).toFixed(2) + "M" : Math.abs(v) >= 1e3 ? (v / 1e3).toFixed(1) + "K" : (+v).toFixed(0));
  const KEY = `maps:${SC}`;
  // map definitions in display order
  const DEFS = BASE === "rd"
    ? [{ id: "rd", geo: "rd", col: "rd_post", title: "PRE · previously tagged", cls: "" },
       { id: "pre", geo: "pre", col: "pre", title: "RDs proposed", cls: "" },
       { id: "post", geo: "post", col: "post", title: "POST · your changes", cls: "post-edit" }]
    : [{ id: "pre", geo: "pre", col: "pre", title: "PRE · current", cls: "" },
       { id: "post", geo: "post", col: "post", title: "POST · plan", cls: "post-edit" },
       { id: "rd", geo: "rd", col: "rd_post", title: "POST · from RDs", cls: "rd" }];
  let st = { on: { pre: true, post: true, rd: true }, f: {} };
  try { st = { ...st, ...JSON.parse(localStorage.getItem(KEY) || "{}") }; } catch (e) { /* ignore */ }
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(st)); } catch (e) { /* ignore */ } };

  // ---- maps
  const M = {};
  let syncing = false, fitted = false, res = null, combos = [];
  DEFS.forEach((d) => {
    const box = document.createElement("div");
    box.className = "mbox"; box.id = "box-" + d.id;
    box.innerHTML = `<div class="maptitle ${d.cls}">${esc(d.title)} <span class="cnt"></span></div><div class="map" id="map-${d.id}"></div>`;
    grid.appendChild(box);
    const map = L.map("map-" + d.id, { preferCanvas: true, zoomSnap: 0.5 }).setView([30.5, 72.0], 7);
    L.tileLayer("https://{s}.basemap.cartocdn.com/light_nolabels/{z}/{x}/{y}{r}.png", { attribution: "© OpenStreetMap © CARTO", subdomains: "abcd", maxZoom: 19 }).addTo(map);
    map.createPane("names"); map.getPane("names").style.zIndex = 450; map.getPane("names").style.pointerEvents = "none";
    L.tileLayer("https://{s}.basemap.cartocdn.com/light_only_labels/{z}/{x}/{y}{r}.png", { subdomains: "abcd", maxZoom: 19, pane: "names" }).addTo(map);
    M[d.id] = { map, box, group: L.layerGroup().addTo(map), def: d };
    map.on("move", () => {
      if (syncing) return; syncing = true;
      Object.values(M).forEach((o) => { if (o.map !== map) o.map.setView(map.getCenter(), map.getZoom(), { animate: false }); });
      syncing = false;
    });
  });
  if (window.AreaNames) AreaNames.attach(Object.values(M).map((o) => o.map));
  const visibleIds = () => DEFS.map((d) => d.id).filter((id) => st.on[id] && !(id === "rd" && res && !res.geo.rd));
  function layout() {
    const ids = visibleIds();
    DEFS.forEach((d) => M[d.id].box.classList.toggle("hidden", !ids.includes(d.id)));
    grid.className = "n" + Math.max(1, ids.length);
    setTimeout(() => Object.values(M).forEach((o) => o.map.invalidateSize()), 60);
  }
  function drawToggles() {
    $("#mapTog").innerHTML = DEFS.map((d) => {
      const na = d.id === "rd" && res && !res.geo.rd;
      return `<label title="${na ? "Upload an RD file to enable" : ""}" style="${na ? "opacity:.45" : ""}"><input type="checkbox" data-m="${d.id}" ${st.on[d.id] && !na ? "checked" : ""} ${na ? "disabled" : ""}> ${esc(d.title)}</label>`;
    }).join("");
  }
  $("#mapTog").addEventListener("change", (e) => {
    const id = e.target.dataset.m; if (!id) return;
    st.on[id] = e.target.checked;
    if (!visibleIds().length) { st.on[id] = true; e.target.checked = true; return; } // keep at least one map
    save(); layout();
  });

  // ---- filters (cascading). combos: [region, district, city, bm, arm, n, bigRegion]
  const F = [["fBig", 6, "Big Region", "bigs"], ["fRegion", 0, "Region", "regions"], ["fBm", 3, "BM", "bms"], ["fArm", 4, "ASM", "arms"],
             ["fDistrict", 1, "District", "districts"], ["fCity", 2, "City", "tehsils"]];
  function drawFilters() {
    F.forEach(([id, idx, name], i) => {
      const prior = F.slice(0, i).filter(([pid]) => st.f[pid]);
      const rows = combos.filter((c) => prior.every(([pid, pidx]) => c[pidx] === st.f[pid]));
      const cnt = {}; rows.forEach((c) => { const v = c[idx] || "Unknown"; cnt[v] = (cnt[v] || 0) + c[5]; });
      const vals = Object.keys(cnt).sort();
      if (st.f[id] && !cnt[st.f[id]]) delete st.f[id];
      $("#" + id).innerHTML = `<option value="">All ${name === "City" ? "Cities" : name + "s"}</option>` + vals.map((v) => `<option value="${esc(v)}" ${st.f[id] === v ? "selected" : ""}>${esc(v)} (${cnt[v]})</option>`).join("");
    });
  }
  F.forEach(([id]) => $("#" + id).addEventListener("change", (e) => {
    st.f[id] = e.target.value; if (!st.f[id]) delete st.f[id];
    const i = F.findIndex((x) => x[0] === id);
    F.slice(i + 1).forEach(([nid]) => delete st.f[nid]); // lower levels reset
    save(); drawFilters(); fitted = false; run();
  }));
  $("#fReset").addEventListener("click", () => { st.f = {}; save(); drawFilters(); fitted = false; run(); });
  OPS.companyControl($("#companyCtl"));
  document.addEventListener("company-change", () => { fitted = false; run(); });
  document.addEventListener("ops-change", () => res && draw());

  // ---- data + drawing
  function query() {
    const p = new URLSearchParams();
    F.forEach(([id, , , param]) => { if (st.f[id]) p.set(param, st.f[id]); });
    if (OPS.company() !== "both") p.set("company", OPS.company());
    return p.toString();
  }
  let req = 0;
  async function run() {
    const my = ++req;
    $("#status").textContent = "Loading…";
    let r;
    try { r = await fetch(`/api/scenario/${SC}/run/?${query()}`).then((x) => x.json()); } catch (e) { $("#status").textContent = "Could not load: " + e.message; return; }
    if (my !== req) return;
    if (r.empty || r.error) { $("#status").textContent = r.error || "No data for this selection."; return; }
    res = r;
    drawToggles(); layout(); draw();
    const f = F.filter(([id]) => st.f[id]).map(([id, , n]) => st.f[id]);
    $("#status").textContent = `${f.length ? f.join(" › ") : CIRCLE + " — all"} · ${r.sites.length} sites`;
  }
  function colorMap() {
    const c = {};
    ["pre", "post", "rd"].forEach((k) => ((res.geo[k] || {}).features || []).forEach((f) => { c[f.properties.code] = f.properties.color; }));
    return c;
  }
  function popup(s) {
    const rc = res.meta.recharge_col;
    const lines = BASE === "rd"
      ? [["PRE (previously tagged)", s.rd_post], ["RDs proposed", s.pre], ["Your post", s.post]]
      : [["Current", s.pre], ["Plan", s.post]].concat(s.rd_post ? [["RD final", s.rd_post]] : []);
    return `<div class="pp sitepp"><b class="sid">${esc(s.site_id)}</b> · ${esc(s.operator)}
      <table class="hier">
        <tr><th>Big Region</th><td>${esc(s.big_region || CIRCLE)}</td></tr><tr><th>Region</th><td>${esc(s.region)}</td></tr>
        <tr><th>BM</th><td>${esc(s.bm || "—")}</td></tr><tr><th>ASM</th><td>${esc(s.arm || "—")}</td></tr>
        <tr><th>District</th><td>${esc(s.district || "—")}</td></tr><tr><th>City</th><td>${esc(s.tehsil || "—")}</td></tr>
        <tr><th>Area</th><td>${esc(s.area || "—")}</td></tr>
        ${rc ? `<tr class="rc"><th>${esc(rc)}</th><td><b>${fmt(s.recharge)}</b></td></tr>` : ""}
        ${lines.map(([a, b]) => `<tr><th>${esc(a)}</th><td><b>${esc(b || "—")}</b></td></tr>`).join("")}
      </table></div>`;
  }
  function draw() {
    const g = res.geo, cols = colorMap(), all = L.latLngBounds([]);
    DEFS.forEach((d) => {
      const o = M[d.id]; o.group.clearLayers();
      const fc = g[d.geo];
      if (!fc) { o.box.querySelector(".cnt").textContent = ""; return; }
      if (g.context && (g.context.features || []).length) {
        o.group.addLayer(L.geoJSON(g.context, { interactive: false, style: { color: "#b6bdca", weight: 0.6, fillColor: "#cfd4de", fillOpacity: 0.35 } }));
      }
      const terr = L.geoJSON(fc, {
        style: (f) => ({ color: "#ffffff", weight: 1.2, fillColor: f.properties.color, fillOpacity: 0.55 }),
        onEachFeature: (f, l) => l.bindTooltip(`<b>${esc(f.properties.code)}</b> · ${f.properties.sites} sites`, { sticky: true, className: "tt" }),
      });
      o.group.addLayer(terr);
      if (terr.getBounds().isValid()) all.extend(terr.getBounds());
      fc.features.filter((f) => f.properties.ly != null).forEach((f) => o.group.addLayer(L.marker([f.properties.ly, f.properties.lx], {
        interactive: false, icon: L.divIcon({ className: "frlabel", html: `<span style="border-color:${f.properties.color}">${esc(f.properties.code)}</span>`, iconSize: null }) })));
      res.sites.filter((s) => s.latitude != null && OPS.visible(s.operator)).forEach((s) => {
        const code = s[d.col];
        o.group.addLayer(OPS.marker([s.latitude, s.longitude], s.operator, { radius: 4, fillColor: OPS.fill(s.operator, cols[code] || "#8a93a6"), fillOpacity: 0.95, color: "#fff", weight: 0.8 })
          .bindTooltip(`${esc(s.site_id)} · ${esc(code || "")}`, { className: "tt" })
          .on("click", (ev) => L.popup({ maxWidth: 340 }).setLatLng(ev.latlng).setContent(popup(s)).openOn(o.map)));
      });
      o.box.querySelector(".cnt").textContent = `${fc.features.length ? new Set(fc.features.map((f) => f.properties.code)).size : 0} franchises`;
    });
    if (!fitted && all.isValid()) { fitted = true; const first = M[visibleIds()[0]]; setTimeout(() => first.map.fitBounds(all.pad(0.03)), 80); }
  }
  window.addEventListener("resize", () => Object.values(M).forEach((o) => o.map.invalidateSize()));

  fetch("/api/filters/").then((x) => x.json()).then((f) => { combos = f.combos || []; drawFilters(); run(); });
})();
