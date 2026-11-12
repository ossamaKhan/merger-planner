"""Read any KML / KMZ boundary file into named shapes.

Handles the usual ways these files are made:
* polygons / multi-polygons (most PTA and operator exports)
* names in <name>, in ExtendedData (Data / SchemaData SimpleData), in the HTML table
  that ArcGIS / QGIS put in <description>, or only in the folder the placemark sits in
* files where borders are drawn as LINES (no filled shapes): the lines are turned into
  areas, and each area takes the name of a label point inside it
* point files (e.g. one point per competitor site): kept as points, grouped by a chosen
  field into territories later
"""
import io
import re
import zipfile
from collections import Counter
from html import unescape
from xml.etree import ElementTree as ET

import shapely
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, mapping

SIMPLIFY_DEG = 0.0003  # ~30 m — keeps files light without visibly moving borders


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def read_kml_bytes(raw, filename=""):
    """Return KML text from .kml or .kmz bytes."""
    if raw[:2] == b"PK" or filename.lower().endswith(".kmz"):
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            kmls = [n for n in z.namelist() if n.lower().endswith(".kml")]
            if not kmls:
                raise ValueError("The KMZ has no .kml file inside.")
            name = "doc.kml" if "doc.kml" in kmls else sorted(kmls, key=lambda n: (n.count("/"), n))[0]
            raw = z.read(name)
    for enc in ("utf-8", "utf-16", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("Could not read the file's text encoding.")


def _coords(text):
    pts = []
    for tok in (text or "").replace("\n", " ").split():
        parts = tok.split(",")
        if len(parts) >= 2:
            try:
                pts.append((float(parts[0]), float(parts[1])))
            except ValueError:
                pass
    return pts


def _html_table_fields(desc):
    """ArcGIS/QGIS style: <tr><td>FIELD</td><td>VALUE</td></tr> inside the description."""
    out = {}
    if not desc or "<t" not in desc.lower():
        return out
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", desc, flags=re.I | re.S):
        cells = [unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.I | re.S)]
        if len(cells) == 2 and cells[0] and len(cells[0]) < 60:
            out[cells[0]] = cells[1]
    return out


def _geoms(pm):
    """All geometries in a placemark, as shapely objects."""
    polys, lines, points = [], [], []
    for el in pm.iter():
        t = _local(el.tag)
        if t == "Polygon":
            outer, inner = None, []
            for b in el:
                bt = _local(b.tag)
                ring = next((c for c in b.iter() if _local(c.tag) == "coordinates"), None)
                pts = _coords(ring.text if ring is not None else "")
                if len(pts) < 3:
                    continue
                if bt == "outerBoundaryIs":
                    outer = pts
                elif bt == "innerBoundaryIs":
                    inner.append(pts)
            if outer:
                p = Polygon(outer, [h for h in inner if len(h) >= 3])
                if not p.is_valid:
                    p = shapely.make_valid(p)
                polys.append(p)
        elif t in ("LineString", "LinearRing"):
            c = next((x for x in el if _local(x.tag) == "coordinates"), None)
            pts = _coords(c.text if c is not None else "")
            if len(pts) >= 2:
                lines.append(LineString(pts))
        elif t == "Point":
            c = next((x for x in el if _local(x.tag) == "coordinates"), None)
            pts = _coords(c.text if c is not None else "")
            if pts:
                points.append(Point(pts[0]))
    # LinearRings that belong to Polygons were also collected as lines — drop them
    if polys:
        lines = []
    return polys, lines, points


def parse_kml(text):
    """Return a list of dicts: {kind, geom, props} where props include name/folder/attributes."""
    # Some exports declare namespaces badly; strip the default xmlns so ElementTree is simple.
    text = re.sub(r'\sxmlns="[^"]+"', "", text, count=1)
    try:
        root = ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
    except ET.ParseError as exc:
        raise ValueError(f"The KML is not valid XML: {exc}")
    items = []

    def walk(node, folders):
        for ch in node:
            t = _local(ch.tag)
            if t in ("Folder", "Document"):
                nm = next((c.text for c in ch if _local(c.tag) == "name"), None)
                walk(ch, folders + ([nm.strip()] if nm and nm.strip() and t == "Folder" else []))
            elif t == "Placemark":
                props = {}
                nm = next((c.text for c in ch if _local(c.tag) == "name"), None)
                if nm and nm.strip():
                    props["name"] = nm.strip()
                desc = next((c.text for c in ch if _local(c.tag) == "description"), None)
                for el in ch.iter():
                    lt = _local(el.tag)
                    if lt == "Data":
                        v = next((c.text for c in el if _local(c.tag) == "value"), None)
                        if el.get("name") and v is not None:
                            props[el.get("name")] = v.strip()
                    elif lt == "SimpleData" and el.get("name"):
                        props[el.get("name")] = (el.text or "").strip()
                for k, v in _html_table_fields(desc).items():
                    props.setdefault(k, v)
                if folders:
                    props["folder"] = folders[-1]
                    props["folder_path"] = " / ".join(folders)
                    if len(folders) > 1:
                        props["parent_folder"] = folders[-2]
                polys, lines, points = _geoms(ch)
                for g in polys:
                    items.append({"kind": "polygon", "geom": g, "props": dict(props)})
                for g in lines:
                    items.append({"kind": "line", "geom": g, "props": dict(props)})
                for g in points:
                    items.append({"kind": "point", "geom": g, "props": dict(props)})
            else:
                walk(ch, folders)

    walk(root, [])
    return items


def polygonize_lines(items):
    """Borders drawn as lines -> areas. Each area is named after a labelled point inside it."""
    lines = [i["geom"] for i in items if i["kind"] == "line"]
    if not lines:
        return items
    merged = shapely.union_all(lines)
    areas = [a for a in shapely.polygonize([merged]).geoms if a.area > 0]
    points = [i for i in items if i["kind"] == "point"]
    labelled, unlabelled = [], []
    for a in areas:
        lab = next((p for p in points if a.contains(p["geom"])), None)
        (labelled if lab else unlabelled).append((a, lab))
    if not labelled:  # no label points at all: keep anonymous areas
        out = [i for i in items if i["kind"] == "point"]
        out += [{"kind": "polygon", "geom": a, "props": {"name": f"Area {n}"}} for n, (a, _) in enumerate(sorted(unlabelled, key=lambda x: -x[0].area), 1)]
        return out
    # slivers without a label join the labelled neighbour they share the longest border with
    geoms = [a for a, _ in labelled]
    tree = shapely.STRtree(geoms)
    extra = {i: [] for i in range(len(labelled))}
    for a, _ in unlabelled:
        best, best_len = None, 0.0
        for j in tree.query(a.buffer(1e-6)):
            ln = a.boundary.intersection(geoms[j].boundary.buffer(1e-6)).length
            if ln > best_len:
                best, best_len = j, ln
        if best is None:
            best = int(tree.nearest(a))
        extra[best].append(a)
    used = set()
    out = []
    for i, (a, lab) in enumerate(labelled):
        used.add(id(lab))
        out.append({"kind": "polygon", "geom": shapely.union_all([a] + extra[i]), "props": dict(lab["props"], _from_lines="yes")})
    out += [p for p in points if id(p) not in used]
    return out


def field_summary(items):
    """Fields with a few sample values and how many distinct values each has."""
    stats = {}
    for it in items:
        for k, v in it["props"].items():
            if k.startswith("_"):
                continue
            s = stats.setdefault(k, {"values": Counter(), "n": 0})
            s["n"] += 1
            if v not in (None, ""):
                s["values"][str(v)] += 1
    out = []
    for k, s in stats.items():
        out.append({"field": k, "filled": s["n"], "distinct": len(s["values"]),
                    "samples": [v for v, _ in s["values"].most_common(4)]})
    return sorted(out, key=lambda f: (-f["filled"], f["field"]))


def guess_name_field(items, prefer=("district", "dist", "name")):
    """Pick the field that most likely holds the area name (e.g. the district)."""
    fields = field_summary([i for i in items if i["kind"] == "polygon"] or items)
    if not fields:
        return "name"
    n = max(f["filled"] for f in fields)

    def score(f):
        k = f["field"].lower()
        s = 0
        for i, word in enumerate(prefer):
            if word in k:
                s += 30 - i * 5
        if any(w in k for w in ("code", "id", "fid", "objectid", "shape", "area", "len", "pcode")):
            s -= 25
        if f["samples"] and all(re.fullmatch(r"[-\d.]+", x) for x in f["samples"]):
            s -= 40  # numbers are not names
        s += 10 * f["distinct"] / max(1, n)  # names are mostly unique per polygon
        s += 5 * f["filled"] / max(1, n)
        if k == "name" and f["distinct"] <= 1:
            s -= 30  # every placemark called the same thing
        return s

    return max(fields, key=score)["field"]


def to_features(items, name_field):
    """Group items by name -> list of {name, kind, geometry(GeoJSON), props}."""
    groups = {}
    for it in items:
        nm = str(it["props"].get(name_field) or it["props"].get("name") or it["props"].get("folder") or "Unnamed").strip()
        g = groups.setdefault((nm, it["kind"]), {"geoms": [], "props": it["props"]})
        g["geoms"].append(it["geom"])
    feats = []
    for (nm, kind), g in groups.items():
        if kind == "polygon":
            geom = shapely.make_valid(shapely.union_all(g["geoms"]))
            parts = [p for p in getattr(geom, "geoms", [geom]) if p.geom_type == "Polygon"]
            if not parts:
                continue
            geom = MultiPolygon(parts) if len(parts) > 1 else parts[0]
            geom = geom.simplify(SIMPLIFY_DEG, preserve_topology=True)
            feats.append({"name": nm, "kind": "polygon", "geometry": mapping(geom), "props": _clean(g["props"])})
        elif kind == "point":
            for p in g["geoms"]:
                feats.append({"name": nm, "kind": "point", "geometry": mapping(p), "props": _clean(g["props"])})
    return feats


def _clean(props):
    return {k: v for k, v in props.items() if not k.startswith("_") and len(str(v)) < 300}


def load(raw, filename, name_field=None, prefer=("district", "dist", "name")):
    """Bytes -> (features, fields, chosen_name_field, counts)."""
    items = parse_kml(read_kml_bytes(raw, filename))
    if not items:
        raise ValueError("No shapes or points were found in this file.")
    counts = Counter(i["kind"] for i in items)
    if not counts.get("polygon") and counts.get("line"):
        items = polygonize_lines(items)
    fields = field_summary(items)
    nf = name_field if name_field and any(f["field"] == name_field for f in fields) else guess_name_field(items, prefer)
    feats = to_features(items, nf)
    return feats, fields, nf, dict(counts), items
