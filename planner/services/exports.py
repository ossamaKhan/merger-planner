"""Excel and KML exports of a scenario run."""
import io
from xml.sax.saxutils import escape

import pandas as pd

from planner.models import FranchiseMapping, Site, SiteOverride
from planner.services.engine import resolve_months


def scenario_excel(scenario, result):
    buf = io.BytesIO()
    meta, summ = result["meta"], result["summary"]
    kcols = [k["key"] for k in meta["kpis"]]
    dlabels = [d["label"] for d in meta["derived"]]
    agg_txt = "monthly average" if meta["agg"] == "avg" else "total over period"

    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        rows = [
            ("Scenario", scenario.name),
            ("Months", ", ".join(meta["months"])),
            ("Values are", agg_txt),
            ("Coverage radius (km)", result["geo"]["stats"].get("radius_km") if result.get("geo") else scenario.coverage_km),
            ("Projection method", scenario.get_projection_method_display()),
            ("Gap realisation %", scenario.realization_pct),
            ("Franchises pre", summ["pre_franchises"]),
            ("Franchises post", summ["post_franchises"]),
            ("Sites", summ["sites"]),
            ("Avg sites / franchise pre", round(summ["pre_avg_sites"], 1)),
            ("Avg sites / franchise post", round(summ["post_avg_sites"], 1)),
        ]
        if result.get("geo"):
            for k, v in result["geo"]["stats"].items():
                rows.append((k.replace("_", " ").title(), v))
        for k in kcols:
            rows.append((f"{k} – baseline", summ["totals"][k]))
            rows.append((f"{k} – projected", summ["projected_totals"][k]))
        pd.DataFrame(rows, columns=["Item", "Value"]).to_excel(xw, sheet_name="Summary", index=False)

        mp = pd.DataFrame(
            [{"Old franchise": p["code"], "Sites": p["sites"], "New franchise(s)": ", ".join(f"{k} ({v})" for k, v in p["mapped_to"].items()),
              "Districts": ", ".join(p["districts"])} for p in result["pre"]]
        )
        mp.to_excel(xw, sheet_name="Mapping", index=False)

        def flat(r, extra=None):
            d = {"Franchise": r["code"], "Name": r["name"], "Sites": round(r["sites"], 1),
                 "Districts": ", ".join(r["districts"]), "Regions": ", ".join(r["regions"]),
                 "TP sites": r["operators"].get("Telenor", 0), "Ufone sites": r["operators"].get("Ufone", 0)}
            d.update({k: r["kpis"][k] for k in kcols})
            d.update({lbl: r["derived"].get(lbl) for lbl in dlabels})
            if extra:
                d.update(extra)
            return d

        pd.DataFrame([flat(r) for r in result["pre"]]).to_excel(xw, sheet_name="Pre", index=False)
        post = []
        for r in result["post"]:
            extra = {"Members": ", ".join(m["code"] for m in r["members"]), "New code": r["is_new"]}
            extra.update({f"{k} (proj)": r["projected"][k] for k in kcols})
            extra.update({f"{k} (trend next)": r["trend_next"].get(k) for k in kcols})
            post.append(flat(r, extra))
        pd.DataFrame(post).to_excel(xw, sheet_name="Post", index=False)

        mem = []
        for r in result["post"]:
            for m in r["members"]:
                d = {"New franchise": r["code"], "Member (old)": m["code"], "Sites": round(m["sites"], 1)}
                d.update({k: m["kpis"][k] for k in kcols})
                mem.append(d)
        pd.DataFrame(mem).to_excel(xw, sheet_name="Post members", index=False)

        sites = pd.DataFrame(result["sites"]).rename(columns={"pre": "Old franchise", "post": "New franchise", "kpi": meta["primary"]})
        sites.to_excel(xw, sheet_name="Sites", index=False)
        if result.get("enclaves"):
            pd.DataFrame([{**e, "sites": ", ".join(e["sites"])} for e in result["enclaves"]]).to_excel(xw, sheet_name="Enclaves", index=False)
        if result.get("warnings"):
            pd.DataFrame(result["warnings"]).to_excel(xw, sheet_name="Warnings", index=False)

        for ws in xw.book.worksheets:
            for col in ws.columns:
                width = max(len(str(c.value or "")) for c in col[:200])
                ws.column_dimensions[col[0].column_letter].width = min(45, max(10, width + 2))
            ws.freeze_panes = "B2"
    buf.seek(0)
    return buf


def _kml_color(hex_color, alpha="99"):
    h = (hex_color or "#888888").lstrip("#")
    return f"{alpha}{h[4:6]}{h[2:4]}{h[0:2]}"


def _coords(ring):
    return " ".join(f"{x:.5f},{y:.5f},0" for x, y in ring)


def _polys(geometry):
    if geometry["type"] == "Polygon":
        return [geometry["coordinates"]]
    if geometry["type"] == "MultiPolygon":
        return geometry["coordinates"]
    return []


def _op_kind(op):
    o = str(op or "").lower()
    if "ufone" in o:
        return "ufone"
    if "telenor" in o or o.strip() in ("tp", "t") or "tpl" in o:
        return "tp"
    return "other"


def _n(v, dec=None):
    if v is None:
        return "—"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return escape(str(v))
    if dec is None:
        dec = 2 if abs(v) < 10 else (1 if abs(v) < 100 else 0)
    return f"{v:,.{dec}f}"


def _pct(a, b):
    if a is None or b in (None, 0):
        return ""
    p = (a - b) / abs(b) * 100
    return "" if abs(p) < 0.05 else f"{'+' if p > 0 else ''}{p:.1f}%"


TH = 'style="background:#1f2a44;color:#fff;padding:3px 6px;text-align:{a};font-size:11px"'
TD = 'style="padding:2px 6px;border-bottom:1px solid #ddd;text-align:{a};font-size:11px;white-space:nowrap"'


def _table(head, rows, first_left=True):
    h = "".join(f"<th {TH.format(a='left' if i == 0 and first_left else 'right')}>{escape(str(c))}</th>" for i, c in enumerate(head))
    body = ""
    for i, r in enumerate(rows):
        bg = ' bgcolor="#f4f5f8"' if i % 2 else ""
        body += f"<tr{bg}>" + "".join(f"<td {TD.format(a='left' if j == 0 and first_left else 'right')}>{c}</td>" for j, c in enumerate(r)) + "</tr>"
    return f'<table cellspacing="0" style="border-collapse:collapse;margin:4px 0">{"<tr>" + h + "</tr>"}{body}</table>'


def _data(name, value):
    return f'<Data name="{escape(name)}"><value>{escape("" if value is None else str(value))}</value></Data>'


def _poly_xml(geometry, label_at=None):
    out = []
    if label_at and label_at[0] is not None:
        out.append(f"<Point><coordinates>{label_at[0]:.5f},{label_at[1]:.5f},0</coordinates></Point>")
    for poly in _polys(geometry):
        out.append("<Polygon><tessellate>1</tessellate><outerBoundaryIs><LinearRing><coordinates>" + _coords(poly[0]) + "</coordinates></LinearRing></outerBoundaryIs>")
        for hole in poly[1:]:
            out.append("<innerBoundaryIs><LinearRing><coordinates>" + _coords(hole) + "</coordinates></LinearRing></innerBoundaryIs>")
        out.append("</Polygon>")
    return "<MultiGeometry>" + "".join(out) + "</MultiGeometry>"


def scenario_kml(scenario, result, post_only=False):
    """Complete KML: post + pre territories with full KPI tables, sites with their KPIs, islands."""
    from django.conf import settings as _st
    circle = _st.PLANNER_CIRCLE_NAME
    meta, summ = result["meta"], result["summary"]
    kpis = meta["kpis"]
    derived = meta["derived"]
    agg_txt = "monthly average" if meta["agg"] == "avg" else "total over the period"
    months_txt = ", ".join(meta["months"])
    geo = result.get("geo") or {}
    stats = geo.get("stats", {})
    site_kpis = result.get("site_kpis") or {}
    post_rows = {r["code"]: r for r in result["post"]}
    pre_rows = {r["code"]: r for r in result["pre"]}

    # main region of each franchise (most sites) for folder grouping
    reg_count = {}
    for s in result["sites"]:
        for side in ("pre", "post"):
            reg_count.setdefault((side, s[side]), {}).setdefault(s["region"] or "Unknown", 0)
            reg_count[(side, s[side])][s["region"] or "Unknown"] += 1
    main_region = {k: max(v, key=v.get) for k, v in reg_count.items()}

    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
           f"<name>{escape(scenario.name)} — pre/post franchise plan ({escape(meta.get('company', 'Ufone + TP'))})</name><open>1</open>"]
    reviews = result.get("reviews") or {}
    rev_txt = " · ".join(f"{k.upper()}: {len(v)}" for k, v in reviews.items()) or "none"
    doc_rows = [
        ["Months", escape(months_txt)], ["Values", agg_txt],
        ["Franchises", f"{summ['pre_franchises']} → {summ['post_franchises']}"], ["Sites", _n(summ["sites"], 0)],
        ["Avg sites / franchise", f"{_n(summ['pre_avg_sites'], 1)} → {_n(summ['post_avg_sites'], 1)}"],
        ["Coverage radius", f"{stats.get('radius_km', scenario.coverage_km)} km"],
        ["Coverage", f"{stats.get('coverage_pct', '—')}%"], ["Reviewed", escape(rev_txt)],
    ]
    kpi_tot = [[escape(k["label"]), _n(summ["totals"][k["key"]]), _n(summ["projected_totals"][k["key"]]),
                _pct(summ["projected_totals"][k["key"]], summ["totals"][k["key"]])] for k in kpis]
    out.append("<description><![CDATA[" + _table(["Scenario", escape(scenario.name)], doc_rows)
               + _table(["KPI (all sites)", "Baseline", "Projected", "Δ"], kpi_tot) + "]]></description>")

    # styles
    colors = {}
    for side in ("pre", "post"):
        for f in geo.get(side, {}).get("features", []):
            colors.setdefault(f["properties"]["color"], f"s{len(colors)}")
    for c, sid in colors.items():
        out.append(f'<Style id="{sid}"><IconStyle><scale>0</scale></IconStyle><LabelStyle><scale>0.85</scale></LabelStyle>'
                   f'<LineStyle><color>{_kml_color(c, "ff")}</color><width>2.5</width></LineStyle>'
                   f"<PolyStyle><color>{_kml_color(c, '70')}</color></PolyStyle></Style>"
                   f'<Style id="{sid}p"><IconStyle><color>{_kml_color(c, "ff")}</color><scale>0.55</scale>'
                   f"<Icon><href>http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png</href></Icon></IconStyle>"
                   f"<LabelStyle><scale>0</scale></LabelStyle></Style>")
        out.append(f'<Style id="{sid}t"><IconStyle><color>{_kml_color(c, "ff")}</color><scale>0.6</scale>'
                   f"<Icon><href>http://maps.google.com/mapfiles/kml/shapes/triangle.png</href></Icon></IconStyle>"
                   f"<LabelStyle><scale>0</scale></LabelStyle></Style>")
    for lvl, col, w in (("big", "#000000", 7), ("region", "#c81e1e", 5), ("bm", "#7a1fa2", 4), ("arm", "#111111", 3), ("district", "#1f2a44", 3), ("tehsil", "#0b6e4f", 2)):
        out.append(f'<Style id="u_{lvl}"><IconStyle><scale>0</scale></IconStyle><LabelStyle><scale>{0.8 + w / 10:.1f}</scale></LabelStyle>'
                   f'<LineStyle><color>{_kml_color(col, "ff")}</color><width>{w}</width></LineStyle><PolyStyle><fill>0</fill></PolyStyle></Style>')
    out.append('<Style id="island"><LineStyle><color>ff1e1ec8</color><width>2</width></LineStyle><PolyStyle><color>551e1ec8</color></PolyStyle></Style>')

    def kpi_block(row, members=None):
        """KPI rows x (members..., consolidated, projected, Δ)."""
        if members:
            head = ["KPI"] + [m["code"] for m in members] + ["Consolidated", "Projected", "Δ"]
            rows = [["Sites"] + [_n(m["sites"], 0) for m in members] + [_n(row["sites"], 0), "", ""]]
            for k in kpis:
                key = k["key"]
                rows.append([escape(k["label"])] + [_n(m["kpis"][key]) for m in members]
                            + [f"<b>{_n(row['kpis'][key])}</b>", _n(row["projected"][key]), _pct(row["projected"][key], row["kpis"][key])])
            for d in derived:
                lbl = d["label"]
                rows.append([escape(lbl)] + [_n(m["derived"].get(lbl)) for m in members]
                            + [f"<b>{_n(row['derived'].get(lbl))}</b>", _n(row["projected_derived"].get(lbl)),
                               _pct(row["projected_derived"].get(lbl), row["derived"].get(lbl))])
            return _table(head, rows)
        rows = [["Sites", _n(row["sites"], 0)]] + [[escape(k["label"]), _n(row["kpis"][k["key"]])] for k in kpis] \
            + [[escape(d["label"]), _n(row["derived"].get(d["label"]))] for d in derived]
        return _table(["KPI", "Value"], rows)

    def info_block(row):
        ops = row.get("operators", {})
        rows = [["Sites", f"{_n(row['sites'], 0)} (Telenor {ops.get('Telenor', 0)} · Ufone {ops.get('Ufone', 0)})"],
                ["Regions", escape(", ".join(row["regions"]))], ["Districts", escape(", ".join(row["districts"]))],
                ["BMs", escape(", ".join(row["bms"]))], ["ARM/ASMs", escape(", ".join(row["arms"]))]]
        return _table(["", ""], rows)

    units = geo.get("units", {})
    unit_geo = {lvl: {f["properties"]["name"]: f for f in fc.get("features", [])} for lvl, fc in units.items()}
    pieces = {}
    for f in geo.get("arm_pieces", {}).get("features", []):
        pieces.setdefault(f["properties"]["arm"], []).append(f)

    def unit_body(level_name, name, sts):
        ops = {}
        pre, post = {}, {}
        tot = {k["key"]: 0.0 for k in kpis}
        for st in sts:
            ops[st["operator"]] = ops.get(st["operator"], 0) + 1
            pre[st["pre"]] = pre.get(st["pre"], 0) + 1
            post[st["post"]] = post.get(st["post"], 0) + 1
            sk = site_kpis.get(st["site_id"], {})
            for k in kpis:
                tot[k["key"]] += sk.get(k["key"]) or 0.0
        rows = [["Sites", f"{len(sts)} (Telenor {ops.get('Telenor', 0)} · Ufone {ops.get('Ufone', 0)})"],
                ["Franchises", f"{len(pre)} current → {len(post)} planned"],
                ["Current", escape(", ".join(f"{c} ({n})" for c, n in sorted(pre.items(), key=lambda x: -x[1])))],
                ["Planned", escape(", ".join(f"{c} ({n})" for c, n in sorted(post.items(), key=lambda x: -x[1])))]]
        kt = [[escape(k["label"]), _n(tot[k["key"]])] for k in kpis]
        body = (f"<h3 style='margin:0'>{escape(level_name)}: {escape(name)}</h3><div>{escape(agg_txt)} · {escape(months_txt)}</div>"
                + _table(["", ""], rows) + "<b>KPIs (consolidated)</b>" + _table(["KPI", "Total"], kt))
        ext = [_data("Level", level_name), _data("Name", name), _data("Sites", len(sts)),
               _data("Current franchises", len(pre)), _data("Planned franchises", len(post))]
        ext += [_data(k["label"], round(tot[k["key"]], 2)) for k in kpis]
        return body, "".join(ext)

    def unit_placemark(level, level_name, name, sts):
        f = unit_geo.get(level, {}).get(name)
        body, ext = unit_body(level_name, name, sts)
        geom = _poly_xml(f["geometry"]) if f else ""
        vis = "<visibility>0</visibility>" if level in ("district", "tehsil") else ""
        out.append(f"<Placemark><name>{escape(level_name)}: {escape(name)}</name>{vis}<styleUrl>#u_{level}</styleUrl>"
                   f"<description><![CDATA[{body}]]></description><ExtendedData>{ext}</ExtendedData>{geom}</Placemark>")

    def hierarchy_folders():
        sts = result["sites"]
        # sales hierarchy: Region > BM > ASM > planned franchise pieces
        out.append("<Folder><name>HIERARCHY — Big Region › Region › BM › ASM</name><description>Boundaries and consolidated KPIs per Big Region, Region, BM and ASM. "
                   "Each ASM folder also has the planned-franchise pieces inside it.</description>")
        for big in sorted({st.get("big_region") or circle for st in sts}):
          big_sites = [st for st in sts if (st.get("big_region") or circle) == big]
          out.append(f"<Folder><name>{escape(big)} ({len(big_sites)} sites)</name>")
          unit_placemark("big", "Big Region", big, big_sites)
          tree = {}
          for st in big_sites:
            tree.setdefault(st["region"] or "Unknown", {}).setdefault(st["bm"] or "(no BM)", {}).setdefault(st["arm"] or "(no ASM)", []).append(st)
          for reg in sorted(tree):
            reg_sites = [x for b in tree[reg].values() for a in b.values() for x in a]
            out.append(f"<Folder><name>{escape(reg)} ({len(reg_sites)} sites)</name>")
            unit_placemark("region", "Region", reg, reg_sites)
            for bm in sorted(tree[reg]):
                bm_sites = [x for a in tree[reg][bm].values() for x in a]
                out.append(f"<Folder><name>BM {escape(bm)} ({len(bm_sites)} sites)</name>")
                unit_placemark("bm", "BM", bm, bm_sites)
                for arm in sorted(tree[reg][bm]):
                    arm_sites = tree[reg][bm][arm]
                    out.append(f"<Folder><name>ASM {escape(arm)} ({len(arm_sites)} sites)</name>")
                    unit_placemark("arm", "ASM", arm, arm_sites)
                    pcs = [] if post_only else pieces.get(arm, [])
                    if pcs:
                        out.append("<Folder><name>Planned franchises in this ASM</name><visibility>0</visibility>")
                        for f in sorted(pcs, key=lambda x: -x["properties"]["sites"]):
                            p = f["properties"]
                            sub = [x for x in arm_sites if x["post"] == p["code"]]
                            body, ext = unit_body("Planned franchise in ASM " + arm, p["code"], sub)
                            out.append(f"<Placemark><name>{escape(p['code'])} ({p['sites']})</name><visibility>0</visibility>"
                                       f"<styleUrl>#{colors.get(p['color'], 's0')}</styleUrl><description><![CDATA[{body}]]></description>"
                                       f"<ExtendedData>{ext}</ExtendedData>{_poly_xml(f['geometry'])}</Placemark>")
                        out.append("</Folder>")
                    out.append("</Folder>")
                out.append("</Folder>")
            out.append("</Folder>")
          out.append("</Folder>")
        out.append("</Folder>")
        if post_only:
            return
        # geography: District > City
        gtree = {}
        for st in sts:
            gtree.setdefault(st["district"] or "Unknown", {}).setdefault(st["tehsil"] or "Unknown", []).append(st)
        out.append("<Folder><name>GEOGRAPHY — District › City</name><visibility>0</visibility>")
        for d in sorted(gtree):
            d_sites = [x for t in gtree[d].values() for x in t]
            out.append(f"<Folder><name>{escape(d)} ({len(d_sites)} sites)</name><visibility>0</visibility>")
            unit_placemark("district", "District", d, d_sites)
            for t in sorted(gtree[d]):
                unit_placemark("tehsil", "City", f"{d} / {t}", gtree[d][t])
            out.append("</Folder>")
        out.append("</Folder>")

    def reference_folders():
        """PTA district borders and comparison layers (e.g. Zong), as outlines (hidden by default)."""
        import json as _json

        from planner.models import BoundaryLayer
        from planner.services import boundary_check
        from planner.services import geo as _geo

        out.append('<Style id="ref_pta"><LineStyle><color>ff442a1f</color><width>2.5</width></LineStyle><PolyStyle><fill>0</fill></PolyStyle></Style>'
                   '<Style id="ref_cmp"><LineStyle><color>ffbf2f6b</color><width>2.5</width></LineStyle><PolyStyle><fill>0</fill></PolyStyle></Style>')
        layer = _geo.active_district_layer()
        if layer:
            gj = _json.loads(_geo.raw_geojson("district"))
            out.append(f"<Folder><name>PTA DISTRICTS — {escape(layer.label)}</name><visibility>0</visibility>")
            for f in gj["features"]:
                out.append(f"<Placemark><name>{escape(f['properties']['name'])}</name><visibility>0</visibility><styleUrl>#ref_pta</styleUrl>{_poly_xml(f['geometry'])}</Placemark>")
            out.append("</Folder>")
        for cl in BoundaryLayer.objects.filter(role="compare"):
            gj = boundary_check.zones_geojson(cl)
            out.append(f"<Folder><name>COMPARE — {escape(cl.label)}</name><visibility>0</visibility>")
            for f in gj["features"]:
                out.append(f"<Placemark><name>{escape(f['properties']['name'])}</name><visibility>0</visibility><styleUrl>#ref_cmp</styleUrl>{_poly_xml(f['geometry'])}</Placemark>")
            out.append("</Folder>")

    def territory_folder(side, title):
        feats = geo.get(side, {}).get("features", [])
        by_region = {}
        for f in feats:
            by_region.setdefault(main_region.get((side, f["properties"]["code"]), "Unknown"), []).append(f)
        out.append(f"<Folder><name>{title}</name>{'<open>1</open>' if side == 'post' else '<visibility>0</visibility>'}")
        for region in sorted(by_region):
            out.append(f"<Folder><name>{escape(region)}</name>")
            for f in sorted(by_region[region], key=lambda x: -x["properties"]["sites"]):
                p = f["properties"]
                row = (post_rows if side == "post" else pre_rows).get(p["code"])
                if not row:
                    continue
                geo_rows = [["Territory", f"{_n(p['area_km2'])} km²"], ["Covered (≤ radius)", f"{_n(p['covered_km2'])} km²"],
                            ["Island-filled", f"{_n(p['filled_km2'])} km²"], ["Separate pieces", p["fragments"]]]
                if side == "post":
                    mem = row["members"]
                    head = f"<h3 style='margin:0'>{escape(p['code'])}</h3><div>Planned franchise · {len(mem)} franchise(s) merged · {escape(agg_txt)} · {escape(months_txt)}</div>"
                    body = head + info_block(row) + "<b>Merged franchises</b>" + _table(
                        ["Franchise", "Sites", "Share of sites"],
                        [[escape(m["code"]), _n(m["sites"], 0), f"{m['sites'] / row['sites'] * 100:.0f}%" if row["sites"] else ""] for m in mem]) \
                        + "<b>KPIs: members → consolidated → projected</b>" + kpi_block(row, mem) + _table(["Geography", ""], geo_rows)
                    ext = [_data("Franchise", p["code"]), _data("Type", "Post (planned)"), _data("Sites", round(row["sites"], 1)),
                           _data("Members", ", ".join(m["code"] for m in mem)), _data("Districts", ", ".join(row["districts"])),
                           _data("BMs", ", ".join(row["bms"])), _data("ARM/ASMs", ", ".join(row["arms"]))]
                    ext += [_data(k["label"], round(row["kpis"][k["key"]], 2)) for k in kpis]
                    ext += [_data(k["label"] + " (projected)", round(row["projected"][k["key"]], 2)) for k in kpis]
                else:
                    into = ", ".join(f"{c} ({n} sites)" for c, n in row["mapped_to"].items())
                    body = f"<h3 style='margin:0'>{escape(p['code'])}</h3><div>Current franchise → <b>{escape(into)}</b> · {escape(agg_txt)}</div>" \
                        + info_block(row) + kpi_block(row) + _table(["Geography", ""], geo_rows)
                    ext = [_data("Franchise", p["code"]), _data("Type", "Pre (current)"), _data("Planned into", into),
                           _data("Sites", round(row["sites"], 1))]
                    ext += [_data(k["label"], round(row["kpis"][k["key"]], 2)) for k in kpis]
                vis = "<visibility>0</visibility>" if side == "pre" else ""
                out.append(f"<Placemark><name>{escape(p['code'])}</name>{vis}<styleUrl>#{colors[p['color']]}</styleUrl>"
                           f"<description><![CDATA[{body}]]></description><ExtendedData>{''.join(ext)}</ExtendedData>"
                           f"{_poly_xml(f['geometry'], (p.get('lx'), p.get('ly')))}</Placemark>")
            out.append("</Folder>")
        out.append("</Folder>")

    territory_folder("post", "POST — planned franchise boundaries")
    hierarchy_folders()
    reference_folders()
    if post_only:
        out.append("</Document></kml>")
        return "\n".join(out)
    territory_folder("pre", "PRE — current franchises (hidden; tick to compare)")

    # sites by Region > BM > ASM
    color_of = {f["properties"]["code"]: f["properties"]["color"] for f in geo.get("post", {}).get("features", [])}
    klabels = [(k["key"], k["label"]) for k in kpis]
    stree = {}
    for st in result["sites"]:
        stree.setdefault(f'{st.get("big_region") or circle} › {st["region"] or "Unknown"}', {}).setdefault(st["bm"] or "(no BM)", {}).setdefault(st["arm"] or "(no ASM)", []).append(st)
    out.append("<Folder><name>SITES — Big Region › Region › BM › ASM</name><visibility>0</visibility>")
    for reg in sorted(stree):
        out.append(f"<Folder><name>{escape(reg)}</name><visibility>0</visibility>")
        for bm in sorted(stree[reg]):
            out.append(f"<Folder><name>BM {escape(bm)}</name><visibility>0</visibility>")
            for arm in sorted(stree[reg][bm]):
                lst = stree[reg][bm][arm]
                out.append(f"<Folder><name>ASM {escape(arm)} ({len(lst)})</name><visibility>0</visibility>")
                for st in lst:
                    if st["latitude"] is None or st["longitude"] is None:
                        continue
                    sid = colors.get(color_of.get(st["post"], ""), "")
                    ext = [_data("Site", st["site_id"]), _data("Operator", st["operator"]), _data("Symbol", "▲ TP" if _op_kind(st["operator"]) == "tp" else "● Ufone" if _op_kind(st["operator"]) == "ufone" else "■ other"), _data("Current franchise", st["pre"]),
                           _data("Planned franchise", st["post"]), _data("Moved individually", "yes" if st["overridden"] else "no"),
                           _data("Big Region", st.get("big_region") or circle), _data("Region", st["region"]), _data("Area", st.get("area") or ""), _data("BM", st["bm"]), _data("ARM/ASM", st["arm"]),
                           _data("District", st["district"]), _data("City/Tehsil", st["tehsil"]),
                           _data("Type", st["site_type"]), _data("Status", st["status"])]
                    sk = site_kpis.get(st["site_id"], {})
                    ext += [_data(lbl, sk.get(key)) for key, lbl in klabels]
                    out.append(f"<Placemark><name>{escape(str(st['site_id']))}</name><visibility>0</visibility>"
                               + (f"<styleUrl>#{sid}{'t' if _op_kind(st['operator']) == 'tp' else 'p'}</styleUrl>" if sid else "")
                               + f"<ExtendedData>{''.join(ext)}</ExtendedData>"
                               f"<Point><coordinates>{st['longitude']:.5f},{st['latitude']:.5f},0</coordinates></Point></Placemark>")
                out.append("</Folder>")
            out.append("</Folder>")
        out.append("</Folder>")
    out.append("</Folder>")

    out.append("</Document></kml>")
    return "\n".join(out)


def seed_from_mocan(scenario, month_key=None):
    """Pre-fill a scenario from the 'Franchise ID After MOCAN' column.

    Each old franchise is tagged to the post-MOCAN franchise most of its sites
    carry; sites that point elsewhere become single-site overrides.
    """
    months = resolve_months([month_key] if month_key else None)
    if not months:
        return 0, 0
    qs = Site.objects.filter(month=months[-1]).exclude(franchise_after_mocan="").values_list(
        "site_id", "franchise_code", "franchise_after_mocan")
    df = pd.DataFrame(list(qs), columns=["site", "pre", "post"])
    if df.empty:
        return 0, 0
    majority = df.groupby("pre")["post"].agg(lambda s: s.value_counts().index[0])
    n_map = n_ov = 0
    for pre, post in majority.items():
        if pre != post:
            FranchiseMapping.objects.update_or_create(scenario=scenario, source_code=pre, defaults={"target_code": post, "note": "seeded from MOCAN"})
            n_map += 1
    for site, pre, post in df.itertuples(index=False):
        if post != majority[pre]:
            SiteOverride.objects.update_or_create(scenario=scenario, site_id=site, defaults={"target_code": post, "note": "seeded from MOCAN"})
            n_ov += 1
    scenario.save()
    return n_map, n_ov
