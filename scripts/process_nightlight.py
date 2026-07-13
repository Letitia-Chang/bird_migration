"""
process_nightlight.py

Downloads NASA VIIRS Black Marble monthly nightlight composites (VNP46A3)
and processes them into H3-aggregated averages, then uploads to Supabase.

── HOW TO GET THE DATA ──────────────────────────────────────────────────────

NASA Earthdata (free account required):

1. Register at https://urs.earthdata.nasa.gov/users/new
2. Go to https://appeears.earthdatacloud.nasa.gov  (AppEEARS — easiest method)
3. Click "Start" → "Extract" → "Point" or "Area"
   - For an Area request:
     * Layer: VNP46A3 (VIIRS/NPP Monthly Lunar BRDF-Adjusted NTL Composite)
     * Product field: search "VNP46A3" → select
       "Gap_Filled_DNB_BRDF-Corrected_NTL" (the main nightlight band)
     * Date range: 01/01/2024 – 12/31/2026
     * Spatial: upload a GeoJSON bounding box for the contiguous US:
       {"type":"Feature","geometry":{"type":"Polygon","coordinates":
       [[[-125,24],[-66,24],[-66,50],[-125,50],[-125,24]]]}}
     * Output format: HDF-EOS5 (.h5)
4. Submit and wait for the email (hours to a day for 3 years of data)
5. Download all .h5 files into:  data/nightlight_raw/

Alternative (NASA Earthdata Search):
  https://search.earthdata.nasa.gov
  Search "VNP46A3", filter by date and bounding box, download HDF5 tiles.
  Note: this gives you tiles you need to mosaic — AppEEARS is easier.

── WHAT THIS SCRIPT DOES ────────────────────────────────────────────────────

For each monthly .h5 file:
  - Reads the DNB nightlight band + lat/lon grids
  - Bins pixels into H3 resolution-6 cells
  - Aggregates to mean nightlight per cell per (year, month)
  - Uploads to the `nightlight` table in your Supabase

── REQUIREMENTS ─────────────────────────────────────────────────────────────

  pip install h5py numpy pandas h3 psycopg[binary] python-dotenv

── RUN ──────────────────────────────────────────────────────────────────────

  python process_nightlight.py

"""

import os
import re
import math
from datetime import datetime
from pathlib import Path
from collections import defaultdict

import h5py
import numpy as np
import pandas as pd
import h3
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

H3_RESOLUTION = 6
INPUT_DIR = Path("data/nightlight_raw")   # folder with your .h5 files
YEARS = [2024, 2025, 2026]

# Each raw tile is 2400x2400 pixels at 15 arc-second resolution (~463m/pixel).
# H3 resolution 6 cells are ~36 km^2, so dozens of pixels fall in the same
# cell — no need to convert every pixel to H3. Sampling every Nth pixel in
# each dimension cuts compute by STEP^2 with negligible accuracy loss.
DOWNSAMPLE_STEP = 8

# Bounding box for contiguous US (filter out non-US pixels)
LAT_MIN, LAT_MAX = 24.0, 50.0
LON_MIN, LON_MAX = -125.0, -66.0

# AppEEARS uses this dataset path; adjust if your HDF5 has a different structure
APPEEARS_LIGHT_KEY = "SDS/Gap_Filled_DNB_BRDF-Corrected_NTL"
# Fallback paths used by raw NASA tiles
FALLBACK_PATHS = [
    "HDFEOS/GRIDS/VIIRS_Grid_DNB_2d/Data Fields/AllAngle_Composite_Snow_Free",
    "HDFEOS/GRIDS/VNP46A3/Data Fields/Gap_Filled_DNB_BRDF-Corrected_NTL",
    "SDS/DNB_At_Sensor_Radiance_500m",
]

# ── DB helpers ────────────────────────────────────────────────────────────────

def get_connection():
    return psycopg.connect(
        host=os.environ["SUPABASE_DB_HOST"],
        port=int(os.environ.get("SUPABASE_DB_PORT", 5432)),
        dbname=os.environ.get("SUPABASE_DB_NAME", "postgres"),
        user=os.environ.get("SUPABASE_DB_USER", "postgres"),
        password=os.environ["SUPABASE_DB_PASSWORD"],
        sslmode="require",
        row_factory=dict_row,
    )


def ensure_nightlight_table(conn):
    # Yearly aggregates only — matches the project report's design ("we chose
    # yearly nightlight data because we are more concerned with overarching
    # nightlight trends"). Monthly granularity produces ~12x more rows than
    # this app needs and can blow through a free-tier DB's storage quota.
    with conn.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS nightlight (
            id BIGSERIAL PRIMARY KEY,
            h3_cell TEXT,
            year INT,
            nightlight_mean DOUBLE PRECISION,
            UNIQUE (h3_cell, year)
        );
        CREATE TABLE IF NOT EXISTS h3_cells (
            h3_cell TEXT PRIMARY KEY,
            cell_center_lat DOUBLE PRECISION,
            cell_center_lon DOUBLE PRECISION
        );
        """)
    conn.commit()


def upsert_h3_cells(conn, cells: dict):
    rows = [(cell, lat, lon) for cell, (lat, lon) in cells.items()]
    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO h3_cells (h3_cell, cell_center_lat, cell_center_lon)
            VALUES (%s, %s, %s)
            ON CONFLICT (h3_cell) DO NOTHING
        """, rows)
    conn.commit()


def upload_nightlight(conn, rows: list[dict]):
    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO nightlight (h3_cell, year, nightlight_mean)
            VALUES (%(h3_cell)s, %(year)s, %(nightlight_mean)s)
            ON CONFLICT (h3_cell, year) DO UPDATE
              SET nightlight_mean = EXCLUDED.nightlight_mean
        """, rows)
    conn.commit()


# ── HDF5 reading ─────────────────────────────────────────────────────────────

def read_light_array(f: h5py.File):
    """Try known dataset paths and return (light_array, scale, fill)."""
    candidates = [APPEEARS_LIGHT_KEY] + FALLBACK_PATHS
    for path in candidates:
        if path in f:
            ds = f[path]
            arr = ds[:].astype("float64")
            attrs = ds.attrs
            fill = None
            for k in ["_FillValue", "FillValue", "fillvalue", "missing_value"]:
                if k in attrs:
                    v = attrs[k]
                    fill = float(v.flat[0]) if hasattr(v, "flat") else float(v)
                    break
            scale = float(attrs["scale_factor"].flat[0]) if "scale_factor" in attrs else 1.0
            offset_key = "add_offset" if "add_offset" in attrs else "offset"
            offset = float(attrs[offset_key].flat[0]) if offset_key in attrs else 0.0
            if fill is not None:
                arr[arr == fill] = np.nan
            arr = arr * scale + offset
            arr[arr < 0] = np.nan
            return arr
    return None


def read_lat_lon(f: h5py.File):
    """Return (lat_1d, lon_1d) arrays if stored, else None."""
    for lat_path in ["SDS/lat", "HDFEOS/GRIDS/VIIRS_Grid_DNB_2d/Data Fields/lat"]:
        for lon_path in ["SDS/lon", "HDFEOS/GRIDS/VIIRS_Grid_DNB_2d/Data Fields/lon"]:
            if lat_path in f and lon_path in f:
                return f[lat_path][:].astype("float64"), f[lon_path][:].astype("float64")
    return None, None


def extract_year_month(filename: str):
    # AppEEARS: VNP46A3.A2024001.h00v00.001.2024032123456.h5
    m = re.search(r"A(\d{4})(\d{3})", filename)
    if m:
        year = int(m.group(1))
        doy = int(m.group(2))
        date = datetime.strptime(f"{year}-{doy}", "%Y-%j")
        return date.year, date.month
    # Pattern: 2024_01.h5 or 202401.h5
    m = re.search(r"(\d{4})[_\-]?(\d{2})", filename)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


# ── Core processing ───────────────────────────────────────────────────────────

def read_file_h3_means(filepath: Path):
    """Return {h3_cell: mean_light} for this single monthly tile."""
    with h5py.File(filepath, "r") as f:
        light = read_light_array(f)
        if light is None:
            print(f"    WARNING: could not find a nightlight band. Skipping.")
            return {}

        lat_1d, lon_1d = read_lat_lon(f)

        if lat_1d is not None:
            lat_1d = lat_1d[::DOWNSAMPLE_STEP]
            lon_1d = lon_1d[::DOWNSAMPLE_STEP]
            light = light[::DOWNSAMPLE_STEP, ::DOWNSAMPLE_STEP]
            lon_grid, lat_grid = np.meshgrid(lon_1d, lat_1d)
        else:
            # AppEEARS area extract: lat/lon embedded as HDF-EOS metadata
            # Try to reconstruct from grid metadata or assume global grid
            print("    No lat/lon arrays found — attempting grid reconstruction.")
            rows, cols = light.shape
            lat_1d = np.linspace(90, -90, rows)
            lon_1d = np.linspace(-180, 180, cols)
            lon_grid, lat_grid = np.meshgrid(lon_1d, lat_1d)

    flat = pd.DataFrame({
        "lat": lat_grid.ravel(),
        "lon": lon_grid.ravel(),
        "light": light.ravel(),
    }).dropna(subset=["light"])

    flat = flat[
        (flat["lat"] >= LAT_MIN) & (flat["lat"] <= LAT_MAX) &
        (flat["lon"] >= LON_MIN) & (flat["lon"] <= LON_MAX)
    ]

    if flat.empty:
        return {}

    flat["h3_cell"] = flat.apply(
        lambda r: h3.latlng_to_cell(r["lat"], r["lon"], H3_RESOLUTION),
        axis=1,
    )

    grouped = flat.groupby("h3_cell")["light"].mean()
    return grouped.to_dict()


def main():
    files = sorted(INPUT_DIR.glob("*.h5")) + sorted(INPUT_DIR.glob("*.HDF5"))
    if not files:
        print(f"No .h5 files found in {INPUT_DIR}")
        print("Download VNP46A3 HDF5 files from AppEEARS and place them there.")
        return

    conn = get_connection()
    ensure_nightlight_table(conn)

    # Accumulate monthly tile means into a per-(h3_cell, year) running
    # sum/count, then upload one aggregated yearly row per cell at the end.
    # {(h3_cell, year): [sum, count]}
    accumulator = defaultdict(lambda: [0.0, 0])
    h3_cell_centers = {}

    for idx, filepath in enumerate(files, start=1):
        year, month = extract_year_month(filepath.name)
        if year is None or year not in YEARS:
            print(f"Skipping {filepath.name} (year not in {YEARS})")
            continue

        print(f"[{idx}/{len(files)}] {filepath.name} → {year}-{month:02d}")
        cell_means = read_file_h3_means(filepath)

        for cell, mean_light in cell_means.items():
            entry = accumulator[(cell, year)]
            entry[0] += mean_light
            entry[1] += 1
            if cell not in h3_cell_centers:
                lat_c, lon_c = h3.cell_to_latlng(cell)
                h3_cell_centers[cell] = (lat_c, lon_c)

    print(f"\nUpserting {len(h3_cell_centers)} H3 cells...")
    upsert_h3_cells(conn, h3_cell_centers)

    print(f"Uploading {len(accumulator)} yearly (h3_cell, year) rows...")
    rows = [
        {"h3_cell": cell, "year": year, "nightlight_mean": total / count}
        for (cell, year), (total, count) in accumulator.items()
    ]
    BATCH = 2000
    for i in range(0, len(rows), BATCH):
        upload_nightlight(conn, rows[i : i + BATCH])
        print(f"  {min(i + BATCH, len(rows))} / {len(rows)}")

    conn.close()
    print("\nNightlight processing complete.")


if __name__ == "__main__":
    main()
