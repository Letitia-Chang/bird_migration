# Bird Migration Network Visualizer

An interactive tool for visualizing and stress-testing migratory bird movement across the United States, with a focus on how artificial light at night (ALAN) intersects with migration corridors.

Built with eBird observation data (2024–2026) and NASA VIIRS nightlight satellite imagery, aggregated onto an H3 hexagonal grid.

## Features

- **Map View** — Bird observation density overlaid with ALAN glow by month and species
- **Network View** — Inferred migration routes as a directed graph, color-coded by season
- **Stress Test** — Click to remove stopover nodes and watch connectivity metrics (LCC, fragmentation) update live
- **Experiments** — Fragility curve comparing targeted (high-light) vs. random node removal

**Species:** Ruby-throated Hummingbird, Swainson's Thrush, Magnolia Warbler, Song Sparrow  
**Years:** 2024, 2025, 2026

## Tech Stack

| Layer | Stack |
|---|---|
| Frontend | React, D3.js, TopoJSON, Vite |
| Backend | FastAPI (Python) |
| Database | PostgreSQL via Supabase |
| Data processing | pandas, H3, h5py (NASA HDF5), HDBSCAN |

## Setup

### 1. Clone & configure environment

```bash
git clone <your-repo-url>
cd bird_migration
```

Copy the example env files and fill in your Supabase credentials:

```bash
cp backend/.env.example backend/.env
cp scripts/.env.example scripts/.env
cp frontend/.env.example frontend/.env   # optional: only needed for deployment
```

### 2. Backend

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload
```

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

The app will be available at `http://localhost:5173`.

## Database Setup (from scratch)

You'll need a free Supabase account and data from two public sources.

### 1. Create a Supabase project

1. Go to [supabase.com](https://supabase.com) and create a free project
2. From **Project Settings → Database**, copy your connection string details
3. Fill in `backend/.env` and `scripts/.env` with your credentials

### 2. Get eBird data

eBird is free but requires a one-time access request (usually approved in 1–3 days).

1. Create an account at [ebird.org](https://ebird.org)
2. Go to [ebird.org/data/download](https://ebird.org/data/download) → **Request Access** under "eBird Basic Dataset (EBD)"
3. Once approved, request a **Custom Download** for each species:
   - Ruby-throated Hummingbird
   - Swainson's Thrush
   - Magnolia Warbler
   - Song Sparrow
   - Region: United States · Date range: Jan 2024 – Dec 2026
4. Place the downloaded `.txt` files in `scripts/data/ebird_raw/`
5. Run:
   ```bash
   cd scripts
   pip install pandas h3 psycopg[binary] python-dotenv
   python process_ebird.py
   ```

### 3. Get NASA nightlight data

1. Register at [NASA Earthdata](https://urs.earthdata.nasa.gov/users/new) (free)
2. Go to [AppEEARS](https://appeears.earthdatacloud.nasa.gov) → **Extract → Area**
3. Configure the request:
   - **Layer:** search `VNP46A3` → select `Gap_Filled_DNB_BRDF-Corrected_NTL`
   - **Date range:** 01/01/2024 – 12/31/2026
   - **Spatial:** paste this bounding box for the contiguous US:
     ```json
     {"type":"Feature","geometry":{"type":"Polygon","coordinates":[[[-125,24],[-66,24],[-66,50],[-125,50],[-125,24]]]}}
     ```
   - **Output format:** HDF-EOS5 (.h5)
4. Submit and wait for the email (a few hours to a day)
5. Download all `.h5` files into `scripts/data/nightlight_raw/`
6. Run:
   ```bash
   pip install h5py numpy
   python process_nightlight.py
   ```

### 4. Regenerate saved networks (optional)

The `backend/saved_networks/` folder contains pre-computed network JSONs. If you re-populate the DB with fresh data, regenerate them by hitting the `/nodes` and `/edges` endpoints for each species+year combination and saving the results.

## Data Sources

- **eBird** — Cornell Lab of Ornithology, [ebird.org/data/download](https://ebird.org/data/download)
- **NASA VIIRS Black Marble** — Monthly nightlight composites (VNP46A3), via [AppEEARS](https://appeears.earthdatacloud.nasa.gov)
- Network graphs pre-cached in `backend/saved_networks/`
