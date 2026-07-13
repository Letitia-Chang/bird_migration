"""
export_to_new_supabase.py

Copies all data from the old Supabase project into a new one.

Usage:
  1. Fill in OLD_* and NEW_* connection details below (or set as env vars).
  2. pip install psycopg[binary]
  3. python export_to_new_supabase.py

Tables exported: h3_cells, species_ref, nightlight,
                 hummingbird, swainsons_thrush, magnolia_warbler, song_sparrow
"""

import os
import psycopg
from psycopg.rows import dict_row

# ── Connection details ────────────────────────────────────────────────────────
# Old project (the one you're migrating away from)
OLD = dict(
    host=os.environ.get("OLD_DB_HOST", "aws-1-us-east-1.pooler.supabase.com"),
    port=int(os.environ.get("OLD_DB_PORT", "6543")),
    dbname=os.environ.get("OLD_DB_NAME", "postgres"),
    user=os.environ.get("OLD_DB_USER", ""),        # fill in
    password=os.environ.get("OLD_DB_PASSWORD", ""), # fill in
    sslmode="require",
)

# New project (your own Supabase)
NEW = dict(
    host=os.environ.get("NEW_DB_HOST", ""),        # fill in
    port=int(os.environ.get("NEW_DB_PORT", "5432")),
    dbname=os.environ.get("NEW_DB_NAME", "postgres"),
    user=os.environ.get("NEW_DB_USER", "postgres"),
    password=os.environ.get("NEW_DB_PASSWORD", ""), # fill in
    sslmode="require",
)

# Tables to copy in order (respects foreign-key dependencies)
TABLES = [
    "h3_cells",
    "species_ref",
    "nightlight",
    "hummingbird",
    "swainsons_thrush",
    "magnolia_warbler",
    "song_sparrow",
]

BATCH_SIZE = 2000


def get_columns(conn, table: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table,),
        )
        return [row["column_name"] for row in cur.fetchall()]


def create_table_like(src_conn, dst_conn, table: str):
    """Recreate the table in the destination using CREATE TABLE IF NOT EXISTS LIKE."""
    with src_conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT column_name, data_type, character_maximum_length,
                   is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table,),
        )
        cols = cur.fetchall()

    col_defs = []
    for col in cols:
        name = col["column_name"]
        dtype = col["data_type"]
        nullable = "" if col["is_nullable"] == "YES" else " NOT NULL"
        default = f" DEFAULT {col['column_default']}" if col["column_default"] else ""
        if col["character_maximum_length"]:
            dtype = f"{dtype}({col['character_maximum_length']})"
        col_defs.append(f'"{name}" {dtype}{nullable}{default}')

    ddl = f'CREATE TABLE IF NOT EXISTS public."{table}" (\n  ' + ",\n  ".join(col_defs) + "\n);"
    with dst_conn.cursor() as cur:
        cur.execute(ddl)
    dst_conn.commit()


def copy_table(src_conn, dst_conn, table: str):
    columns = get_columns(src_conn, table)
    quoted_cols = ", ".join(f'"{c}"' for c in columns)
    placeholders = ", ".join(["%s"] * len(columns))

    with src_conn.cursor(row_factory=dict_row) as src_cur:
        src_cur.execute(f'SELECT {quoted_cols} FROM public."{table}"')

        total = 0
        while True:
            rows = src_cur.fetchmany(BATCH_SIZE)
            if not rows:
                break

            with dst_conn.cursor() as dst_cur:
                values = [tuple(row[c] for c in columns) for row in rows]
                dst_cur.executemany(
                    f'INSERT INTO public."{table}" ({quoted_cols}) VALUES ({placeholders}) ON CONFLICT DO NOTHING',
                    values,
                )
            dst_conn.commit()
            total += len(rows)
            print(f"  {table}: {total} rows copied...")

    print(f"  {table}: done ({total} total rows)")


def main():
    print("Connecting to source (old Supabase)...")
    src = psycopg.connect(**OLD, row_factory=dict_row)

    print("Connecting to destination (new Supabase)...")
    dst = psycopg.connect(**NEW)

    for table in TABLES:
        print(f"\nMigrating table: {table}")
        try:
            create_table_like(src, dst, table)
            copy_table(src, dst, table)
        except Exception as e:
            print(f"  ERROR on {table}: {e}")
            dst.rollback()

    src.close()
    dst.close()
    print("\nMigration complete.")


if __name__ == "__main__":
    main()
