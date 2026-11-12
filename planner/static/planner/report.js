/* Scenario report: merged franchise tables, consolidated table, pre/post maps, KML */
(() => {
  "use strict";
  const root = document.getElementById("report");
  const SC = root.dataset.scenario;
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ALL = "__all__";
  const R = { filters: null, res: null, level: "", unit: "", months: new Set(), agg: "avg", tree: "sales", open: new Set(), closed: new Set(), kcols: null, expandAll: false };
  const LV = { big: [6, "Big Region"], region: [0, "Region"], bm: [3, "BM"], arm: [4, "ASM"], district: [1, "District"], tehsil: [2, "City"] };

  function fmt(v, dec) {
    if (v === null || v === undefined || Number.isNaN(v)) return "—";
    const a = Math.abs(v);
    if (a >= 1e9) return (v / 1e9).toFixed(2) + "B";
    if (a >= 1e6) return (v / 1e6).toFixed(2) + "M";
    if (a >= 1e4) return (v / 1e3).toFixed(1) + "K";
    return v.toLocaleString(undefined, { maximumFractionDigits: dec ?? (a < 10 ? 2 : a < 100 ? 1 : 0) });
  }
  const pct = (a, b) => (b ? ((a - b) / Math.abs(b)) * 100 : null);
  const pctTxt = (p) => (p === null || !isFinite(p) || Math.abs(p) < 0.05 ? "" : `${p > 0 ? "+" : ""}${p.toFixed(1)}%`);
  const status = (t, busy) => { const el = $("#rStatus"); el.textContent = t; el.classList.toggle("busy", !!busy); el.classList.toggle("hidden", !t); };

  function query() {
    const p = new URLSearchParams();
    p.set("months", [...R.months].join("|"));
    p.set("agg", R.agg);
    p.set("sitekpis", "1");
    if (OPS.company() !== "both") p.set("company", OPS.company());
    const u = R.unit;
    if (R.level && u) {
      if (R.level === "big") p.set("bigs", u);
      if (R.level === "region") p.set("regions", u);
      if (R.level === "district") p.set("districts", u);
      if (R.level === "tehsil") { const [d, t] = u.split(" / "); p.set("districts", d); p.set("tehsils", t); }
      if (R.level === "bm") p.set("bms", u);
      if (R.level === "arm") p.set("arms", u);
    }
    return p.toString();
  }

  // ---------------- maps
  const M = {};
  ["pre", "post"].forEach((side) => {
    const map = L.map(side === "pre" ? "rMapPre" : "rMapPost", { preferCanvas: true, zoomSnap: 0.5 }).setView([30.5, 72], 7);
    L.tileLayer("https://{s}.basemap.cartocdn.com/light_nolabels/{z}/{x}/{y}{r}.png", { attribution: "© OpenStreetMap © CARTO", subdomains: "abcd", maxZoom: 19 }).addTo(map);
    map.createPane("names"); map.getPane("names").style.zIndex = 450; map.getPane("names").style.pointerEvents = "none";
    L.tileLayer("https://{s}.basemap.cartocdn.com/light_only_labels/{z}/{x}/{y}{r}.png", { subdomains: "abcd", maxZoom: 19, pane: "names" }).addTo(map); // area names above colours
    M[side] = { map, layers: [] };
  });
  let syncing = false;
  const sync = (a, b) => a.on("move", () => { if (syncing) return; syncing = true; b.setView(a.getCenter(), a.getZoom(), { animate: false }); syncing = false; });
  sync(M.pre.map, M.post.map); sync(M.post.map, M.pre.map);
  AreaNames.attach([M.post.map, M.pre.map]); // neighbourhood / town names, on by default

  function drawMaps() {
    const r = R.res, k = r.meta.primary;
    const rows = { pre: Object.fromEntries(r.pre.map((x) => [x.code, x])), post: Object.fromEntries(r.post.map((x) => [x.code, x])) };
    ["pre", "post"].forEach((side) => {
      const { map } = M[side];
      M[side].layers.forEach((l) => l.remove());
      const terr = L.geoJSON(r.geo[side], {
        style: (f) => ({ color: f.properties.color, weight: 1.2, fillColor: f.properties.color, fillOpacity: 0.45 }),
        onEachFeature: (f, l) => {
          const p = f.properties, row = rows[side][p.code];
          l.bindTooltip(p.code, { sticky: true });
          l.bindPopup(`<b>${esc(p.code)}</b><br>${p.sites} sites · ${fmt(p.area_km2)} km²` + (row && k ? `<br>${esc(k)}: <b>${fmt(row.kpis[k])}</b>` : "") +
            (side === "post" && row ? `<br>Members: ${row.members.map((m) => esc(m.code)).join(", ")}<br><a href="#m-${encodeURIComponent(p.code)}">open table ↓</a>` : "") +
            (side === "pre" && row ? `<br>→ ${Object.keys(row.mapped_to).map(esc).join(", ")}` : ""));
        },
      }).addTo(map);
      const key = side;
      const color = Object.fromEntries(r.geo[side].features.map((f) => [f.properties.code, f.properties.color]));
      const dots = L.layerGroup(r.sites.filter((s) => s.latitude != null && OPS.visible(s.operator)).map((s) => OPS.marker([s.latitude, s.longitude], s.operator,
        { radius: 3.4, fillColor: OPS.fill(s.operator, color[s[key]] || "#888"), fillOpacity: 0.95, color: "#fff", weight: 0.7 }).bindTooltip(`${s.site_id} · ${OPS.NAME[OPS.kind(s.operator)]} · ${s[key]}`)));
      const labels = L.layerGroup(r.geo[side].features.filter((f) => f.properties.ly != null).map((f) => L.marker([f.properties.ly, f.properties.lx], { interactive: false, icon: L.divIcon({ className: "frlabel", html: `<span style="border-color:${f.properties.color}">${esc(f.properties.code)}</span>`, iconSize: null }) })));
      const ctx = L.geoJSON(r.geo.context || { type: "FeatureCollection", features: [] }, {
        style: { color: "#6b7385", weight: 1, dashArray: "3 3", fillColor: "#b8bfcc", fillOpacity: 0.35 },
        onEachFeature: (f, l) => { const p = f.properties; l.bindTooltip(`<b>Outside this view</b><br>${esc(p.code)} · ${p.sites} sites<br>ASM: ${esc(p.arms.join(", "))}<br>BM: ${esc(p.bms.join(", "))}<br>City: ${esc(p.tehsils.join(", "))}`, { sticky: true }); },
      }).addTo(map);
      ctx.bringToBack();
      const units = {};
      const UST = { arm: { color: "#111", weight: 2.4, dashArray: "8 5" }, bm: { color: "#7a1fa2", weight: 3.2, dashArray: "12 6" },
        tehsil: { color: "#0b6e4f", weight: 1.8, dashArray: "2 4" }, district: { color: "#1f2a44", weight: 2.4 }, region: { color: "#c81e1e", weight: 4 } };
      Object.entries(r.geo.units || {}).forEach(([lvl, fc]) => {
        units[lvl] = L.geoJSON(fc, { style: { ...UST[lvl], fill: false, opacity: 0.9 },
          onEachFeature: (f, l) => l.bindTooltip(`<b>${LV[lvl][1]}: ${esc(f.properties.name)}</b><br>${f.properties.sites} sites · ${f.properties.pre.length} → ${f.properties.post.length} franchises`, { sticky: true }) });
      });
      M[side].layers = [terr, dots, labels, ctx, ...Object.values(units)];
      M[side].units = units;
      $$(".rU").forEach((cb) => { if (cb.checked && units[cb.value]) units[cb.value].addTo(map); });
      if ($("#rSites").checked) dots.addTo(map);
      if ($("#rLabels").checked) labels.addTo(map);
      map.getContainer().classList.toggle("z-low", map.getZoom() < 9);
      map.on("zoomend", () => map.getContainer().classList.toggle("z-low", map.getZoom() < 9));
    });
    const pts = r.sites.filter((s) => s.latitude != null).map((s) => [s.latitude, s.longitude]);
    if (pts.length && !R.keepView) M.post.map.fitBounds(pts, { padding: [20, 20] });
    R.keepView = false;
    OPS.counts(r.sites);
    $("#rPreN").textContent = `${r.summary.pre_franchises} franchises`;
    $("#rPostN").textContent = `${r.summary.post_franchises} franchises`;
  }
  $$(".rU").forEach((cb) => cb.addEventListener("change", () => ["pre", "post"].forEach((sd) => {
    const l = (M[sd].units || {})[cb.value]; if (!l) return; cb.checked ? l.addTo(M[sd].map) : l.remove();
  })));
  ["rSites", "rLabels"].forEach((id, i) => $("#" + id).addEventListener("change", (e) => ["pre", "post"].forEach((sd) => {
    const l = M[sd].layers[i + 1]; if (!l) return; e.target.checked ? l.addTo(M[sd].map) : l.remove();
  })));

  // ---------------- tables
  function kpiRows(r) {
    return [...r.meta.kpis.map((k) => ({ label: k.label, get: (o) => o.kpis[k.key], proj: (o) => o.projected[k.key], lb: k.lower_better, sum: true })),
      ...r.meta.derived.map((d) => ({ label: d.label, get: (o) => o.derived[d.label], proj: (o) => o.projected_derived[d.label], lb: d.lower_better, sum: false }))];
  }
  function drawMerged() {
    const r = R.res, q = $("#rFind").value.trim().toLowerCase();
    const merged = r.post.filter((p) => p.members.length > 1).sort((a, b) => b.sites - a.sites);
    $("#rMergedN").textContent = `${merged.length} planned franchise(s) formed by merging`;
    const rows = kpiRows(r);
    const list = merged.filter((p) => !q || p.code.toLowerCase().includes(q) || p.members.some((m) => m.code.toLowerCase().includes(q)));
    $("#rMerged").innerHTML = list.map((p) => {
      const ops = p.operators;
      return `<article class="mcard" id="m-${encodeURIComponent(p.code)}" style="--c:${p.color}">
        <header><span class="sw lg" style="background:${p.color}"></span><div><h3>${esc(p.code)} ${p.is_new ? '<span class="tag">new code</span>' : ""}</h3>
          <div class="muted small">${fmt(p.sites)} sites (Telenor ${ops.Telenor || 0} · Ufone ${ops.Ufone || 0}) · ${esc(p.districts.join(", "))} · ${esc(p.regions.join(", "))}</div>
          <div class="muted small">BM: ${esc(p.bms.join(", ") || "—")} · ASM: ${esc(p.arms.join(", ") || "—")}</div></div></header>
        <div class="tblwrap"><table class="tbl compact"><thead><tr><th>KPI</th>
          ${p.members.map((m) => `<th class="r"><span class="sw" style="background:${m.color}"></span>${esc(m.code)}</th>`).join("")}
          <th class="r cons">Consolidated</th><th class="r">Projected</th><th class="r">Δ</th></tr></thead><tbody>
          <tr><td>Sites</td>${p.members.map((m) => `<td class="r">${fmt(m.sites)}</td>`).join("")}<td class="r cons"><b>${fmt(p.sites)}</b></td><td class="r">${fmt(p.sites)}</td><td></td></tr>
          ${rows.map((x) => {
            const base = x.get(p), pr = x.proj(p), d = pct(pr, base);
            return `<tr><td>${esc(x.label)}</td>${p.members.map((m) => `<td class="r">${fmt(x.get(m))}</td>`).join("")}
              <td class="r cons"><b>${fmt(base)}</b></td><td class="r">${fmt(pr)}</td><td class="r ${Math.abs(d) > 0.05 ? ((d > 0) !== !!x.lb ? "up" : "down") : ""}">${pctTxt(d)}</td></tr>`;
          }).join("")}</tbody></table></div></article>`;
    }).join("") || '<div class="muted">No merged franchises yet — tag franchises in the planner.</div>';
  }
  function drawConsolidated() {
    const r = R.res, k = r.meta.primary;
    const kp = r.meta.kpis, dv = r.meta.derived;
    const rows = [...r.post].sort((a, b) => b.sites - a.sites);
    const head = `<thead><tr><th>Planned franchise</th><th>Members</th><th>BM</th><th>ASM</th><th class="r">Sites</th><th class="r">TP</th><th class="r">Ufone</th>
      ${kp.map((x) => `<th class="r">${esc(x.label)}</th>`).join("")}${dv.map((d) => `<th class="r">${esc(d.label)}</th>`).join("")}
      ${k ? `<th class="r">${esc(k)} projected</th>` : ""}</tr></thead>`;
    const body = rows.map((p) => `<tr><td><span class="sw" style="background:${p.color}"></span><b>${esc(p.code)}</b><div class="muted small">${esc(p.districts.join(", "))}</div></td>
      <td class="small">${p.members.map((m) => esc(m.code)).join(", ")}</td><td class="small">${esc(p.bms.join(", "))}</td><td class="small">${esc(p.arms.join(", "))}</td><td class="r">${fmt(p.sites)}</td><td class="r">${p.operators.Telenor || 0}</td><td class="r">${p.operators.Ufone || 0}</td>
      ${kp.map((x) => `<td class="r">${fmt(p.kpis[x.key])}</td>`).join("")}${dv.map((d) => `<td class="r">${fmt(p.derived[d.label])}</td>`).join("")}
      ${k ? `<td class="r">${fmt(p.projected[k])} <small class="up">${pctTxt(pct(p.projected[k], p.kpis[k]))}</small></td>` : ""}</tr>`).join("");
    const s = r.summary;
    const tot = `<tr class="totrow"><td><b>Total (${rows.length})</b></td><td></td><td></td><td></td><td class="r"><b>${fmt(s.sites)}</b></td><td class="r">${s.operators.Telenor || 0}</td><td class="r">${s.operators.Ufone || 0}</td>
      ${kp.map((x) => `<td class="r"><b>${fmt(s.totals[x.key])}</b></td>`).join("")}${dv.map((d) => `<td class="r"><b>${fmt(s.derived_totals[d.label])}</b></td>`).join("")}
      ${k ? `<td class="r"><b>${fmt(s.projected_totals[k])}</b></td>` : ""}</tr>`;
    $("#rConsol").innerHTML = head + `<tbody>${body}${tot}</tbody>`;
  }
  function drawMapping() {
    const r = R.res, k = r.meta.primary;
    $("#rMap").innerHTML = `<thead><tr><th>Current franchise</th><th class="r">Sites</th>${k ? `<th class="r">${esc(k)}</th>` : ""}<th>Planned into</th><th>Districts</th><th>BM</th><th>ASM</th></tr></thead><tbody>` +
      [...r.pre].sort((a, b) => a.code.localeCompare(b.code)).map((p) => `<tr><td><span class="sw" style="background:${p.color}"></span>${esc(p.code)}</td><td class="r">${fmt(p.sites)}</td>
        ${k ? `<td class="r">${fmt(p.kpis[k])}</td>` : ""}<td>${Object.entries(p.mapped_to).map(([c, n]) => `<b>${esc(c)}</b>${Object.keys(p.mapped_to).length > 1 ? ` (${n})` : ""}`).join(", ")}</td>
        <td class="small">${esc(p.districts.join(", "))}</td><td class="small">${esc(p.bms.join(", "))}</td><td class="small">${esc(p.arms.join(", "))}</td></tr>`).join("") + "</tbody>";
  }
  function drawOutside() {
    const cs = R.res.geo.context_summary || [], el = $("#rOutside");
    el.classList.toggle("hidden", !cs.length);
    if (!cs.length) return;
    const tot = cs.reduce((a, c) => a + c.sites, 0);
    el.innerHTML = `<div class="o-head"><span class="sw grey"></span><b>Grey area = outside this view</b> · ${tot} neighbouring sites in ${cs.length} planned franchise(s), not counted in the numbers below.</div>
      <div class="o-list">${cs.slice(0, 16).map((c) => `<div class="o-item"><span class="sw" style="background:${c.color}"></span><b>${esc(c.code)}</b> ${c.sites} sites <span class="muted">· ASM ${esc(c.arms.join(", "))} · BM ${esc(c.bms.join(", "))} · ${esc(c.tehsils.join(", "))}</span></div>`).join("")}</div>`;
  }

  // Region breakdown: each region, the sum of regions and the circle total side by side (they must match)
  function drawAlign() {
    const r = R.res, sk = r.site_kpis || {}, sec = $("#rAlignSec");
    const regions = [...new Set(r.sites.map((s) => s.region || "Unknown"))].sort();
    sec.classList.toggle("hidden", regions.length < 2);
    if (regions.length < 2) return;
    const k = r.meta.primary, keys = [k, ...r.meta.kpis.map((x) => x.key).filter((x) => x !== k && ["FCA.", "Act 90D", "Gross Churn", "Total Recharge"].includes(x))];
    const ratio = {};
    r.post.forEach((p) => { ratio[p.code] = {}; keys.forEach((x) => { ratio[p.code][x] = p.kpis[x] ? p.projected[x] / p.kpis[x] : 1; }); });
    const row = () => ({ sites: 0, tp: 0, uf: 0, pre: new Set(), post: new Set(), base: {}, proj: {} });
    const by = {}, sum = row();
    r.sites.forEach((s) => {
      const g = (by[s.region || "Unknown"] ||= row()), v = sk[s.site_id] || {};
      [g, sum].forEach((t) => {
        t.sites++; if (OPS.kind(s.operator) === "tp") t.tp++; else if (OPS.kind(s.operator) === "ufone") t.uf++;
        t.pre.add(s.pre); t.post.add(s.post);
        keys.forEach((x) => { t.base[x] = (t.base[x] || 0) + (v[x] || 0); t.proj[x] = (t.proj[x] || 0) + (v[x] || 0) * ((ratio[s.post] || {})[x] ?? 1); });
      });
    });
    const cb = r.summary;
    const regionsOf = {};
    r.sites.forEach((s) => { (regionsOf[s.post] ||= new Set()).add(s.region || "Unknown"); });
    const shared = Object.entries(regionsOf).filter(([, v]) => v.size > 1);
    const tr = (name, t, cls = "") => `<tr class="${cls}"><td>${name}</td><td class="r">${fmt(t.sites)}</td><td class="r">${fmt(t.tp)}</td><td class="r">${fmt(t.uf)}</td>
      <td class="r">${t.pre.size} → <b>${t.post.size}</b></td>${keys.map((x) => `<td class="r">${fmt(t.base[x])}</td><td class="r">${fmt(t.proj[x])}</td>`).join("")}</tr>`;
    const close = (a, b) => Math.abs((a || 0) - (b || 0)) <= Math.max(1, 1e-6 * Math.abs(b || 0));  // per-site values are rounded to 2 decimals
    const ok = keys.every((x) => close(sum.base[x], cb.totals[x]) && close(sum.proj[x], cb.projected_totals[x])) && sum.sites === r.sites.length;
    $("#rAlign").innerHTML = `<thead><tr><th>Region</th><th class="r" title="Sites in the latest month each site appears in">Sites</th><th class="r">TP</th><th class="r">Ufone</th><th class="r">Franchises pre → post</th>
      ${keys.map((x) => `<th class="r">${esc(x)}</th><th class="r">${esc(x)} proj.</th>`).join("")}</tr></thead><tbody>
      ${regions.map((g) => tr(esc(g), by[g])).join("")}
      ${tr("<b>Sum of regions</b>", sum, "totrow")}
      <tr class="totrow"><td><b>${esc(circle())} (all franchises)</b></td><td class="r"><b>${fmt(r.sites.length)}</b></td><td class="r">${fmt(cb.operators.Telenor || 0)}</td><td class="r">${fmt(cb.operators.Ufone || 0)}</td>
        <td class="r">${cb.pre_franchises} → <b>${cb.post_franchises}</b></td>${keys.map((x) => `<td class="r"><b>${fmt(cb.totals[x])}</b></td><td class="r"><b>${fmt(cb.projected_totals[x])}</b></td>`).join("")}</tr></tbody>`;
    $("#rAlignNote").innerHTML = `<span class="${ok ? "up" : "down"}"><b>${ok ? "✓ Regions add up to " + esc(circle()) : "⚠ Regions do not add up — check the warnings"}</b></span>
      · Sites, KPIs and projections add up exactly. ${shared.length
        ? `Franchise counts don't: ${shared.length} planned franchise(s) span more than one region and are counted in each (${shared.slice(0, 8).map(([c, v]) => `${esc(c)}: ${[...v].map(esc).join(" + ")}`).join("; ")}${shared.length > 8 ? "…" : ""}).`
        : "No planned franchise spans more than one region."}`;
  }

  function hierKpis() {
    const keys = R.res.meta.kpis.map((k) => k.key);
    if (!R.kcols) R.kcols = new Set([R.res.meta.primary, "FCA.", "Act 90D", "Gross Churn", "Total Recharge"].filter((k) => keys.includes(k)));
    return R.res.meta.kpis.filter((k) => R.kcols.has(k.key));
  }
  function drawHier() {
    const r = R.res, sk = r.site_kpis || {}, cols = hierKpis();
    const big = (s) => s.big_region || circle();
    const path = R.tree === "sales" ? (s) => [big(s), s.region || "Unknown", "BM " + (s.bm || "—"), "ASM " + (s.arm || "—"), s.post]
      : (s) => [big(s), s.region || "Unknown", s.district || "Unknown", s.tehsil || "Unknown", s.post];
    const names = R.tree === "sales" ? ["Big Region", "Region", "BM", "ASM", "Planned franchise"] : ["Big Region", "Region", "District", "City", "Planned franchise"];
    const root = { kids: {} };
    r.sites.forEach((s) => {
      let node = root;
      const add = (n) => { n.sites = (n.sites || 0) + 1; n.ops = n.ops || {}; n.ops[s.operator] = (n.ops[s.operator] || 0) + 1;
        n.pre = n.pre || new Set(); n.pre.add(s.pre); n.post = n.post || new Set(); n.post.add(s.post);
        n.k = n.k || {}; const v = sk[s.site_id] || {}; cols.forEach((c) => { n.k[c.key] = (n.k[c.key] || 0) + (v[c.key] || 0); }); };
      add(root);
      path(s).forEach((p) => { node.kids[p] = node.kids[p] || { kids: {} }; node = node.kids[p]; add(node); });
    });
    const rows = [];
    const walk = (node, depth, id) => {
      Object.entries(node.kids).sort((a, b) => b[1].sites - a[1].sites).forEach(([name, n]) => {
        const nid = id + "|" + name, leaf = depth === 4, open = R.expandAll || R.open.has(nid) || (depth === 0 && !R.closed.has(nid));
        const color = leaf ? (r.post.find((p) => p.code === name) || {}).color : null;
        const preList = [...n.pre];
        rows.push(`<tr class="lv${depth}" data-id="${esc(nid)}"><td style="padding-left:${8 + depth * 18}px">${leaf ? "" : `<button class="exp">${open ? "▾" : "▸"}</button>`}
            ${color ? `<span class="sw" style="background:${color}"></span>` : ""}<span class="hn">${esc(name)}</span> <span class="muted small">${names[depth]}</span></td>
          <td class="r">${n.sites}</td><td class="r">${n.ops.Telenor || 0}</td><td class="r">${n.ops.Ufone || 0}</td>
          <td class="r">${leaf ? `<span class="small">${preList.map(esc).join(", ")}</span>` : `${n.pre.size} → <b>${n.post.size}</b>`}</td>
          ${cols.map((c) => `<td class="r">${fmt(n.k[c.key])}</td>`).join("")}</tr>`);
        if (!leaf && open) walk(n, depth + 1, nid);
      });
    };
    walk(root, 0, "");
    $("#rHier").innerHTML = `<thead><tr><th>${names.join(" › ")}</th><th class="r">Sites</th><th class="r">TP</th><th class="r">Ufone</th><th class="r">Franchises pre → post</th>
      ${cols.map((c) => `<th class="r">${esc(c.label)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}
      <tr class="totrow"><td><b>Total</b></td><td class="r"><b>${root.sites || 0}</b></td><td class="r">${(root.ops || {}).Telenor || 0}</td><td class="r">${(root.ops || {}).Ufone || 0}</td>
      <td class="r">${root.pre ? root.pre.size : 0} → <b>${root.post ? root.post.size : 0}</b></td>${cols.map((c) => `<td class="r"><b>${fmt((root.k || {})[c.key])}</b></td>`).join("")}</tr></tbody>`;
    $("#rKpick").innerHTML = r.meta.kpis.map((k) => `<label><input type="checkbox" value="${esc(k.key)}" ${R.kcols.has(k.key) ? "checked" : ""}> ${esc(k.label)}</label>`).join("");
  }
  $("#rHier").addEventListener("click", (e) => {
    const b = e.target.closest(".exp"); if (!b) return;
    const id = b.closest("tr").dataset.id;
    if (R.expandAll) { R.expandAll = false; $$("#rHier tr[data-id]").forEach((tr) => { if (tr.querySelector(".exp")) R.open.add(tr.dataset.id); }); }
    if (!id.slice(1).includes("|")) { R.closed.has(id) ? R.closed.delete(id) : R.closed.add(id); } // Big Region rows start open
    else R.open.has(id) ? R.open.delete(id) : R.open.add(id);
    drawHier();
  });
  $("#rTree").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; R.tree = b.dataset.v; R.open.clear(); $$("#rTree button").forEach((x) => x.classList.toggle("on", x === b)); drawHier(); });
  $("#rExpand").addEventListener("click", () => { R.expandAll = !R.expandAll; if (!R.expandAll) R.open.clear(); $("#rExpand").textContent = R.expandAll ? "Collapse all" : "Expand all"; drawHier(); });
  $("#rKcols").addEventListener("click", () => $("#rKpick").classList.toggle("hidden"));
  $("#rKpick").addEventListener("change", (e) => { e.target.checked ? R.kcols.add(e.target.value) : R.kcols.delete(e.target.value); drawHier(); });

  function card(title, pre, post, sub) {
    return `<div class="kcard"><div class="kt">${title}</div><div class="kval">${pre !== null ? `<span class="pre">${pre}</span><span class="arr">→</span>` : ""}<span class="post">${post}</span></div><div class="ks">${sub || ""}</div></div>`;
  }
  function drawCards() {
    const s = R.res.summary, g = R.res.geo.stats || {}, k = s.primary;
    $("#rCards").innerHTML = [
      card("Franchises", s.pre_franchises, s.post_franchises, `${s.post_franchises - s.pre_franchises} franchises`),
      card("Sites", null, fmt(s.sites), Object.entries(s.operators).map(([o, n]) => `${esc(o)} ${n}`).join(" · ")),
      card("Avg sites / franchise", fmt(s.pre_avg_sites), fmt(s.post_avg_sites), `range ${fmt(s.post_min_sites)}–${fmt(s.post_max_sites)}`),
      card(`${esc(k)} / franchise`, fmt(s.pre_per_fr), fmt(s.post_per_fr), R.agg === "avg" ? "monthly average" : "period total"),
      card(`${esc(k)} total`, fmt(s.totals[k]), fmt(s.projected_totals[k]), `projected <b class="up">${pctTxt(pct(s.projected_totals[k], s.totals[k]))}</b>`),
      card("Coverage", null, g.coverage_pct != null ? g.coverage_pct + "%" : "—", `within ${g.radius_km} km of a site`),
    ].join("");
  }
  function drawProgress() {
    const lv = [["arm", 4, "ASM"], ["bm", 3, "BM"], ["tehsil", 2, "City"], ["district", 1, "District"], ["region", 0, "Region"]];
    const rev = R.res.reviews || {};
    $("#rProgress").innerHTML = lv.map(([key, i, name]) => {
      const units = new Set(R.filters.combos.map((c) => (key === "tehsil" ? `${c[1]} / ${c[2]}` : c[i])).filter(Boolean));
      const done = [...units].filter((u) => (rev[key] || []).includes(u)).length;
      const p = units.size ? (done / units.size) * 100 : 0;
      return `<div class="pl"><span class="pn">${name}</span><span class="pbar wide"><span style="width:${p}%"></span></span><span>${done} / ${units.size} reviewed</span></div>`;
    }).join("");
  }

  // ---------------- controls & run
  async function run() {
    status("Calculating the complete plan…", true);
    const q = query();
    $("#dlKml").href = `/scenario/${SC}/export.kml?${q}`;
    $("#dlKmz").href = `/scenario/${SC}/export.kml?${q}&fmt=kmz`;
    $("#dlPost").href = `/scenario/${SC}/export.kml?${q}&layers=post`;
    $("#dlXlsx").href = `/scenario/${SC}/export.xlsx?${q}`;
    try {
      const res = await fetch(`/api/scenario/${SC}/run/?${q}`).then((x) => x.json());
      if (res.empty) { status("No data for this selection."); return; }
      $("#dlKml").textContent = R.level && R.unit ? `⬇ KML (${LV[R.level][1]} ${R.unit})` : `⬇ KML (${circle()} — complete)`;
      $("#rViewChip").textContent = viewLabel();
      R.res = res;
    } catch (e) { status("Error: " + e.message); return; }
    drawCards(); drawProgress(); drawMaps(); drawOutside(); drawAlign(); drawHier(); drawMerged(); drawConsolidated(); drawMapping();
    status("");
  }
  function drawMonths() {
    $("#rMonths").innerHTML = R.filters.months.slice().reverse().map((m) => `<label class="chip ${R.months.has(m.key) ? "on" : ""}"><input type="checkbox" value="${m.key}" ${R.months.has(m.key) ? "checked" : ""}>${esc(m.label)}</label>`).join("");
  }
  $("#rMonths").addEventListener("change", (e) => {
    e.target.checked ? R.months.add(e.target.value) : R.months.delete(e.target.value);
    if (!R.months.size) R.months.add(e.target.value);
    drawMonths(); syncUrl(); run();
  });
  $("#rAgg").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; R.agg = b.dataset.v; $$("#rAgg button").forEach((x) => x.classList.toggle("on", x === b)); run(); });
  function drawViewSel() {
    const groups = [["arm", "ASM"], ["bm", "BM"], ["tehsil", "City"], ["district", "District"], ["region", "Region"]];
    if ((R.filters.bigs || []).length > 1) groups.push(["big", "Big Region"]);
    const html = [`<option value="">${esc(circle())} — all franchises (complete plan)</option>`];
    groups.forEach(([lvl, name]) => {
      const [i] = LV[lvl], cnt = {};
      R.filters.combos.forEach((c) => { const v = lvl === "tehsil" ? `${c[1]} / ${c[2]}` : c[i]; if (v) cnt[v] = (cnt[v] || 0) + c[5]; });
      html.push(`<optgroup label="${name}">` + Object.keys(cnt).sort().map((v) => `<option value="${lvl}::${esc(v)}">${name}: ${esc(v)} · ${cnt[v]} sites</option>`).join("") + "</optgroup>");
    });
    $("#rView").innerHTML = html.join("");
    $("#rView").value = R.level ? `${R.level}::${R.unit}` : "";
    if ($("#rView").value !== (R.level ? `${R.level}::${R.unit}` : "")) { R.level = ""; R.unit = ""; $("#rView").value = ""; }
  }
  const coTag = () => ({ ufone: " · Ufone only", tp: " · TP only" }[OPS.company()] || "");
  function viewLabel() { return (R.level ? `${LV[R.level][1]}: ${R.unit}` : `${circle()} — all franchises`) + coTag(); }
  function circle() { return (R.filters && R.filters.circle) || "Central B"; }
  $("#rView").addEventListener("change", (e) => {
    const v = e.target.value;
    [R.level, R.unit] = v ? [v.split("::")[0], v.split("::").slice(1).join("::")] : ["", ""];
    syncUrl(); run();
  });
  function syncUrl() {
    const u = new URL(location.href);
    if (R.level) u.searchParams.set("view", `${R.level}::${R.unit}`); else u.searchParams.delete("view");
    u.searchParams.set("months", [...R.months].join("|"));
    u.searchParams.set("company", OPS.company());
    history.replaceState(null, "", u);
  }
  $("#rFind").addEventListener("input", drawMerged);

  { const q0 = new URLSearchParams(location.search).get("company"); if (q0) OPS.setCompany(q0); }
  OPS.control($("#rOps"));
  OPS.companyControl($("#rCompany"));
  document.addEventListener("company-change", () => { syncUrl(); run(); });
  document.addEventListener("ops-change", () => { if (R.res) { R.keepView = true; drawMaps(); } });

  (async () => {
    fetch("/api/layers/").then((x) => x.json()).then(({ layers }) => {
      const ls = layers.filter((l) => l.role === "compare");
      if (!ls.length) return;
      $("#rCmp").innerHTML = `<span class="tg-sep">Compare:</span>` + ls.map((l) => `<label class="small"><input type="checkbox" data-cmp="${l.id}"> ${esc(l.label)}</label>`).join(" ");
      const cache = {};
      $$("#rCmp input").forEach((cb) => cb.addEventListener("change", async () => {
        if (!cache[cb.dataset.cmp]) {
          const gj = await fetch(`/api/layers/${cb.dataset.cmp}/zones/`).then((x) => x.json());
          cache[cb.dataset.cmp] = ["pre", "post"].map((sd) => [sd, L.geoJSON(gj, { style: { color: "#6b2fbf", weight: 2.4, fill: false },
            onEachFeature: (f, l) => l.bindTooltip(`<b>${esc(f.properties.layer)}</b>: ${esc(f.properties.name)}`, { sticky: true }) })]);
        }
        cache[cb.dataset.cmp].forEach(([sd, l]) => (cb.checked ? l.addTo(M[sd].map) : l.remove()));
      }));
    });
    R.filters = await fetch("/api/filters/").then((x) => x.json());
    // open on what was selected in the planner (?view=arm::NAME&months=2026-09|2026-10)
    const qs = new URLSearchParams(location.search);
    const valid = new Set(R.filters.months.map((m) => m.key));
    (qs.get("months") || "").split("|").filter((k) => valid.has(k)).forEach((k) => R.months.add(k));
    if (!R.months.size) R.filters.months.forEach((m) => R.months.add(m.key));
    if (qs.get("view")) { const v = qs.get("view"); R.level = v.split("::")[0]; R.unit = v.split("::").slice(1).join("::"); if (!LV[R.level]) { R.level = ""; R.unit = ""; } }
    if (qs.get("agg") === "sum") { R.agg = "sum"; $$("#rAgg button").forEach((x) => x.classList.toggle("on", x.dataset.v === "sum")); }
    drawViewSel(); drawMonths();
    if (!R.filters.months.length) { status("No data uploaded yet."); return; }
    run();
  })();
})();
