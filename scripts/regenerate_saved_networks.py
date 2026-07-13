"""
regenerate_saved_networks.py

Rebuilds backend/saved_networks/*.json from the live database by calling the
running backend's own /nodes and /edges endpoints for every month, then
combining them into the same per-species-per-year JSON structure the app
already expects. Requires the saved_networks/*.json files to be absent (or
moved aside) for this to actually hit the live DB instead of returning
existing cache — main.py checks the cache file first.

Run: python regenerate_saved_networks.py
(with the backend running on BASE_URL below)
"""

import json
import requests
from pathlib import Path

BASE_URL = "http://127.0.0.1:8123"

SPECIES = [
    "Ruby-throated Hummingbird",
    "Swainson's Thrush",
    "Magnolia Warbler",
    "Song Sparrow",
]

YEARS = [2024, 2025, 2026]

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

OUTPUT_DIR = Path("../backend/saved_networks")


def safe_species_filename(species: str) -> str:
    return species.lower().replace(" ", "_").replace("'", "")


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for species in SPECIES:
        for year in YEARS:
            filename = OUTPUT_DIR / f"{safe_species_filename(species)}_{year}_network.json"
            if filename.exists():
                print(f"\n{species} {year}: already exists, skipping")
                continue

            print(f"\n{species} {year}")
            all_nodes = []
            all_edges = []

            for month in MONTHS:
                nodes_resp = requests.get(
                    f"{BASE_URL}/nodes",
                    params={"species": species, "year": year, "month": month},
                    timeout=600,
                )
                nodes_resp.raise_for_status()
                nodes = nodes_resp.json()
                all_nodes.extend(nodes)

                edges_resp = requests.get(
                    f"{BASE_URL}/edges",
                    params={"species": species, "year": year, "month": month},
                    timeout=600,
                )
                edges_resp.raise_for_status()
                edges = edges_resp.json()
                all_edges.extend(edges)

                print(f"  {month}: {len(nodes)} nodes, {len(edges)} edges")

            out = {
                "species": species,
                "year": year,
                "nodes": all_nodes,
                "edges": all_edges,
            }

            filename = OUTPUT_DIR / f"{safe_species_filename(species)}_{year}_network.json"
            with open(filename, "w") as f:
                json.dump(out, f)

            print(f"  -> wrote {filename} ({len(all_nodes)} nodes, {len(all_edges)} edges)")

    print("\nAll done.")


if __name__ == "__main__":
    main()
