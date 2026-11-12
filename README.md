# MergeCo Franchise Planner (Django)

Plan how Telenor (TP) and Ufone franchises merge after MOCAN, and show HQ the
**pre vs post** picture: KPIs, projections, site counts, and side-by-side maps
of franchise territories with coverage radius and island filling.

## Quick start

```bash
python -m venv .venv && .venv\Scripts\activate        # Windows (source .venv/bin/activate on Linux/Mac)
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser                     # optional, for /admin
python manage.py runserver
```

Open http://127.0.0.1:8000 → **Upload data** → pick your workbook and the month it
belongs to → back to **Scenarios** → create a scenario → planner opens.

Command-line import works too (handy for back-filling many months):

```bash
python manage.py import_sites "G:\Planning\Sites_Oct26.xlsx" --month 2026-10
python manage.py import_sites "G:\Planning\Sites_Sep26.xlsx" --month 2026-09
```

Want to try it before using real data? A synthetic workbook is in
`sample_data/sample_sites.xlsx` (3 months, made-up numbers, real district
locations). Or generate your own: `python manage.py make_sample_data demo.xlsx`.


## Hosting on Vercel (via GitHub)

The project is Vercel-ready (`pyproject.toml`, `vercel.json`, `vercel_build.py`). Vercel finds `manage.py`,
installs the packages, runs `vercel_build.py` (creates/updates the tables and your login user), collects the
static files and serves the app as one function.

1. **Push to a PRIVATE GitHub repo** — the site data is confidential; never commit `db.sqlite3` (already in `.gitignore`).
2. **Supabase** → Project → *Connect* → copy the **Transaction pooler** connection string (port **6543**).
3. **Vercel** → *Add New… → Project* → import the repo → open *Environment Variables* and add:

   | Name | Value |
   |---|---|
   | `DATABASE_URL` | the Supabase pooler string (with your DB password filled in) |
   | `DJANGO_SECRET_KEY` | any long random text |
   | `PLANNER_ADMIN_USER` | the login name you want, e.g. `ossama` |
   | `PLANNER_ADMIN_PASSWORD` | a strong password |

   Then **Deploy**. Every page asks for that login (add more people under *Admin › Users*).
4. Move your work in: on your local copy open **Backup → Download**, then on the Vercel site **Backup → Restore**
   (or just upload your Excel files on *Upload data*).

Notes
* Vercel caps one request/response at 4.5 MB. The app compresses downloads (~10×) and sends uploads
  over 3 MB in pieces automatically, so normal Excel/KMZ/backup files work.
* Data lives in Supabase, so it survives redeploys. Each push to GitHub redeploys automatically.
* Optional: `PLANNER_CIRCLE_NAME`, `DJANGO_DEBUG=1` (only to see an error page while setting up).

## Your data

* Just pick the file: it reads **Sheet1** with all its data; nothing to choose.
  The month is detected automatically: from a `Month` / `Period` / `Date` column (one file can hold many
  months), else from the file name (`Sites Oct 2026.xlsx`, `data_2026-10.xlsx`, `Oct26`), else the
  current month. Re-uploading a month replaces it; months can be removed on the Upload page.
  All loaded months appear as chips in the planner and report: tick any combination.
* Required: `Sites`, `Franchises`, `latitude`, `longitude`.
* Recognised identity columns (case/spacing-insensitive, typos like `Stauts` ok):
  Operator, Region, BM, ARM/ASM, Types, Mocan Date, Franchise ID After MOCAN,
  Status, TP MOCAN, Ufone MOCAN, Ufone MOCAN Fr, TP MOCAN Fr, District, and optional
  `City` / `BU` (BU defaults to Region, City to the tehsil).
* **Islands tab** (planner, left side) lists stray sites: a site where at least 6 of its 8 nearest
  sites belong to one other planned franchise and at most 1 is its own. Red rings mark them on the
  maps. Each card says why, suggests the surrounding franchise (one click to tag), lets you pick
  any other franchise, zooms to the site (📍 Show) or keeps it as-is. "Tag all to their suggestion"
  does the lot (undo with ↶). Unmerged Ufone and TP franchises count as separate networks.
* **Area / neighbourhood names** (Cantt, Gulgasht, Model Town…). Every site gets an *Area*
  (pop-ups, tooltips, Islands cards, Consolidated table "Main areas", Excel, KML). Names are drawn on
  the maps by the app itself, so they show even when the online map tiles are blocked.
  * Built in: ~73,000 Punjab neighbourhoods, towns and villages (Who's On First / GeoNames, CC-BY).
    City neighbourhoods are patchy there, so add your own; yours always win.
  * Add on the map: **＋ Area name**, click the spot, type the name. Click a yellow name to rename/delete.
  * Or upload on **Boundaries › Area names**: Excel/CSV `Name, Latitude, Longitude[, City]`, or a
    KML/KMZ of pins or drawn shapes (sites inside a shape get its name; pins name sites within 2 km).
  * **🔍 Find area** above the maps jumps to any name. Your names are in the Backup file too.
* **Consolidated IDs (live)** table under the maps: every merged ID with its members, sites,
  Ufone/TP split, KPI now vs projected, grouped by Region / BM / ASM / District / City with
  subtotals. It refreshes after every tag; click a row for its deep dive; CSV download.
* **Company filter (Ufone / TP / Both)** — first control on the planner and report. Ufone shows only
  Ufone sites, their areas (territories rebuilt from Ufone sites alone), KPIs and franchises; TP the
  same for TP; Both is the merged view. Exports (KML/KMZ/Excel) and the report link follow it, and
  the KML file name gets `_Ufone` / `_TP`. Tagging still works in any company view.
* **Big Region** (first column, e.g. `Central B`; also read from `Circle` / `Zone`) is the top
  level above Region. It names the "all franchises" view, heads the report hierarchy
  (Big Region › Region › BM › ASM) and the KML folders, and gets its own outer border.
  With several Big Regions in the data, each one becomes selectable in the planner's region
  list and the report's *Showing* list. Files without the column are treated as `Central B`
  (env `PLANNER_CIRCLE_NAME`).
* **Every other numeric column becomes a KPI automatically** (FCA., Act 90D, …,
  Total Recharge). New columns next month need no code change.
* Derived KPIs are added when their inputs exist: Rev/Site, FCA/Site,
  Act 90D/Site, Churn % of Act 30D, 4G share, Recharge per active recharger,
  Data GB per Act 90D (edit `DERIVED` in `planner/services/engine.py`).

## Districts (PTA / administrative)

PTA does not publish district boundary polygons, so the app ships the official
administrative boundaries from **geoBoundaries (gbOpen, derived from OCHA /
Pakistan Bureau of Statistics)**:

* `planner/data/pak_districts.geojson` — 126 districts (ADM2), tagged with province
* `planner/data/pak_tehsils.geojson` — ~550 tehsils (ADM3), tagged with district and province; used as the City/Tehsil level

On import every site is placed in a district and tehsil by its coordinates
(point-in-polygon). Your own `District` column is kept; where the two disagree
it's listed under **Issues** so you can fix the source. These boundaries pre-date
some newer districts (e.g. Chiniot, Nankana Sahib, Talagang are inside their
parent districts). If HQ / PTA gives you a newer district layer, drop the GeoJSON
in `planner/data/` with the same file name (property `name`, optional `province`)
and run `python manage.py retag_sites`.

## How the planner works

**Work level by level, everything saves as you go.** The top bar is a navigator:

`[Region] Work at: ASM | BM | City | District | Region  ◀ [unit ▼] ▶  ✓ Reviewed, next   3/16 ASMs reviewed`

1. Start at **ASM**: the planner opens on the first ASM. Only that ASM's sites and
   franchises appear on the board, maps and tables. Maps still draw true
   borders, using the neighbouring sites of the same districts.
2. Merge on the **Merge board**: each box is a planned franchise, and the chips are the
   current TP/Ufone franchises in it (`54/210` = 54 sites here of 210 in total).
   * **Drag** a chip onto another box, or use the **Tag** form (`franchise → into`).
   * If the franchise also has sites outside the current ASM/BM/city, you're
     asked: **Whole franchise** (all its sites, every area) or **Only these N
     sites** (just this unit). Whole = franchise tag; only-these = site moves.
   * `×` on a chip undoes it; **↶ Undo** reverts the last change (tags or site moves).
   * *+ New franchise* (or "＋ new franchise code…" in the Tag form) creates a new post-merger code.
3. Press **✓ Reviewed, next**. The unit is marked reviewed and the next unreviewed one opens.
   When all ASMs are done you're prompted to move up to **BM**, then City, District, Region.
   Earlier decisions carry upward; nothing is lost when you switch level.

Single sites: pick a target in the *Move sites* bar and click sites on the POST
map (or drag a box), then *Save moves*. The deep dive also lets you tick and move sites.

**Deep dive** (click a box name, a POST territory, a legend entry or a table row):
every KPI for each member next to the combined, projected and next-month value,
member shares, tehsils, BMs/ASMs and the site list.

All months are selected by default; click a month chip to drop it (*All* / *Latest*).
Sites with blank, zero or out-of-Pakistan coordinates count in KPIs but are left off maps.

## Report & complete KML

**Report & KML** (top right in the planner, or *Report* on the scenarios page).
**View**: *Complete plan* (default) or any Region / BM / ASM / District / City.

* review progress per level
* summary cards; **pre vs post maps** with Region / BM / ASM / City / District border overlays
* **Hierarchy** table: Region › BM › ASM › planned franchise, or Region › District ›
  City › planned franchise. Each row shows sites, TP/Ufone, franchises pre → post
  and the KPIs you choose. Expand or collapse any level.
* **Merged franchises**: one table per planned franchise with 2+ members
  (members → consolidated → projected for every KPI)
* **Consolidated** table of all planned franchises (with BM and ASM), and the **mapping** table
* **⬇ KML** / **KMZ** of the current view (complete plan by default), **KML: post boundaries only**
  (smallest file: post boundaries and Region/BM/ASM outlines, best for Google My Maps or Earth Web), Excel, Print/PDF
* **Report & KML** in the planner opens the report on the ASM / BM / City you are working on,
  with the same months; switch with **Showing** at the top. ("Draw borders" ticks only draw lines.)

KML folders:
* **POST — planned franchise boundaries** (visible, labelled, grouped by region). Clicking one
  shows the merged franchises and the full KPI table.
* **HIERARCHY — Region › BM › ASM**: an outline for each Region, BM and ASM with
  consolidated KPIs. Each ASM folder also holds the planned-franchise pieces inside it.
* **GEOGRAPHY — District › City**: outlines with consolidated KPIs
* **PRE — current franchises** (hidden by default)
* **SITES — Region › BM › ASM**: every site with all its KPIs

## Territories, islands and filtered views

* Merges always follow through: a site moved into franchise B (by hand, an "only these sites"
  tag or *Seed from MOCAN*) goes wherever B is later merged.

* Every site owns the area closer to it than to any other site, inside its district.
* **Islands are filled**: area beyond the site radius goes to the nearest franchise.
  So does any uncovered area enclosed by coverage, including a district with no sites
  surrounded by districts that have them. The maps show no gaps; the
  *Beyond-radius shading* toggle shows how much area is filled that way.
* **Territories are always built from all sites**, whatever filter is on. An
  ASM, BM or city view never claims area that belongs to sites outside it, so there is no overlap.
* **The grey area** on a filtered map is what lies outside the view. Hover it, or read the panel
  under the maps, to see which planned franchise, ASM, BM and city each grey
  area belongs to (click an ASM there to jump to it). Grey sites never count in the view's numbers.

**Projections** (per planned franchise, per KPI):
* *Lift to best member / site* — if the merged franchise moves part of the way
  (realisation %, default 50%) from its combined per-site productivity to that
  of its best member (members with <10% of the sites are ignored).
* *Lift to area average / site* — same, towards the scope average.
* *No uplift* — plain sum.
Churn-type KPIs are never "lifted". Baseline totals never change with the plan —
only how they are distributed.

**Territories & coverage.** Every site gets a Voronoi cell (the area closer to it
than to any other site), clipped to the district boundary. The part within the
site radius (default 1 km, editable) is coverage. Anything beyond is an
uncovered *island* and is filled by the **nearest franchise** — that's exactly
the franchise owning that Voronoi cell — shown faded on the maps. Uncovered
pockets fully surrounded by coverage are outlined red. Options:
* *Island fill: within district* (default) only lets a franchise absorb gaps in
  its own district; *nearest overall* ignores district lines.
* *Planning area: full district* or *served area* (district clipped to the area
  around its sites — leaves out deserts and riverbeds from area stats).
* Co-located TP + Ufone sites (same tower) split their shared cell.

**Issues tab.** Overloaded / tiny planned franchises, franchises spanning
regions or many districts, sites without coordinates, district mismatches, and
**enclaves**: pieces of a planned franchise cut off from its main territory, with
a one-click "give to neighbour" (the neighbour with the longest shared border).

**Hierarchy tab.** Region → District → Tehsil/City → Franchise → member
franchises, for the plan or the current state. Click a franchise to zoom.

**Exports.** *Excel* (summary, mapping, pre, post with projections and trends,
members, site list, enclaves, warnings) and *KML* of pre and post territories
for Google Earth. **Compare** puts scenarios side by side. **Clone** a scenario
to try a variant.

Colours are assigned per view from a high-contrast palette so every franchise on
screen is distinct; existing franchises keep their colour when you add new codes.

## Moving to a new version without losing work (Backup page)

1. In the version you are using now: **Backup → Download full backup** (one .zip with every plan, franchise tag,
   site move, reviewed ASM/BM, setting, new franchise code, PTA/Zong file and all uploaded site data).
   *Plans only* is a small file without the site data.
2. Unzip the new version, run `migrate` (or `share.py`), open **Backup → Restore** and choose the .zip.
* Restore also accepts an **old `db.sqlite3`** from any earlier version, for work done before this page existed.
* Even simpler: copy `db.sqlite3` from the old folder into the new one before starting it.
* A plan with the same name is replaced by the backup's version (or kept side by side, if you choose that).

## TP / Ufone sites

Every map (planner, report, Boundaries) has a **Sites: Both / Ufone / TP** switch and
**Franchise colours / Operator colours**. Ufone sites are circles, TP sites are triangles (also in the KML),
so they stay easy to tell apart in any colour mode. With one operator showing, *Drag a box* only moves that
operator's sites. The switch only changes what is drawn; the numbers always include both operators.

## Central B and the regions add up

* The region list starts with **Central B — all franchises** (the whole circle). Pick a region to plan it
  on its own; the other levels (BM, ASM, City, District) work inside either.
* Sites, KPIs and **projections are additive**: in a region (or BM / ASM) view, each planned franchise uses
  its Central-B-wide uplift, so Central 5 + 6 + 7 always equal Central B. The report's **Region breakdown**
  shows each region, the sum of regions and Central B side by side, with a ✓.
* Franchise *counts* are not additive: a planned franchise spanning two regions counts in both (listed under the table).
* Uploading one file per region for the same month keeps every region: a re-upload replaces only the
  regions inside that file. The **Upload data** page shows sites per month × region, so a missing
  region (a 0) is easy to spot. The planner warns when a region has no data for a ticked month.
* Maps and lists use each site's latest row across the ticked months, so a region missing from the newest
  month still appears. Set `PLANNER_CIRCLE_NAME` to use another circle name.

## Tagging any franchise from anywhere

The Tag form, the *Move sites* target list, site pop-ups and the deep dive list every franchise:
those in the current view first, then *All other franchises (anywhere)*. Tagging a franchise that is outside the
current view merges the whole franchise. It then shows in the target box as a dashed "outside view" chip; × undoes it.

## PTA district borders and competitor comparison (Boundaries page)

**Boundaries** in the top menu.

1. **Load PTA's district KMZ/KML** as *PTA district borders*. It becomes the official district map:
   every site is re-tagged to the PTA district it physically sits in, and territories are clipped to PTA
   borders, so no planned franchise is drawn across them. Any export style works: names in the placemark,
   data fields, the ArcGIS/QGIS description table or folders; borders drawn as lines are turned into areas.
   If names come from the wrong field (e.g. "1, 2, 3…" instead of district names), pick the right field and press *Apply*.
   Load a newer PTA file later and press *Use this one*; removing it falls back to the built-in OCHA/PBS borders.
2. **Load a comparison KMZ** (e.g. Zong Central B) as *Comparison layer*: territory polygons, or one point per
   site (grouped into territories by the field you choose, e.g. Franchise).
3. **Check**, for the chosen plan:
   * sites **outside every PTA district** (with the nearest district and how far out)
   * sites **in a different PTA district** than your file says, and file district names PTA doesn't know.
     Fix name differences (e.g. *D.G. Khan* vs *Dera Ghazi Khan*) in the *District names* tab; the fix is remembered.
   * **franchises crossing PTA borders** (current and planned): home district, out-of-bound sites and where they are
   * **compare with Zong**: for each planned franchise, the Zong area covering most of it, its share and how many
     Zong areas it touches; for each Zong area, your sites and franchises inside it
   * map with PTA borders, coloured sites, your planned territories and the comparison layer; Excel export
* The planner and report maps get a *Compare* toggle for each comparison layer, and the KML export gets
  *PTA DISTRICTS* and *COMPARE* outline folders (hidden by default).

## Letting someone test it from anywhere (no hosting, no terminal)

Open `share.py` in your Python editor (VS Code, PyCharm, Spyder, IDLE) and press **Run**, or
double-click it. After ~30 s it prints a public `https://….trycloudflare.com` link plus a
`tester` login (also saved in `share_link.txt`). Send both to your tester.

* It installs anything missing, turns login on, hides debug pages, creates the `tester`
  account and opens a free Cloudflare quick tunnel to the planner running on your laptop.
* The link works while that window is open and the laptop is awake; it changes every run.
* What the tester tags is saved in your database, the same as your own work.
* Office networks sometimes block tunnels. If step 5 fails, connect the laptop to a mobile hotspot.
* It sends real site data through an outside service. Check your data policy, or give testers a copy
  loaded with `sample_data/sample_sites.xlsx`.

## Deploying (Render + Supabase)

Set `DATABASE_URL` (Supabase Postgres), `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=0`,
`DJANGO_ALLOWED_HOSTS=your-app.onrender.com`,
`DJANGO_CSRF_ORIGINS=https://your-app.onrender.com` and
`PLANNER_REQUIRE_LOGIN=1` (site data is confidential).
Build: `pip install -r requirements.txt && python manage.py collectstatic --noinput && python manage.py migrate`
Start: `gunicorn config.wsgi`

Leaflet and Chart.js are bundled in `planner/static/planner/vendor` so the app
works on networks that block CDNs; map tiles come from CARTO/OpenStreetMap.

## Performance

~1,850 sites across 3 regions compute in about 1.5–2 s (all regions, maps
included). For 4,000+ sites, plan one region or a few districts at a time via the
*Scope* tab; Voronoi cells are cached so re-tagging is quicker than the first run.

## Layout

```
planner/services/importer.py   Excel -> DB, KPI detection, district tagging
planner/services/geo.py        boundaries, point-in-polygon, local km projection
planner/services/engine.py     mapping, KPIs, projections, Voronoi/coverage/islands, enclaves
planner/services/exports.py    Excel + KML, seed from MOCAN
planner/static/planner/        planner.js (UI), planner.css, vendor libs
```
