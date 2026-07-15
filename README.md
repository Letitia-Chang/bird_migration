# Bird Migration Network Visualizer

An interactive tool that models North American bird migration as a network and tests whether stopover sites exposed to artificial light at night (ALAN) are structurally critical to that network — i.e., whether losing them would fragment migration connectivity more than losing random sites.

**[Live Demo →](https://bird-migration-git-main-ting-ya.vercel.app/)**

## The Research Question

Prior research shows artificial light at night is a strong predictor of where birds stop over during migration, and that it alters nocturnal migration behavior. What's less understood is whether migration *networks* become structurally dependent on those brightly-lit sites — whether disrupting them (through further development, or conservation intervention) would fragment connectivity more severely than losing an equivalent number of random sites.

This project builds a migration network from real observation data, then runs stress tests comparing two disruption scenarios:
1. Removing the brightest (most light-polluted) stopover sites first
2. Removing an equivalent number of random sites

...and measures how much each scenario fragments the network.

## Approach

1. **Bird observations** are pulled from the eBird Basic Dataset and aggregated onto an H3 hexagonal grid (~city-sized cells), grouped by month, for 4 species over 2024–2026.
2. **Nightlight exposure** comes from NASA VIIRS Black Marble monthly composites, aggregated to the same H3 grid and averaged per year (light pollution at a location is a chronic, stable property — an annual estimate avoids noise from transient cloud/snow cover in any single month).
3. **The migration network** treats each (H3 cell, month) as a node and infers directed edges from month *t* to month *t+1*, weighted by `(source activity × target activity) / distance`, keeping only edges that rank in both their source's and target's top-10 strongest connections.
4. **Stress testing** removes nodes from this network — manually, or automatically by brightness vs. randomly — and tracks how the largest connected component and fragmentation rate respond.

Full methodology, database schema, and API reference are in [DOCUMENTATION.md](DOCUMENTATION.md).

## Dataset

| Source | What it provides |
|---|---|
| [eBird Basic Dataset](https://ebird.org/data/download) (Cornell Lab of Ornithology) | Species-level observation records, United States, Jan 2024 – present |
| [NASA VIIRS Black Marble (VNP46A3)](https://ladsweb.modaps.eosdis.nasa.gov) | Monthly nighttime lights composites, aggregated yearly |

**Species:** Ruby-throated Hummingbird, Swainson's Thrush, Magnolia Warbler, Song Sparrow
**Years:** 2024, 2025, 2026

## Features

- **Map View** — Bird observation density overlaid with an ALAN glow layer, filterable by species/year/month
- **Network View** — The full-year inferred migration network, stopover nodes sized by activity and edges color-coded by season
- **Stress Test** — Click individual stopover nodes to remove them and watch connectivity metrics (largest connected component, number of components, fragmentation rate) update live
- **Experiments** — Automated comparison of high-light vs. random node removal, plus a fragility curve sweeping removal percentage from 0–100%

## Tech Stack

| Layer | Stack |
|---|---|
| Frontend | React, D3.js, TopoJSON, Vite — deployed on Vercel |
| Backend | FastAPI (Python) — deployed on Railway |
| Database | PostgreSQL via Supabase |
| Data processing | pandas, H3, h5py (NASA HDF5), numpy |

## Running Locally

```bash
git clone https://github.com/Letitia-Chang/bird_migration.git
cd bird_migration

# Backend
cd backend
cp .env.example .env   # fill in your own Supabase credentials
pip install -r requirements.txt
uvicorn main:app --reload

# Frontend (in a separate terminal, requires Node 18+)
cd frontend
npm install
npm run dev
```

The app will be available at `http://localhost:5173`.
