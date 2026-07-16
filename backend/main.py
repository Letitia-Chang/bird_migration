from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from pathlib import Path
import os
import random
from statistics import mean
import psycopg
from psycopg.rows import dict_row
import math
from collections import defaultdict
from functools import lru_cache
import json

### GLOBAL VARIABLES ###
SPECIES_DICT = {
    "Ruby-throated Hummingbird": "hummingbird",
    "Swainson's Thrush": "swainsons_thrush",
    "Magnolia Warbler": "magnolia_warbler",
    "Song Sparrow": "song_sparrow"
}

MONTH_DICT = {
    "January": 1, 
    "February": 2,
    "March": 3,
    "April": 4,
    "May": 5,
    "June": 6,
    "July": 7,
    "August": 8,
    "September": 9,
    "October": 10,
    "November": 11,
    "December": 12
}

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
        "https://bird-migration-git-main-ting-ya.vercel.app",
    ],
    allow_origin_regex=r"https://bird-migration.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SAVED_NETWORK_DIR = Path("saved_networks")

def safe_species_filename(species: str):
    return species.lower().replace(" ", "_").replace("'", "")

def get_saved_network_path(species: str, year: int):
    safe_species = safe_species_filename(species)
    return SAVED_NETWORK_DIR / f"{safe_species}_{year}_network.json"

@lru_cache(maxsize=None)
def load_saved_network(species: str, year: int):
    # A single network file can be 70+ MB; a full Network/Experiments view
    # load fires ~24 requests (12 months x nodes + edges), each of which used
    # to re-read and re-parse the whole file from scratch. Caching in memory
    # means the file is only ever parsed once per process, not once per
    # request — this was the dominant cost in slow page loads.
    path = get_saved_network_path(species, year)

    if not path.exists():
        return None

    with open(path, "r") as f:
        return json.load(f)

def get_connection():
    return psycopg.connect(
        host=os.environ["SUPABASE_DB_HOST"],
        dbname=os.environ.get("SUPABASE_DB_NAME", "postgres"),
        user=os.environ.get("SUPABASE_DB_USER", "postgres"),
        password=os.environ["SUPABASE_DB_PASSWORD"],
        port=os.environ.get("SUPABASE_DB_PORT", "5432"),
        sslmode="require",
        row_factory=dict_row,
    )

### edge helper function ###
def get_next_month_num(year: int, month_num: int):
    if month_num == 12:
        return year + 1, 1
    return year, month_num + 1

def haversine_miles(lat1, lon1, lat2, lon2):
    radius_miles = 3958.8

    lat1 = math.radians(float(lat1))
    lon1 = math.radians(float(lon1))
    lat2 = math.radians(float(lat2))
    lon2 = math.radians(float(lon2))

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )

    return radius_miles * 2 * math.asin(math.sqrt(a))


def compute_edges_backend(
    source_nodes,
    target_nodes,
    max_distance_miles=1500.0,
    min_distance_miles=0.0,
    min_count=6,
    top_k_per_source=10,
    top_k_per_target=10,
):
    scored_edges = []

    for source in source_nodes:
        if source["grouped_count"] < min_count:
            continue

        for target in target_nodes:
            if target["grouped_count"] < min_count:
                continue

            if source["h3_cell"] == target["h3_cell"]:
                continue

            distance_miles = haversine_miles(
                source["lat"],
                source["lon"],
                target["lat"],
                target["lon"],
            )

            if distance_miles < min_distance_miles:
                continue

            if distance_miles > max_distance_miles:
                continue

            weight = (
                source["grouped_count"] * target["grouped_count"]
            ) / (distance_miles + 1.0)

            scored_edges.append({
                "source_node_id": source["node_id"],
                "source_h3": source["h3_cell"],
                "source_year": source["year"],
                "source_month": source["month"],
                "source_count": source["grouped_count"],
                "source_lat": source["lat"],
                "source_lon": source["lon"],

                "target_node_id": target["node_id"],
                "target_h3": target["h3_cell"],
                "target_year": target["year"],
                "target_month": target["month"],
                "target_count": target["grouped_count"],
                "target_lat": target["lat"],
                "target_lon": target["lon"],

                "distance_miles": distance_miles,
                "weight": weight,
            })

    by_source = defaultdict(list)
    by_target = defaultdict(list)

    for edge in scored_edges:
        by_source[edge["source_node_id"]].append(edge)
        by_target[edge["target_node_id"]].append(edge)

    kept_by_source = set()
    kept_by_target = set()

    for source_id, edges in by_source.items():
        edges.sort(key=lambda e: (-e["weight"], e["distance_miles"]))

        for rank, edge in enumerate(edges[:top_k_per_source], start=1):
            edge["source_rank"] = rank
            kept_by_source.add(id(edge))

    for target_id, edges in by_target.items():
        edges.sort(key=lambda e: (-e["weight"], e["distance_miles"]))

        for rank, edge in enumerate(edges[:top_k_per_target], start=1):
            edge["target_rank"] = rank
            kept_by_target.add(id(edge))

    final_edges = []

    for edge in scored_edges:
        if id(edge) in kept_by_source and id(edge) in kept_by_target:
            final_edges.append(edge)

    final_edges.sort(key=lambda e: (-e["weight"], e["distance_miles"]))

    return final_edges


# =========================================================
# NEW HELPER FUNCTIONS FOR EXPERIMENTS
# =========================================================

def load_experiment_network(
    species: str,
    year: int,
    month: str,
    max_distance_miles: float = 1500.0,
    min_distance_miles: float = 0.0,
    min_count: int = 6,
    top_k_per_source: int = 10,
    top_k_per_target: int = 10,
):
    """
    Loads the same network inputs used by the frontend stress/network views.
    """
    nodes = _fetch_nodes_data(species, year, month)
    edges = _fetch_edges_data(
        species, year, month,
        max_distance_miles, min_distance_miles,
        min_count, top_k_per_source, top_k_per_target,
    )
    return nodes, edges


def build_active_graph(nodes, edges, removed_ids):
    """
    Keep only nodes/edges that remain after removal.
    Also filters edges to nodes that exist in the node table for this month.
    """
    active_nodes = [n for n in nodes if n["node_id"] not in removed_ids]
    active_node_ids = set(n["node_id"] for n in active_nodes)

    active_edges = []
    for e in edges:
        source_id = e["source_node_id"]
        target_id = e["target_node_id"]

        if source_id in active_node_ids and target_id in active_node_ids:
            active_edges.append(e)

    return active_nodes, active_edges


def compute_weak_components(nodes, edges):
    """
    Weak connectivity:
    treat directed edges as undirected when computing connected components.
    """
    adjacency = {n["node_id"]: set() for n in nodes}

    for e in edges:
        s = e["source_node_id"]
        t = e["target_node_id"]

        if s in adjacency and t in adjacency:
            adjacency[s].add(t)
            adjacency[t].add(s)

    visited = set()
    components = []

    for node_id in adjacency:
        if node_id in visited:
            continue

        stack = [node_id]
        component = []

        while stack:
            current = stack.pop()
            if current in visited:
                continue

            visited.add(current)
            component.append(current)

            for neighbor in adjacency[current]:
                if neighbor not in visited:
                    stack.append(neighbor)

        components.append(component)

    return components


def compute_fragility_metrics(nodes, edges, removed_ids):
    """
    Metrics are always normalized against the original node set.
    """
    total_nodes = len(nodes)

    if total_nodes == 0:
        return {
            "lcc": 0,
            "lcc_ratio": 0.0,
            "components": 0,
            "fragmentation": 1.0,
            "removed_count": 0,
        }

    active_nodes, active_edges = build_active_graph(nodes, edges, removed_ids)

    if len(active_nodes) == 0:
        return {
            "lcc": 0,
            "lcc_ratio": 0.0,
            "components": 0,
            "fragmentation": 1.0,
            "removed_count": len(removed_ids),
        }

    components = compute_weak_components(active_nodes, active_edges)
    lcc = max(len(c) for c in components) if components else 0
    lcc_ratio = lcc / total_nodes
    fragmentation = 1 - lcc_ratio

    return {
        "lcc": lcc,
        "lcc_ratio": lcc_ratio,
        "components": len(components),
        "fragmentation": fragmentation,
        "removed_count": len(removed_ids),
    }


def get_top_light_removed_ids(nodes, k: int):
    """
    Remove brightest nodes first.
    Treat missing light values as 0.
    """
    sorted_nodes = sorted(
        nodes,
        key=lambda n: (n["nightlight_mean"] if n["nightlight_mean"] is not None else 0),
        reverse=True,
    )
    return set(n["node_id"] for n in sorted_nodes[:k])


def average_metric_dict(metric_dicts):
    if not metric_dicts:
        return {
            "lcc": 0.0,
            "lcc_ratio": 0.0,
            "components": 0.0,
            "fragmentation": 1.0,
            "removed_count": 0.0,
        }

    keys = metric_dicts[0].keys()
    return {
        key: float(mean(m[key] for m in metric_dicts))
        for key in keys
    }


@app.get("/")
def root():
    return {"message": "Backend is running"}

@app.get("/bird-observations")
def get_bird_observations(
    species: str = Query(..., description="Common name of the bird species"),
    year: int = Query(..., description="Year to filter by"),
    month: str = Query(..., description="Month to filter by")
):
    table_name = SPECIES_DICT.get(species)
    month_num = MONTH_DICT.get(month)

    if not table_name:
        raise HTTPException(status_code=400, detail="Invalid species")
    if not month_num:
        raise HTTPException(status_code=400, detail="Invalid month")

    sql = f"""
    select
        r.common_name,
        r.scientific_name,
        o.year,
        o.month,
        o.h3_cell,
        h.cell_center_lat as lat,
        h.cell_center_lon as lon,
        o.grouped_count as observation_count
    from {table_name} o
    join species_ref r
        on o.species_id = r.species_id
    join h3_cells h
        on o.h3_cell = h.h3_cell
    where r.common_name = %s
    and o.year = %s
    and o.month = %s
    order by o.grouped_count desc
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (species, year, month_num))
                rows = cur.fetchall()
        return JSONResponse(content=rows)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/nightlight")
def get_nightlight(year: int = Query(..., description="Year to filter by")):
    sql = """
    select
        n.h3_cell,
        n.year,
        n.nightlight_mean,
        h.cell_center_lat as lat,
        h.cell_center_lon as lon
    from nightlight n
    join h3_cells h on n.h3_cell = h.h3_cell
    and n.year = %s
    order by n.nightlight_mean desc
    LIMIT 5000
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (year,))
                rows = cur.fetchall()
        return JSONResponse(content=rows)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

### nodes endpoint ###
def _fetch_nodes_data(species: str, year: int, month: str):
    """Raw list of node dicts — used directly by the /nodes route and, for
    the live-DB fallback path, called in-process by get_edges() (which needs
    a plain list to iterate, not an HTTP response object)."""
    table_name = SPECIES_DICT.get(species)
    month_num = MONTH_DICT.get(month)

    if not table_name:
        raise HTTPException(status_code=400, detail="Invalid species")
    if not month_num:
        raise HTTPException(status_code=400, detail="Invalid month")

    saved_network = load_saved_network(species, year)
    if saved_network is not None and month_num:
        return [
            node for node in saved_network["nodes"]
            if node["year"] == year and node["month"] == month_num
        ]

    sql = f"""
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
    from {table_name} o
    join species_ref r
        on o.species_id = r.species_id
    join h3_cells h
        on o.h3_cell = h.h3_cell
    left join nightlight n
        on o.h3_cell = n.h3_cell
       and o.year = n.year
    where r.common_name = %s
      and o.year = %s
      and o.month = %s
      and o.grouped_count >= 6
    order by o.grouped_count desc
    """

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (species, year, month_num))
                return cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/nodes")
def get_nodes(
    species: str = Query(..., description="Common name of the bird species"),
    year: int = Query(..., description="Year to filter by"),
    month: str = Query(..., description="Month to filter by")
):
    rows = _fetch_nodes_data(species, year, month)
    return JSONResponse(content=rows)

def _fetch_edges_data(
    species: str,
    year: int,
    month: str,
    max_distance_miles: float = 1500.0,
    min_distance_miles: float = 0.0,
    min_count: int = 6,
    top_k_per_source: int = 10,
    top_k_per_target: int = 10,
):
    """Raw list of edge dicts — used directly by the /edges route and, for
    the live-DB fallback path, by load_experiment_network() in-process."""
    month_num = MONTH_DICT.get(month)

    if not month_num:
        raise HTTPException(status_code=400, detail="Invalid month")

    saved_network = load_saved_network(species, year)

    if saved_network is not None:
        return [
            edge for edge in saved_network["edges"]
            if edge["source_year"] == year and edge["source_month"] == month_num
        ]

    next_year, next_month_num = get_next_month_num(year, month_num)

    next_month = None
    for name, number in MONTH_DICT.items():
        if number == next_month_num:
            next_month = name
            break

    if next_month is None:
        raise HTTPException(status_code=500, detail="Could not determine next month")

    try:
        source_nodes = _fetch_nodes_data(species, year, month)
        target_nodes = _fetch_nodes_data(species, next_year, next_month)

        return compute_edges_backend(
            source_nodes=source_nodes,
            target_nodes=target_nodes,
            max_distance_miles=max_distance_miles,
            min_distance_miles=min_distance_miles,
            min_count=min_count,
            top_k_per_source=top_k_per_source,
            top_k_per_target=top_k_per_target,
        )

    except HTTPException:
        raise

    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/edges")
def get_edges(
    species: str = Query(..., description="Common name of the bird species"),
    year: int = Query(..., description="Source year"),
    month: str = Query(..., description="Source month"),
    max_distance_miles: float = Query(1500.0, description="Maximum distance allowed for an edge"),
    min_distance_miles: float = Query(0.0, description="Minimum distance required for an edge"),
    min_count: int = Query(6, description="Minimum grouped_count threshold"),
    top_k_per_source: int = Query(10, description="Keep top K outgoing edges per source"),
    top_k_per_target: int = Query(10, description="Keep top K incoming edges per target")
):
    rows = _fetch_edges_data(
        species, year, month,
        max_distance_miles, min_distance_miles,
        min_count, top_k_per_source, top_k_per_target,
    )
    return JSONResponse(content=rows)


# =========================================================
# NEW EXPERIMENT APIs
# =========================================================

@app.get("/experiment/robustness")
def robustness_experiment(
    species: str = Query(..., description="Common name of the bird species"),
    year: int = Query(..., description="Year to filter by"),
    month: str = Query(..., description="Month to filter by"),
    removal_pct: float = Query(0.30, description="Fraction of nodes to remove (0 to 1)"),
    trials: int = Query(30, description="Number of random trials"),
    max_distance_miles: float = Query(1500.0, description="Maximum distance allowed for an edge"),
    min_distance_miles: float = Query(0.0, description="Minimum distance required for an edge"),
    min_count: int = Query(6, description="Minimum grouped_count threshold for experiment edges"),
    top_k_per_source: int = Query(10, description="Keep top K outgoing edges per source"),
    top_k_per_target: int = Query(10, description="Keep top K incoming edges per target"),
):
    try:
        if removal_pct < 0 or removal_pct > 1:
            raise HTTPException(status_code=400, detail="removal_pct must be between 0 and 1")
        if trials < 1:
            raise HTTPException(status_code=400, detail="trials must be >= 1")

        nodes, edges = load_experiment_network(
            species=species,
            year=year,
            month=month,
            max_distance_miles=max_distance_miles,
            min_distance_miles=min_distance_miles,
            min_count=min_count,
            top_k_per_source=top_k_per_source,
            top_k_per_target=top_k_per_target,
        )

        total_nodes = len(nodes)
        if total_nodes == 0:
            return {
                "species": species,
                "year": year,
                "month": month,
                "total_nodes": 0,
                "total_edges": len(edges),
                "removal_pct": removal_pct,
                "removal_count": 0,
                "baseline": {
                    "lcc": 0,
                    "lcc_ratio": 0.0,
                    "components": 0,
                    "fragmentation": 1.0,
                    "removed_count": 0,
                },
                "high_light": {
                    "lcc": 0,
                    "lcc_ratio": 0.0,
                    "components": 0,
                    "fragmentation": 1.0,
                    "removed_count": 0,
                },
                "random_avg": {
                    "lcc": 0.0,
                    "lcc_ratio": 0.0,
                    "components": 0.0,
                    "fragmentation": 1.0,
                    "removed_count": 0.0,
                },
                "trials": trials,
            }

        removal_count = min(max(int(round(total_nodes * removal_pct)), 0), total_nodes)

        baseline_metrics = compute_fragility_metrics(nodes, edges, removed_ids=set())

        high_light_removed = get_top_light_removed_ids(nodes, removal_count)
        high_light_metrics = compute_fragility_metrics(nodes, edges, high_light_removed)

        node_ids = [n["node_id"] for n in nodes]
        random_trial_metrics = []

        for _ in range(trials):
            random_removed = set(random.sample(node_ids, removal_count))
            random_trial_metrics.append(
                compute_fragility_metrics(nodes, edges, random_removed)
            )

        random_avg_metrics = average_metric_dict(random_trial_metrics)

        return {
            "species": species,
            "year": year,
            "month": month,
            "total_nodes": total_nodes,
            "total_edges": len(edges),
            "removal_pct": removal_pct,
            "removal_count": removal_count,
            "baseline": baseline_metrics,
            "high_light": high_light_metrics,
            "random_avg": random_avg_metrics,
            "trials": trials,
        }

    except HTTPException:
        raise
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/experiment/fragility-curve")
def fragility_curve_experiment(
    species: str = Query(..., description="Common name of the bird species"),
    year: int = Query(..., description="Year to filter by"),
    month: str = Query(..., description="Month to filter by"),
    steps: int = Query(10, description="Number of removal steps from 0% to 100%"),
    trials: int = Query(20, description="Number of random trials per step"),
    max_distance_miles: float = Query(1500.0, description="Maximum distance allowed for an edge"),
    min_distance_miles: float = Query(0.0, description="Minimum distance required for an edge"),
    min_count: int = Query(6, description="Minimum grouped_count threshold for experiment edges"),
    top_k_per_source: int = Query(10, description="Keep top K outgoing edges per source"),
    top_k_per_target: int = Query(10, description="Keep top K incoming edges per target"),
):
    try:
        if steps < 1:
            raise HTTPException(status_code=400, detail="steps must be >= 1")
        if trials < 1:
            raise HTTPException(status_code=400, detail="trials must be >= 1")

        nodes, edges = load_experiment_network(
            species=species,
            year=year,
            month=month,
            max_distance_miles=max_distance_miles,
            min_distance_miles=min_distance_miles,
            min_count=min_count,
            top_k_per_source=top_k_per_source,
            top_k_per_target=top_k_per_target,
        )

        total_nodes = len(nodes)
        if total_nodes == 0:
            return {
                "species": species,
                "year": year,
                "month": month,
                "total_nodes": 0,
                "total_edges": len(edges),
                "steps": steps,
                "trials": trials,
                "points": [],
            }

        node_ids = [n["node_id"] for n in nodes]
        sorted_nodes = sorted(
            nodes,
            key=lambda n: (n["nightlight_mean"] if n["nightlight_mean"] is not None else 0),
            reverse=True,
        )

        points = []

        for i in range(steps + 1):
            pct_removed = i / steps
            removal_count = min(max(int(round(total_nodes * pct_removed)), 0), total_nodes)

            # High-light removal
            high_removed = set(n["node_id"] for n in sorted_nodes[:removal_count])
            high_metrics = compute_fragility_metrics(nodes, edges, high_removed)

            # Random removal (averaged)
            random_trial_metrics = []
            for _ in range(trials):
                random_removed = set(random.sample(node_ids, removal_count))
                random_trial_metrics.append(
                    compute_fragility_metrics(nodes, edges, random_removed)
                )

            random_avg_metrics = average_metric_dict(random_trial_metrics)

            points.append({
                "pct_removed": pct_removed,
                "removal_count": removal_count,
                "light_based": high_metrics,
                "random_avg": random_avg_metrics,
            })

        return {
            "species": species,
            "year": year,
            "month": month,
            "total_nodes": total_nodes,
            "total_edges": len(edges),
            "steps": steps,
            "trials": trials,
            "points": points,
        }

    except HTTPException:
        raise
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail=str(e))