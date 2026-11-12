import json
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from planner.models import DataMonth, Franchise, FranchiseMapping, Scenario, ScopeReview, Site, SiteOverride
from planner.services import engine, exports, geo, importer
from planner.services.colors import color_for_index


def gated(view):
    """Require login only when PLANNER_REQUIRE_LOGIN=1."""
    if settings.PLANNER_REQUIRE_LOGIN:
        return login_required(view)
    return view


def _body(request):
    try:
        return json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return {}


def _params(request):
    g = request.GET
    split = lambda k: [v for v in g.get(k, "").split("|") if v]  # noqa: E731
    radius = g.get("km")
    return dict(
        month_keys=split("months"),
        regions=split("regions"),
        districts=split("districts"),
        tehsils=split("tehsils"),
        bms=split("bms"),
        arms=split("arms"),
        bigs=split("bigs"),
        company=g.get("company", "both"),
        agg=g.get("agg", "avg"),
        radius_km=float(radius) if radius else None,
        by_district=None if g.get("bydist") in (None, "") else g.get("bydist") == "1",
        clip="hull" if g.get("clip") == "hull" else "district",
    )


def circle_name(combos=None):
    """Name for 'everything': the Big Region from the data when there is one, else the setting."""
    bigs = sorted({c[6] for c in combos or [] if c[6]})
    if len(bigs) == 1:
        return bigs[0]
    if len(bigs) > 1:
        return "All big regions"
    return settings.PLANNER_CIRCLE_NAME


def _run(sc, p, **kw):
    return engine.run(sc, p["month_keys"], p["regions"], p["districts"], p["agg"], p["radius_km"], p["by_district"],
                      clip=p["clip"], tehsils=p["tehsils"], bms=p["bms"], arms=p["arms"], bigs=p["bigs"], company=p["company"], **kw)


def _reviews(sc):
    out = {}
    for lvl, val in sc.reviews.values_list("level", "value"):
        out.setdefault(lvl, []).append(val)
    return out


# --------------------------------------------------------------------------- pages

@gated
def home(request):
    months = list(DataMonth.objects.all())
    latest = months[0] if months else None
    by_region = []
    if latest:
        by_region = list(
            Site.objects.filter(month=latest).values("region").annotate(
                sites=Count("id"), franchises=Count("franchise_code", distinct=True)).order_by("region")
        )
    return render(request, "planner/home.html", {
        "months": months, "latest": latest, "by_region": by_region,
        "scenarios": Scenario.objects.annotate(n_map=Count("mappings", distinct=True), n_ov=Count("overrides", distinct=True)),
        "franchise_count": Franchise.objects.filter(is_planned=False).count(),
    })


@gated
def upload(request):
    result = None
    if request.method == "POST" and request.FILES.get("file"):
        f = request.FILES["file"]
        month = importer.parse_month(request.POST.get("month"))
        try:
            df, sheet, sheets = importer.read_workbook(f, request.POST.get("sheet") or None)
            result = importer.import_dataframe(df, month=month, source_file=f.name)
            result["sheet"] = sheet
            result["sheets"] = sheets
            messages.success(request, f"Imported {result['rows']} site rows for {', '.join(result['months'])} from sheet '{sheet}'.")
        except Exception as exc:  # show the reason to the planner, not a 500
            messages.error(request, f"Import failed: {exc}")
    cov = {}
    regions = sorted(set(Site.objects.values_list("region", flat=True)))
    for m, r, n in Site.objects.values_list("month__month", "region").annotate(n=Count("id")).values_list("month__month", "region", "n"):
        cov.setdefault(m, {})[r] = n
    coverage = [{"month": m.strftime("%b %Y"), "cells": [cov[m].get(r, 0) for r in regions], "total": sum(cov[m].values())}
                for m in sorted(cov, reverse=True)]
    return render(request, "planner/upload.html", {"result": result, "months": DataMonth.objects.all(),
                                                   "regions": regions, "coverage": coverage})


@gated
@require_POST
def month_delete(request, key):
    dm = get_object_or_404(DataMonth, month=f"{key}-01")
    label = str(dm)
    dm.delete()
    messages.info(request, f"Removed {label}.")
    return redirect("upload")


@gated
@require_POST
def scenario_create(request):
    name = request.POST.get("name", "").strip() or "Untitled plan"
    sc = Scenario.objects.create(
        name=name, description=request.POST.get("description", ""),
        coverage_km=float(request.POST.get("coverage_km") or 1.0),
    )
    if request.POST.get("seed") == "mocan":
        n_map, n_ov = exports.seed_from_mocan(sc)
        messages.info(request, f"Seeded {n_map} franchise tags and {n_ov} site overrides from 'Franchise ID After MOCAN'.")
    return redirect("planner", pk=sc.pk)


@gated
@require_POST
def scenario_clone(request, pk):
    src = get_object_or_404(Scenario, pk=pk)
    with transaction.atomic():
        new = Scenario.objects.create(
            name=f"{src.name} (copy)", description=src.description, coverage_km=src.coverage_km,
            fill_within_district=src.fill_within_district, projection_method=src.projection_method,
            realization_pct=src.realization_pct,
        )
        FranchiseMapping.objects.bulk_create([FranchiseMapping(scenario=new, source_code=m.source_code, target_code=m.target_code, note=m.note) for m in src.mappings.all()])
        SiteOverride.objects.bulk_create([SiteOverride(scenario=new, site_id=o.site_id, target_code=o.target_code, note=o.note) for o in src.overrides.all()])
    return redirect("planner", pk=new.pk)


@gated
@require_POST
def scenario_delete(request, pk):
    get_object_or_404(Scenario, pk=pk).delete()
    return redirect("home")


@gated
def planner(request, pk):
    sc = get_object_or_404(Scenario, pk=pk)
    return render(request, "planner/planner.html", {"scenario": sc, "has_data": DataMonth.objects.exists()})


@gated
def compare(request):
    ids = [int(i) for i in request.GET.getlist("s") if i.isdigit()]
    scenarios = list(Scenario.objects.filter(pk__in=ids)) if ids else []
    p = _params(request)
    rows = []
    for sc in scenarios:
        r = _run(sc, p, with_geometry=False)
        if not r.get("empty"):
            rows.append({"scenario": sc, "s": r["summary"], "primary": r["meta"]["primary"], "warnings": len(r["warnings"])})
    return render(request, "planner/compare.html", {
        "rows": rows, "all": Scenario.objects.all(), "selected": ids, "months": DataMonth.objects.all(),
        "sel_months": p["month_keys"],
    })


# --------------------------------------------------------------------------- API

@gated
def api_filters(request):
    months = list(DataMonth.objects.order_by("-month"))
    latest = months[0] if months else None
    tree, combos, fr_sites = {}, [], {}
    if latest:
        import pandas as pd

        # every site's most recent row across ALL loaded months (a region missing from the newest month still appears)
        rows = pd.DataFrame(list(Site.objects.values("site_id", "month__month", "big_region", "region", "geo_district", "district", "geo_tehsil", "bm", "arm", "franchise_code")))
        rows = rows.sort_values("month__month").drop_duplicates("site_id", keep="last").fillna("")
        rows["d"] = rows["geo_district"].where(rows["geo_district"] != "", rows["district"]).replace("", "Unknown")
        rows["r"] = rows["region"].replace("", "Unknown")
        rows["b"] = rows["big_region"].replace("", settings.PLANNER_CIRCLE_NAME)
        for (r, d, t, bm, arm, big), n in rows.groupby(["r", "d", "geo_tehsil", "bm", "arm", "b"]).size().items():
            tree.setdefault(r, {}).setdefault(d, 0)
            tree[r][d] += int(n)
            # one row per region/district/tehsil/BM/ARM combination -> drives the level navigator
            combos.append([r, d, t or "Unknown", bm, arm, int(n), big])
        fr_sites = {k: int(v) for k, v in rows.groupby("franchise_code").size().items()}
    frs = [{"code": f.code, "name": f.name, "operator": f.operator, "color": f.color, "planned": f.is_planned}
           for f in Franchise.objects.all()]
    return JsonResponse({
        "months": [{"key": m.key, "label": str(m), "rows": m.row_count} for m in months],
        "regions": {r: dict(sorted(d.items())) for r, d in sorted(tree.items())},
        "franchises": frs,
        "combos": combos,
        "fr_sites": fr_sites,
        "circle": circle_name(combos),
        "bigs": sorted({c[6] for c in combos}),
    })


@gated
def api_run(request, pk):
    sc = get_object_or_404(Scenario, pk=pk)
    p = _params(request)
    res = _run(sc, p, site_kpis=request.GET.get("sitekpis") == "1")
    res["reviews"] = _reviews(sc)
    return JsonResponse(res, json_dumps_params={"separators": (",", ":")})


@gated
@require_POST
def api_review(request, pk):
    """Body: {"level": "arm"|"bm"|"tehsil"|"district"|"region", "value": "...", "done": true|false}"""
    sc = get_object_or_404(Scenario, pk=pk)
    b = _body(request)
    level, value = b.get("level"), (b.get("value") or "").strip()
    if level not in dict(ScopeReview.LEVELS) or not value:
        return JsonResponse({"ok": False, "error": "level and value required"}, status=400)
    if b.get("done", True):
        ScopeReview.objects.update_or_create(scenario=sc, level=level, value=value, defaults={"note": b.get("note", "")})
    else:
        ScopeReview.objects.filter(scenario=sc, level=level, value=value).delete()
    sc.save()
    return JsonResponse({"ok": True, "reviews": _reviews(sc)})


@gated
def report(request, pk):
    sc = get_object_or_404(Scenario, pk=pk)
    return render(request, "planner/report.html", {"scenario": sc})


@gated
@require_POST
def api_mapping(request, pk):
    """Body: {"changes": {"OLD": "NEW" | null}}  (null / same code = remove tag)."""
    sc = get_object_or_404(Scenario, pk=pk)
    changes = _body(request).get("changes", {})
    with transaction.atomic():
        for src, tgt in changes.items():
            if not tgt or tgt == src:
                FranchiseMapping.objects.filter(scenario=sc, source_code=src).delete()
            else:
                _ensure_franchise(tgt)
                FranchiseMapping.objects.update_or_create(scenario=sc, source_code=src, defaults={"target_code": tgt})
        sc.save()
    return JsonResponse({"ok": True, "mapping": dict(sc.mappings.values_list("source_code", "target_code"))})


@gated
@require_POST
def api_override(request, pk):
    """Body: {"site_ids": [...], "target": "CODE" | null}  (null = back to franchise tag)."""
    sc = get_object_or_404(Scenario, pk=pk)
    body = _body(request)
    ids, tgt = body.get("site_ids", []), body.get("target")
    with transaction.atomic():
        if not tgt:
            SiteOverride.objects.filter(scenario=sc, site_id__in=ids).delete()
        else:
            _ensure_franchise(tgt)
            for sid in ids:
                SiteOverride.objects.update_or_create(scenario=sc, site_id=sid, defaults={"target_code": tgt, "note": body.get("note", "")})
        sc.save()
    return JsonResponse({"ok": True, "count": len(ids)})


@gated
@require_POST
def api_reset(request, pk):
    sc = get_object_or_404(Scenario, pk=pk)
    body = _body(request)
    what = body.get("what", "all")
    codes = body.get("codes")
    if what in ("all", "mappings"):
        qs = sc.mappings.all()
        if codes:
            qs = qs.filter(source_code__in=codes)
        qs.delete()
    if what in ("all", "overrides"):
        sc.overrides.all().delete()
    sc.save()
    return JsonResponse({"ok": True})


@gated
@require_POST
def api_seed(request, pk):
    sc = get_object_or_404(Scenario, pk=pk)
    n_map, n_ov = exports.seed_from_mocan(sc, _body(request).get("month"))
    return JsonResponse({"ok": True, "mappings": n_map, "overrides": n_ov})


@gated
@require_POST
def api_settings(request, pk):
    sc = get_object_or_404(Scenario, pk=pk)
    b = _body(request)
    for f, cast in (("name", str), ("description", str), ("coverage_km", float), ("realization_pct", float),
                    ("projection_method", str), ("fill_within_district", bool)):
        if f in b:
            setattr(sc, f, cast(b[f]))
    sc.coverage_km = max(0.05, min(50.0, sc.coverage_km))
    sc.save()
    return JsonResponse({"ok": True})


@gated
@require_POST
def api_franchise(request):
    """Create a planned (new) franchise code. Body: {"code":..., "name":...}"""
    b = _body(request)
    code = (b.get("code") or "").strip().upper()
    if not code:
        return JsonResponse({"ok": False, "error": "Code required"}, status=400)
    fr = _ensure_franchise(code, b.get("name"))
    return JsonResponse({"ok": True, "franchise": {"code": fr.code, "name": fr.name, "color": fr.color, "planned": fr.is_planned}})


def _ensure_franchise(code, name=None):
    fr, created = Franchise.objects.get_or_create(
        code=code, defaults={"name": name or code, "operator": "MergeCo", "is_planned": True,
                             "color": color_for_index(Franchise.objects.count() + 3)})
    if not created and name and fr.name != name:
        fr.name = name
        fr.save(update_fields=["name"])
    return fr


@gated
def boundaries(request, level):
    return HttpResponse(geo.raw_geojson("district" if level == "districts" else "tehsil"), content_type="application/geo+json")


@gated
def export_xlsx(request, pk):
    sc = get_object_or_404(Scenario, pk=pk)
    p = _params(request)
    res = _run(sc, p)
    if res.get("empty"):
        return HttpResponse("No data for this selection", status=404)
    buf = exports.scenario_excel(sc, res)
    resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="{sc.name.replace(" ", "_")}_pre_post.xlsx"'
    return resp


@gated
def export_kml(request, pk):
    sc = get_object_or_404(Scenario, pk=pk)
    p = _params(request)
    res = _run(sc, p, site_kpis=True, pieces=True)
    if res.get("empty"):
        return HttpResponse("No data for this selection", status=404)
    res["reviews"] = _reviews(sc)
    post_only = request.GET.get("layers") == "post"
    kml = exports.scenario_kml(sc, res, post_only=post_only)
    fname = sc.name.replace(" ", "_").replace("/", "-") + {"ufone": "_Ufone", "tp": "_TP"}.get(p["company"], "")
    if request.GET.get("fmt") == "kmz":
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("doc.kml", kml)
        resp = HttpResponse(buf.getvalue(), content_type="application/vnd.google-earth.kmz")
        resp["Content-Disposition"] = f'attachment; filename="{fname}_{"post_boundaries" if post_only else "plan"}.kmz"'
        return resp
    resp = HttpResponse(kml, content_type="application/vnd.google-earth.kml+xml")
    resp["Content-Disposition"] = f'attachment; filename="{fname}_{"post_boundaries" if post_only else "plan"}.kml"'
    return resp


# --------------------------------------------------------------------------- boundaries (PTA / Zong)
from planner.models import BoundaryFeature, BoundaryLayer  # noqa: E402
from planner.services import boundary_check, kml_import  # noqa: E402

PREFER = {"districts": ("district", "dist", "name"), "compare": ("franchise", "fr", "territory", "folder", "name")}


def _store_layer(layer, raw, filename, name_field=None):
    feats, fields, nf, counts, _ = kml_import.load(raw, filename, name_field, PREFER[layer.role])
    layer.fields, layer.name_field, layer.counts = fields, nf, counts
    layer.save()
    layer.features.all().delete()
    BoundaryFeature.objects.bulk_create([BoundaryFeature(layer=layer, name=f["name"], kind=f["kind"], geometry=f["geometry"], props=f["props"]) for f in feats], batch_size=500)
    if layer.role == "districts" and layer.active:
        BoundaryLayer.objects.filter(role="districts").exclude(pk=layer.pk).update(active=False)
        geo.reset()
        importer.retag_all_sites()
    return len(feats)


@gated
def boundaries_page(request):
    return render(request, "planner/boundaries.html", {
        "layers": BoundaryLayer.objects.all(), "scenarios": Scenario.objects.all(), "roles": BoundaryLayer.ROLES,
        "source": geo.district_source(),
        "area_sources": __import__("planner.models", fromlist=["AreaName"]).AreaName.objects.values("source").annotate(n=Count("id")).order_by("source"),
    })


@gated
@require_POST
def boundary_upload(request):
    f = request.FILES.get("file")
    role = request.POST.get("role", "compare")
    if not f or role not in dict(BoundaryLayer.ROLES):
        messages.error(request, "Choose a KML/KMZ file and what it is.")
        return redirect("boundaries")
    raw = f.read()
    label = request.POST.get("label", "").strip() or ("PTA districts" if role == "districts" else f.name.rsplit(".", 1)[0])
    layer = BoundaryLayer(label=label, role=role, source_file=f.name, raw=raw)
    try:
        with transaction.atomic():
            layer.save()
            n = _store_layer(layer, raw, f.name)
    except Exception as exc:  # noqa: BLE001
        messages.error(request, f"Could not read {f.name}: {exc}")
        return redirect("boundaries")
    extra = " Every site has been re-tagged to these districts and territories now follow them." if role == "districts" else ""
    messages.success(request, f"Loaded '{label}': {n} named {'districts' if role == 'districts' else 'areas/points'} "
                              f"(names from the '{layer.name_field}' field — change it below if they look wrong).{extra}")
    return redirect("boundaries")


@gated
@require_POST
def boundary_field(request, pk):
    layer = get_object_or_404(BoundaryLayer, pk=pk)
    try:
        with transaction.atomic():
            n = _store_layer(layer, bytes(layer.raw), layer.source_file, request.POST.get("name_field"))
        messages.success(request, f"'{layer.label}' now named by '{layer.name_field}' ({n} features).")
    except Exception as exc:  # noqa: BLE001
        messages.error(request, f"Could not re-read the layer: {exc}")
    return redirect("boundaries")


@gated
@require_POST
def boundary_delete(request, pk):
    layer = get_object_or_404(BoundaryLayer, pk=pk)
    was_districts = layer.role == "districts" and layer.active
    layer.delete()
    if was_districts:
        nxt = BoundaryLayer.objects.filter(role="districts").order_by("-uploaded_at").first()
        if nxt:
            nxt.active = True
            nxt.save()
        geo.reset()
        importer.retag_all_sites()
    messages.info(request, "Layer removed." + (" Sites re-tagged to the remaining district borders." if was_districts else ""))
    return redirect("boundaries")


@gated
@require_POST
def boundary_activate(request, pk):
    layer = get_object_or_404(BoundaryLayer, pk=pk, role="districts")
    BoundaryLayer.objects.filter(role="districts").update(active=False)
    layer.active = True
    layer.save()
    geo.reset()
    importer.retag_all_sites()
    messages.success(request, f"'{layer.label}' is now the official district boundary. Sites re-tagged.")
    return redirect("boundaries")


@gated
@require_POST
def api_boundary_alias(request):
    """Body: {"file": "D.G. Khan", "pta": "Dera Ghazi Khan" | null} on the active PTA layer."""
    layer = geo.active_district_layer()
    if not layer:
        return JsonResponse({"ok": False, "error": "Load a PTA district layer first."}, status=400)
    b = _body(request)
    key = boundary_check.norm(b.get("file"))
    aliases = dict(layer.aliases or {})
    if b.get("pta"):
        aliases[key] = b["pta"]
    else:
        aliases.pop(key, None)
    BoundaryLayer.objects.filter(pk=layer.pk).update(aliases=aliases)
    return JsonResponse({"ok": True})


def _scenario_param(request):
    sid = request.GET.get("scenario")
    return Scenario.objects.filter(pk=sid).first() if sid and sid.isdigit() else None


@gated
def api_boundary_check(request):
    months = [m for m in request.GET.get("months", "").split("|") if m]
    return JsonResponse(boundary_check.check(_scenario_param(request), months or None), json_dumps_params={"separators": (",", ":")})


@gated
def api_layers(request):
    return JsonResponse({"layers": [{"id": l.pk, "label": l.label, "role": l.role, "active": l.active, "name_field": l.name_field,
                                     "counts": l.counts} for l in BoundaryLayer.objects.all()]})


@gated
def api_layer_zones(request, pk):
    layer = get_object_or_404(BoundaryLayer, pk=pk)
    return JsonResponse(boundary_check.zones_geojson(layer), json_dumps_params={"separators": (",", ":")})


@gated
def api_layer_compare(request, pk):
    layer = get_object_or_404(BoundaryLayer, pk=pk)
    months = [m for m in request.GET.get("months", "").split("|") if m]
    return JsonResponse(engine.json_safe(boundary_check.compare(layer, _scenario_param(request), months or None)),
                        json_dumps_params={"separators": (",", ":")})


@gated
def boundary_xlsx(request):
    import io

    import pandas as pd

    sc = _scenario_param(request)
    res = boundary_check.check(sc)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        if not res.get("empty"):
            pd.DataFrame([{"Item": "District borders", "Value": res["source"]}, {"Item": "Month", "Value": res["month"]}]
                         + [{"Item": k, "Value": v} for k, v in res["summary"].items()]).to_excel(xw, sheet_name="Summary", index=False)
            s = pd.DataFrame(res["sites"])
            s[s["status"] != "ok"].to_excel(xw, sheet_name="Sites to check", index=False)
            s.to_excel(xw, sheet_name="All sites", index=False)
            pd.DataFrame(res["name_map"]).to_excel(xw, sheet_name="District names", index=False)
            pd.DataFrame(res["pre_spread"]).drop(columns=["out_sites"], errors="ignore").to_excel(xw, sheet_name="Current fr. vs PTA", index=False)
            if res["post_spread"]:
                pd.DataFrame(res["post_spread"]).drop(columns=["out_sites"], errors="ignore").to_excel(xw, sheet_name="Planned fr. vs PTA", index=False)
        lid = request.GET.get("layer")
        layer = BoundaryLayer.objects.filter(pk=lid).first() if lid and lid.isdigit() else None
        if layer:
            c = boundary_check.compare(layer, sc)
            if not c.get("empty"):
                pd.DataFrame(c["ours"]).to_excel(xw, sheet_name=f"Ours vs {layer.label}"[:31], index=False)
                pd.DataFrame(c["zones"]).to_excel(xw, sheet_name=f"{layer.label} areas"[:31], index=False)
                pd.DataFrame(c["outside_sites"]).to_excel(xw, sheet_name=f"Outside {layer.label}"[:31], index=False)
        for ws in xw.book.worksheets:
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = min(50, max(10, max(len(str(c.value or "")) for c in col[:200]) + 2))
    resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = 'attachment; filename="boundary_check.xlsx"'
    return resp


# --------------------------------------------------------------------------- backup & restore
from planner.services import backup as backup_svc  # noqa: E402


@gated
def backup_page(request):
    result = None
    if request.method == "POST" and request.FILES.get("file"):
        f = request.FILES["file"]
        try:
            result = backup_svc.restore(
                f.read(), f.name,
                plans_on=bool(request.POST.get("plans")), data_on=bool(request.POST.get("data")),
                layers_on=bool(request.POST.get("layers")), same_name=request.POST.get("same_name", "replace"))
            messages.success(request, f"Restored from {f.name}.")
        except Exception as exc:  # noqa: BLE001
            messages.error(request, f"Could not restore {f.name}: {exc}")
    return render(request, "planner/backup.html", {
        "result": result,
        "counts": {"scenarios": Scenario.objects.count(), "months": DataMonth.objects.count(),
                   "sites": Site.objects.count(), "layers": BoundaryLayer.objects.count(),
                   "mappings": FranchiseMapping.objects.count(), "overrides": SiteOverride.objects.count()},
        "scenarios": Scenario.objects.annotate(n_map=Count("mappings", distinct=True), n_ov=Count("overrides", distinct=True)),
    })


@gated
def backup_download(request):
    from django.utils import timezone

    full = request.GET.get("kind", "full") == "full"
    data = backup_svc.export_backup(include_data=full)
    stamp = timezone.localtime().strftime("%Y-%m-%d_%H%M")
    resp = HttpResponse(data, content_type="application/zip")
    resp["Content-Disposition"] = f'attachment; filename="planner_backup_{"full" if full else "plans"}_{stamp}.zip"'
    return resp


# ---------------------------------------------------------------- area / neighbourhood names
@gated
def api_areas(request):
    from .models import AreaName
    from .services import areas
    if request.method == "POST":
        body = json.loads(request.body or "{}")
        act = body.get("action", "add")
        if act == "add":
            name = (body.get("name") or "").strip()
            if not name:
                return JsonResponse({"error": "Give the area a name."}, status=400)
            a = AreaName.objects.create(name=name[:120], city=(body.get("city") or "").strip()[:120],
                                        latitude=float(body["lat"]), longitude=float(body["lon"]), source="added on map")
            areas.reset()
            return JsonResponse({"ok": True, "id": a.pk})
        a = get_object_or_404(AreaName, pk=body.get("id"))
        if act == "delete":
            a.delete()
        elif act == "rename":
            a.name = (body.get("name") or a.name).strip()[:120]
            a.city = (body.get("city") if body.get("city") is not None else a.city).strip()[:120]
            a.save()
        elif act == "move":
            a.latitude, a.longitude = float(body["lat"]), float(body["lon"])
            a.save()
        areas.reset()
        return JsonResponse({"ok": True})
    if request.GET.get("q"):
        return JsonResponse({"results": areas.search(request.GET["q"])})
    if request.GET.get("bbox"):
        s_, w_, n_, e_ = [float(x) for x in request.GET["bbox"].split(",")]
        return JsonResponse({"labels": areas.labels(s_, w_, n_, e_, int(float(request.GET.get("z", 12))))})
    return JsonResponse({"areas": list(AreaName.objects.values("id", "name", "city", "latitude", "longitude", "source"))})


@gated
@require_POST
def area_upload(request):
    from .services import areas
    f = request.FILES.get("file")
    if not f:
        messages.error(request, "Choose a KML/KMZ, CSV or Excel file with area names.")
        return redirect("boundaries")
    try:
        n = areas.import_file(f.read(), f.name)
        messages.success(request, f"Added {n} area names from {f.name}. Sites now show these names (open a scenario to see them).")
    except Exception as exc:  # noqa: BLE001
        messages.error(request, f"Could not read {f.name}: {exc}")
    return redirect("boundaries")


@gated
@require_POST
def area_clear(request):
    from .models import AreaName
    from .services import areas
    src = request.POST.get("source")
    qs = AreaName.objects.filter(source=src) if src else AreaName.objects.all()
    n = qs.count()
    qs.delete()
    areas.reset()
    messages.success(request, f"Removed {n} area names.")
    return redirect("boundaries")


@gated
def area_csv(request):
    import csv
    from .models import AreaName
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = 'attachment; filename="area_names.csv"'
    w = csv.writer(resp)
    w.writerow(["Name", "City", "Latitude", "Longitude", "Source"])
    for a in AreaName.objects.all():
        w.writerow([a.name, a.city, a.latitude, a.longitude, a.source])
    return resp
