"""Backup & restore of all planning work, so it survives moving to a new version of the project.

A backup is one .zip:
  backup.json            plans (scenarios with tags, site moves, reviews, settings), franchises,
                         boundary-layer settings, list of months
  layers/<n>.bin         the original PTA / comparison KML/KMZ files
  data/<YYYY-MM>.json.gz the uploaded site data of each month (full backup only)

restore() also accepts an old db.sqlite3 from any earlier version of the project, so work done
before the backup feature existed can be carried over too.
"""
import gzip
import io
import json
import sqlite3
import zipfile
from datetime import date, datetime

from django.db import transaction
from django.utils import timezone

from planner.models import (BoundaryFeature, BoundaryLayer, DataMonth, Franchise, FranchiseMapping, Scenario,
                            ScopeReview, Site, SiteOverride)

FORMAT = "franchise-planner-backup"
VERSION = 1
SITE_FIELDS = ["site_id", "operator", "big_region", "region", "bu", "city", "district", "geo_district", "geo_tehsil", "province",
               "franchise_code", "bm", "arm", "latitude", "longitude", "site_type", "status", "mocan_date",
               "franchise_after_mocan", "tp_mocan", "ufone_mocan", "ufone_mocan_fr", "tp_mocan_fr", "kpis"]
SCEN_FIELDS = ["description", "coverage_km", "fill_within_district", "projection_method", "realization_pct"]


def _jd(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


# --------------------------------------------------------------------------- export

def export_backup(include_data=True):
    plans = {
        "format": FORMAT, "version": VERSION, "created": timezone.now().isoformat(), "includes_data": include_data,
        "franchises": [{"code": f.code, "name": f.name, "operator": f.operator, "color": f.color, "is_planned": f.is_planned}
                       for f in Franchise.objects.all()],
        "scenarios": [], "layers": [], "months": [],
        "area_names": [],
    }
    from planner.models import AreaName
    plans["area_names"] = list(AreaName.objects.values("name", "city", "latitude", "longitude", "geometry", "source"))
    for sc in Scenario.objects.all():
        plans["scenarios"].append({
            "name": sc.name, **{f: getattr(sc, f) for f in SCEN_FIELDS},
            "created_at": _jd(sc.created_at), "updated_at": _jd(sc.updated_at),
            "mappings": list(sc.mappings.values_list("source_code", "target_code", "note")),
            "overrides": list(sc.overrides.values_list("site_id", "target_code", "note")),
            "reviews": list(sc.reviews.values_list("level", "value", "note")),
        })
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i, l in enumerate(BoundaryLayer.objects.all()):
            path = f"layers/{i}.bin"
            z.writestr(path, bytes(l.raw))
            plans["layers"].append({"label": l.label, "role": l.role, "source_file": l.source_file, "name_field": l.name_field,
                                    "aliases": l.aliases, "active": l.active, "file": path})
        for dm in DataMonth.objects.order_by("month"):
            entry = {"month": dm.month.isoformat(), "source_file": dm.source_file, "kpi_columns": dm.kpi_columns, "rows": dm.row_count}
            if include_data:
                rows = [{k: _jd(v) for k, v in r.items()} for r in dm.sites.values(*SITE_FIELDS)]
                entry["file"] = f"data/{dm.month:%Y-%m}.json.gz"
                z.writestr(entry["file"], gzip.compress(json.dumps(rows, separators=(",", ":")).encode()))
            plans["months"].append(entry)
        z.writestr("backup.json", json.dumps(plans, indent=1, default=str))
    return buf.getvalue()


# --------------------------------------------------------------------------- read old db.sqlite3

def _rows(con, table):
    try:
        cur = con.execute(f"SELECT * FROM {table}")
    except sqlite3.OperationalError:
        return []
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _j(v, default):
    if v is None:
        return default
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return default


def sqlite_to_backup(path):
    """Read an old project database into the same structure a backup zip has (in memory)."""
    con = sqlite3.connect(path)
    try:
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "planner_scenario" not in names and "planner_site" not in names:
            raise ValueError("This database is not from the Franchise Planner.")
        plans = {"format": FORMAT, "version": VERSION, "created": "from old db.sqlite3", "includes_data": True,
                 "franchises": [], "scenarios": [], "layers": [], "months": []}
        files = {}
        for f in _rows(con, "planner_franchise"):
            plans["franchises"].append({k: f.get(k) for k in ("code", "name", "operator", "color")} | {"is_planned": bool(f.get("is_planned"))})
        maps, ovs, revs = {}, {}, {}
        for m in _rows(con, "planner_franchisemapping"):
            maps.setdefault(m["scenario_id"], []).append([m["source_code"], m["target_code"], m.get("note", "")])
        for o in _rows(con, "planner_siteoverride"):
            ovs.setdefault(o["scenario_id"], []).append([o["site_id"], o["target_code"], o.get("note", "")])
        for r in _rows(con, "planner_scopereview"):
            revs.setdefault(r["scenario_id"], []).append([r["level"], r["value"], r.get("note", "")])
        for sc in _rows(con, "planner_scenario"):
            plans["scenarios"].append({
                "name": sc["name"], **{f: sc.get(f) for f in SCEN_FIELDS if f in sc},
                "fill_within_district": bool(sc.get("fill_within_district", 1)),
                "mappings": maps.get(sc["id"], []), "overrides": ovs.get(sc["id"], []), "reviews": revs.get(sc["id"], []),
            })
        for i, l in enumerate(_rows(con, "planner_boundarylayer")):
            path = f"layers/{i}.bin"
            files[path] = bytes(l["raw"]) if l.get("raw") is not None else b""
            plans["layers"].append({"label": l["label"], "role": l["role"], "source_file": l["source_file"], "name_field": l.get("name_field", ""),
                                    "aliases": _j(l.get("aliases"), {}), "active": bool(l.get("active", 1)), "file": path})
        sites_by_month = {}
        for s in _rows(con, "planner_site"):
            sites_by_month.setdefault(s["month_id"], []).append(s)
        for dm in _rows(con, "planner_datamonth"):
            path = f"data/{str(dm['month'])[:7]}.json.gz"
            rows = [{k: (_j(s.get(k), {}) if k == "kpis" else s.get(k)) for k in SITE_FIELDS} for s in sites_by_month.get(dm["id"], [])]
            files[path] = rows  # already decoded
            plans["months"].append({"month": str(dm["month"])[:10], "source_file": dm.get("source_file", ""),
                                    "kpi_columns": _j(dm.get("kpi_columns"), []), "rows": len(rows), "file": path})
        return plans, files
    finally:
        con.close()


def read_backup(raw, filename=""):
    """bytes of a backup .zip or an old db.sqlite3 -> (plans, files)."""
    if raw[:16].startswith(b"SQLite format 3"):
        import os
        import tempfile

        fd, tmp = tempfile.mkstemp(suffix=".sqlite3")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(raw)
            return sqlite_to_backup(tmp)
        finally:
            os.unlink(tmp)
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise ValueError("This is neither a planner backup (.zip) nor a db.sqlite3 file.")
    if "backup.json" not in z.namelist():
        raise ValueError("This zip is not a Franchise Planner backup (no backup.json inside).")
    plans = json.loads(z.read("backup.json"))
    if plans.get("format") != FORMAT:
        raise ValueError("This zip is not a Franchise Planner backup.")
    files = {n: z.read(n) for n in z.namelist() if n != "backup.json"}
    return plans, files


# --------------------------------------------------------------------------- restore

def restore(raw, filename="", plans_on=True, data_on=True, layers_on=True, same_name="replace"):
    """Load a backup. same_name: 'replace' an existing scenario of the same name, or 'keep' both."""
    from planner.services import colors, geo, importer
    from planner.views import _store_layer

    plans, files = read_backup(raw, filename)
    summary = {"scenarios": 0, "replaced": 0, "mappings": 0, "overrides": 0, "reviews": 0,
               "months": [], "sites": 0, "layers": 0, "franchises": 0, "source": plans.get("created", "")}

    with transaction.atomic():
        # franchises (names / colours / planned codes)
        for f in plans.get("franchises", []):
            if not f.get("code"):
                continue
            fr, created = Franchise.objects.get_or_create(code=f["code"], defaults={
                "name": f.get("name") or f["code"], "operator": f.get("operator") or "", "color": f.get("color") or "",
                "is_planned": bool(f.get("is_planned"))})
            if not created and f.get("is_planned") and not fr.is_planned and not Site.objects.filter(franchise_code=fr.code).exists():
                fr.is_planned = True
                fr.save(update_fields=["is_planned"])
            summary["franchises"] += int(created)

        # your own area / neighbourhood names (merge: skip ones already there)
        from planner.models import AreaName
        have = {(a.name, round(a.latitude, 5), round(a.longitude, 5)) for a in AreaName.objects.all()}
        new = [AreaName(**{k: a.get(k) or ("" if k in ("city", "source") else None) for k in ("name", "city", "latitude", "longitude", "geometry", "source")})
               for a in plans.get("area_names", []) if a.get("name") and a.get("latitude") is not None
               and (a["name"], round(a["latitude"], 5), round(a["longitude"], 5)) not in have]
        AreaName.objects.bulk_create(new)
        summary["area_names"] = len(new)
        from planner.services import areas as _areas
        _areas.reset()

        # site data
        if data_on:
            for m in plans.get("months", []):
                fobj = files.get(m.get("file"))
                if fobj is None:
                    continue
                rows = fobj if isinstance(fobj, list) else json.loads(gzip.decompress(fobj))
                month = date.fromisoformat(str(m["month"])[:10])
                dm, _ = DataMonth.objects.update_or_create(month=month, defaults={"source_file": m.get("source_file", ""), "kpi_columns": m.get("kpi_columns", [])})
                dm.sites.all().delete()
                objs = []
                for r in rows:
                    kw = {k: r.get(k) for k in SITE_FIELDS if k in r}
                    for k in ("operator", "big_region", "region", "bu", "city", "district", "geo_district", "geo_tehsil", "province", "bm", "arm",
                              "site_type", "status", "franchise_after_mocan", "tp_mocan", "ufone_mocan", "ufone_mocan_fr", "tp_mocan_fr"):
                        kw[k] = kw.get(k) or ""
                    if kw.get("mocan_date"):
                        kw["mocan_date"] = date.fromisoformat(str(kw["mocan_date"])[:10])
                    kw["kpis"] = _j(kw.get("kpis"), {})
                    objs.append(Site(month=dm, **kw))
                Site.objects.bulk_create(objs, batch_size=2000)
                dm.row_count = len(objs)
                dm.save(update_fields=["row_count"])
                summary["months"].append(month.strftime("%b %Y"))
                summary["sites"] += len(objs)
            for code, op in Site.objects.values_list("franchise_code", "operator").distinct():
                Franchise.objects.get_or_create(code=code, defaults={"name": code, "operator": op})
            colors.assign_missing_colors()

        # plans
        if plans_on:
            for s in plans.get("scenarios", []):
                name = s["name"]
                existing = Scenario.objects.filter(name=name).first()
                if existing and same_name == "replace":
                    existing.delete()
                    summary["replaced"] += 1
                elif existing:
                    name = f"{name} (restored)"
                sc = Scenario.objects.create(name=name, **{f: s[f] for f in SCEN_FIELDS if s.get(f) is not None})
                FranchiseMapping.objects.bulk_create([FranchiseMapping(scenario=sc, source_code=a, target_code=b, note=n or "") for a, b, n in s.get("mappings", [])], ignore_conflicts=True)
                SiteOverride.objects.bulk_create([SiteOverride(scenario=sc, site_id=a, target_code=b, note=n or "") for a, b, n in s.get("overrides", [])], ignore_conflicts=True)
                ScopeReview.objects.bulk_create([ScopeReview(scenario=sc, level=a, value=b, note=n or "") for a, b, n in s.get("reviews", [])], ignore_conflicts=True)
                for code in {b for _, b, _ in s.get("mappings", [])} | {b for _, b, _ in s.get("overrides", [])}:
                    Franchise.objects.get_or_create(code=code, defaults={"name": code, "operator": "MergeCo", "is_planned": True})
                summary["scenarios"] += 1
                summary["mappings"] += len(s.get("mappings", []))
                summary["overrides"] += len(s.get("overrides", []))
                summary["reviews"] += len(s.get("reviews", []))
            colors.assign_missing_colors()

    # boundary layers (outside the transaction: re-parsing can take a moment)
    if layers_on:
        for l in plans.get("layers", []):
            raw_l = files.get(l.get("file"))
            if not raw_l:
                continue
            BoundaryLayer.objects.filter(label=l["label"], role=l["role"]).delete()
            layer = BoundaryLayer.objects.create(label=l["label"], role=l["role"], source_file=l["source_file"], raw=raw_l,
                                                 aliases=l.get("aliases") or {}, active=l.get("active", True))
            try:
                _store_layer(layer, raw_l, l["source_file"], l.get("name_field") or None)
                summary["layers"] += 1
            except Exception:  # noqa: BLE001
                layer.delete()

    # tag every site to the (possibly restored) district borders
    geo.reset()
    if Site.objects.exists():
        importer.retag_all_sites()
    return summary
