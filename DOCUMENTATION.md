# Bird Migration Network Visualizer — Project Documentation

An interactive web application modeling North American bird migration as a network, and testing whether stopover sites exposed to artificial light at night (ALAN) are structurally critical to that network's connectivity.

Originally built as a Georgia Tech course project ("Artificial Light at Night and Migration Network Fragility," Team 15), rebuilt here on an independent data pipeline and database.

---

## 1. Research Question

Prior research shows ALAN is a strong predictor of bird stopover density and alters nocturnal migration behavior, but doesn't establish whether migration *networks* become structurally dependent on brightly-lit sites — i.e., whether losing those sites would disproportionately fragment connectivity compared to losing random sites. This project builds a stress-testable migration network and compares two disruption scenarios: removing the brightest stopover sites first vs. removing random sites, then measures the resulting network fragmentation.

## 2. Architecture

```
┌──────────────┐      HTTP/JSON       ┌──────────────┐      SQL       ┌─────────────┐
│   Frontend   │  ─────────────────▶  │   Backend    │  ───────────▶  │  Supabase   │
│  React + D3  │  ◀─────────────────  │   FastAPI    │  ◀───────────  │  (Postgres) │
└──────────────┘                      └──────────────┘                └─────────────┘
                                              │
                                              │ falls back to
                                              ▼
                                    backend/saved_networks/*.json
                                    (pre-computed node/edge cache)
```

- **Frontend** (`frontend/`): React + D3.js SVG rendering, four views (Map, Network, Stress Test, Experiments), Vite dev server.
- **Backend** (`backend/main.py`): FastAPI service exposing bird observation, nightlight, network, and experiment endpoints.
- **Database**: Supabase (managed Postgres) storing raw aggregated observations, nightlight averages, and H3 cell geometry.
- **Cache layer** (`backend/saved_networks/`): Since live node/edge computation is O(sources × targets) per month and can take minutes for large species, each species×year's full 12-month node/edge set is pre-computed once and cached as JSON. The `/nodes` and `/edges` endpoints check this cache first and only fall back to a live DB query if no cache file exists for that species+year.

## 3. Data Sources & Collection

### 3.1 Bird observations — eBird Basic Dataset (EBD)

- **Source**: [ebird.org/data/download](https://ebird.org/data/download), Cornell Lab of Ornithology
- **Access**: requires a one-time approved request for the EBD (raw, non-aggregated record-level data)
- **Species**: Ruby-throated Hummingbird, Swainson's Thrush, Magnolia Warbler, Song Sparrow
- **Scope**: United States, January 2024 – (most recent available month)
- **Volume**: ~2 GB raw text per species

### 3.2 Artificial light at night — NASA VIIRS Black Marble (VNP46A3)

- **Source**: [LAADS DAAC](https://ladsweb.modaps.eosdis.nasa.gov) (not available via AppEEARS — Black Marble products are LAADS-exclusive)
- **Product**: VNP46A3, Gap-Filled DNB BRDF-Corrected monthly nighttime lights composite, "Night" coverage
- **Access**: free NASA Earthdata account, ordered via LAADS's tile-search-and-order workflow, delivered via `wget` + Earthdata Login (EDL) token
- **Scope**: contiguous US bounding box (`W:-125, S:24, E:-66, N:50`), Jan 2024 – May 2026 (latest available at pipeline run time), 609 monthly granule tiles

## 4. Data Processing Pipeline

All processing scripts live in `scripts/`.

### 4.1 `process_ebird.py`

1. Streams each species' raw EBD `.txt` file in chunks (files are multi-GB).
2. Filters to the target species, drops rows missing coordinates/date/count.
3. Buckets each observation into an **H3 resolution-6 cell** (~city-sized hexagon) using `h3.latlng_to_cell`.
4. Aggregates `OBSERVATION COUNT` by **(h3_cell, year, month)** — explicitly *not* by week. Node identity in this system is `h3_cell + year + month`; any finer grouping would silently split one logical node's count across multiple rows.
5. Upserts H3 cell centers into `h3_cells`, upserts the species into `species_ref`, and bulk-inserts the aggregated rows into the species' table (`hummingbird`, `swainsons_thrush`, `magnolia_warbler`, `song_sparrow`).

### 4.2 `process_nightlight.py`

1. Reads each monthly VNP46A3 HDF5 tile, extracts the nightlight band + lat/lon grid, clips to the contiguous US.
2. Buckets pixels into the same H3 resolution-6 grid used for bird data.
3. **Aggregates to one value per (h3_cell, year)** — averaging across all 12 monthly tiles for that cell within a year, not per month.
   - This mirrors the original project's explicit design choice: yearly aggregation avoids skew from transient cloud cover / snow cover in any single month, and light pollution at a location is a comparatively stable, chronic property — well-suited to an annual estimate.
   - It also keeps data volume manageable: monthly resolution across 3 years produced ~6.7M rows and nearly exhausted a free-tier database's storage quota mid-run; yearly aggregation reduces this by ~12x.
4. Upserts into `nightlight (h3_cell, year, nightlight_mean)`.

### 4.3 `build_saved_networks.py`

Regenerates the `backend/saved_networks/*.json` cache files directly from the database (see §6), replicating the backend's exact node/edge query and scoring logic but vectorized with numpy/pandas so a full species-year (12 months of pairwise distance scoring) completes in seconds rather than timing out over HTTP.

## 5. Database Schema

| Table | Columns | Notes |
|---|---|---|
| `h3_cells` | `h3_cell` (PK), `cell_center_lat`, `cell_center_lon` | Shared across bird and nightlight data — geometry is looked up once per cell. |
| `species_ref` | `species_id` (PK), `common_name`, `scientific_name` | |
| `hummingbird`, `swainsons_thrush`, `magnolia_warbler`, `song_sparrow` | `id`, `species_id`, `h3_cell`, `year`, `month`, `week_of_year` (unused, kept for schema compatibility), `grouped_count` | One row per (h3_cell, year, month). |
| `nightlight` | `id`, `h3_cell`, `year`, `nightlight_mean` | One row per (h3_cell, year). |

## 6. Migration Network Construction

Implemented in `backend/main.py`, functions `get_nodes` / `get_edges` / `compute_edges_backend`.

**Nodes**: each node is an H3 cell during a specific month, filtered to `grouped_count >= 6`, joined to its H3 centroid and that year's nightlight mean.

**Candidate edges**: built from every node in month *t* to every node in month *t+1* (December wraps to January of the following year), subject to:
- Both source and target must clear the `grouped_count >= 6` threshold
- No self-loops (same H3 cell)
- Haversine distance between 0 and 1,500 miles

**Edge weight**:
```
w(i,j) = (count_i × count_j) / (distance_miles + 1)
```
Favors high-activity cell pairs that are geographically close.

**Edge pruning**: for each source node, keep only its top-10 outgoing edges by weight (ties broken by shorter distance); for each target node, keep only its top-10 incoming edges. An edge survives only if it appears in **both** rankings — this mutual-ranking rule sharply reduces graph density while preserving the strongest, most mutually-significant connections.

## 7. Structural Stress Testing

**Metrics** (computed over the migration network, treated as weakly connected — i.e., edge direction ignored for component analysis):
- **LCC** (largest connected component size)
- **LCC ratio** = LCC / total original node count
- **Number of components**
- **Fragmentation rate** = 1 − LCC ratio

**Experiments**:
- **Manual stress test** (Stress Test view): user clicks individual stopover nodes to remove them and watches metrics update live.
- **Robustness comparison** (`/experiment/robustness`): compares removing the *N* brightest nodes first ("high-light removal") against removing *N* random nodes, averaged over multiple trials, at a chosen removal percentage.
- **Fragility curve** (`/experiment/fragility-curve`): repeats the robustness comparison across removal percentages from 0% to 100%, plotting LCC ratio vs. % removed for both strategies. A steeper decline under high-light removal indicates the network is structurally dependent on illuminated sites.

## 8. API Reference

Base URL: `http://127.0.0.1:8000` (configurable via `VITE_API_URL` on the frontend).

| Endpoint | Params | Returns |
|---|---|---|
| `GET /bird-observations` | `species, year, month` | Raw per-cell observation counts for the map view |
| `GET /nightlight` | `year` | Top 5,000 brightest H3 cells for that year (ALAN overlay) |
| `GET /nodes` | `species, year, month` | Nodes for that species/month (cache-first, live DB fallback) |
| `GET /edges` | `species, year, month, max_distance_miles, min_distance_miles, min_count, top_k_per_source, top_k_per_target` | Edges from that month to the next (cache-first, live DB fallback) |
| `GET /experiment/robustness` | `species, year, month, removal_pct, trials, ...edge params` | Baseline vs. high-light vs. random-average metrics at one removal level |
| `GET /experiment/fragility-curve` | `species, year, month, steps, trials, ...edge params` | Robustness comparison swept across removal percentages |

## 9. Frontend Views

- **Map View**: eBird observation density (scaled circles) with optional ALAN glow overlay, filterable by species/year/month, pan/zoom.
- **Network View**: full-year migration network — stopover nodes sized by activity, edges colored by season, ALAN glow per node.
- **Stress Test View**: interactive node removal with live LCC/fragmentation/component metrics.
- **Experiments View**: automated high-light-vs-random robustness comparison, summary table, and fragility curve chart.

## 10. Project Structure

```
bird_migration-main/
├── backend/
│   ├── main.py                  # FastAPI app: all endpoints + network/metrics logic
│   ├── requirements.txt
│   ├── saved_networks/          # Pre-computed per-species-per-year node/edge JSON cache
│   └── .env                     # Supabase credentials (not committed)
├── frontend/
│   ├── src/App.jsx              # All views, D3 rendering, API client
│   └── .env                     # VITE_API_URL (optional, for deployment)
├── scripts/
│   ├── process_ebird.py         # eBird EBD → h3_cells + species tables
│   ├── process_nightlight.py    # VNP46A3 HDF5 → nightlight table (yearly)
│   ├── build_saved_networks.py  # Regenerates saved_networks/*.json from live DB
│   ├── export_to_new_supabase.py# Migrates all tables between Supabase projects
│   └── .env                     # Supabase + NASA/eBird credentials (not committed)
└── README.md                    # Quickstart / setup instructions
```

## 11. Known Limitations

- Migration edges are inferred heuristically from aggregated observation co-occurrence across consecutive months, not from direct bird tracking — the resulting network is a plausible migration structure, not a confirmed one.
- Live (uncached) node/edge computation is a pure-Python O(n²) pairwise loop and becomes slow (minutes) for species/months with thousands of active cells; the `saved_networks` cache exists specifically to avoid this in production use. Any change to the underlying data requires regenerating the cache (`scripts/build_saved_networks.py`).
- Nightlight resolution is yearly, so within a given year every month shares the same brightness value per cell — appropriate for testing whether *chronically* bright sites are structurally important, but not for detecting month-specific lighting events.
