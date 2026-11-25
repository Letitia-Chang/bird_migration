"""
experiment_results.py

Reproduces the live site's Experiments view offline, for every species and
year, and writes the summary used in the README.

This is a faithful Python port of the frontend logic in frontend/src/App.jsx
(buildYearSeasonNetwork, computeGraphMetrics, computeFrontendRobustness,
computeFrontendFragilityCurve), run on the same cached network data the API
serves (backend/saved_networks/). It adds more random trials and a fixed seed
so the numbers are stable, plus empirical p-values: the share of random
removals that fragment the network at least as much as removing the brightest
sites does. Besides plain random removal it compares against random sites
matched to the brightest ones on bird activity (the key confounder) and on
latitude, decile by decile.

Run:
    cd scripts && python experiment_results.py
Outputs:
    ../docs/results/experiment_summary.csv
    ../docs/results/fragility_curves.png
"""

import csv
import gzip
import json
import math
import random
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAVED = ROOT / "backend" / "saved_networks"
OUT = ROOT / "docs" / "results"

SPECIES = [
    "Ruby-throated Hummingbird",
    "Swainson's Thrush",
    "Magnolia Warbler",
    "Song Sparrow",
]
YEARS = [2024, 2025, 2026]

# Constants copied from frontend/src/App.jsx
CITY_BIN_DEGREES = 0.60
LIGHT_QUANTILE = 0.0
TOP_CITY_EDGES = 600
MIN_CITY_ACTIVITY = 1
REMOVAL_PCT = 0.30

TRIALS = 200
MATCHED_TRIALS = 500
CURVE_STEPS = 10
CURVE_TRIALS = 50
SEED = 42


def safe(species):
    return species.lower().replace(" ", "_").replace("'", "")


def load(species, year, month, kind):
    path = SAVED / f"{safe(species)}_{year}" / f"{month:02d}_{kind}.json.gz"
    return json.loads(gzip.decompress(path.read_bytes())) if path.exists() else []


def js_round(x):
    return math.floor(x + 0.5)


def cluster_key(lat, lng, b=CITY_BIN_DEGREES):
    return f"{js_round(lat / b) * b:.2f}_{js_round(lng / b) * b:.2f}"


def season(m):
    return "Q1" if m <= 3 else "Q2" if m <= 6 else "Q3" if m <= 9 else "Q4"


def build_year_network(species, year):
    """Port of buildYearSeasonNetwork: 12 months of nodes, edges for Jan–Nov."""
    node_rows = [r for m in range(1, 13) for r in load(species, year, m, "nodes")]
    edge_rows = [r for m in range(1, 12) for r in load(species, year, m, "edges")]
    if not node_rows:
        return [], []

    clusters, node_to_cluster = {}, {}
    for r in node_rows:
        cid = cluster_key(float(r["lat"]), float(r["lon"]))
        c = clusters.setdefault(cid, {"id": cid, "activity": 0, "light": 0.0})
        c["activity"] += float(r["grouped_count"] or 0)
        c["light"] = max(c["light"], float(r["nightlight_mean"] or 0))
        node_to_cluster[r["node_id"]] = cid

    lights = sorted(c["light"] for c in clusters.values())
    threshold = lights[0] if LIGHT_QUANTILE == 0 else lights[int(LIGHT_QUANTILE * (len(lights) - 1))]
    allowed = {
        cid for cid, c in clusters.items()
        if c["light"] >= threshold and c["activity"] >= MIN_CITY_ACTIVITY
    }

    agg = {}
    for e in edge_rows:
        s, t = node_to_cluster.get(e["source_node_id"]), node_to_cluster.get(e["target_node_id"])
        if not s or not t or s not in allowed or t not in allowed or s == t:
            continue
        key = (season(int(e["source_month"])), s, t)
        a = agg.setdefault(key, {"source": s, "target": t, "season": key[0], "w": 0.0})
        a["w"] += float(e["weight"] or 0)

    edges = []
    for q in ("Q1", "Q2", "Q3", "Q4"):
        es = sorted((a for a in agg.values() if a["season"] == q), key=lambda a: -a["w"])
        edges.extend(es[:TOP_CITY_EDGES])

    connected = {e["source"] for e in edges} | {e["target"] for e in edges}
    nodes = [
        {"id": cid, "light": c["light"], "activity": c["activity"],
         "lat": float(cid.split("_")[0])}
        for cid, c in clusters.items()
        if cid in allowed and cid in connected
    ]
    return nodes, edges


def lcc_ratio(nodes, edges, removed):
    """Port of computeGraphMetrics: largest weak component / original node count."""
    if not nodes:
        return 0.0
    active = [n["id"] for n in nodes if n["id"] not in removed]
    if not active:
        return 0.0
    adj = {i: set() for i in active}
    for e in edges:
        s, t = e["source"], e["target"]
        if s in adj and t in adj:
            adj[s].add(t)
            adj[t].add(s)
    seen, best = set(), 0
    for start in active:
        if start in seen:
            continue
        stack, size = [start], 0
        seen.add(start)
        while stack:
            cur = stack.pop()
            size += 1
            for nxt in adj[cur]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        best = max(best, size)
    return best / len(nodes)


def brightest(nodes, k):
    return {n["id"] for n in sorted(nodes, key=lambda n: -n["light"])[:k]}


def busiest(nodes, k):
    """Control: remove the sites with the most bird observations instead."""
    return {n["id"] for n in sorted(nodes, key=lambda n: -n["activity"])[:k]}


def decile_bins(nodes, key):
    """Assign each node to a decile bin of `key` (ties share a bin)."""
    vals = sorted(n[key] for n in nodes)
    cuts = [vals[int(q / 10 * (len(vals) - 1))] for q in range(1, 10)]
    return {n["id"]: sum(n[key] > c for c in cuts) for n in nodes}


def matched_random(rng, nodes, target_ids, key):
    """Random sites with the same decile mix of `key` as `target_ids`.

    This is the null that matters for the light question: if bright sites only
    look important because they are busy (or sit in a particular latitude
    band), removing random sites with the same activity (or latitude) profile
    should do just as much damage.
    """
    bins = decile_bins(nodes, key)
    pools = defaultdict(list)
    for n in nodes:
        pools[bins[n["id"]]].append(n["id"])
    need = defaultdict(int)
    for nid in target_ids:
        need[bins[nid]] += 1
    out = set()
    for b, c in need.items():
        out |= set(rng.sample(pools[b], c))
    return out


def spearman(a, b):
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for t in range(i, j + 1):
                r[order[t]] = (i + j) / 2
            i = j + 1
        return r
    ra, rb = ranks(a), ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = math.sqrt(sum((x - ma) ** 2 for x in ra))
    vb = math.sqrt(sum((y - mb) ** 2 for y in rb))
    return cov / (va * vb) if va and vb else 0.0


def random_ids(rng, nodes, k):
    return set(rng.sample([n["id"] for n in nodes], k))


def main():
    rng = random.Random(SEED)
    OUT.mkdir(parents=True, exist_ok=True)
    rows, curves = [], {}

    for sp in SPECIES:
        for yr in YEARS:
            nodes, edges = build_year_network(sp, yr)
            if not nodes:
                continue
            k = max(0, min(len(nodes), js_round(len(nodes) * REMOVAL_PCT)))
            base = lcc_ratio(nodes, edges, set())
            light = lcc_ratio(nodes, edges, brightest(nodes, k))
            busy = lcc_ratio(nodes, edges, busiest(nodes, k))
            bright_ids = brightest(nodes, k)
            act = [lcc_ratio(nodes, edges, matched_random(rng, nodes, bright_ids, "activity"))
                   for _ in range(MATCHED_TRIALS)]
            lat = [lcc_ratio(nodes, edges, matched_random(rng, nodes, bright_ids, "lat"))
                   for _ in range(MATCHED_TRIALS)]
            rand = [lcc_ratio(nodes, edges, random_ids(rng, nodes, k)) for _ in range(TRIALS)]
            rand_mean = sum(rand) / len(rand)
            p = sum(r <= light for r in rand) / len(rand)
            rows.append({
                "species": sp, "year": yr,
                "sites": len(nodes), "routes": len(edges),
                "baseline_lcc": round(base, 4),
                "lcc_after_brightest_30pct": round(light, 4),
                "lcc_after_random_30pct_mean": round(rand_mean, 4),
                "gap_pts": round((rand_mean - light) * 100, 1),
                "p_random_at_least_as_bad": round(p, 3),
                "lcc_after_activity_matched_30pct_mean": round(sum(act) / len(act), 4),
                "p_activity_matched_at_least_as_bad": round(sum(a <= light for a in act) / len(act), 3),
                "lcc_after_latitude_matched_30pct_mean": round(sum(lat) / len(lat), 4),
                "p_latitude_matched_at_least_as_bad": round(sum(a <= light for a in lat) / len(lat), 3),
                "lcc_after_busiest_30pct": round(busy, 4),
                "spearman_light_vs_activity": round(
                    spearman([n["light"] for n in nodes], [n["activity"] for n in nodes]), 2),
            })
            print(rows[-1])

            if yr == 2025:
                pts = []
                for step in range(CURVE_STEPS + 1):
                    kk = js_round(len(nodes) * step / CURVE_STEPS)
                    l = lcc_ratio(nodes, edges, brightest(nodes, kk))
                    b = lcc_ratio(nodes, edges, busiest(nodes, kk))
                    bk = brightest(nodes, kk)
                    m = sum(lcc_ratio(nodes, edges, matched_random(rng, nodes, bk, "activity"))
                            for _ in range(CURVE_TRIALS)) / CURVE_TRIALS
                    r = sum(lcc_ratio(nodes, edges, random_ids(rng, nodes, kk))
                            for _ in range(CURVE_TRIALS)) / CURVE_TRIALS
                    pts.append({"pct_removed": step / CURVE_STEPS, "brightest": l,
                                "random": r, "activity_matched": m, "busiest": b})
                curves[sp] = pts

    with open(OUT / "experiment_summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    json.dump(curves, open(OUT / "fragility_curves_2025.json", "w"), indent=1)
    print(f"\nWrote {OUT / 'experiment_summary.csv'}")


if __name__ == "__main__":
    main()
