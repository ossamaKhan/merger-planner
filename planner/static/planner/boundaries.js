/* Boundaries page: PTA district check + comparison layer */
(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const csrf = () => ($("input[name=csrfmiddlewaretoken]") || {}).value || "";
  const B = { chk: null, cmp: null, zones: null, plan: null, side: "post" };
  const STATUS_COLOR = { ok: "#13824a", "in a different district": "#ff7a00", "outside PTA districts": "#c81e1e", "file district not in PTA list": "#9b0057", "no coordinates": "#888" };
  const status = (t, busy) => { const el = $("#bStatus"); el.textContent = t; el.classList.toggle("busy", !!busy); el.classList.toggle("hidden", !t); };

  // ---------------- map
  const map = L.map("bMap", { preferCanvas: true, zoomSnap: 0.5 }).setView([30.8, 72.5], 7);
  L.tileLayer("https://{s}.basemap.cartocdn.com/light_nolabels/{z}/{x}/{y}{r}.png", { attribution: "© OpenStreetMap © CARTO", subdomains: "abcd", maxZoom: 19 }).addTo(map);
  map.createPane("names"); map.getPane("names").style.zIndex = 450; map.getPane("names").style.pointerEvents = "none";
  L.tileLayer("https://{s}.basemap.cartocdn.com/light_only_labels/{z}/{x}/{y}{r}.png", { subdomains: "abcd", maxZoom: 19, pane: "names" }).addTo(map); // area names above colours
  AreaNames.attach([map]);
  const LY = {};
  const toggle = (id, key) => $("#" + id).addEventListener("change", (e) => { const l = LY[key]; if (!l) return; e.target.checked ? l.addTo(map) : l.remove(); });
  [["lPta", "pta"], ["lOk", "ok"], ["lBad", "bad"], ["lPlan", "plan"], ["lCmp", "cmp"]].forEach(([i, k]) => toggle(i, k));
  const setLayer = (key, layer, cb) => { if (LY[key]) LY[key].remove(); LY[key] = layer; if ($("#" + cb).checked) layer.addTo(map); };

  async function loadPta() {
    const gj = await fetch("/api/boundaries/districts/").then((r) => r.json());
    setLayer("pta", L.geoJSON(gj, {
      style: { color: "#1f2a44", weight: 2, fill: true, fillOpacity: 0.02, dashArray: "6 4" },
      onEachFeature: (f, l) => l.bindTooltip(`<b>${esc(f.properties.name)}</b>${f.properties.province ? " · " + esc(f.properties.province) : ""}<br><span class="muted">${esc(f.properties.source || "built-in")}</span>`, { sticky: true }),
    }), "lPta");
  }
  function drawSites() {
    const ok = [], bad = [];
    B.chk.sites.forEach((s) => {
      if (s.latitude == null || !OPS.visible(s.operator)) return;
      const m = OPS.marker([s.latitude, s.longitude], s.operator, { radius: s.status === "ok" ? 3.4 : 5, fillColor: STATUS_COLOR[s.status], fillOpacity: 0.95, color: "#fff", weight: 0.8 })
        .bindPopup(sitePopup(s));
      (s.status === "ok" ? ok : bad).push(m);
    });
    setLayer("ok", L.layerGroup(ok), "lOk");
    setLayer("bad", L.layerGroup(bad), "lBad");
    OPS.counts(B.chk.sites);
    if (B.keepView) { B.keepView = false; return; }
    const pts = B.chk.sites.filter((s) => s.latitude != null).map((s) => [s.latitude, s.longitude]);
    if (pts.length) map.fitBounds(pts, { padding: [20, 20] });
  }
  function sitePopup(s) {
    const zone = B.cmp && B.cmp.site_zone ? B.cmp.site_zone[s.site_id] : null;
    return `<b>${esc(s.site_id)}</b> · ${esc(s.operator)}<br><b style="color:${STATUS_COLOR[s.status]}">${esc(s.status)}</b><br>
      File district: ${esc(s.file_district || "—")}<br>PTA district: <b>${esc(s.pta_district || "none")}</b>
      ${s.km_outside != null ? `<br>${s.km_outside} km outside ${esc(s.nearest_pta)}` : ""}
      <br>Franchise: ${esc(s.pre)} → <b>${esc(s.post)}</b><br>ASM ${esc(s.arm)} · BM ${esc(s.bm)}${zone ? `<br>${esc(B.cmp.layer)}: <b>${esc(zone)}</b>` : ""}`;
  }
  async function loadPlan() {
    const sc = $("#bScenario").value;
    if (!sc || !$("#lPlan").checked) return;
    status("Drawing planned territories…", true);
    const r = await fetch(`/api/scenario/${sc}/run/`).then((x) => x.json());
    status("");
    if (r.empty) return;
    setLayer("plan", L.geoJSON(r.geo.post, {
      style: (f) => ({ color: f.properties.color, weight: 1.2, fillColor: f.properties.color, fillOpacity: 0.3 }),
      onEachFeature: (f, l) => l.bindTooltip(`<b>${esc(f.properties.code)}</b> · ${f.properties.sites} sites`, { sticky: true }),
    }), "lPlan");
    if (LY.pta && $("#lPta").checked) LY.pta.bringToFront();
  }
  $("#lPlan").addEventListener("change", (e) => { if (e.target.checked && !LY.plan) loadPlan(); });

  async function loadZones(id) {
    if (!id) { if (LY.cmp) LY.cmp.remove(); LY.cmp = null; return; }
    const gj = await fetch(`/api/layers/${id}/zones/`).then((r) => r.json());
    const pts = (gj.points || []).map((p) => L.circleMarker([p[1], p[2]], { radius: 2.5, color: "#6b2fbf", weight: 1, fillOpacity: 0.6 }).bindTooltip(`${esc(p[0])}`));
    setLayer("cmp", L.layerGroup([L.geoJSON(gj, {
      style: { color: "#6b2fbf", weight: 2.2, fill: true, fillOpacity: 0.04 },
      onEachFeature: (f, l) => l.bindTooltip(`<b>${esc(f.properties.layer)}</b>: ${esc(f.properties.name)}`, { sticky: true }),
    }), ...pts]), "lCmp");
  }

  // ---------------- cards & tables
  function card(t, v, sub, cls = "") { return `<div class="kcard ${cls}"><div class="kt">${t}</div><div class="kval"><span class="post">${v}</span></div><div class="ks">${sub || ""}</div></div>`; }
  function drawCards() {
    const s = B.chk.summary, n = (k) => s[k] || 0;
    const preX = B.chk.pre_spread.filter((x) => x.districts > 1).length, postX = B.chk.post_spread.filter((x) => x.districts > 1).length;
    const html = [
      card("Sites checked", s.sites, `${esc(B.chk.month)} · borders: ${esc(B.chk.source)}`),
      card("Inside their district", n("ok"), `${((n("ok") / s.sites) * 100).toFixed(1)}%`, "good"),
      card("Outside every PTA district", n("outside PTA districts"), "out of bound — check coordinates", n("outside PTA districts") ? "bad" : ""),
      card("In a different district", n("in a different district"), "PTA district ≠ district in your file", n("in a different district") ? "warn" : ""),
      card("File district not in PTA list", n("file district not in PTA list"), "fix in the District names tab", n("file district not in PTA list") ? "warn" : ""),
      card("Current franchises crossing borders", preX, `of ${B.chk.pre_spread.length}`),
      card("Planned franchises crossing borders", postX, `of ${B.chk.post_spread.length}`, postX ? "warn" : "good"),
    ];
    if (B.cmp && !B.cmp.empty) {
      const c = B.cmp.summary;
      html.push(card(`Planned franchises inside one ${esc(B.cmp.layer)} area`, c.franchises_one_zone, `${c.franchises_multi_zone} span 2+ areas`),
        card(`My sites outside ${esc(B.cmp.layer)}`, c.our_sites_outside, `${c.zones} areas${c.their_sites != null ? ` · ${c.their_sites} of their sites` : ""}`));
    }
    $("#bCards").innerHTML = html.join("");
  }

  function drawSitesTable() {
    const f = $("#sFilter").value, q = $("#sFind").value.trim().toLowerCase();
    const rows = B.chk.sites.filter((s) => (f === "__bad" ? s.status !== "ok" : !f || s.status === f) &&
      (!q || `${s.site_id} ${s.pre} ${s.post} ${s.arm} ${s.bm} ${s.file_district} ${s.pta_district}`.toLowerCase().includes(q)));
    $("#sTbl").innerHTML = `<thead><tr><th>Site</th><th>Status</th><th>File district</th><th>PTA district</th><th class="r">km outside</th><th>Current → planned</th><th>ASM</th><th>BM</th><th>Region</th></tr></thead><tbody>` +
      rows.slice(0, 2000).map((s) => `<tr><td><a href="#" data-zoom="${esc(s.site_id)}">${esc(s.site_id)}</a> <span class="muted small">${esc((s.operator || "").slice(0, 2))}</span></td>
        <td><span class="pill" style="background:${STATUS_COLOR[s.status]}22;color:${STATUS_COLOR[s.status]}">${esc(s.status)}</span></td>
        <td>${esc(s.file_district || "—")}</td><td><b>${esc(s.pta_district || "—")}</b>${s.nearest_pta && !s.pta_district ? ` <span class="muted small">nearest ${esc(s.nearest_pta)}</span>` : ""}</td>
        <td class="r">${s.km_outside ?? ""}</td><td>${esc(s.pre)} → <b>${esc(s.post)}</b></td><td class="small">${esc(s.arm)}</td><td class="small">${esc(s.bm)}</td><td class="small">${esc(s.region)}</td></tr>`).join("") +
      (rows.length > 2000 ? `<tr><td colspan="9" class="muted">${rows.length - 2000} more — use Excel</td></tr>` : "") + "</tbody>";
  }
  $("#sFilter").addEventListener("change", drawSitesTable);
  $("#sFind").addEventListener("input", drawSitesTable);
  document.addEventListener("click", (e) => {
    const z = e.target.closest("[data-zoom]"); if (!z) return;
    e.preventDefault();
    const s = B.chk.sites.find((x) => x.site_id === z.dataset.zoom);
    if (s && s.latitude != null) { map.setView([s.latitude, s.longitude], 13); L.popup().setLatLng([s.latitude, s.longitude]).setContent(sitePopup(s)).openOn(map); $("#bMap").scrollIntoView({ behavior: "smooth", block: "center" }); }
  });

  function drawNames() {
    const opts = (sel) => `<option value="">— no match —</option>` + B.chk.pta_names.map((n) => `<option ${n === sel ? "selected" : ""}>${esc(n)}</option>`).join("");
    const cnt = {};
    B.chk.sites.forEach((s) => { cnt[s.file_district] = (cnt[s.file_district] || 0) + 1; });
    $("#nTbl").innerHTML = `<thead><tr><th>District in your file</th><th class="r">Sites</th><th>PTA district</th><th>How matched</th></tr></thead><tbody>` +
      B.chk.name_map.map((m) => `<tr class="${m.pta ? "" : "nomatch"}"><td><b>${esc(m.file)}</b></td><td class="r">${cnt[m.file] || 0}</td>
        <td>${B.chk.using_pta ? `<select data-alias="${esc(m.file)}">${opts(m.pta)}</select>` : esc(m.pta || "—")}</td>
        <td class="small ${m.pta ? "" : "down"}">${esc(m.how || "not found")}</td></tr>`).join("") + "</tbody>" +
      (B.chk.mismatch_pairs.length ? `<tbody><tr><td colspan="4"><h4>Sites physically in another district</h4></td></tr>` +
        B.chk.mismatch_pairs.map((p) => `<tr><td>${esc(p.file_district)} (file)</td><td class="r">${p.sites}</td><td>→ actually in <b>${esc(p.pta_district)}</b></td><td></td></tr>`).join("") + "</tbody>" : "");
    if (!B.chk.using_pta) $("#nTbl").insertAdjacentHTML("beforebegin", "");
  }
  $("#nTbl").addEventListener("change", async (e) => {
    const s = e.target.closest("[data-alias]"); if (!s) return;
    await fetch("/api/boundary-alias/", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() }, body: JSON.stringify({ file: s.dataset.alias, pta: s.value || null }) });
    runCheck();
  });

  function drawSpread() {
    const rows = (B.side === "post" ? B.chk.post_spread : B.chk.pre_spread).filter((r) => !$("#spOnly").checked || r.districts > 1);
    $("#spTbl").innerHTML = `<thead><tr><th>Franchise</th><th class="r">Sites</th><th>Home district</th><th class="r">Home share</th><th class="r">Out of bound</th><th>Other districts</th><th>ASM</th><th>BM</th></tr></thead><tbody>` +
      rows.map((r) => `<tr><td><b>${esc(r.franchise)}</b></td><td class="r">${r.sites}</td><td>${esc(r.home_district)}</td><td class="r">${r.home_share}%</td>
        <td class="r ${r.out_of_bound ? "down" : ""}"><b>${r.out_of_bound}</b></td><td class="small">${esc(r.other_districts)}
        ${r.out_sites.length ? `<div class="muted small">${r.out_sites.slice(0, 8).map((s) => `<a href="#" data-zoom="${esc(s)}">${esc(s)}</a>`).join(", ")}${r.out_sites.length > 8 ? "…" : ""}</div>` : ""}</td>
        <td class="small">${esc(r.arms)}</td><td class="small">${esc(r.bms)}</td></tr>`).join("") +
      (rows.length ? "" : `<tr><td colspan="8" class="muted">No franchise crosses a PTA district border.</td></tr>`) + "</tbody>";
  }
  $("#spSide").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; B.side = b.dataset.v; $$("#spSide button").forEach((x) => x.classList.toggle("on", x === b)); drawSpread(); });
  $("#spOnly").addEventListener("change", drawSpread);

  function drawCompare() {
    const c = B.cmp;
    $("#cEmpty").classList.toggle("hidden", !!(c && !c.empty));
    $("#cBody").classList.toggle("hidden", !(c && !c.empty));
    if (!c || c.empty) { if (c && c.error) $("#cEmpty").textContent = c.error; return; }
    $$(".cName").forEach((x) => { x.textContent = c.layer; });
    $("#cOurs").innerHTML = `<thead><tr><th>My planned franchise</th><th class="r">Sites</th><th class="r">Area km²</th><th>Mostly in (${esc(c.layer)})</th><th class="r">Share of my area</th><th class="r">Their areas touched</th><th>Breakdown</th><th class="r">Sites outside their areas</th></tr></thead><tbody>` +
      c.ours.map((r) => `<tr><td><b>${esc(r.franchise)}</b></td><td class="r">${r.sites}</td><td class="r">${r.area_km2}</td><td>${esc(r.best_zone || "—")}</td>
        <td class="r">${r.best_share}%</td><td class="r ${r.zones > 2 ? "down" : ""}">${r.zones}</td><td class="small">${esc(r.zone_detail)}</td><td class="r">${r.sites_outside_zones}</td></tr>`).join("") + "</tbody>";
    $("#cZones").innerHTML = `<thead><tr><th>${esc(c.layer)} area</th><th class="r">Area km²</th>${c.summary.their_sites != null ? '<th class="r">Their sites</th>' : ""}<th class="r">My sites</th><th class="r">My franchises</th><th>Main franchise</th><th>My franchises inside</th></tr></thead><tbody>` +
      c.zones.map((z) => `<tr><td><b>${esc(z.zone)}</b></td><td class="r">${z.area_km2}</td>${c.summary.their_sites != null ? `<td class="r">${z.their_sites}</td>` : ""}<td class="r">${z.our_sites}</td>
        <td class="r">${z.our_franchises}</td><td>${esc(z.main_franchise || "—")} ${z.main_franchise ? `<span class="muted small">${z.main_share}%</span>` : ""}</td><td class="small">${esc(z.our_detail)}</td></tr>`).join("") + "</tbody>";
  }

  $$("#bTabs button").forEach((b) => b.addEventListener("click", () => {
    $$("#bTabs button").forEach((x) => x.classList.toggle("on", x === b));
    $$(".btab").forEach((t) => t.classList.toggle("hidden", t.id !== "t-" + b.dataset.t));
  }));

  // ---------------- run
  async function runCheck() {
    const sc = $("#bScenario").value, lid = $("#bLayer").value;
    $("#bXlsx").href = `/boundaries/check.xlsx?scenario=${sc}&layer=${lid}`;
    status("Checking every site against the PTA districts…", true);
    B.chk = await fetch(`/api/boundary-check/?scenario=${sc}`).then((r) => r.json());
    if (B.chk.empty) { status("No site data loaded yet — upload your site file first."); return; }
    const st = Object.keys(B.chk.summary).filter((k) => k !== "sites");
    $("#sFilter").innerHTML = `<option value="__bad">All needing a check (${B.chk.sites.filter((s) => s.status !== "ok").length})</option><option value="">All sites (${B.chk.summary.sites})</option>` +
      st.map((k) => `<option value="${esc(k)}">${esc(k)} (${B.chk.summary[k]})</option>`).join("");
    B.cmp = null;
    if (lid) {
      status("Comparing with the other layer…", true);
      B.cmp = await fetch(`/api/layers/${lid}/compare/?scenario=${sc}`).then((r) => r.json());
    }
    drawCards(); drawSites(); drawSitesTable(); drawNames(); drawSpread(); drawCompare();
    status("");
  }
  $("#bScenario").addEventListener("change", () => { if (LY.plan) { LY.plan.remove(); LY.plan = null; } runCheck(); loadPlan(); });
  $("#bLayer").addEventListener("change", (e) => { loadZones(e.target.value); runCheck(); });

  OPS.control($("#bOps"), { noColor: true, companyHere: true });
  document.addEventListener("ops-change", () => { if (B.chk && !B.chk.empty) { B.keepView = true; drawSites(); } });

  (async () => {
    const first = $("#bLayer option[value]:not([value=''])");
    if (first) { $("#bLayer").value = first.value; loadZones(first.value); }
    await loadPta();
    runCheck();
  })();
})();
