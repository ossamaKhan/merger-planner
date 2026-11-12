/* Shared site symbols: Ufone = circle, TP (Telenor) = triangle, other = square.
   Show Both / Ufone / TP, and colour by franchise or by operator. Remembered per browser. */
window.OPS = (() => {
  "use strict";
  const KEY = "planner:ops";
  const COLOR = { ufone: "#ff7a00", tp: "#00a9e0", other: "#7d8597" };
  const SHAPE = { ufone: "circle", tp: "triangle", other: "square" };
  const NAME = { ufone: "Ufone", tp: "TP", other: "Other" };
  let state = { show: "both", color: "franchise" };
  try { state = { ...state, ...JSON.parse(localStorage.getItem(KEY) || "{}") }; } catch (e) { /* storage unavailable */ }
  const listeners = [];

  function kind(op) {
    const o = String(op || "").toLowerCase().trim();
    if (o.includes("ufone") || o === "uf" || o === "u" || o.startsWith("ptml")) return "ufone";
    if (o.includes("telenor") || o === "tp" || o.startsWith("tp ") || o.includes("tpl") || o === "t") return "tp";
    return "other";
  }

  // canvas drawing for non-circle shapes
  L.Canvas.include({
    _updateShape(layer) {
      if (!this._drawing || layer._empty()) return;
      const p = layer._point, ctx = this._ctx, r = Math.max(Math.round(layer._radius), 1);
      ctx.beginPath();
      if (layer.options.shape === "triangle") {
        const h = r * 1.35;
        ctx.moveTo(p.x, p.y - h);
        ctx.lineTo(p.x + h * 0.95, p.y + h * 0.7);
        ctx.lineTo(p.x - h * 0.95, p.y + h * 0.7);
        ctx.closePath();
      } else {
        ctx.rect(p.x - r, p.y - r, r * 2, r * 2);
      }
      this._fillStroke(ctx, layer);
    },
  });
  const ShapeMarker = L.CircleMarker.extend({
    options: { shape: "circle" },
    _updatePath() {
      if (this.options.shape === "circle" || !this._renderer._updateShape) return L.CircleMarker.prototype._updatePath.call(this);
      this._renderer._updateShape(this);
    },
  });

  function marker(latlng, op, style) {
    const k = kind(op);
    return new ShapeMarker(latlng, { ...style, shape: SHAPE[k] });
  }
  const visible = (op) => state.show === "both" || kind(op) === state.show;
  const fill = (op, franchiseColor) => (state.color === "operator" ? COLOR[kind(op)] : franchiseColor);

  function sym(k, size = 12) {
    const c = COLOR[k];
    if (k === "ufone") return `<svg width="${size}" height="${size}" viewBox="0 0 12 12"><circle cx="6" cy="6" r="5" fill="${c}" stroke="#fff"/></svg>`;
    if (k === "tp") return `<svg width="${size}" height="${size}" viewBox="0 0 12 12"><polygon points="6,0.5 11.5,11 0.5,11" fill="${c}" stroke="#fff"/></svg>`;
    return `<svg width="${size}" height="${size}" viewBox="0 0 12 12"><rect x="1" y="1" width="10" height="10" fill="${c}" stroke="#fff"/></svg>`;
  }

  /* Company filter (Ufone / TP / both). Changing it re-runs the plan: sites, areas and KPIs. */
  function companyControl(el) {
    if (!el) return;
    const draw = () => {
      el.innerHTML = `<span class="ops company"><span class="lbl">Company</span>
        <span class="seg ops-show">
          <button data-v="both" class="${state.show === "both" ? "on" : ""}">${sym("ufone")}${sym("tp")} Both</button>
          <button data-v="ufone" class="${state.show === "ufone" ? "on" : ""}">${sym("ufone")} Ufone</button>
          <button data-v="tp" class="${state.show === "tp" ? "on" : ""}">${sym("tp")} TP</button></span></span>`;
      el.querySelector(".ops-show").onclick = (e) => { const b = e.target.closest("button"); if (!b || b.dataset.v === state.show) return; set({ show: b.dataset.v }, true); };
    };
    draw();
    listeners.push(draw);
  }
  const setCompany = (v) => { if (["both", "ufone", "tp"].includes(v)) { state = { ...state, show: v }; try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) { /* ignore */ } } };
  const company = () => (state.show === "ufone" || state.show === "tp" ? state.show : "both");
  /* Render the control into `el`. opts.noColor hides the colour switch. */
  function control(el, opts = {}) {
    if (!el) return;
    const draw = () => {
      el.innerHTML = `<span class="ops">
        ${opts.companyHere ? `<span class="lbl">Sites</span>
        <span class="seg small-seg ops-show">
          <button data-v="both" class="${state.show === "both" ? "on" : ""}">Both</button>
          <button data-v="ufone" class="${state.show === "ufone" ? "on" : ""}">${sym("ufone")} Ufone</button>
          <button data-v="tp" class="${state.show === "tp" ? "on" : ""}">${sym("tp")} TP</button>
        </span>` : ""}
        ${opts.noColor ? "" : `<span class="seg small-seg ops-color" title="Colour sites by franchise or by operator">
          <button data-v="franchise" class="${state.color === "franchise" ? "on" : ""}">Franchise colours</button>
          <button data-v="operator" class="${state.color === "operator" ? "on" : ""}">Operator colours</button>
        </span>`}
        <span class="ops-count muted small"></span></span>`;
      const sh = el.querySelector(".ops-show");
      if (sh) sh.onclick = (e) => { const b = e.target.closest("button"); if (!b) return; set({ show: b.dataset.v }); };
      const cc = el.querySelector(".ops-color");
      if (cc) cc.onclick = (e) => { const b = e.target.closest("button"); if (!b) return; set({ color: b.dataset.v }); };
    };
    draw();
    listeners.push(draw);
  }
  function set(patch, companyChanged) {
    state = { ...state, ...patch };
    try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) { /* ignore */ }
    listeners.forEach((f) => f());
    document.dispatchEvent(new CustomEvent(companyChanged ? "company-change" : "ops-change", { detail: state }));
  }
  /* Show counts like "412 Ufone · 380 TP" next to the control. */
  function counts(sites) {
    const c = { ufone: 0, tp: 0, other: 0 };
    sites.forEach((s) => { c[kind(s.operator)]++; });
    const txt = ["ufone", "tp", "other"].filter((k) => c[k]).map((k) => `${sym(k, 10)} ${c[k]} ${NAME[k]}`).join(" · ");
    document.querySelectorAll(".ops-count").forEach((x) => { x.innerHTML = txt; });
  }
  return { kind, marker, visible, fill, control, companyControl, company, setCompany, counts, sym, COLOR, NAME, get state() { return state; } };
})();

/* Area / neighbourhood names drawn by the app (report + boundaries maps). The planner has its own
   richer version with add/rename. Usage: AreaNames.attach([mapA, mapB]) — first map drives loading. */
window.AreaNames = (function () {
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  function attach(maps) {
    const groups = maps.map((m) => {
      if (!m.getPane("areas")) { m.createPane("areas"); m.getPane("areas").style.zIndex = 640; m.getPane("areas").style.pointerEvents = "none"; }
      return L.layerGroup().addTo(m);
    });
    let req = 0;
    async function load() {
      const map = maps[0], z = map.getZoom(), b = map.getBounds().pad(0.15), my = ++req;
      let res;
      try { res = await fetch(`/api/areas/?bbox=${b.getSouth()},${b.getWest()},${b.getNorth()},${b.getEast()}&z=${z}`).then((r) => r.json()); } catch (e) { return; }
      if (my !== req) return;
      const placed = [], keep = [];
      res.labels.forEach((a) => {
        const pt = map.latLngToContainerPoint([a.lat, a.lon]);
        const w = a.name.length * 6.4 + 10, h = 15, x0 = pt.x - w / 2, y0 = pt.y - h / 2;
        if (a.t !== "yours" && placed.some((r) => x0 < r[2] && x0 + w > r[0] && y0 < r[3] && y0 + h > r[1])) return;
        placed.push([x0 - 14, y0 - 7, x0 + w + 14, y0 + h + 7]); keep.push(a);
      });
      groups.forEach((g) => {
        g.clearLayers();
        keep.forEach((a) => g.addLayer(L.marker([a.lat, a.lon], { pane: "areas", interactive: false, keyboard: false,
          icon: L.divIcon({ className: "arealbl " + (a.t === "yours" ? "yours" : a.t === "neighbourhood" ? "nb" : "town") + ((a.pop || 0) >= 50000 ? " major" : ""), html: `<span>${esc(a.name)}</span>`, iconSize: null }) })));
      });
    }
    maps[0].on("moveend", load);
    load();
    return { reload: load, groups };
  }
  return { attach };
})();
