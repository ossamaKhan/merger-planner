"""Area / neighbourhood names (Cantt, Gulgasht, Model Town…).

Two sources:
* built-in: ~74k neighbourhoods and towns/villages for Punjab from Who's On First (planner/data/places_punjab.json.gz)
* yours: AreaName rows — pins added on the map or uploaded (KML/KMZ/CSV/Excel), optionally with polygons.

Each site gets an `area`: inside one of your polygons → that name; else your nearest pin within
CUSTOM_KM; else nearest built-in neighbourhood within NB_KM; else nearest town/village within LOC_KM.
"""
import gzip
import json
from pathlib import Path

import numpy as np

CUSTOM_KM, NB_KM, LOC_KM = 2.0, 2.0, 4.0
DATA = Path(__file__).resolve().parent.parent / "data" / "places_punjab.json.gz"
_BUILTIN = None
_CACHE = {}


def _xy(lat, lon):
    lat = np.asarray(lat, float)
    lon = np.asarray(lon, float)
    return np.c_[lon * 111.32 * np.cos(np.radians(30.5)), lat * 110.57]


def builtin():
    global _BUILTIN
    if _BUILTIN is None:
        from .nn import NearTree as cKDTree
        try:
            with gzip.open(DATA, "rt", encoding="utf-8") as f:
                rows = json.load(f)["rows"]
        except OSError:
            rows = []
        out = {}
        for t in (0, 1):
            rs = [r for r in rows if r[1] == t]
            if rs:
                arr = np.array([[r[2], r[3]] for r in rs])
                out[t] = (rs, cKDTree(_xy(arr[:, 0], arr[:, 1])))
        _BUILTIN = out
    return _BUILTIN


def reset():
    _CACHE.clear()


def custom():
    if "custom" not in _CACHE:
        from .nn import NearTree as cKDTree
        from shapely.geometry import shape
        from ..models import AreaName
        pts, polys = [], []
        for a in AreaName.objects.all():
            label = a.name
            if a.geometry:
                try:
                    polys.append((label, shape(a.geometry), a))
                    continue
                except Exception:  # noqa: BLE001
                    pass
            pts.append((label, a))
        tree = cKDTree(_xy([p[1].latitude for p in pts], [p[1].longitude for p in pts])) if pts else None
        _CACHE["custom"] = (pts, tree, polys)
    return _CACHE["custom"]


def tag(lat, lon):
    """Arrays of lat/lon -> (names, source) lists. source: yours | neighbourhood | near town."""
    lat = np.asarray(lat, float)
    lon = np.asarray(lon, float)
    n = len(lat)
    names, src = [""] * n, [""] * n
    ok = ~(np.isnan(lat) | np.isnan(lon))
    if not ok.any():
        return names, src
    q = _xy(lat, lon)
    pts, tree, polys = custom()
    if polys:
        import shapely
        from shapely.geometry import Point
        for i in np.where(ok)[0]:
            p = Point(lon[i], lat[i])
            for label, geom, _a in polys:
                if geom.contains(p):
                    names[i], src[i] = label, "yours"
                    break
    todo = [i for i in np.where(ok)[0] if not names[i]]

    def nearest(idx, tr, rows, km, label_of, s):
        if not idx or tr is None:
            return idx
        d, j = tr.query(q[idx], k=1)
        left = []
        for i, dd, jj in zip(idx, d, j):
            if dd <= km:
                names[i], src[i] = label_of(rows[jj]), s
            else:
                left.append(i)
        return left

    todo = nearest(todo, tree, pts, CUSTOM_KM, lambda r: r[0], "yours")
    b = builtin()
    if 0 in b:
        todo = nearest(todo, b[0][1], b[0][0], NB_KM, lambda r: r[0], "neighbourhood")
    if 1 in b:
        nearest(todo, b[1][1], b[1][0], LOC_KM, lambda r: r[0], "near town")
    return names, src


def labels(s, w, n, e, zoom, limit=1500):
    """Names to draw on the map inside a bounding box."""
    from ..models import AreaName
    out = []
    for a in AreaName.objects.filter(latitude__gte=s, latitude__lte=n, longitude__gte=w, longitude__lte=e)[:limit]:
        out.append({"id": a.pk, "name": a.name, "city": a.city, "lat": a.latitude, "lon": a.longitude, "t": "yours",
                    "poly": a.geometry})
    # Built-in names at every zoom. Most important first (bigger population, neighbourhoods before
    # villages); the map then drops any label that would overlap one already drawn.
    b = builtin()
    sel = []
    for t in (0, 1):
        if t in b:
            sel += [(r, t) for r in b[t][0] if s <= r[2] <= n and w <= r[3] <= e]
    sel.sort(key=lambda x: (-x[0][4], x[1]))
    for r, t in sel[: max(0, limit - len(out))]:
        out.append({"name": r[0], "lat": r[2], "lon": r[3], "t": "neighbourhood" if t == 0 else "town", "pop": r[4]})
    return out


def search(q, limit=25):
    from ..models import AreaName
    ql = q.strip().lower()
    if len(ql) < 2:
        return []
    out = [{"name": a.name, "city": a.city, "lat": a.latitude, "lon": a.longitude, "t": "yours"}
           for a in AreaName.objects.filter(name__icontains=ql)[:limit]]
    b = builtin()
    for t in (0, 1):
        if t in b:
            for r in b[t][0]:
                if ql in r[0].lower():
                    out.append({"name": r[0], "lat": r[2], "lon": r[3], "t": "neighbourhood" if t == 0 else "town"})
                    if len(out) >= limit:
                        return out
    return out


def import_file(raw, filename):
    """KML/KMZ (pins or polygons) or CSV/Excel (name, latitude, longitude[, city]) -> AreaName rows."""
    import io

    import pandas as pd
    from shapely.geometry import shape

    from ..models import AreaName
    low = filename.lower()
    made = []
    if low.endswith((".kml", ".kmz")):
        from . import kml_import
        feats, _f, _nf, _c, _items = kml_import.load(raw, filename, prefer=("name", "area", "neighbourhood", "town"))
        for f in feats:
            g = shape(f["geometry"])
            c = g.representative_point() if f["kind"] == "polygon" else g
            made.append(AreaName(name=str(f["name"])[:120], latitude=c.y, longitude=c.x, source=filename[:120],
                                 geometry=f["geometry"] if f["kind"] == "polygon" else None,
                                 city=str(f["props"].get("city") or f["props"].get("City") or "")[:120]))
    else:
        df = pd.read_csv(io.BytesIO(raw)) if low.endswith(".csv") else pd.read_excel(io.BytesIO(raw))
        cols = {str(c).strip().lower().replace(" ", "").replace("_", ""): c for c in df.columns}

        def col(*names):
            for nme in names:
                if nme in cols:
                    return cols[nme]
            return None
        cn, cla, clo, cc = col("name", "area", "areaname", "neighbourhood", "neighborhood", "locality"), col("latitude", "lat"), col("longitude", "lon", "long", "lng"), col("city", "town", "district")
        if not (cn and cla and clo):
            raise ValueError("Need columns: Name, Latitude, Longitude (City optional).")
        for _, r in df.iterrows():
            try:
                la, lo = float(r[cla]), float(r[clo])
            except (TypeError, ValueError):
                continue
            if np.isnan(la) or np.isnan(lo) or not str(r[cn]).strip() or str(r[cn]) == "nan":
                continue
            made.append(AreaName(name=str(r[cn]).strip()[:120], latitude=la, longitude=lo, source=filename[:120],
                                 city=("" if cc is None or str(r[cc]) == "nan" else str(r[cc]).strip()[:120])))
    if not made:
        raise ValueError("No named pins or shapes found.")
    AreaName.objects.bulk_create(made)
    reset()
    return len(made)
