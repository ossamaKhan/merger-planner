"""Generate a SYNTHETIC demo workbook in the same layout as the real Sheet1.

Numbers and codes are made up; only the district centres are real places.
    python manage.py make_sample_data sample_sites.xlsx --months 2026-08 2026-09 2026-10
"""
import math
import random

import pandas as pd
from django.core.management.base import BaseCommand

AREAS = [  # region, district, lat, lon, spread_deg, sites, tp_franchises, ufone_franchises
    ("Central 5", "Faisalabad", 31.418, 73.079, 0.16, 520, 6, 5),
    ("Central 5", "Toba Tek Singh", 30.975, 72.483, 0.14, 150, 2, 2),
    ("Central 6", "Multan", 30.196, 71.475, 0.13, 430, 7, 6),
    ("Central 6", "Khanewal", 30.301, 71.932, 0.15, 170, 2, 2),
    ("Central 7", "Bahawalpur", 29.395, 71.683, 0.15, 260, 3, 3),
    ("Central 7", "Lodhran", 29.538, 71.633, 0.10, 90, 1, 1),
]
KPIS = ["FCA.", "Act 90D", "Act 30D", "Act 90D (4G)", "HVC Base", "Total Rev Amt", "BVS Retailer", "EVC Retailer",
        "Outgoing Minutes", "Volume (GBs)", "DATA (4G)", "FCA (Adjusted)", "Total Revival", "Gross Churn", "Net Adds",
        "Avg. Daily Active", "Active Rechargers", "MO Revenue", "MNP FCA", "Handset 4G", "Conventional Recharge",
        "Total Recharge"]
NAMES = ["Imran Mehmood", "Ali Raza", "Sana Tariq", "Bilal Ahmad", "Hina Akhtar", "Usman Ghani", "Fahad Iqbal"]


class Command(BaseCommand):
    help = "Create a synthetic multi-month site workbook for trying the planner."

    def add_arguments(self, parser):
        parser.add_argument("out", nargs="?", default="sample_sites.xlsx")
        parser.add_argument("--months", nargs="+", default=["2026-08", "2026-09", "2026-10"])
        parser.add_argument("--seed", type=int, default=7)

    def handle(self, out, months, seed, **kw):
        rnd = random.Random(seed)
        base = []
        ufone_no = 1
        for region, dist, lat, lon, spread, n, n_tp, n_uf in AREAS:
            code3 = dist[:3].upper()
            frs = [("Telenor", f"DDF{code3}30{i+1:02d}") for i in range(n_tp)] + \
                  [("Ufone", f"UFRSMD{ufone_no + i:04d}") for i in range(n_uf)]
            ufone_no += n_uf
            # each franchise owns a sector of the district (angle band) so territories look realistic
            prod = {f: rnd.uniform(0.7, 1.35) for _, f in frs}
            for k in range(n):
                op, fr = frs[k % len(frs)]
                ang = (frs.index((op, fr)) + rnd.random()) / len(frs) * 6.283
                r = abs(rnd.gauss(0, spread)) * (0.35 if rnd.random() < 0.65 else 1.0)
                la, lo = lat + r * math.sin(ang), lon + r * math.cos(ang) * 1.15
                sid = f"{code3}{k:04d}" if op == "Telenor" else f"U{code3}{k:04d}"
                mocan = rnd.random() < 0.3
                after = frs[(frs.index((op, fr)) + (1 if rnd.random() < 0.15 else 0)) % len(frs)][1] if mocan else ""
                scale = prod[fr] * (1.6 if r < spread * 0.35 else 0.8)
                # one BM per district, one ASM per 3 franchise sectors (geographically contiguous)
                bm = f"{NAMES[AREAS.index((region, dist, lat, lon, spread, n, n_tp, n_uf)) % len(NAMES)]} ({dist})"
                arm = f"ASM {dist} {frs.index((op, fr)) // 3 + 1}"
                base.append(dict(op=op, sid=sid, region=region, fr=fr, dist=dist, lat=la, lon=lo,
                                 bm=bm, arm=arm, mocan=mocan, after=after, scale=scale))
                if mocan and rnd.random() < 0.5:  # co-located site of the other network
                    other = next(((o, f) for o, f in frs if o != op), (op, fr))
                    base.append(dict(op=other[0], sid=sid + "M", region=region, fr=other[1], dist=dist, lat=la, lon=lo,
                                     bm=bm, arm=f"ASM {dist} {frs.index(other) // 3 + 1}", mocan=True, after=after or fr, scale=scale * 0.7))

        rows = []
        for mi, m in enumerate(months):
            growth = 1 + 0.025 * mi
            for s in base:
                act90 = rnd.uniform(2500, 7000) * s["scale"] * growth
                act30 = act90 * rnd.uniform(0.72, 0.85)
                rev = act90 * rnd.uniform(110, 150)
                churn = act30 * rnd.uniform(0.04, 0.09) / growth
                fca = rnd.uniform(60, 240) * s["scale"] * growth
                vals = {
                    "FCA.": fca, "Act 90D": act90, "Act 30D": act30, "Act 90D (4G)": act90 * rnd.uniform(0.5, 0.7),
                    "HVC Base": act90 * rnd.uniform(0.12, 0.2), "Total Rev Amt": rev, "BVS Retailer": rnd.randint(0, 6),
                    "EVC Retailer": rnd.randint(0, 10), "Outgoing Minutes": act90 * rnd.uniform(90, 140),
                    "Volume (GBs)": act90 * rnd.uniform(2.5, 4.5), "DATA (4G)": act90 * rnd.uniform(2.4, 4.4),
                    "FCA (Adjusted)": fca * 0.9, "Total Revival": churn * rnd.uniform(0.4, 0.8), "Gross Churn": churn,
                    "Net Adds": fca - churn * 0.3, "Avg. Daily Active": act30 * 0.55, "Active Rechargers": act30 * 0.6,
                    "MO Revenue": rev * 0.35, "MNP FCA": fca * 0.03, "Handset 4G": act90 * 0.45,
                    "Conventional Recharge": rev * 0.6, "Total Recharge": rev * 1.5,
                }
                rows.append({
                    "Big Region": "Central B", "Month": m, "Operator": s["op"], "Sites": s["sid"], "Region": s["region"], "Franchises": s["fr"],
                    "BM": s["bm"], "ARM/ASM": s["arm"], "latitude": round(s["lat"], 5), "longitude": round(s["lon"], 5),
                    "Types": "MOCAN" if s["mocan"] else "Normal", "Mocan Date": "8/31/2026" if s["mocan"] else None,
                    "Franchise ID After MOCAN": s["after"], "Stauts": "Active", "TP MOCAN": int(s["mocan"] and s["op"] == "Telenor"),
                    "Ufone MOCAN": int(s["mocan"]), "Ufone MOCAN Fr": s["after"] if s["mocan"] else "", "TP MOCAN Fr": s["fr"] if s["op"] == "Telenor" else "",
                    "District": s["dist"], **{k: round(v, 2) for k, v in vals.items()},
                })
        df = pd.DataFrame(rows)
        with pd.ExcelWriter(out) as xw:
            df.to_excel(xw, sheet_name="Sheet1", index=False)
            pd.DataFrame({"Note": ["Synthetic demo data generated by make_sample_data"]}).to_excel(xw, sheet_name="Sheet2", index=False)
            pd.DataFrame({"Note": [""]}).to_excel(xw, sheet_name="Sheet3", index=False)
        self.stdout.write(self.style.SUCCESS(f"Wrote {len(df)} rows ({len(base)} sites x {len(months)} months) to {out}"))
