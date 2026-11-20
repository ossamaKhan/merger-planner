"""Pre/post planning engine.

Given a scenario (old franchise -> new franchise tags + single-site overrides),
a set of months and a scope (regions / districts) it returns:

* KPI tables for every pre franchise and every post franchise (with member
  breakdown), period average or period total across the selected months;
* projections per post franchise (productivity uplift + month-on-month trend);
* territories for both maps: every site gets a Voronoi cell (the area closer to
  it than to any other site), clipped to the district boundary. The part of the
  cell inside the coverage radius is "covered"; the rest is an uncovered island
  that is filled by the nearest franchise (which is, by construction, the
  franchise owning that cell). Cells are dissolved per franchise;
* enclosed uncovered pockets, territory fragmentation and post-merger enclaves
  (with a suggested absorbing franchise), plus planning warnings.
"""
import hashlib
import math
import re
from collections import OrderedDict, defaultdict

import numpy as np
import pandas as pd
import shapely
from shapely.geometry import MultiPoint, mapping as geo_mapping
from shapely.ops import polylabel
from django.db.models import Q

from planner.models import DataMonth, Franchise, FranchiseMapping, Site, SiteOverride
from planner.services import geo
from planner.services.colors import color_for_code, color_for_index


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


LOWER_IS_BETTER = ("churn",)
# (label, numerator, denominator, multiplier). "__sites" = site count.
DERIVED = [
    ("Rev / Site", "totalrevamt", "__sites", 1),
    ("FCA / Site", "fca", "__sites", 1),
    ("Act 90D / Site", "act90d", "__sites", 1),
    ("Churn % of Act 30D", "grosschurn", "act30d", 100),
    ("4G Share % of Act 90D", "act90d4g", "act90d", 100),
    ("Recharge / Active Recharger", "totalrecharge", "activerechargers", 1),
    ("Data GB / Act 90D", "volumegbs", "act90d", 1),
]
PRIMARY_CANDIDATES = ["totalrevamt", "totalrecharge", "fca", "act90d"]

_CELL_CACHE = OrderedDict()
_CELL_CACHE_MAX = 12


# --------------------------------------------------------------------------- data

def json_safe(o):
    """NaN / inf are not valid JSON (browsers reject them) - turn them into null."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {str(k): json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [json_safe(v) for v in o]
    if isinstance(o, np.generic):
        return json_safe(o.item())
    if o is pd.NaT or (o is not None and not isinstance(o, (str, bool, int)) and pd.isna(o) is True):
        return None
    return o


def latest_per_site(df):
    """One row per site: its most recent month among the selected months.
    (A region missing from the newest month still shows with its latest data.)"""
    if df.empty:
        return df
    return df.sort_values("mkey").drop_duplicates("site_id", keep="last").reset_index(drop=True)


def resolve_months(keys):
    qs = DataMonth.objects.all()
    if keys:
        qs = qs.filter(month__in=[f"{k}-01" for k in keys])
    return list(qs.order_by("month"))


def op_kind(op):
    o = str(op or "").lower()
    if "ufone" in o:
        return "ufone"
    if "telenor" in o or o.strip() in ("tp", "t") or "tpl" in o:
        return "tp"
    return "other"


def load_frame(months, regions=None, districts=None, tehsils=None, bms=None, arms=None, bigs=None, company=None):
    qs = Site.objects.filter(month__in=months)
    if bigs:
        from django.conf import settings as _s
        q = Q(big_region__in=bigs)
        if _s.PLANNER_CIRCLE_NAME in bigs:  # rows uploaded before the Big Region column existed
            q |= Q(big_region="")
        qs = qs.filter(q)
    if regions:
        qs = qs.filter(region__in=regions)
    if districts:
        qs = qs.filter(Q(geo_district__in=districts) | (Q(geo_district="") & Q(district__in=districts)))
    if tehsils:
        qs = qs.filter(geo_tehsil__in=tehsils)
    if bms:
        qs = qs.filter(bm__in=bms)
    if arms:
        qs = qs.filter(arm__in=arms)
    rows = list(
        qs.values(
            "month__month", "site_id", "operator", "big_region", "region", "bu", "city", "district", "geo_district",
            "geo_tehsil", "province", "franchise_code", "bm", "arm", "latitude", "longitude", "site_type",
            "status", "franchise_after_mocan", "kpis",
        )
    )
    if company in ("ufone", "tp"):
        rows = [r for r in rows if op_kind(r["operator"]) == company]
    if not rows:
        return pd.DataFrame(), []
    df = pd.DataFrame(rows)
    kpi = pd.DataFrame(list(df.pop("kpis"))).fillna(0.0)
    kcols = []
    for m in months:
        for c in m.kpi_columns or []:
            if c in kpi.columns and c not in kcols:
                kcols.append(c)
    for c in kpi.columns:
        if c not in kcols:
            kcols.append(c)
    df = pd.concat([df.reset_index(drop=True), kpi[kcols].reset_index(drop=True)], axis=1)
    # blank, zero or out-of-Pakistan coordinates are treated as missing (kept in KPIs, left off maps)
    lat = pd.to_numeric(df["latitude"], errors="coerce")
    lon = pd.to_numeric(df["longitude"], errors="coerce")
    bad = ~(lat.between(20, 40) & lon.between(58, 80))
    df["latitude"] = lat.mask(bad)
    df["longitude"] = lon.mask(bad)
    from django.conf import settings as _s
    df["big_region"] = df["big_region"].replace("", _s.PLANNER_CIRCLE_NAME).fillna(_s.PLANNER_CIRCLE_NAME)
    df["mkey"] = df["month__month"].map(lambda d: d.strftime("%Y-%m"))
    df["dist"] = df["geo_district"].where(df["geo_district"] != "", df["district"]).replace("", "Unknown")
    df["tehsil"] = df["geo_tehsil"].replace("", "Unknown")
    return df, kcols


def mapping_for(scenario):
    mp = dict(FranchiseMapping.objects.filter(scenario=scenario).values_list("source_code", "target_code"))
    ov = dict(SiteOverride.objects.filter(scenario=scenario).values_list("site_id", "target_code"))
    return mp, ov


def resolve_chain(code, mp):
    """A->B and B->C means A ends up in C. Cycle-safe."""
    seen = {code}
    cur = code
    while cur in mp and mp[cur] != cur:
        cur = mp[cur]
        if cur in seen:
            break
        seen.add(cur)
    return cur


def apply_mapping(df, mp, ov):
    final = {c: resolve_chain(c, mp) for c in df["franchise_code"].unique()}
    post = df["franchise_code"].map(final)
    if ov:
        # a site moved into franchise B follows B when B is later merged into C
        ov_final = {sid: resolve_chain(t, mp) for sid, t in ov.items()}
        o = df["site_id"].map(ov_final)
        post = o.where(o.notna(), post)
    df["post"] = post
    df["overridden"] = df["site_id"].isin(ov.keys()) if ov else False
    return df


# --------------------------------------------------------------------------- KPIs

def _kpi_meta(kcols):
    meta = []
    for c in kcols:
        n = _norm(c)
        meta.append({"key": c, "label": c.rstrip(". "), "lower_better": any(t in n for t in LOWER_IS_BETTER)})
    norm_map = {_norm(c): c for c in kcols}
    derived = []
    for label, num, den, mult in DERIVED:
        if num in norm_map and (den == "__sites" or den in norm_map):
            derived.append({"label": label, "num": norm_map[num], "den": "__sites" if den == "__sites" else norm_map[den],
                            "mult": mult, "lower_better": "churn" in label.lower()})
    primary = next((norm_map[p] for p in PRIMARY_CANDIDATES if p in norm_map), kcols[0] if kcols else None)
    return meta, derived, primary


def _derive(values, sites, derived):
    out = {}
    for d in derived:
        den = sites if d["den"] == "__sites" else values.get(d["den"], 0)
        out[d["label"]] = (values.get(d["num"], 0) / den * d["mult"]) if den else None
    return out


def _group(df, by, kcols, n_months, agg):
    div = n_months if agg == "avg" else 1
    g = df.groupby(by)
    vals = g[kcols].sum() / div
    sites = g.size() / n_months  # sites always as monthly average
    return vals, sites


def _cv(arr):
    arr = np.asarray([a for a in arr if a is not None], dtype=float)
    if len(arr) < 2 or arr.mean() == 0:
        return None
    return float(arr.std() / arr.mean())


def _post_series(monthly, months, kcols, codes):
    """{code: {kpi: [value per month]}} from a (post, mkey) x kpi table, without a lookup per franchise."""
    full = monthly.reindex(pd.MultiIndex.from_product([list(codes), months], names=monthly.index.names)).fillna(0.0).round(2)
    arr = full[kcols].to_numpy().reshape(len(codes), len(months), len(kcols))
    return {code: {k: arr[i, :, j].tolist() for j, k in enumerate(kcols)} for i, code in enumerate(codes)}


def compute_kpis(df, kcols, scenario, agg="avg", palette=None):
    palette = palette or {}
    months = sorted(df["mkey"].unique())
    n_months = len(months)
    meta, derived, primary = _kpi_meta(kcols)
    lower = {m["key"] for m in meta if m["lower_better"]}

    pre_vals, pre_sites = _group(df, "franchise_code", kcols, n_months, agg)
    post_vals, post_sites = _group(df, "post", kcols, n_months, agg)
    mem_vals, mem_sites = _group(df, ["post", "franchise_code"], kcols, n_months, agg)

    scope_rate = {k: (df[k].sum() / len(df)) * (1 if agg == "avg" else n_months) for k in kcols} if len(df) else {}
    realization = max(0.0, min(100.0, scenario.realization_pct)) / 100.0
    method = scenario.projection_method

    # month-on-month trend (linear fit on monthly totals -> next month)
    monthly = df.groupby(["post", "mkey"])[kcols].sum()
    trend_next = {}
    if n_months >= 2:
        x = np.arange(n_months, dtype=float)
        xm = x.mean()
        for code in post_vals.index:
            sub = monthly.loc[code].reindex(months).fillna(0.0)
            y = sub.values  # months x kpis
            slope = ((x - xm)[:, None] * (y - y.mean(axis=0))).sum(axis=0) / ((x - xm) ** 2).sum()
            nxt = y.mean(axis=0) + slope * (n_months - xm)
            trend_next[code] = {k: float(max(0.0, v)) for k, v in zip(kcols, nxt)}

    fr_info = {f.code: f for f in Franchise.objects.all()}

    def color(code):
        return palette.get(code) or color_for_code(code)

    def name(code):
        f = fr_info.get(code)
        return f.name if f and f.name else code

    lat_all = latest_per_site(df)

    # descriptors per franchise — computed once per column with groupby (was a filter per franchise)
    desc_cache = {}

    def _desc_table(col):
        if col not in desc_cache:
            g = df.groupby(col)
            uniq = {f: g[f].unique() for f in ("dist", "region", "bm", "arm")}
            ops = lat_all.groupby([col, "operator"]).size()
            opd = defaultdict(dict)
            for (c, o), n in ops.items():
                opd[c][o] = int(n)
            desc_cache[col] = (uniq, opd)
        return desc_cache[col]

    def describe(col, code):
        uniq, opd = _desc_table(col)
        return {
            "operators": opd.get(code, {}),
            "districts": sorted(uniq["dist"].get(code, []).tolist()) if code in uniq["dist"] else [],
            "regions": sorted(uniq["region"][code].tolist()) if code in uniq["region"] else [],
            "bms": sorted([b for b in uniq["bm"][code].tolist() if b]) if code in uniq["bm"] else [],
            "arms": sorted([a for a in uniq["arm"][code].tolist() if a]) if code in uniq["arm"] else [],
        }

    pre_rows = []
    latest_df = lat_all
    pre_to_post = defaultdict(dict)
    for (a, b), cnt in latest_df.groupby(["franchise_code", "post"]).size().items():
        pre_to_post[a][b] = int(cnt)
    pre_d = pre_vals[kcols].astype(float).to_dict("index")
    pre_s = pre_sites.astype(float).to_dict()
    for code in pre_vals.index:
        vals = pre_d[code]
        s = pre_s[code]
        pre_rows.append({
            "code": code, "name": name(code), "color": color(code), "sites": s, "kpis": vals,
            "derived": _derive(vals, s, derived),
            "mapped_to": pre_to_post.get(code, {}),
            **describe("franchise_code", code),
        })

    post_rows = []
    post_d = post_vals[kcols].astype(float).to_dict("index")
    post_s = post_sites.astype(float).to_dict()
    mem_by = defaultdict(list)
    msd = mem_sites.astype(float).to_dict()
    for (pc, mc), mv in mem_vals[kcols].astype(float).to_dict("index").items():
        mem_by[pc].append((mc, mv, msd[(pc, mc)]))
    pre_codes = set(df["franchise_code"])
    for code in post_vals.index:
        vals = post_d[code]
        n = post_s[code]
        members = []
        for mcode, mv, ms in mem_by[code]:
            members.append({"code": mcode, "name": name(mcode), "color": color(mcode), "sites": ms,
                            "kpis": mv, "derived": _derive(mv, ms, derived)})
        members.sort(key=lambda m: -m["sites"])
        projected = {}
        for k in kcols:
            base = vals[k]
            if method == "none" or k in lower or n == 0:
                projected[k] = base
                continue
            if method == "best":
                elig = [m for m in members if m["sites"] >= max(1.0, 0.1 * n)] or members
                target_rate = max((m["kpis"][k] / m["sites"]) for m in elig if m["sites"]) if elig else 0
            else:
                target_rate = scope_rate.get(k, 0)
            projected[k] = base + realization * max(0.0, target_rate * n - base)
        post_rows.append({
            "code": code, "name": name(code), "color": color(code), "sites": n, "kpis": vals,
            "derived": _derive(vals, n, derived), "projected": projected,
            "projected_derived": _derive(projected, n, derived),
            "trend_next": trend_next.get(code, {}), "members": members,
            "is_new": code not in pre_codes,
            **describe("post", code),
        })
    post_rows.sort(key=lambda r: -r["sites"])
    pre_rows.sort(key=lambda r: -r["sites"])

    # monthly series for charts
    tot = df.groupby("mkey")[kcols].sum().reindex(months).fillna(0.0)
    sites_m = df.groupby("mkey").size().reindex(months).fillna(0)
    series = {
        "months": months,
        "total": {k: tot[k].round(2).tolist() for k in kcols},
        "sites": sites_m.astype(int).tolist(),
        "post": _post_series(monthly, months, kcols, post_vals.index),
    }

    totals = {k: float(pre_vals[k].sum()) for k in kcols}
    proj_totals = {k: float(sum(r["projected"][k] for r in post_rows)) for k in kcols}
    total_sites = float(pre_sites.sum())
    psites = [r["sites"] for r in post_rows]
    summary = {
        "n_months": n_months,
        "pre_franchises": len(pre_rows),
        "post_franchises": len(post_rows),
        "sites": total_sites,
        "pre_avg_sites": total_sites / max(1, len(pre_rows)),
        "post_avg_sites": total_sites / max(1, len(post_rows)),
        "post_min_sites": min(psites) if psites else 0,
        "post_max_sites": max(psites) if psites else 0,
        "totals": totals,
        "projected_totals": proj_totals,
        "derived_totals": _derive(totals, total_sites, derived),
        "projected_derived_totals": _derive(proj_totals, total_sites, derived),
        "primary": primary,
        "pre_cv": _cv([r["kpis"][primary] for r in pre_rows]) if primary else None,
        "post_cv": _cv([r["kpis"][primary] for r in post_rows]) if primary else None,
        "pre_per_fr": totals.get(primary, 0) / max(1, len(pre_rows)) if primary else None,
        "post_per_fr": totals.get(primary, 0) / max(1, len(post_rows)) if primary else None,
        "operators": lat_all["operator"].value_counts().to_dict(),
    }
    meta_out = {"kpis": meta, "derived": derived, "primary": primary, "months": months, "agg": agg}
    return meta_out, summary, pre_rows, post_rows, series


# --------------------------------------------------------------------------- geometry

def _jitter(x, y, eps=0.003):
    """Spread co-located sites (e.g. MOCAN TP + Ufone on one tower) on a tiny ring
    so each gets its own wedge of the shared Voronoi cell."""
    key = np.round(x, 4) * 1e6 + np.round(y, 4)
    order = pd.Series(key).groupby(key).cumcount().values
    counts = pd.Series(key).map(pd.Series(key).value_counts()).values
    ang = 2 * np.pi * order / np.maximum(counts, 1)
    dup = counts > 1
    x = x + np.where(dup, eps * np.cos(ang), 0)
    y = y + np.where(dup, eps * np.sin(ang), 0)
    return x, y


def _cells_key(sites, radius_km, by_district, clip):
    return hashlib.md5(
        (",".join(sites["site_id"]) + "|" + ",".join(sites["dist"]) + f"|{radius_km}|{by_district}|{clip}|"
         + f"{sites['latitude'].sum():.6f}{sites['longitude'].sum():.6f}").encode()
    ).hexdigest()


# territory of one franchise = union of its sites' cells. Remembered per (cell set, site indices), so after
# a tag only the franchises whose sites changed are rebuilt.
_TERR_CACHE = OrderedDict()
_TERR_CACHE_MAX = 3000


def _cached_geom(ckey, kind, idx, cells, proj, tol):
    """Outline (GeoJSON geometry) of a group of site cells, remembered between runs."""
    k = (ckey, kind, tol, idx.tobytes())
    hit = _TERR_CACHE.get(k)
    if hit is None:
        f = _to_feature(_dissolve(shapely.union_all(cells[idx])), proj, {}, tol=tol)
        hit = {"geom": f["geometry"] if f else None}
        _TERR_CACHE[k] = hit
        if len(_TERR_CACHE) > _TERR_CACHE_MAX:
            _TERR_CACHE.popitem(last=False)
    else:
        _TERR_CACHE.move_to_end(k)
    return hit["geom"]


def _cells(sites, radius_km, by_district, clip="district"):
    """Return (cells, gaps, areas_union, proj) aligned to `sites` rows."""
    key = _cells_key(sites, radius_km, by_district, clip)
    if key in _CELL_CACHE:
        _CELL_CACHE.move_to_end(key)
        return _CELL_CACHE[key]

    proj = geo.LocalProjection(float(sites["latitude"].mean()))
    x = sites["longitude"].values * proj.kx
    y = sites["latitude"].values * proj.ky
    x, y = _jitter(x, y)
    pts = shapely.points(np.column_stack([x, y]))
    n = len(sites)
    cells = np.empty(n, dtype=object)
    groups = sites.groupby("dist").indices if by_district else {"__all__": np.arange(n)}
    dgeoms = {k: proj.to_km(g) for k, g in geo.district_geoms(list(sites["dist"].unique())).items()}
    pad = max(3 * radius_km, 2.0)
    areas = []
    all_area = shapely.union_all(list(dgeoms.values())) if (not by_district and dgeoms) else None

    for gkey, idx in groups.items():
        gpts = pts[idx]
        hull_area = shapely.convex_hull(shapely.multipoints(gpts)).buffer(pad)
        area = dgeoms.get(gkey) if by_district else all_area
        if area is None or area.is_empty:
            area = hull_area
        elif clip == "hull":  # only the part of the district around its sites (skip deserts / rivers)
            area = shapely.intersection(area, hull_area)
        # sites lying outside the polygon (data or boundary error) must still own their surroundings
        outside = ~shapely.contains(area, gpts)
        if outside.any():
            area = shapely.union(area, shapely.union_all(shapely.buffer(gpts[outside], radius_km)))
        areas.append(area)
        if len(idx) == 1:
            cells[idx[0]] = area
            continue
        vor = shapely.voronoi_polygons(MultiPoint(list(gpts)), extend_to=area)
        polys = np.array(list(vor.geoms), dtype=object)
        polys = shapely.intersection(polys, area)
        tree = shapely.STRtree(gpts)
        pi, ci = tree.query(polys, predicate="contains")  # polygon i contains point j
        for p, c in zip(pi, ci):
            if cells[idx[c]] is None:
                cells[idx[c]] = polys[p]
        for j in idx:  # safety net
            if cells[j] is None:
                cells[j] = shapely.buffer(pts[j], 0.01)

    area_union = shapely.union_all(areas)
    # Islands between covered districts (a district with no sites surrounded by districts
    # that have sites, or slivers between boundary polygons) are holes in the covered area.
    # Fill them with the nearest site's franchise.
    filled_shape = _fill_holes(area_union)
    filler = shapely.difference(filled_shape, area_union)
    if not filler.is_empty and filler.area > 0.01 and n > 1:
        vor = shapely.voronoi_polygons(MultiPoint(list(pts)), extend_to=filled_shape)
        polys = np.array(list(vor.geoms), dtype=object)
        pieces = shapely.intersection(polys, filler)
        pi, ci = shapely.STRtree(pts).query(polys, predicate="contains")
        for p_, c_ in zip(pi, ci):
            if not pieces[p_].is_empty:
                cells[c_] = shapely.union(cells[c_], pieces[p_])
        area_union = filled_shape

    cells = shapely.make_valid(cells)
    circles = shapely.buffer(pts, radius_km, quad_segs=12)
    gaps = shapely.difference(cells, circles)
    res = (cells, gaps, area_union, proj)
    _CELL_CACHE[key] = res
    if len(_CELL_CACHE) > _CELL_CACHE_MAX:
        _CELL_CACHE.popitem(last=False)
    return res


def _fill_holes(geom):
    """Same shape with every interior hole filled in (exterior rings only)."""
    from shapely.geometry import Polygon
    parts = [Polygon(p.exterior) for p in _parts(geom)]
    return shapely.union_all(parts) if parts else geom


UNIT_LEVELS = [("big", "big_region"), ("region", "region"), ("bm", "bm"), ("arm", "arm"), ("district", "dist"), ("tehsil", "_tehkey")]


def _dissolve(geom):
    """Union with a 10 m closing so touching pieces of one territory become one polygon
    (removes hairline seams left by district boundaries)."""
    if geom.is_empty:
        return geom
    return shapely.make_valid(geom.buffer(0.01, quad_segs=2).buffer(-0.01, quad_segs=2))


def _fc(features):
    return {"type": "FeatureCollection", "features": features}


def _to_feature(geom_km, proj, props, tol=0.02):
    g = geom_km.simplify(tol, preserve_topology=True) if tol else geom_km
    g = shapely.transform(proj.to_deg(g), lambda c: np.round(c, 5))
    if g.is_empty:
        return None
    return {"type": "Feature", "properties": props, "geometry": geo_mapping(g)}


def _parts(geom):
    if geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    return [g for g in getattr(geom, "geoms", []) if g.geom_type == "Polygon"]


RD_NONE = "Not in RD file"


def rd_lookup():
    """site_id (upper) -> Final Franchise from the RD file, or None when no RD file is loaded."""
    from ..models import RDTag
    rows = RDTag.objects.values_list("site_id", "final_franchise")
    m = {sid.strip().upper(): (fin or "").strip() for sid, fin in rows}
    return m or None


def rd_prev_lookup():
    """site_id (upper) -> (previous ex-TP franchise, previous ex-Ufone franchise) from the RD file."""
    from ..models import RDTag
    bad = ("", "-", "NAN", "NONE", "#N/A", "0")
    clean = lambda v: "" if str(v or "").strip().upper() in bad else str(v).strip()  # noqa: E731
    return {sid.strip().upper(): (clean(tp), clean(uf))
            for sid, tp, uf in RDTag.objects.values_list("site_id", "prev_tp", "prev_ufone")}


def rebase_on_rd(df, rd, prev=None):
    """RD-based plan.
    * franchise_code (PRE of the plan) = RD Final Franchise ("RDs proposed"); sites not in the RD file keep theirs.
    * orig_code (the separate PRE map) = PREVIOUSLY TAG FRANCHISE from the RD file: if only one of ex-TP /
      ex-Ufone is given, that one; if both, the one matching the site's operator (Telenor -> ex-TP,
      Ufone -> ex-Ufone). Falls back to the franchise in the site file when the RD file has neither."""
    if df is None or df.empty:
        return df
    df = df.copy()
    df["orig_code"] = df["franchise_code"]
    if prev:
        ids0 = df["site_id"].astype(str).str.strip().str.upper()

        def pick(sid, op, cur):
            tp, uf = prev.get(sid, ("", ""))
            if tp and uf:
                return tp if op_kind(op) == "tp" else uf if op_kind(op) == "ufone" else tp
            return tp or uf or cur
        df["orig_code"] = [pick(s, o, c) for s, o, c in zip(ids0, df["operator"], df["franchise_code"])]
    if rd:
        ids = df["site_id"].astype(str).str.strip().str.upper()
        fin = ids.map(rd)
        has = fin.notna() & (fin.astype(str).str.len() > 0)
        df.loc[has, "franchise_code"] = fin[has]
        df["in_rd"] = has
    else:
        df["in_rd"] = False
    return df


def attach_rd(df, rd):
    if rd is None or df is None or df.empty:
        return df
    df = df.copy()
    df["rd_post"] = [rd.get(str(x).strip().upper()) or RD_NONE for x in df["site_id"]]
    return df


def compute_geometry(latest, radius_km, by_district, color_of, clip="district", focus_ids=None, pieces=False):
    """`latest` holds ALL sites of the month; Voronoi cells are built from all of them, so
    territories never overlap whatever filter is applied. `focus_ids` are the sites in the
    current view: only their cells are coloured; the neighbouring cells come back as grey
    "context" so the planner can see what lies outside the view."""
    sites = latest.dropna(subset=["latitude", "longitude"]).reset_index(drop=True)
    fmask = np.ones(len(sites), dtype=bool) if focus_ids is None else sites["site_id"].isin(focus_ids).values
    if sites.empty or not fmask.any():
        empty = _fc([])
        return {"pre": empty, "post": empty, "fill_pre": empty, "fill_post": empty, "islands": empty,
                "districts_in_scope": [], "stats": {}}, [], {}
    cells, gaps, area_union, proj = _cells(sites, radius_km, by_district, clip)
    ckey = _cells_key(sites, radius_km, by_district, clip)
    pts_km = shapely.points(np.column_stack([sites["longitude"].values * proj.kx, sites["latitude"].values * proj.ky]))

    fpos = np.where(fmask)[0]
    fsites = sites.iloc[fpos]
    if focus_ids is not None:
        area_union = shapely.union_all(cells[fpos])
    out = {}
    terr_geoms = {}
    frag_counts = {}
    sides = [("pre", "franchise_code"), ("post", "post")]
    if "rd_post" in fsites.columns:
        sides.append(("rd", "rd_post"))  # POST (from RDs): Final Franchise from the RD file
    for side, col in sides:
        feats, fills = [], []
        terr_geoms[side] = {}
        frag = 0
        for code, rel in fsites.groupby(col).indices.items():
            idx = fpos[rel]
            tkey = (ckey, idx.tobytes())
            hit = _TERR_CACHE.get(tkey)
            if hit is None:
                terr = _dissolve(shapely.union_all(cells[idx]))
                gap = shapely.union_all(gaps[idx])
                parts = [p for p in _parts(terr) if p.area > 0.05]
                lx = ly = None
                if parts:
                    big = max(parts, key=lambda q: q.area)
                    try:
                        lp = polylabel(big, tolerance=0.05)
                    except Exception:
                        lp = big.representative_point()
                    lx, ly = round(lp.x / proj.kx, 5), round(lp.y / proj.ky, 5)
                tf = _to_feature(terr, proj, {})
                gf = None if gap.is_empty else _to_feature(gap, proj, {})
                hit = {"terr": terr, "gap_area": gap.area, "n_parts": len(parts), "lx": lx, "ly": ly,
                       "tgeom": tf["geometry"] if tf else None, "ggeom": gf["geometry"] if gf else None}
                _TERR_CACHE[tkey] = hit
                if len(_TERR_CACHE) > _TERR_CACHE_MAX:
                    _TERR_CACHE.popitem(last=False)
            else:
                _TERR_CACHE.move_to_end(tkey)
            terr = hit["terr"]
            terr_geoms[side][code] = (terr, idx)
            frag += max(0, hit["n_parts"] - 1)
            props = {
                "code": code, "color": "#9aa3b2" if code == RD_NONE else color_of(code), "sites": int(len(idx)),
                "area_km2": round(terr.area, 2), "filled_km2": round(hit["gap_area"], 2),
                "covered_km2": round(terr.area - hit["gap_area"], 2), "fragments": hit["n_parts"],
                "lx": hit["lx"], "ly": hit["ly"],
            }
            if hit["tgeom"]:
                feats.append({"type": "Feature", "properties": props, "geometry": hit["tgeom"]})
            if hit["ggeom"]:
                fills.append({"type": "Feature", "properties": {"code": code, "color": color_of(code), "km2": round(hit["gap_area"], 2)},
                              "geometry": hit["ggeom"]})
        out[side] = _fc(feats)
        out["fill_" + side] = _fc(fills)
        frag_counts[side] = frag

    # uncovered pockets fully enclosed by coverage = true "islands"
    gap_all = shapely.union_all(gaps[fpos])
    outer = area_union.boundary.buffer(0.05)
    isl_feats = []
    enclosed_km2 = 0.0
    for p in _parts(gap_all):
        if p.area < 0.01 or p.intersects(outer):
            continue
        near = fpos[int(shapely.STRtree(pts_km[fpos]).nearest(p.representative_point()))]
        enclosed_km2 += p.area
        f = _to_feature(p, proj, {"km2": round(p.area, 3), "filled_by": str(sites.loc[int(near), "post"]),
                                  "nearest_site": str(sites.loc[int(near), "site_id"])}, tol=0.01)
        if f:
            isl_feats.append(f)
    out["islands"] = _fc(isl_feats)

    # enclaves in the post plan: detached pieces of a post franchise's territory
    enclaves = []
    post_terr = terr_geoms["post"]
    codes = list(post_terr.keys())
    tgeoms = [post_terr[c][0] for c in codes]
    ttree = shapely.STRtree(tgeoms)
    tbuf = {}  # neighbour territory widened by 20 m, built once per neighbour (was once per pair)

    def widened(j):
        if j not in tbuf:
            tbuf[j] = tgeoms[j].buffer(0.02)
        return tbuf[j]
    for code, (terr, idx) in post_terr.items():
        parts = sorted([p for p in _parts(terr) if p.area > 0.05], key=lambda p: -p.area)
        if len(parts) < 2:
            continue
        fr_pts = pts_km[idx]
        for part in parts[1:]:
            inside = idx[shapely.contains(part, fr_pts) | shapely.touches(part, fr_pts)]
            if len(inside) == 0:
                continue
            best, best_len = None, 0.0
            ring = part.boundary
            for j in ttree.query(part.buffer(0.05)):
                other = codes[j]
                if other == code:
                    continue
                shared = ring.intersection(widened(j)).length
                if shared > best_len:
                    best, best_len = other, shared
            enclaves.append({
                "franchise": code, "sites": sites.loc[inside, "site_id"].tolist(), "n_sites": int(len(inside)),
                "km2": round(part.area, 2), "district": sites.loc[inside[0], "dist"],
                "suggest": best, "shared_border_pct": round(100 * best_len / ring.length, 0) if ring.length else 0,
            })
    enclaves.sort(key=lambda e: (e["n_sites"], -e["shared_border_pct"]))

    total = area_union.area
    filled = float(sum(g.area for g in gaps[fpos]))
    out["stats"] = {
        "planning_area_km2": round(total, 1),
        "covered_km2": round(total - filled, 1),
        "filled_km2": round(filled, 1),
        "coverage_pct": round(100 * (total - filled) / total, 1) if total else None,
        "enclosed_islands": len(isl_feats),
        "enclosed_km2": round(enclosed_km2, 2),
        "pre_fragments": frag_counts.get("pre", 0),
        "post_fragments": frag_counts.get("post", 0),
        "radius_km": radius_km,
        "by_district": by_district,
        "clip": clip,
    }
    out["districts_in_scope"] = sorted(fsites["dist"].unique().tolist())

    # hierarchy boundaries of the view: Region, BM, ASM, District, City (union of their site cells)
    sites["_tehkey"] = sites["dist"] + " / " + sites["tehsil"]
    fsites = sites.iloc[fpos]
    out["units"] = {}
    for level, col in UNIT_LEVELS:
        feats = []
        for val, rel in fsites.groupby(col).indices.items():
            if not val:
                continue
            idx = fpos[rel]
            sub = sites.iloc[idx]
            geom = _cached_geom(ckey, "u", idx, cells, proj, 0.05)
            if geom:
                feats.append({"type": "Feature", "geometry": geom, "properties": {
                    "level": level, "name": val, "sites": int(len(idx)),
                    "pre": sorted(sub["franchise_code"].unique().tolist()), "post": sorted(sub["post"].unique().tolist()),
                    "region": sub["region"].mode().iat[0] if len(sub) else "", "bm": sub["bm"].mode().iat[0] if len(sub) else "",
                }})
        out["units"][level] = _fc(feats)

    # planned-franchise piece inside each ASM (for the KML hierarchy)
    if pieces:
        feats = []
        for (arm, code), rel in fsites.groupby(["arm", "post"]).indices.items():
            idx = fpos[rel]
            sub = sites.iloc[idx]
            geom = _cached_geom(ckey, "p", idx, cells, proj, 0.03)
            if geom:
                feats.append({"type": "Feature", "geometry": geom, "properties": {
                    "arm": arm, "code": code, "color": color_of(code), "sites": int(len(idx)),
                    "bm": sub["bm"].mode().iat[0], "region": sub["region"].mode().iat[0],
                    "pre": sorted(sub["franchise_code"].unique().tolist()), "site_ids": sub["site_id"].tolist(),
                }})
        out["arm_pieces"] = _fc(feats)

    # what lies around the view (grey on the maps): neighbouring cells grouped by planned franchise
    out["context"] = _fc([])
    out["context_summary"] = []
    if focus_ids is not None and len(fpos) < len(sites):
        view = shapely.union_all(cells[fpos])
        near = view.buffer(15.0)  # 15 km ring around the view
        cand = np.where(~fmask)[0]
        hit = cand[shapely.intersects(cells[cand], near)]
        if len(hit):
            ctx = sites.iloc[hit]
            # one set per map: the grey area is grouped by THAT map's franchise (PRE / POST / third map),
            # and says whether the franchise is also inside the view (then it is drawn in its colour)
            for side, col in sides:
                in_view = set(fsites[col].unique())
                feats, summ = [], []
                for code, rel in ctx.groupby(col).indices.items():
                    idx = hit[rel]
                    sub = sites.iloc[idx]
                    arms = sub["arm"].value_counts().index.tolist()
                    bms = sub["bm"].value_counts().index.tolist()
                    tehs = sub["_tehkey"].value_counts().index.tolist()
                    props = {"code": code, "sites": int(len(idx)), "arms": arms[:4], "bms": bms[:3], "tehsils": tehs[:4],
                             "region": sub["region"].mode().iat[0], "color": "#9aa3b2" if code == RD_NONE else color_of(code),
                             "in_view": code in in_view}
                    geom = _cached_geom(ckey, "c", idx, cells, proj, 0.05)
                    if geom:
                        feats.append({"type": "Feature", "properties": props, "geometry": geom})
                    summ.append(props)
                key = "context" if side == "post" else "context_" + side
                out[key] = _fc(feats)
                if side == "post":
                    out["context_summary"] = sorted(summ, key=lambda x: -x["sites"])
            has_rd = "rd_post" in ctx.columns
            out["context_sites"] = [[r.site_id, round(r.latitude, 5), round(r.longitude, 5), r.post, r.arm, r.bm, r.operator,
                                     r.franchise_code, r.rd_post if has_rd else ""]
                                    for r in ctx.itertuples()]
    return out, enclaves, terr_geoms


# --------------------------------------------------------------------------- warnings

def build_warnings(df, post_rows, latest):
    w = []
    months = sorted(df["mkey"].unique())
    if len(months) > 1:
        have = df.groupby("region")["mkey"].unique()
        for reg, ms in have.items():
            miss = [m for m in months if m not in set(ms)]
            if miss:
                w.append({"level": "warn", "text": f"{reg or 'Unknown region'} has no data for {', '.join(miss)} — its monthly average counts those months as zero. Upload that region's file for those months, or untick them."})
    if len(post_rows) >= 3:
        med = float(np.median([r["sites"] for r in post_rows]))
        for r in post_rows:
            if r["sites"] > 1.75 * med:
                w.append({"level": "warn", "text": f"{r['code']} would hold {r['sites']:.0f} sites — {r['sites']/med:.1f}× the median post franchise ({med:.0f})."})
            elif r["sites"] < 0.4 * med:
                w.append({"level": "info", "text": f"{r['code']} would hold only {r['sites']:.0f} sites (median {med:.0f}) — candidate for further merging."})
    for r in post_rows:
        if len(r["regions"]) > 1:
            w.append({"level": "warn", "text": f"{r['code']} spans regions: {', '.join(r['regions'])}."})
        if len(r["districts"]) > 3:
            w.append({"level": "info", "text": f"{r['code']} spans {len(r['districts'])} districts."})
    nocoord = int(latest[["latitude", "longitude"]].isna().any(axis=1).sum())
    if nocoord:
        w.append({"level": "warn", "text": f"{nocoord} site(s) have no coordinates and are left off the maps."})
    mism = latest[(latest["geo_district"] != "") & (latest["district"] != "")]
    mism = mism[mism["geo_district"].map(_norm) != mism["district"].map(_norm)]
    if len(mism):
        top = mism.groupby(["district", "geo_district"]).size().sort_values(ascending=False).head(5)
        txt = "; ".join(f"{a}→{b} ({n})" for (a, b), n in top.items())
        w.append({"level": "info", "text": f"{len(mism)} site(s) fall in a different boundary district than the file says. Top: {txt}"})
    return w


def scope_palette(df):
    """Colours assigned per view so every franchise on screen is clearly distinct.

    Current franchises take the first slots (alphabetical), new codes follow, so
    adding a new code during planning never recolours existing franchises.
    """
    pre = sorted(df["franchise_code"].unique())
    new = sorted(set(df["post"].unique()) - set(pre))
    return {c: color_for_index(i) for i, c in enumerate(pre + new)}


def align_projections(post_rows, summary, meta, global_post, kcols):
    """Scale each view's baseline by the franchise's Central B projected/baseline ratio.
    Then region (BM, ASM…) projections sum exactly to the Central B projection."""
    g = {r["code"]: r for r in global_post}
    derived = meta["derived"]
    for r in post_rows:
        gr = g.get(r["code"])
        if not gr:
            continue
        for k in kcols:
            gb, gp = gr["kpis"].get(k) or 0.0, gr["projected"].get(k) or 0.0
            r["projected"][k] = r["kpis"][k] * (gp / gb) if gb else r["kpis"][k]
        r["projected_derived"] = _derive(r["projected"], r["sites"], derived)
    summary["projected_totals"] = {k: float(sum(r["projected"][k] for r in post_rows)) for k in kcols}
    summary["projected_derived_totals"] = _derive(summary["projected_totals"], summary["sites"], derived)


# --------------------------------------------------------------------------- entry point


def find_strays(view, everything, k=8, max_same=1, min_share=0.75):
    """Stray sites ("islands"): a site whose nearest neighbours almost all belong to OTHER planned
    franchises, i.e. it sits inside someone else's area. Neighbours come from ALL sites (not just
    the filtered view), so a site at the edge of an ASM is judged on the real picture.
    Returns one row per stray site in `view` with a suggested franchise to tag it to."""
    from .nn import NearTree as cKDTree
    import numpy as np
    allp = everything.dropna(subset=["latitude", "longitude"])
    vp = view.dropna(subset=["latitude", "longitude"])
    if len(allp) < k + 1 or vp.empty:
        return []
    lat0 = float(allp["latitude"].mean())
    cx = 111.32 * np.cos(np.radians(lat0))
    xy = np.c_[allp["longitude"].to_numpy() * cx, allp["latitude"].to_numpy() * 110.57]
    tree = cKDTree(xy)
    posts = allp["post"].to_numpy()
    kinds = allp["operator"].map(op_kind).to_numpy()
    # Until a Ufone and a TP franchise are merged they are parallel networks on the same ground, so a
    # site is only compared with its own operator's sites plus franchises that already mix both.
    mixed = {c for c, g in allp.groupby("post")["operator"] if len({op_kind(o) for o in g}) > 1}
    ids_all = allp["site_id"].to_numpy()
    q = np.c_[vp["longitude"].to_numpy() * cx, vp["latitude"].to_numpy() * 110.57]
    kk = min(len(allp), k * 5 + 1)
    dist, idx = tree.query(q, k=kk)
    out = []
    for i, row in enumerate(vp.itertuples(index=False)):
        mine = op_kind(row.operator)
        keep = [(dd, j) for dd, j in zip(dist[i], idx[i])
                if ids_all[j] != row.site_id and (kinds[j] == mine or posts[j] in mixed or row.post in mixed)][:k]
        if len(keep) < k:
            continue
        d = np.array([x[0] for x in keep]); ix = np.array([x[1] for x in keep])
        nb = posts[ix]
        same = int((nb == row.post).sum())
        if same > max_same:
            continue
        w = {}
        for code, dd in zip(nb, d):
            if code != row.post:
                w[code] = w.get(code, 0.0) + 1.0 / max(dd, 0.05)
        cnt = {}
        for code in nb:
            cnt[code] = cnt.get(code, 0) + 1
        best = max(w, key=w.get)
        if cnt.get(best, 0) / k < min_share:
            continue  # mixed neighbourhood (border zone), not a clear island
        same_d = None
        own = np.where(posts == row.post)[0]
        own = own[ids_all[own] != row.site_id]
        if len(own):
            same_d = float(np.min(np.hypot(*(xy[own] - q[i]).T)))
        alts = sorted((c for c in w if c != best), key=lambda c: -w[c])[:2]
        out.append({
            "site_id": row.site_id, "latitude": row.latitude, "longitude": row.longitude,
            "operator": row.operator, "region": row.region, "bm": row.bm, "arm": row.arm,
            "district": row.dist, "tehsil": row.tehsil, "post": row.post,
            "suggest": best, "suggest_n": cnt[best], "k": k, "alts": alts,
            "same_n": same, "nearest_km": round(float(d[0]), 2),
            "nearest_same_km": None if same_d is None else round(same_d, 2),
        })
    out.sort(key=lambda r: (-(r["nearest_same_km"] or 999), r["post"]))
    return out


def run(scenario, month_keys=None, regions=None, districts=None, agg="avg", radius_km=None,
        by_district=None, with_geometry=True, clip="district", tehsils=None, bms=None, arms=None,
        site_kpis=False, pieces=False, bigs=None, company=None):
    months = resolve_months(month_keys)
    company = company if company in ("ufone", "tp") else None
    df, kcols = load_frame(months, regions, districts, tehsils, bms, arms, bigs, company)
    if df.empty:
        return {"empty": True, "months": [m.key for m in months]}
    rd = rd_lookup()
    rd_base = getattr(scenario, "base", "own") == "rd"
    prev = rd_prev_lookup() if rd_base else None
    if rd_base:
        df = rebase_on_rd(df, rd, prev)
    mp, ov = mapping_for(scenario)
    df = apply_mapping(df, mp, ov)
    meta, summary, pre_rows, post_rows, series = compute_kpis(df, kcols, scenario, agg, scope_palette(df))
    meta["company"] = {"ufone": "Ufone only", "tp": "TP only"}.get(company, "Ufone + TP")

    colors = {r["code"]: r["color"] for r in pre_rows + post_rows}
    def third(frame):
        # third map: RD Final Franchise (own plan) / the real current franchise (RD-based plan)
        if rd_base:
            frame = frame.copy()
            frame["rd_post"] = frame["orig_code"]
            return frame
        return attach_rd(frame, rd)
    latest = third(latest_per_site(df))
    filtered = bool(regions or districts or tehsils or bms or arms or bigs)
    full = None
    if filtered:
        full, _ = load_frame(months, company=company)
        if rd_base:
            full = rebase_on_rd(full, rd, prev)
        full = apply_mapping(full, mp, ov)
        # Projections use each planned franchise's Central-B-wide uplift, so the views add up exactly
        _, _, _, g_post, _ = compute_kpis(full, kcols, scenario, agg, {})
        align_projections(post_rows, summary, meta, g_post, kcols)
        # Whole franchise (every ASM / BM, both operators unless the Company filter says otherwise):
        # a franchise's TP and Ufone sites usually sit under different ASMs, so a view shows only part of it.
        gmap = {r["code"]: r for r in g_post}
        view_ids = set(df["site_id"])
        fl = latest_per_site(full)
        codes_here = {r["code"] for r in post_rows}
        pk_ = meta["primary"]
        away = fl[fl["post"].isin(codes_here) & ~fl["site_id"].isin(view_ids)]
        away_by = {c: g for c, g in away.groupby("post")}
        for r in post_rows:
            gr = gmap.get(r["code"])
            if not gr or gr["sites"] <= r["sites"]:
                continue
            a = away_by.get(r["code"])
            r["whole"] = {k: gr[k] for k in ("sites", "kpis", "projected", "derived", "projected_derived", "operators",
                                              "members", "districts", "regions", "bms", "arms") if k in gr}
            r["whole"]["away_sites"] = [] if a is None else [
                {"site_id": x["site_id"], "operator": x["operator"], "arm": x["arm"], "bm": x["bm"], "region": x["region"],
                 "district": x["dist"], "tehsil": x["tehsil"], "pre": x["franchise_code"],
                 "kpi": float(x[pk_]) if pk_ and pk_ in a.columns else None,
                 "latitude": x["latitude"], "longitude": x["longitude"]}
                for x in a.to_dict("records")]
    radius = float(radius_km if radius_km is not None else scenario.coverage_km)
    byd = scenario.fill_within_district if by_district is None else by_district

    geom, enclaves = None, []
    if with_geometry:
        color_of = lambda c: colors.get(c) or color_for_code(c)  # noqa: E731
        if filtered:
            # Territories always come from ALL sites of the month (cached), so a filtered view
            # can never claim area that belongs to sites outside it.
            ctx = third(latest_per_site(full))
            geom, enclaves, _ = compute_geometry(ctx, radius, byd, color_of, clip, focus_ids=set(latest["site_id"]), pieces=pieces)
        else:
            geom, enclaves, _ = compute_geometry(latest, radius, byd, color_of, clip, pieces=pieces)

    try:
        strays = find_strays(latest, latest_per_site(full) if filtered else latest)
    except Exception:  # noqa: BLE001 — never break the plan over this helper
        strays = []
    from . import areas as _areas
    try:
        latest = latest.copy()
        latest["area"], latest["area_src"] = _areas.tag(latest["latitude"].to_numpy(), latest["longitude"].to_numpy())
    except Exception:  # noqa: BLE001 — names are a convenience, never break the plan
        latest["area"], latest["area_src"] = "", ""
    _area_of = dict(zip(latest["site_id"], latest["area"]))
    for x in strays:
        x["area"] = _area_of.get(x["site_id"], "")
    rd_info = None
    if rd_base:
        from ..models import RDTag
        last = RDTag.objects.order_by("-uploaded_at").values("source_file", "uploaded_at").first() or {}
        changed = latest[latest["post"] != latest["franchise_code"]]
        rd_info = {
            "mode": "rd", "file": last.get("source_file", ""), "uploaded": str(last.get("uploaded_at", ""))[:16],
            "sites": int(latest["in_rd"].sum()), "not_in_rd": int((~latest["in_rd"]).sum()),
            "franchises": int(latest["orig_code"].nunique()),
            "rd_franchises": int(latest.loc[latest["in_rd"], "franchise_code"].nunique()),
            "differs": int(len(changed)), "rd_not_in_data": 0, "loaded": rd is not None,
        }
    elif rd is not None:
        from ..models import RDTag
        last = RDTag.objects.order_by("-uploaded_at").values("source_file", "uploaded_at").first() or {}
        tagged = latest["rd_post"] != RD_NONE
        in_data = set(str(x).strip().upper() for x in (full if full is not None else df)["site_id"].unique())
        differs = latest[tagged & (latest["rd_post"] != latest["post"])]
        rd_info = {
            "file": last.get("source_file", ""), "uploaded": str(last.get("uploaded_at", ""))[:16],
            "sites": int(tagged.sum()), "not_in_rd": int((~tagged).sum()),
            "franchises": int(latest.loc[tagged, "rd_post"].nunique()),
            "differs": int(len(differs)),
            "rd_not_in_data": int(sum(1 for k in rd if k not in in_data)), "mode": "own",
        }
    site_cols = ["site_id", "latitude", "longitude", "operator", "big_region", "region", "dist", "tehsil", "area", "area_src", "franchise_code",
                 "post", "site_type", "status", "bm", "arm", "overridden", "franchise_after_mocan"]
    if "rd_post" in latest.columns:
        site_cols.append("rd_post")
    pk = meta["primary"]
    norm = {c: "".join(ch for ch in str(c).lower() if ch.isalnum()) for c in kcols}
    rc = next((c for c, n in norm.items() if n == "totalrecharge"), None) or next((c for c, n in norm.items() if "recharge" in n and "rechargers" not in n), None)
    if rc and rc != pk:
        site_cols.append(rc)
    if rd_base:
        site_cols.append("in_rd")
    sites = latest[site_cols + ([pk] if pk else [])].copy()
    if rc:
        sites["recharge"] = latest[rc].values
        if rc != pk:
            sites = sites.drop(columns=[rc])
    meta["recharge_col"] = rc
    sites = sites.rename(columns={"franchise_code": "pre", "dist": "district", pk: "kpi"} if pk else {"franchise_code": "pre", "dist": "district"})
    sites = sites.where(pd.notna(sites), None)

    skpi = None
    if site_kpis:
        div = len(meta["months"]) if agg == "avg" else 1
        skpi = (df.groupby("site_id")[kcols].sum() / div).round(2).to_dict(orient="index")

    return json_safe({
        "empty": False,
        "site_kpis": skpi,
        "meta": meta,
        "summary": summary,
        "pre": pre_rows,
        "post": post_rows,
        "series": series,
        "sites": sites.to_dict(orient="records"),
        "strays": strays,
        "rd": rd_info,
        "geo": geom,
        "enclaves": enclaves,
        "warnings": build_warnings(df, post_rows, latest),
        "mapping": mp,
        "overrides": ov,
    })
