"""Import the monthly site workbook (Sheet1) into the database.

Identity columns are matched case/space-insensitively (so "Stauts" and
"Status" both work). Every other numeric column becomes a KPI automatically,
so new KPI columns in next month's file need no code change.
"""
import re
from datetime import date

import pandas as pd
from django.db import transaction

from planner.models import DataMonth, Franchise, Site
from planner.services import colors, geo


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


# model field -> accepted header spellings (normalised)
IDENTITY = {
    "operator": ["operator"],
    "site_id": ["sites", "site", "siteid", "sitecode"],
    "big_region": ["bigregion", "circle", "zone", "bigregions", "bregion", "circlename"],
    "region": ["region"],
    "bu": ["bu", "businessunit"],
    "city": ["city"],
    "franchise_code": ["franchises", "franchise", "franchiseid", "franchisecode"],
    "bm": ["bm"],
    "arm": ["armasm", "arm", "asm"],
    "latitude": ["latitude", "lat"],
    "longitude": ["longitude", "long", "lon", "lng"],
    "site_type": ["types", "type", "sitetype"],
    "mocan_date": ["mocandate", "mocndate"],
    "franchise_after_mocan": ["franchiseidaftermocan", "franchiseaftermocan", "franchiseidaftermocn"],
    "status": ["stauts", "status"],
    "tp_mocan": ["tpmocan", "tpmocn"],
    "ufone_mocan": ["ufonemocan", "ufonemocn"],
    "ufone_mocan_fr": ["ufonemocanfr", "ufonemocnfr"],
    "tp_mocan_fr": ["tpmocanfr", "tpmocnfr"],
    "district": ["district"],
    "month": ["month", "period", "monthyear", "yearmonth", "mon", "mth", "reportmonth", "datamonth", "monthname",
              "date", "reportdate", "datadate", "asondate", "ason", "monthid"],
}
REQUIRED = ["site_id", "franchise_code", "latitude", "longitude"]


def parse_month(value):
    if value is None or (isinstance(value, float) and pd.isna(value)) or value == "":
        return None
    ts = pd.to_datetime(str(value) + ("-01" if re.fullmatch(r"\d{4}-\d{1,2}", str(value)) else ""), errors="coerce")
    if pd.isna(ts):
        return None
    return date(ts.year, ts.month, 1)


MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def month_from_filename(name):
    """'Sites Oct 2026.xlsx', 'data_2026-10.xlsx', 'Oct26', '10-2026' -> date(2026, 10, 1)."""
    n = (name or "").lower()
    m = re.search(r"(20\d\d)[-_ .]?(0[1-9]|1[0-2])(?!\d)", n)
    if m:
        return date(int(m.group(1)), int(m.group(2)), 1)
    m = re.search(r"(?<!\d)(0?[1-9]|1[0-2])[-_ .](20\d\d)", n)
    if m:
        return date(int(m.group(2)), int(m.group(1)), 1)
    m = re.search(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[-_ .']*(20\d\d|\d\d)(?!\d)", n)
    if m:
        y = int(m.group(2))
        return date(y if y > 99 else 2000 + y, MONTHS[m.group(1)], 1)
    return None


def read_workbook(file_obj_or_path, sheet_name=None):
    xls = pd.ExcelFile(file_obj_or_path)
    sheet = sheet_name if sheet_name in xls.sheet_names else ("Sheet1" if "Sheet1" in xls.sheet_names else xls.sheet_names[0])
    return xls.parse(sheet), sheet, xls.sheet_names


def _clean_str(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def import_dataframe(df, month=None, source_file=""):
    """Import a dataframe. `month` (date) is used unless the sheet has a Month column."""
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    lookup = {_norm(c): c for c in df.columns}
    colmap = {}
    for field, names in IDENTITY.items():
        for n in names:
            if n in lookup:
                colmap[field] = lookup[n]
                break
    missing = [f for f in REQUIRED if f not in colmap]
    if missing:
        raise ValueError(f"Missing required column(s): {', '.join(missing)}. Found: {', '.join(df.columns)}")

    identity_cols = set(colmap.values())
    kpi_cols = []
    for c in df.columns:
        if c in identity_cols:
            continue
        num = pd.to_numeric(df[c], errors="coerce")
        if num.notna().sum() >= max(1, int(0.5 * df[c].notna().sum())):
            kpi_cols.append(c)
            df[c] = num.fillna(0.0)

    df = df[df[colmap["site_id"]].notna()]
    df["__lat"] = pd.to_numeric(df[colmap["latitude"]], errors="coerce")
    df["__lon"] = pd.to_numeric(df[colmap["longitude"]], errors="coerce")

    # Month: from a Month/Period/Date column, else the chosen month, else the file name, else this month
    month_source = ""
    if "month" in colmap:
        df["__month"] = df[colmap["month"]].map(parse_month)
        month_source = f"column '{colmap['month']}'"
        fallback = month or month_from_filename(source_file)
        if df["__month"].isna().all() and fallback is None:
            fallback = date.today().replace(day=1)
        if fallback is not None:
            df["__month"] = df["__month"].fillna(fallback)
        df = df[df["__month"].notna()]
    else:
        if month is not None:
            df["__month"], month_source = month, "chosen on upload"
        elif month_from_filename(source_file):
            df["__month"], month_source = month_from_filename(source_file), "file name"
        else:
            df["__month"], month_source = date.today().replace(day=1), "upload date (no month column or month in file name)"

    tags = geo.tag_points(df["__lat"].tolist(), df["__lon"].tolist())

    def get(row, field):
        return _clean_str(row[colmap[field]]) if field in colmap else ""

    summary = {}
    with transaction.atomic():
        months = {}
        replaced = []
        for m in sorted(set(df["__month"])):
            dm, created = DataMonth.objects.get_or_create(month=m, defaults={"source_file": source_file, "kpi_columns": kpi_cols})
            sub = df[df["__month"] == m]
            if not created:
                # Replace only what this file covers: its regions (and its sites, if they moved region).
                # Uploading Central 5, 6 and 7 as separate files for the same month keeps all three.
                regs = sorted({_clean_str(v) for v in sub[colmap["region"]]}) if "region" in colmap else []
                if regs:
                    dm.sites.filter(region__in=regs).delete()
                    ids = [_clean_str(v) for v in sub[colmap["site_id"]]]
                    for i in range(0, len(ids), 500):
                        dm.sites.filter(site_id__in=ids[i:i + 500]).delete()
                    replaced.append(f"{m:%b %Y}: {', '.join(regs)}")
                else:
                    dm.sites.all().delete()
                    replaced.append(f"{m:%b %Y}: whole month")
                dm.kpi_columns = list(dict.fromkeys(list(dm.kpi_columns or []) + kpi_cols))
                files = [f for f in (dm.source_file or "").split(" + ") if f and f != source_file]
                dm.source_file = " + ".join(files + [source_file])[:255]
                dm.save(update_fields=["kpi_columns", "source_file"])
            months[m] = dm

        seen = set()
        dupes = 0
        objs = []
        fr_ops = {}
        for (idx, row), (gd, gt, prov) in zip(df.iterrows(), tags):
            m = row["__month"]
            sid = get(row, "site_id")
            if (m, sid) in seen:
                dupes += 1
                continue
            seen.add((m, sid))
            md = pd.to_datetime(row[colmap["mocan_date"]], errors="coerce") if "mocan_date" in colmap else None
            fr = get(row, "franchise_code") or "UNASSIGNED"
            op = get(row, "operator")
            fr_ops.setdefault(fr, {}).setdefault(op, 0)
            fr_ops[fr][op] += 1
            region = get(row, "region")
            objs.append(
                Site(
                    month=months[m],
                    site_id=sid,
                    operator=op,
                    big_region=get(row, "big_region"),
                    region=region,
                    bu=get(row, "bu") or region,
                    city=get(row, "city") or gt,
                    district=get(row, "district"),
                    geo_district=gd,
                    geo_tehsil=gt,
                    province=prov,
                    franchise_code=fr,
                    bm=get(row, "bm"),
                    arm=get(row, "arm"),
                    latitude=None if pd.isna(row["__lat"]) else float(row["__lat"]),
                    longitude=None if pd.isna(row["__lon"]) else float(row["__lon"]),
                    site_type=get(row, "site_type"),
                    status=get(row, "status"),
                    mocan_date=None if md is None or pd.isna(md) else md.date(),
                    franchise_after_mocan=get(row, "franchise_after_mocan"),
                    tp_mocan=get(row, "tp_mocan"),
                    ufone_mocan=get(row, "ufone_mocan"),
                    ufone_mocan_fr=get(row, "ufone_mocan_fr"),
                    tp_mocan_fr=get(row, "tp_mocan_fr"),
                    kpis={k: float(row[k]) for k in kpi_cols},
                )
            )
        Site.objects.bulk_create(objs, batch_size=2000)
        for dm in months.values():
            dm.row_count = dm.sites.count()
            dm.save(update_fields=["row_count"])

        # franchises found in the data (incl. post-MOCAN codes so they can be targets)
        extra = set(df[colmap["franchise_after_mocan"]].dropna().map(_clean_str)) if "franchise_after_mocan" in colmap else set()
        for code in set(fr_ops) | {c for c in extra if c}:
            ops = fr_ops.get(code, {})
            op = max(ops, key=ops.get) if ops else ""
            fr, created = Franchise.objects.get_or_create(code=code, defaults={"name": code, "operator": op})
            if not created and not fr.operator and op:
                fr.operator = op
                fr.save(update_fields=["operator"])
        colors.assign_missing_colors()

    unmatched = sum(1 for (gd, _, _), d in zip(tags, df[colmap["district"]] if "district" in colmap else [""] * len(tags))
                    if gd and _clean_str(d) and _norm(gd) != _norm(d))
    summary.update(
        rows=len(objs),
        months=[m.strftime("%b %Y") for m in months],
        kpi_columns=kpi_cols,
        no_coordinates=int(df["__lat"].isna().sum()),
        district_mismatches=unmatched,
        month_source=month_source,
        replaced=replaced,
        duplicates=dupes,
    )
    return summary


def retag_all_sites():
    sites = list(Site.objects.all().only("id", "latitude", "longitude"))
    tags = geo.tag_points([s.latitude for s in sites], [s.longitude for s in sites])
    for s, (gd, gt, prov) in zip(sites, tags):
        s.geo_district, s.geo_tehsil, s.province = gd, gt, prov
    Site.objects.bulk_update(sites, ["geo_district", "geo_tehsil", "province"], batch_size=2000)
    return len(sites)
