"""
process_ebird.py

Processes raw eBird Basic Dataset (EBD) tab-separated files into
H3-aggregated observation counts, then uploads to your Supabase DB.

── HOW TO GET THE DATA ──────────────────────────────────────────────────────

1. Create a free account at https://ebird.org/home
2. Go to https://ebird.org/data/download
3. Under "eBird Basic Dataset (EBD)", click "Request Access"
   - Fill in a short description (e.g. "personal research on migration patterns")
   - Approval usually takes 1–3 days
4. Once approved, go back to the download page and request a CUSTOM DOWNLOAD:
   - Species: (request one at a time for smaller files)
       * Ruby-throated Hummingbird
       * Swainson's Thrush
       * Magnolia Warbler
       * Song Sparrow
   - Region: United States
   - Date range: Jan 2024 – Dec 2026
   - Check "Include sampling event data" — not needed, skip it
5. You'll receive an email with a download link (usually within a few hours)
6. Each file will be a .txt or .gz tab-separated file (~200–800 MB per species)
   Unzip and place them in a folder, e.g. data/ebird_raw/

── WHAT THIS SCRIPT DOES ────────────────────────────────────────────────────

For each species file:
  - Reads in chunks (file is large)
  - Filters to valid lat/lon and observation counts
  - Groups observations into H3 resolution-6 cells (~city-sized)
  - Aggregates by (h3_cell, year, month) → grouped_count
  - Saves per-species CSV to data/processed/
  - Uploads to your Supabase tables

── REQUIREMENTS ─────────────────────────────────────────────────────────────

  pip install pandas h3 psycopg[binary] python-dotenv

── RUN ──────────────────────────────────────────────────────────────────────

  python process_ebird.py

"""

import os
import csv
import pandas as pd
import h3
import psycopg
from psycopg.rows import dict_row
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

H3_RESOLUTION = 6
CHUNK_SIZE = 100_000
MIN_GROUPED_COUNT = 1  # keep all, backend filters at >= 6

INPUT_DIR = Path("data/ebird_raw")    # folder with your downloaded EBD .txt files
OUTPUT_DIR = Path("data/processed")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Map from file keyword → (table_name, common_name)
# Includes both human-friendly keywords and eBird's internal 6-letter species
# codes (used in official EBD download filenames, e.g. ebd_US_rthhum_...txt).
SPECIES_CONFIG = {
    "rubythroated":    ("hummingbird",      "Ruby-throated Hummingbird"),
    "hummingbird":     ("hummingbird",      "Ruby-throated Hummingbird"),
    "rthhum":          ("hummingbird",      "Ruby-throated Hummingbird"),
    "swainson":        ("swainsons_thrush", "Swainson's Thrush"),
    "swathr":          ("swainsons_thrush", "Swainson's Thrush"),
    "magnolia":        ("magnolia_warbler", "Magnolia Warbler"),
    "magwar":          ("magnolia_warbler", "Magnolia Warbler"),
    "song_sparrow":    ("song_sparrow",     "Song Sparrow"),
    "songsparrow":     ("song_sparrow",     "Song Sparrow"),
    "sonspa":          ("song_sparrow",     "Song Sparrow"),
}

YEARS = [2024, 2025, 2026]

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


def ensure_schema(conn):
    """Create all required tables if they don't exist."""
    with conn.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS h3_cells (
            h3_cell TEXT PRIMARY KEY,
            cell_center_lat DOUBLE PRECISION,
            cell_center_lon DOUBLE PRECISION
        );

        CREATE TABLE IF NOT EXISTS species_ref (
            species_id SERIAL PRIMARY KEY,
            common_name TEXT UNIQUE,
            scientific_name TEXT
        );

        CREATE TABLE IF NOT EXISTS hummingbird (
            id BIGSERIAL PRIMARY KEY,
            species_id INT,
            h3_cell TEXT,
            year INT,
            month INT,
            week_of_year INT,
            grouped_count INT
        );

        CREATE TABLE IF NOT EXISTS swainsons_thrush (
            id BIGSERIAL PRIMARY KEY,
            species_id INT,
            h3_cell TEXT,
            year INT,
            month INT,
            week_of_year INT,
            grouped_count INT
        );

        CREATE TABLE IF NOT EXISTS magnolia_warbler (
            id BIGSERIAL PRIMARY KEY,
            species_id INT,
            h3_cell TEXT,
            year INT,
            month INT,
            week_of_year INT,
            grouped_count INT
        );

        CREATE TABLE IF NOT EXISTS song_sparrow (
            id BIGSERIAL PRIMARY KEY,
            species_id INT,
            h3_cell TEXT,
            year INT,
            month INT,
            week_of_year INT,
            grouped_count INT
        );
        """)
    conn.commit()


def upsert_species_ref(conn, common_name, scientific_name) -> int:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO species_ref (common_name, scientific_name)
            VALUES (%s, %s)
            ON CONFLICT (common_name) DO UPDATE SET scientific_name = EXCLUDED.scientific_name
            RETURNING species_id
        """, (common_name, scientific_name))
        row = cur.fetchone()
    conn.commit()
    return row["species_id"]


def upsert_h3_cells(conn, cells: dict):
    """cells: {h3_cell: (lat, lon)}"""
    rows = [(cell, lat, lon) for cell, (lat, lon) in cells.items()]
    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO h3_cells (h3_cell, cell_center_lat, cell_center_lon)
            VALUES (%s, %s, %s)
            ON CONFLICT (h3_cell) DO NOTHING
        """, rows)
    conn.commit()


def upload_observations(conn, table_name: str, rows: list[dict]):
    with conn.cursor() as cur:
        cur.executemany(f"""
            INSERT INTO {table_name} (species_id, h3_cell, year, month, week_of_year, grouped_count)
            VALUES (%(species_id)s, %(h3_cell)s, %(year)s, %(month)s, %(week_of_year)s, %(grouped_count)s)
        """, rows)
    conn.commit()


# ── Processing ────────────────────────────────────────────────────────────────

# EBird column names in the EBD format
EBIRD_COLS = [
    "GLOBAL UNIQUE IDENTIFIER",
    "LAST EDITED DATE",
    "TAXONOMIC ORDER",
    "CATEGORY",
    "TAXON CONCEPT ID",
    "COMMON NAME",
    "SCIENTIFIC NAME",
    "SUBSPECIES COMMON NAME",
    "SUBSPECIES SCIENTIFIC NAME",
    "OBSERVATION COUNT",
    "BREEDING CODE",
    "BREEDING CATEGORY",
    "BEHAVIOR CODE",
    "AGE/SEX",
    "COUNTRY",
    "COUNTRY CODE",
    "STATE",
    "STATE CODE",
    "COUNTY",
    "COUNTY CODE",
    "IBA CODE",
    "BCR CODE",
    "USFWS CODE",
    "ATLAS BLOCK",
    "LOCALITY",
    "LOCALITY ID",
    "LOCALITY TYPE",
    "LATITUDE",
    "LONGITUDE",
    "OBSERVATION DATE",
    "TIME OBSERVATIONS STARTED",
    "OBSERVER ID",
    "SAMPLING EVENT IDENTIFIER",
    "PROTOCOL TYPE",
    "PROTOCOL CODE",
    "PROJECT CODE",
    "DURATION MINUTES",
    "EFFORT DISTANCE KM",
    "EFFORT AREA HA",
    "NUMBER OBSERVERS",
    "ALL SPECIES REPORTED",
    "GROUP IDENTIFIER",
    "HAS MEDIA",
    "APPROVED",
    "REVIEWED",
    "REASON",
    "TRIP COMMENTS",
    "SPECIES COMMENTS",
]

NEEDED_COLS = ["COMMON NAME", "SCIENTIFIC NAME", "LATITUDE", "LONGITUDE", "OBSERVATION COUNT", "OBSERVATION DATE"]


def process_file(filepath: Path, common_name: str, table_name: str, conn):
    print(f"\nProcessing {filepath.name} → {table_name}")

    scientific_name_found = None
    all_h3_cells = {}
    aggregated = {}  # (h3_cell, year, month) → count

    for chunk in pd.read_csv(
        filepath,
        sep="\t",
        chunksize=CHUNK_SIZE,
        low_memory=False,
        usecols=lambda c: c in NEEDED_COLS,
        on_bad_lines="skip",
    ):
        # Filter to target species just in case file has multiple
        chunk = chunk[chunk["COMMON NAME"] == common_name].copy()
        if chunk.empty:
            continue

        if scientific_name_found is None and "SCIENTIFIC NAME" in chunk.columns:
            scientific_name_found = chunk["SCIENTIFIC NAME"].dropna().iloc[0] if not chunk["SCIENTIFIC NAME"].dropna().empty else ""

        # Drop rows without location or count
        chunk = chunk.dropna(subset=["LATITUDE", "LONGITUDE", "OBSERVATION DATE"])
        chunk["OBSERVATION COUNT"] = pd.to_numeric(chunk["OBSERVATION COUNT"], errors="coerce")
        chunk = chunk.dropna(subset=["OBSERVATION COUNT"])

        # Parse date
        chunk["date"] = pd.to_datetime(chunk["OBSERVATION DATE"], errors="coerce")
        chunk = chunk.dropna(subset=["date"])
        chunk["year"] = chunk["date"].dt.year
        chunk["month"] = chunk["date"].dt.month

        # Filter years
        chunk = chunk[chunk["year"].isin(YEARS)]
        if chunk.empty:
            continue

        # H3 cell
        chunk["h3_cell"] = chunk.apply(
            lambda r: h3.latlng_to_cell(float(r["LATITUDE"]), float(r["LONGITUDE"]), H3_RESOLUTION),
            axis=1,
        )

        # Cache cell centers
        for cell in chunk["h3_cell"].unique():
            if cell not in all_h3_cells:
                lat_c, lon_c = h3.cell_to_latlng(cell)
                all_h3_cells[cell] = (lat_c, lon_c)

        # Aggregate — one row per (h3_cell, year, month), matching the report's
        # node definition ("each node corresponds to an H3 cell during a
        # specific month"). No week-level granularity: the backend builds
        # node_id from h3_cell+year+month only, so a finer grouping here would
        # silently produce multiple rows per node_id.
        grouped = (
            chunk.groupby(["h3_cell", "year", "month"])["OBSERVATION COUNT"]
            .sum()
            .reset_index()
        )

        for _, row in grouped.iterrows():
            key = (row["h3_cell"], int(row["year"]), int(row["month"]))
            aggregated[key] = aggregated.get(key, 0) + int(row["OBSERVATION COUNT"])

    if not aggregated:
        print(f"  No data found for {common_name} in this file.")
        return

    print(f"  Upserting {len(all_h3_cells)} H3 cells...")
    upsert_h3_cells(conn, all_h3_cells)

    species_id = upsert_species_ref(conn, common_name, scientific_name_found or "")

    rows = [
        {
            "species_id": species_id,
            "h3_cell": h3_cell,
            "year": year,
            "month": month,
            "week_of_year": None,
            "grouped_count": count,
        }
        for (h3_cell, year, month), count in aggregated.items()
        if count >= MIN_GROUPED_COUNT
    ]

    print(f"  Uploading {len(rows)} observation rows to {table_name}...")
    BATCH = 2000
    for i in range(0, len(rows), BATCH):
        upload_observations(conn, table_name, rows[i : i + BATCH])
        print(f"    {min(i + BATCH, len(rows))} / {len(rows)}")

    print(f"  Done: {common_name}")


def detect_species(filepath: Path):
    name = filepath.stem.lower().replace("-", "").replace(" ", "")
    for keyword, config in SPECIES_CONFIG.items():
        if keyword in name:
            return config
    return None


def main():
    files = list(INPUT_DIR.glob("*.txt")) + list(INPUT_DIR.glob("*.tsv"))
    if not files:
        print(f"No .txt or .tsv files found in {INPUT_DIR}")
        print("Download your eBird EBD files and place them there.")
        return

    conn = get_connection()
    ensure_schema(conn)

    for filepath in files:
        config = detect_species(filepath)
        if config is None:
            print(f"Skipping {filepath.name} — could not detect species from filename.")
            print("  Rename the file to include the species name, e.g. 'rubythroated_2024_2026.txt'")
            continue
        table_name, common_name = config
        process_file(filepath, common_name, table_name, conn)

    conn.close()
    print("\nAll done.")


if __name__ == "__main__":
    main()
