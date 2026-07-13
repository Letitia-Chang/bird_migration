# auto_create_and_upload_csv_to_supabase.py
#
# Install:
#   pip install psycopg[binary]
#
# Set environment variables:
#   SUPABASE_DB_HOST=...
#   SUPABASE_DB_NAME=postgres
#   SUPABASE_DB_USER=postgres
#   SUPABASE_DB_PASSWORD=...
#   SUPABASE_DB_PORT=5432
#
# Run:
#   python auto_create_and_upload_csv_to_supabase.py
#
# Notes:
# - This script auto-creates the table from CSV column names.
# - All CSV columns are created as TEXT for safety/simplicity.
# - The table is created only if it does not already exist.
# - CSV header names are normalized into SQL-safe column names.
# - Then the CSV is loaded with COPY ... FROM STDIN.

import csv
import os
import re
from pathlib import Path

import psycopg

from dotenv import load_dotenv
load_dotenv()

# ===== EDIT THESE =====
CSV_FILE = "combined_nightlight.csv"
TABLE_NAME = "nightlight"
SCHEMA_NAME = "public"
ADD_ID_COLUMN = True # adds: id bigserial primary key
TRUNCATE_BEFORE_LOAD = False  # set True if you want to clear existing rows first
# ======================


def quote_ident(name: str) -> str:
    """Safely quote SQL identifiers."""
    return '"' + name.replace('"', '""') + '"'


def normalize_column_name(name: str, used_names: set[str]) -> str:
    """
    Convert CSV header into a Postgres-safe column name:
    - lowercase
    - replace non-alphanumeric chars with underscores
    - avoid leading digits
    - avoid duplicates
    """
    col = name.strip().lower()
    col = re.sub(r"[^\w]+", "_", col)
    col = re.sub(r"^_+", "", col)
    col = re.sub(r"_+$", "", col)

    if not col:
        col = "column"

    if re.match(r"^\d", col):
        col = f"col_{col}"

    base = col
    counter = 2
    while col in used_names:
        col = f"{base}_{counter}"
        counter += 1

    used_names.add(col)
    return col


def get_csv_headers(csv_path: Path) -> list[str]:
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        headers = next(reader, None)
        if not headers:
            raise ValueError("CSV appears to be empty or missing a header row.")
        return headers


def create_table_if_needed(conn, schema_name: str, table_name: str, columns: list[str], add_id: bool):
    full_table_name = f"{quote_ident(schema_name)}.{quote_ident(table_name)}"

    column_defs = []
    if add_id:
        column_defs.append('id bigserial primary key')

    for col in columns:
        column_defs.append(f"{quote_ident(col)} text")

    create_sql = f"""
    CREATE TABLE IF NOT EXISTS {full_table_name} (
        {", ".join(column_defs)}
    )
    """

    with conn.cursor() as cur:
        cur.execute(create_sql)


def truncate_table(conn, schema_name: str, table_name: str):
    full_table_name = f"{quote_ident(schema_name)}.{quote_ident(table_name)}"
    with conn.cursor() as cur:
        cur.execute(f"TRUNCATE TABLE {full_table_name}")


def load_csv_with_copy(conn, csv_path: Path, schema_name: str, table_name: str, columns: list[str]):
    full_table_name = f"{quote_ident(schema_name)}.{quote_ident(table_name)}"
    quoted_cols = ", ".join(quote_ident(c) for c in columns)

    copy_sql = f"""
        COPY {full_table_name} ({quoted_cols})
        FROM STDIN
        WITH (
            FORMAT csv,
            HEADER true
        )
    """

    with conn.cursor() as cur, open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        with cur.copy(copy_sql) as copy:
            while data := f.read(8192):
                copy.write(data)


def main():
    csv_path = Path(CSV_FILE)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path.resolve()}")

    original_headers = get_csv_headers(csv_path)

    used = set()
    normalized_headers = [normalize_column_name(h, used) for h in original_headers]

    if len(set(original_headers)) != len(set(normalized_headers)) or original_headers != normalized_headers:
        print("Original CSV headers:")
        print(original_headers)
        print("\nNormalized SQL column names:")
        print(normalized_headers)
        print("\nIMPORTANT:")
        print("Your table will use the normalized names above.")
        print("But COPY matches the CSV header row, not renamed headers.")
        print("So if any names changed, this direct COPY will fail.")
        print("To fix that, either:")
        print("1. rename the CSV headers to match the normalized names, or")
        print("2. use the rewritten CSV version below.")
        print()
        raise ValueError(
            "CSV headers need normalization. Rename the CSV headers first, "
            "or use the rewritten-CSV version I can give you."
        )

    host = os.environ["SUPABASE_DB_HOST"]
    dbname = os.environ.get("SUPABASE_DB_NAME", "postgres")
    user = os.environ.get("SUPABASE_DB_USER", "postgres")
    password = os.environ["SUPABASE_DB_PASSWORD"]
    port = int(os.environ.get("SUPABASE_DB_PORT", 5432))

    conn = psycopg.connect(
        host=host,
        dbname=dbname,
        user=user,
        password=password,
        port=port,
        sslmode="require",
    )

    try:
        with conn:
            create_table_if_needed(
                conn,
                SCHEMA_NAME,
                TABLE_NAME,
                normalized_headers,
                ADD_ID_COLUMN
            )

            if TRUNCATE_BEFORE_LOAD:
                truncate_table(conn, SCHEMA_NAME, TABLE_NAME)

            load_csv_with_copy(
                conn,
                csv_path,
                SCHEMA_NAME,
                TABLE_NAME,
                normalized_headers
            )

        print(f"Done.")
        print(f"Created/used table: {SCHEMA_NAME}.{TABLE_NAME}")
        print(f"Loaded CSV: {csv_path.name}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()