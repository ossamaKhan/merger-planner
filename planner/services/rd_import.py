"""RD file: CELL SITE (column B) + Final Franchise -> RDTag rows (replaces the previous RD upload)."""
import io
import re

import pandas as pd

from django.db import transaction


def _n(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _clean(v, n=64):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return "" if s.lower() in ("nan", "none", "-") else s[:n]


def read(raw, filename):
    low = filename.lower()
    if low.endswith(".csv"):
        frames = {"csv": pd.read_csv(io.BytesIO(raw), header=None, dtype=str)}
    else:
        frames = pd.read_excel(io.BytesIO(raw), sheet_name=None, header=None, dtype=str)
    for sheet, raw_df in frames.items():
        # header row = first row (within 15) containing CELL SITE and FINAL FRANCHISE
        for h in range(min(15, len(raw_df))):
            names = [_n(x) for x in raw_df.iloc[h].tolist()]
            if "finalfranchise" in names and any(x in ("cellsite", "siteid", "site") for x in names):
                df = raw_df.iloc[h + 1:].reset_index(drop=True)
                df.columns = [f"{c}" for c in raw_df.iloc[h].tolist()]
                return df, names, sheet
    raise ValueError("Couldn't find the header row. It needs columns 'CELL SITE' and 'Final Franchise'.")


def import_file(raw, filename):
    from ..models import RDTag
    df, names, sheet = read(raw, filename)

    def col(*keys, last=False):
        idx = [i for i, x in enumerate(names) if x in keys]
        return idx[-1 if last else 0] if idx else None
    c_site = col("cellsite", "siteid", "site")
    c_final = col("finalfranchise", last=True)
    get = lambda r, i, n=64: _clean(r.iloc[i], n) if i is not None else ""  # noqa: E731
    c = {
        "district": col("cellsitedistrict", "district"), "region": col("region"),
        "prev_tp": col("previouslytagfranchiseextp"), "prev_ufone": col("previouslytagfranchiseexufone"),
        "req_tp": col("requestedfranchiseextp"), "req_ufone": col("requestedfranchiseexufone"),
        "reason": col("reason"), "permanence": col("permanenttemporary", "permanence"),
    }
    rows, seen, dup, blank = [], set(), 0, 0
    for _, r in df.iterrows():
        sid = get(r, c_site).upper()
        if not sid:
            continue
        fin = get(r, c_final)
        if not fin:
            blank += 1
        if sid in seen:
            dup += 1
            rows = [x for x in rows if x.site_id != sid]  # keep the last row for a site
        seen.add(sid)
        rows.append(RDTag(site_id=sid, final_franchise=fin, source_file=filename[:255],
                          **{k: get(r, i, 255 if k == "reason" else 120 if k == "district" else 64) for k, i in c.items()}))
    if not rows:
        raise ValueError("No site rows found under the header.")
    with transaction.atomic():
        RDTag.objects.all().delete()
        RDTag.objects.bulk_create(rows, batch_size=1000)
    return {"rows": len(rows), "sheet": sheet, "duplicates": dup, "no_final": blank,
            "franchises": len({r.final_franchise for r in rows if r.final_franchise})}
