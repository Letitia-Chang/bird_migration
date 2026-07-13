"""
build_saved_networks.py

Regenerates backend/saved_networks/<species>_<year>_network.json directly from
the live database, replicating the backend's /nodes + /edges logic exactly but
with vectorized numpy/pandas so it finishes in seconds instead of timing out
over HTTP.

Node query and edge scoring/ranking mirror backend/main.py:
  - nodes: grouped_count >= 6, joined to h3_cells + yearly nightlight_mean
  - edges: source month t -> target month t+1 (Dec wraps to Jan of year+1)
      weight = (src_count * tgt_count) / (distance_miles + 1)
      keep edges in BOTH top-10-per-source and top-10-per-target (by
      weight desc, distance asc), same mutual-ranking rule as the backend.

Run: python build_saved_networks.py
"""

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv
import os

load_dotenv(override=True)

OUTPUT_DIR = Path("../backend/saved_networks")

SPECIES = [
    ("Ruby-throated Hummingbird", "hummingbird"),
    ("Swainson's Thrush", "swainsons_thrush"),
    ("Magnolia Warbler", "magnolia_warbler"),
    ("Song Sparrow", "song_sparrow"),
]

YEARS = [2024, 2025, 2026]

MIN_COUNT = 6
MAX_DISTANCE_MILES = 1500.0
MIN_DISTANCE_MILES = 0.0
TOP_K_PER_SOURCE = 10
TOP_K_PER_TARGET = 10
RADIUS_MILES = 3958.8


def get_connection():
    return psycopg.connect(
        host=os.environ["SUPABASE_DB_HOST"],
        port=int(os.environ.get("SUPABASE_DB_PORT", 5432)),
        dbname=os.environ.get("SUPABASE_DB_NAME", "postgres"),
        user=os.environ.get("SUPABASE_DB_USER", "postgres"),
        password=os.environ["SUPABASE_DB_PASSWORD"],
        connect_timeout=15,
        options="-c statement_timeout=120000",
        sslmode="require",
        row_factory=dict_row,
    )


NODES_SQL = """
select
    concat(o.h3_cell, '_', o.year, '_', lpad(o.month::text, 2, '0')) as node_id,
    r.common_name,
    r.scientific_name,
    o.species_id,
    o.year,
    o.month,
    o.week_of_year,
    o.h3_cell,
    h.cell_center_lat as lat,
    h.cell_center_lon as lon,
    o.grouped_count,
    n.nightlight_mean
from {table} o
join species_ref r on o.species_id = r.species_id
join h3_cells h on o.h3_cell = h.h3_cell
left join nightlight n on o.h3_cell = n.h3_cell and o.year = n.year
where r.common_name = %s
  and o.year = %s
  and o.month = %s
  and o.grouped_count >= 6
order by o.grouped_count desc
"""


def load_nodes(conn, table, common_name, year, month_num):
    with conn.cursor() as cur:
        cur.execute(NODES_SQL.format(table=table), (common_name, year, month_num))
        return cur.fetchall()


def next_month(year, month_num):
    if month_num == 12:
        return year + 1, 1
    return year, month_num + 1


BATCH_SIZE = 500  # rows per batch — bounds peak memory of the B x other matrices


def _topk_by_row(row_lat, row_lon, row_cnt, row_h3, col_lat, col_lon, col_cnt, col_h3, k):
    """For each row, find its top-k (by weight desc) among all columns passing
    the distance/self-loop filters. Returns arrays (row_idx, col_idx, dist, weight).

    Vectorized in batches of rows against the full column set, using
    argpartition instead of a full sort — only the top-k per row is ever
    materialized, which is what makes this fast (O(n_row * n_col) work, same
    as any correct approach must do, but with O(n_row * k) memory/output
    instead of O(n_row * n_col)).
    """
    n_col = len(col_lat)
    k = min(k, n_col)

    out_row, out_col, out_dist, out_weight = [], [], [], []

    for start in range(0, len(row_lat), BATCH_SIZE):
        end = min(start + BATCH_SIZE, len(row_lat))

        r_lat_b = row_lat[start:end, None]
        r_lon_b = row_lon[start:end, None]
        r_cnt_b = row_cnt[start:end, None]
        r_h3_b = row_h3[start:end, None]

        dlat = col_lat[None, :] - r_lat_b
        dlon = col_lon[None, :] - r_lon_b
        a = np.sin(dlat / 2) ** 2 + np.cos(r_lat_b) * np.cos(col_lat[None, :]) * np.sin(dlon / 2) ** 2
        dist = RADIUS_MILES * 2 * np.arcsin(np.sqrt(a))

        mask = (
            (dist >= MIN_DISTANCE_MILES)
            & (dist <= MAX_DISTANCE_MILES)
            & (r_h3_b != col_h3[None, :])
        )

        weight = (r_cnt_b * col_cnt[None, :]) / (dist + 1.0)
        weight = np.where(mask, weight, -np.inf)

        top_idx = np.argpartition(-weight, k - 1, axis=1)[:, :k]
        top_w = np.take_along_axis(weight, top_idx, axis=1)
        top_d = np.take_along_axis(dist, top_idx, axis=1)

        valid = np.isfinite(top_w)
        if not valid.any():
            continue

        local_i, col_pos = np.nonzero(valid)
        col_idx = top_idx[local_i, col_pos]

        out_row.append(local_i + start)
        out_col.append(col_idx)
        out_dist.append(top_d[local_i, col_pos])
        out_weight.append(top_w[local_i, col_pos])

    if not out_row:
        return None
    return (
        np.concatenate(out_row),
        np.concatenate(out_col),
        np.concatenate(out_dist),
        np.concatenate(out_weight),
    )


def compute_edges(source_nodes, target_nodes):
    """Vectorized replica of compute_edges_backend / compute_edges_backend's
    mutual top-k rule: an edge is kept only if it's in BOTH its source's own
    top-k (by weight) AND its target's own top-k — each ranking computed
    independently from the FULL valid-candidate set (not from an already
    source-pruned subset — pruning first would silently under-count each
    target's true competition and over-admit edges).

    Implemented as two independent bounded top-k passes (one batched by
    source rows, one batched by target rows) joined on (source, target),
    instead of sorting the full n_source x n_target candidate matrix, which
    is what made the naive version take ~10 minutes per month for species
    with 10k+ active nodes.
    """
    src = [s for s in source_nodes if s["grouped_count"] >= MIN_COUNT]
    tgt = [t for t in target_nodes if t["grouped_count"] >= MIN_COUNT]
    if not src or not tgt:
        return []

    s_lat = np.radians(np.array([float(s["lat"]) for s in src]))
    s_lon = np.radians(np.array([float(s["lon"]) for s in src]))
    s_cnt = np.array([float(s["grouped_count"]) for s in src])
    s_h3 = np.array([s["h3_cell"] for s in src])

    t_lat = np.radians(np.array([float(t["lat"]) for t in tgt]))
    t_lon = np.radians(np.array([float(t["lon"]) for t in tgt]))
    t_cnt = np.array([float(t["grouped_count"]) for t in tgt])
    t_h3 = np.array([t["h3_cell"] for t in tgt])

    by_source = _topk_by_row(s_lat, s_lon, s_cnt, s_h3, t_lat, t_lon, t_cnt, t_h3, TOP_K_PER_SOURCE)
    if by_source is None:
        return []
    src_df = pd.DataFrame({"s": by_source[0], "t": by_source[1], "dist": by_source[2], "weight": by_source[3]})
    src_df = src_df.sort_values(["weight", "dist"], ascending=[False, True]).reset_index(drop=True)
    src_df["source_rank"] = src_df.groupby("s").cumcount() + 1

    by_target = _topk_by_row(t_lat, t_lon, t_cnt, t_h3, s_lat, s_lon, s_cnt, s_h3, TOP_K_PER_TARGET)
    if by_target is None:
        return []
    # by_target's "row" is the target, "col" is the source — swap to (s, t).
    tgt_df = pd.DataFrame({"t": by_target[0], "s": by_target[1], "dist": by_target[2], "weight": by_target[3]})
    tgt_df = tgt_df.sort_values(["weight", "dist"], ascending=[False, True]).reset_index(drop=True)
    tgt_df["target_rank"] = tgt_df.groupby("t").cumcount() + 1

    # Keep only edges present in both top-k sets (the backend's mutual rule).
    merged = pd.merge(
        src_df[["s", "t", "dist", "weight", "source_rank"]],
        tgt_df[["s", "t", "target_rank"]],
        on=["s", "t"],
        how="inner",
    )
    merged = merged.sort_values(["weight", "dist"], ascending=[False, True]).reset_index(drop=True)

    edges = []
    for r in merged.itertuples(index=False):
        s = src[int(r.s)]
        t = tgt[int(r.t)]
        edges.append({
            "source_node_id": s["node_id"],
            "source_h3": s["h3_cell"],
            "source_year": s["year"],
            "source_month": s["month"],
            "source_count": s["grouped_count"],
            "source_lat": s["lat"],
            "source_lon": s["lon"],
            "target_node_id": t["node_id"],
            "target_h3": t["h3_cell"],
            "target_year": t["year"],
            "target_month": t["month"],
            "target_count": t["grouped_count"],
            "target_lat": t["lat"],
            "target_lon": t["lon"],
            "distance_miles": float(r.dist),
            "weight": float(r.weight),
            "source_rank": int(r.source_rank),
            "target_rank": int(r.target_rank),
        })
    return edges


def safe_species_filename(species):
    return species.lower().replace(" ", "_").replace("'", "")


def build_one(common_name, table, year, out_path):
    """Build a single species-year with its own connection; retries on pooler drops."""
    for attempt in range(1, 4):
        conn = get_connection()
        try:
            node_cache = {}

            def nodes_for(y, m):
                key = (y, m)
                if key not in node_cache:
                    node_cache[key] = load_nodes(conn, table, common_name, y, m)
                return node_cache[key]

            all_nodes = []
            all_edges = []

            for month_num in range(1, 13):
                print(f"  month {month_num:02d}: querying nodes...")
                src = nodes_for(year, month_num)
                all_nodes.extend(src)

                ny, nm = next_month(year, month_num)
                tgt = nodes_for(ny, nm)

                print(f"  month {month_num:02d}: {len(src)} src / {len(tgt)} tgt nodes, computing edges...")
                edges = compute_edges(src, tgt)
                all_edges.extend(edges)
                print(f"  month {month_num:02d}: {len(src)} nodes, {len(edges)} edges")

            out = {
                "species": common_name,
                "year": year,
                "nodes": all_nodes,
                "edges": all_edges,
            }
            with open(out_path, "w") as f:
                json.dump(out, f)
            print(f"  -> {out_path.name}: {len(all_nodes)} nodes, {len(all_edges)} edges")
            return True
        except psycopg.OperationalError as e:
            print(f"  connection dropped (attempt {attempt}/3): {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass
    print(f"  FAILED after 3 attempts: {out_path.name}")
    return False


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for common_name, table in SPECIES:
        for year in YEARS:
            out_path = OUTPUT_DIR / f"{safe_species_filename(common_name)}_{year}_network.json"
            if out_path.exists():
                print(f"\n{common_name} {year}: already exists, skipping")
                continue
            print(f"\n{common_name} {year}")
            build_one(common_name, table, year, out_path)

    print("\nAll done.")


if __name__ == "__main__":
    main()
