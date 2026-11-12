"""Boundary loading, point-in-polygon tagging and a local km projection.

Boundaries ship in planner/data/ (district = ADM2, tehsil = ADM3, both from
geoBoundaries gbOpen, which is derived from OCHA / Pakistan Bureau of Statistics
data). Replace those files with your own GeoJSON (same `name` property) if HQ
uses a different district list; `python manage.py retag_sites` re-applies them.
"""
import json
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import mapping, shape

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DISTRICTS_FILE = DATA_DIR / "pak_districts.geojson"
TEHSILS_FILE = DATA_DIR / "pak_tehsils.geojson"

KM_PER_DEG_LAT = 110.574


@lru_cache(maxsize=4)
def _load(path_str):
    path = Path(path_str)
    if not path.exists():
        return [], [], None
    data = json.loads(path.read_text(encoding="utf-8"))
    props, geoms = [], []
    for f in data.get("features", []):
        g = shape(f["geometry"])
        if not g.is_valid:
            g = g.buffer(0)
        props.append(f.get("properties", {}))
        geoms.append(g)
    tree = shapely.STRtree(geoms) if geoms else None
    return props, geoms, tree


_DB = {"key": None, "val": None, "json": None}


def _nice(name):
    """'D.G. KHAN' -> 'D.G. Khan'; leaves mixed-case names alone."""
    name = " ".join(str(name).split())
    return name.title() if name.isupper() else name


def active_district_layer():
    try:
        from planner.models import BoundaryLayer

        return BoundaryLayer.objects.filter(role="districts", active=True).order_by("-uploaded_at").first()
    except Exception:  # noqa: BLE001  (table not created yet during first migrate)
        return None


def _db_districts():
    layer = active_district_layer()
    if layer is None:
        return None
    key = (layer.pk, layer.uploaded_at, layer.name_field)
    if _DB["key"] == key:
        return _DB["val"]
    base_props, base_geoms, base_tree = _load(str(DISTRICTS_FILE))  # only used to look up the province
    props, geoms = [], []
    for f in layer.features.filter(kind="polygon"):
        g = shape(f.geometry)
        if not g.is_valid:
            g = g.buffer(0)
        prov = f.props.get("province") or f.props.get("PROVINCE") or f.props.get("Province") or ""
        if not prov and base_tree is not None:
            hit = base_tree.query(g.representative_point(), predicate="within")
            if len(hit):
                prov = base_props[int(hit[0])].get("province", "")
        props.append({"name": _nice(f.name), "province": _nice(prov), "source": layer.label})
        geoms.append(g)
    val = (props, geoms, shapely.STRtree(geoms) if geoms else None)
    _DB.update(key=key, val=val, json=None)
    return val


def reset():
    """Call after a boundary layer is loaded, changed or deleted."""
    _DB.update(key=None, val=None, json=None)
    try:
        from planner.services import engine

        engine._CELL_CACHE.clear()
    except Exception:  # noqa: BLE001
        pass


def districts():
    """Official district borders: the active PTA layer if one is loaded, else the built-in file."""
    return _db_districts() or _load(str(DISTRICTS_FILE))


def district_source():
    layer = active_district_layer()
    return f"{layer.label} ({layer.source_file})" if layer else "Built-in OCHA / PBS districts (geoBoundaries)"


def tehsils():
    return _load(str(TEHSILS_FILE))


def raw_geojson(level):
    if level == "district" and _db_districts():
        if _DB["json"] is None:
            props, geoms, _ = _db_districts()
            _DB["json"] = json.dumps({"type": "FeatureCollection", "features": [
                {"type": "Feature", "properties": p, "geometry": mapping(g)} for p, g in zip(props, geoms)]}, separators=(",", ":"))
        return _DB["json"]
    path = DISTRICTS_FILE if level == "district" else TEHSILS_FILE
    return path.read_text(encoding="utf-8") if path.exists() else '{"type":"FeatureCollection","features":[]}'


def _tag(points_lonlat, layer):
    props, geoms, tree = layer
    out = [None] * len(points_lonlat)
    if tree is None or not points_lonlat:
        return out
    pts = shapely.points(np.asarray(points_lonlat, dtype=float))
    pidx, gidx = tree.query(pts, predicate="within")
    for p, g in zip(pidx, gidx):
        if out[p] is None:
            out[p] = props[g]
    # points slightly outside every polygon (coastline / simplification): nearest polygon
    missing = [i for i, v in enumerate(out) if v is None]
    if missing:
        near = tree.nearest(pts[missing])
        for i, g in zip(missing, near):
            out[i] = props[g]
    return out


def tag_points(lats, lons):
    """Return list of (district, tehsil, province) for each point."""
    pts = []
    valid = []
    for la, lo in zip(lats, lons):
        ok = la is not None and lo is not None and not (math.isnan(la) or math.isnan(lo)) and 20 < la < 40 and 58 < lo < 80
        valid.append(ok)
        pts.append((lo, la) if ok else (0.0, 0.0))
    dist = _tag(pts, districts())
    teh = _tag(pts, tehsils())
    res = []
    for ok, d, t in zip(valid, dist, teh):
        if not ok:
            res.append(("", "", ""))
            continue
        res.append(((d or {}).get("name", ""), (t or {}).get("name", ""), (d or {}).get("province", "")))
    return res


def district_geoms(names):
    props, geoms, _ = districts()
    wanted = set(names)
    return {p["name"]: g for p, g in zip(props, geoms) if p.get("name") in wanted}


class LocalProjection:
    """Equirectangular projection centred on the area of interest (km units).

    Accurate to well under 1% over a district/region, which is plenty for a
    1 km coverage radius and keeps the project free of GDAL/pyproj.
    """

    def __init__(self, lat0):
        self.kx = 111.320 * math.cos(math.radians(lat0))
        self.ky = KM_PER_DEG_LAT

    def fwd_xy(self, lon, lat):
        return lon * self.kx, lat * self.ky

    def to_km(self, geom):
        kx, ky = self.kx, self.ky
        return shapely.transform(geom, lambda c: c * np.array([kx, ky]))

    def to_deg(self, geom):
        kx, ky = self.kx, self.ky
        return shapely.transform(geom, lambda c: c / np.array([kx, ky]))
