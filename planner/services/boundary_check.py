"""Boundary checks against PTA districts, and comparison with another operator's layer (e.g. Zong).

check()            -> sites outside PTA districts, district-name mismatches, franchises that
                      cross PTA district lines (current and planned)
compare()          -> our sites / planned franchises vs a comparison layer's territories
"""
import difflib
import re
from collections import Counter, defaultdict

import numpy as np
import pandas as pd
import shapely
from shapely.geometry import MultiPoint, mapping, shape

from planner.models import BoundaryLayer, FranchiseMapping  # noqa: F401
from planner.services import engine, geo


def norm(name):
    s = re.sub(r"\bdistrict\b|\bdist\b", "", str(name or "").lower())
    return re.sub(r"[^a-z0-9]", "", s)


# common Pakistani district short forms
SHORT = {"dgkhan": "deraghazikhan", "dikhan": "deraismailkhan", "ttsingh": "tobateksingh", "tobatek": "tobateksingh",
         "ryk": "rahimyarkhan", "mbdin": "mandibahauddin", "mandibahaudin": "mandibahauddin", "nankana": "nankanasahib",
         "sheikhupura": "sheikhpura", "muzaffargharh": "muzaffargarh", "kasur": "kasur", "qasur": "kasur"}


def canon(name):
    n = norm(name)
    return SHORT.get(n, n)


def match_district(file_name, pta_names, aliases):
    """Return (pta_name or None, how) for a district name written in the site file."""
    if not file_name:
        return None, ""
    if norm(file_name) in aliases:
        return aliases[norm(file_name)], "your mapping"
    c = canon(file_name)
    by_canon = {canon(p): p for p in pta_names}
    if c in by_canon:
        return by_canon[c], "same name"
    close = difflib.get_close_matches(c, list(by_canon), n=1, cutoff=0.82)
    if close:
        return by_canon[close[0]], "similar spelling"
    return None, ""


def _strict_tag(df):
    """PTA district each site lies in — no 'nearest' fallback. Outside -> nearest + distance."""
    props, geoms, tree = geo.districts()
    names = [p["name"] for p in props]
    n = len(df)
    inside = [None] * n
    nearest = [None] * n
    dist_km = [None] * n
    ok = df["latitude"].notna() & df["longitude"].notna()
    idx = np.where(ok.values)[0]
    if tree is None or not len(idx):
        return inside, nearest, dist_km, names
    pts = shapely.points(np.column_stack([df["longitude"].values[idx], df["latitude"].values[idx]]))
    pi, gi = tree.query(pts, predicate="within")
    for p_, g_ in zip(pi, gi):
        if inside[idx[p_]] is None:
            inside[idx[p_]] = names[g_]
    out = [i for i in idx if inside[i] is None]
    if out:
        sub = shapely.points(np.column_stack([df["longitude"].values[out], df["latitude"].values[out]]))
        near = tree.nearest(sub)
        for k, (i, g_) in enumerate(zip(out, near)):
            nearest[i] = names[g_]
            proj = geo.LocalProjection(float(df["latitude"].values[i]))
            dist_km[i] = round(float(shapely.distance(proj.to_km(sub[k]), proj.to_km(geoms[g_]))), 2)
    return inside, nearest, dist_km, names


def _frame(scenario, month_keys):
    months = engine.resolve_months(month_keys)
    if not months:
        return None, None
    df, kcols = engine.load_frame(months)
    if df.empty:
        return None, None
    mp, ov = engine.mapping_for(scenario) if scenario else ({}, {})
    df = engine.latest_per_site(engine.apply_mapping(df, mp, ov))
    label = str(months[-1]) if len(months) == 1 else f"{months[0]} – {months[-1]} (latest row per site)"
    return df, label


def check(scenario=None, month_keys=None):
    df, month = _frame(scenario, month_keys)
    if df is None:
        return {"empty": True}
    layer = geo.active_district_layer()
    aliases = (layer.aliases if layer else {}) or {}
    inside, nearest, dist_km, pta_names = _strict_tag(df)
    df["pta"] = inside
    df["pta_nearest"] = nearest
    df["pta_km"] = dist_km

    # file district -> PTA district name matching
    file_d = sorted(d for d in df["district"].unique() if d)
    name_map = {}
    for d in file_d:
        m, how = match_district(d, pta_names, aliases)
        name_map[d] = {"file": d, "pta": m, "how": how}

    rows = []
    for r in df.itertuples():
        nocoord = pd.isna(r.latitude) or pd.isna(r.longitude)
        expected = name_map.get(r.district, {}).get("pta") if r.district else None
        if nocoord:
            status = "no coordinates"
        elif r.pta is None:
            status = "outside PTA districts"
        elif r.district and expected is None:
            status = "file district not in PTA list"
        elif r.district and expected != r.pta:
            status = "in a different district"
        else:
            status = "ok"
        rows.append({
            "site_id": r.site_id, "operator": r.operator, "region": r.region, "bm": r.bm, "arm": r.arm,
            "pre": r.franchise_code, "post": r.post, "file_district": r.district, "pta_district": r.pta,
            "nearest_pta": r.pta_nearest, "km_outside": r.pta_km, "status": status,
            "latitude": None if nocoord else round(float(r.latitude), 5), "longitude": None if nocoord else round(float(r.longitude), 5),
        })
    sites = pd.DataFrame(rows)

    # franchises crossing PTA district lines (only sites inside some PTA district count)
    def spread(col):
        out = []
        tagged = df[df["pta"].notna()]
        for code, sub in tagged.groupby(col):
            cnt = sub["pta"].value_counts()
            home = cnt.index[0]
            away = sub[sub["pta"] != home]
            out.append({
                "franchise": code, "sites": int(len(sub)), "home_district": home, "districts": int(len(cnt)),
                "home_share": round(100 * cnt.iloc[0] / len(sub), 1), "out_of_bound": int(len(away)),
                "other_districts": ", ".join(f"{d} ({n})" for d, n in cnt.iloc[1:].items()),
                "out_sites": away["site_id"].tolist()[:200],
                "bms": ", ".join(sorted({b for b in sub["bm"] if b})), "arms": ", ".join(sorted({a for a in sub["arm"] if a})),
            })
        return sorted(out, key=lambda x: (-x["out_of_bound"], -x["districts"]))

    st = Counter(sites["status"])
    mism = sites[sites["status"] == "in a different district"]
    pairs = mism.groupby(["file_district", "pta_district"]).size().sort_values(ascending=False)
    return {
        "empty": False,
        "month": str(month),
        "source": geo.district_source(),
        "using_pta": layer is not None,
        "summary": {"sites": int(len(sites)), **{k: int(v) for k, v in st.items()}},
        "sites": sites.where(pd.notna(sites), None).to_dict(orient="records"),
        "name_map": sorted(name_map.values(), key=lambda x: (x["pta"] is not None, x["how"] == "same name", x["file"])),
        "pta_names": sorted(pta_names),
        "mismatch_pairs": [{"file_district": a, "pta_district": b, "sites": int(n)} for (a, b), n in pairs.items()],
        "pre_spread": spread("franchise_code"),
        "post_spread": spread("post") if scenario else [],
    }


# --------------------------------------------------------------------------- comparison layer

def layer_zones(layer):
    """Territories of a comparison layer: its polygons, or (for point layers) the area closest to
    each group's points, grouped by the chosen field, clipped to the PTA districts they touch."""
    feats = list(layer.features.all())
    polys = [f for f in feats if f.kind == "polygon"]
    points = [f for f in feats if f.kind == "point"]
    zones = {}
    for f in polys:
        g = shape(f.geometry)
        zones[f.name] = shapely.union(zones[f.name], g) if f.name in zones else g
    pts_out = []
    if points and not polys:
        coords = np.array([f.geometry["coordinates"][:2] for f in points], dtype=float)
        names = [f.name for f in points]
        pts_out = [[n, round(c[1], 5), round(c[0], 5)] for n, c in zip(names, coords)]
        hull = MultiPoint([tuple(c) for c in coords]).convex_hull.buffer(0.03)
        _, dgeoms, dtree = geo.districts()
        if dtree is not None:
            hit = dtree.query(hull, predicate="intersects")
            area = shapely.union_all([dgeoms[i] for i in hit]).intersection(hull) if len(hit) else hull
        else:
            area = hull
        vor = shapely.voronoi_polygons(MultiPoint([tuple(c) for c in coords]), extend_to=area)
        tree = shapely.STRtree(shapely.points(coords))
        cells = defaultdict(list)
        for cell in vor.geoms:
            j = tree.query(cell, predicate="contains")
            if len(j):
                cells[names[int(j[0])]].append(cell.intersection(area))
        zones = {n: shapely.union_all(cs) for n, cs in cells.items()}
    return {n: shapely.make_valid(g) for n, g in zones.items() if not g.is_empty}, pts_out


def zones_geojson(layer):
    zones, pts = layer_zones(layer)
    feats = [{"type": "Feature", "properties": {"name": n, "layer": layer.label},
              "geometry": mapping(g.simplify(0.0005, preserve_topology=True))} for n, g in zones.items()]
    return {"type": "FeatureCollection", "features": feats, "points": pts}


def compare(layer, scenario=None, month_keys=None):
    df, month = _frame(scenario, month_keys)
    if df is None:
        return {"empty": True}
    zones, zpts = layer_zones(layer)
    if not zones:
        return {"empty": True, "error": "This layer has no areas to compare with."}
    znames = list(zones)
    zgeoms = [zones[n] for n in znames]
    ztree = shapely.STRtree(zgeoms)

    # our sites -> zone
    ok = df["latitude"].notna() & df["longitude"].notna()
    df["zone"] = None
    idx = np.where(ok.values)[0]
    pts = shapely.points(np.column_stack([df["longitude"].values[idx], df["latitude"].values[idx]]))
    pi, gi = ztree.query(pts, predicate="within")
    zone_of = {}
    for p_, g_ in zip(pi, gi):
        zone_of.setdefault(idx[p_], znames[g_])
    df.loc[list(zone_of), "zone"] = list(zone_of.values())

    # our planned (and current) territories from the engine, all sites
    res = engine.run(scenario, month_keys, with_geometry=True) if scenario else None
    lat0 = float(df["latitude"].dropna().mean())
    proj = geo.LocalProjection(lat0)
    zkm = [proj.to_km(g) for g in zgeoms]
    zkm_tree = shapely.STRtree(zkm)

    def overlaps(features):
        rows = []
        for f in features:
            code = f["properties"]["code"]
            g = proj.to_km(shapely.make_valid(shape(f["geometry"])))
            area = g.area or 1
            touched = []
            for j in zkm_tree.query(g, predicate="intersects"):
                a = g.intersection(zkm[j]).area
                if a / area > 0.01:
                    touched.append((znames[j], a / area * 100))
            touched.sort(key=lambda x: -x[1])
            sub = df[df["post"] == code] if "post" in df else df[df["franchise_code"] == code]
            zc = sub["zone"].value_counts()
            rows.append({
                "franchise": code, "sites": int(len(sub)), "area_km2": round(area, 1),
                "best_zone": touched[0][0] if touched else None, "best_share": round(touched[0][1], 1) if touched else 0,
                "zones": int(len(touched)), "zone_detail": ", ".join(f"{z} {s:.0f}%" for z, s in touched[:5]),
                "sites_in_best_zone": int(zc.iloc[0]) if len(zc) else 0, "sites_outside_zones": int(sub["zone"].isna().sum()),
            })
        return sorted(rows, key=lambda r: (-r["zones"], r["best_share"]))

    ours = overlaps(res["geo"]["post"]["features"]) if res and not res.get("empty") else []

    zone_rows = []
    zsite_counts = Counter(n for n, _, _ in zpts) if zpts else {}
    for n, gk in zip(znames, zkm):
        sub = df[df["zone"] == n]
        fr = sub["post"].value_counts()
        zone_rows.append({
            "zone": n, "area_km2": round(gk.area, 1), "their_sites": int(zsite_counts.get(n, 0)) if zpts else None,
            "our_sites": int(len(sub)), "our_franchises": int(len(fr)),
            "our_detail": ", ".join(f"{c} ({k})" for c, k in fr.head(6).items()),
            "main_franchise": fr.index[0] if len(fr) else None,
            "main_share": round(100 * fr.iloc[0] / len(sub), 1) if len(fr) else 0,
        })
    zone_rows.sort(key=lambda r: -r["our_sites"])
    outside = df[ok & df["zone"].isna()]
    return {
        "empty": False, "layer": layer.label, "month": str(month),
        "summary": {"zones": len(znames), "our_sites": int(ok.sum()), "our_sites_outside": int(len(outside)),
                    "their_sites": len(zpts) if zpts else None, "our_franchises": len(ours),
                    "franchises_one_zone": sum(1 for r in ours if r["zones"] <= 1),
                    "franchises_multi_zone": sum(1 for r in ours if r["zones"] > 1)},
        "ours": ours, "zones": zone_rows,
        "outside_sites": outside[["site_id", "post", "district", "arm", "bm"]].head(500).to_dict(orient="records"),
        "site_zone": {r.site_id: r.zone for r in df.itertuples() if r.zone},
    }
