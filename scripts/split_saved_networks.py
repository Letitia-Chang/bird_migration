"""
split_saved_networks.py

Splits each backend/saved_networks/<species>_<year>_network.json into small,
pre-gzipped per-month files that the API can serve directly:

    backend/saved_networks/<species>_<year>/<MM>_nodes.json.gz
    backend/saved_networks/<species>_<year>/<MM>_edges.json.gz

Why: the full files are up to ~78 MB. Parsing one takes ~250 MB of RAM and
several seconds of CPU on a free-tier instance, and every /nodes or /edges
request then had to filter and re-serialize thousands of rows. Serving
pre-split, pre-compressed bytes makes those requests a file read.

The content is identical to what the API returned before:
  nodes for month M = nodes with year == <year> and month == M
  edges for month M = edges with source_year == <year> and source_month == M

Run after build_saved_networks.py:
    cd scripts && python split_saved_networks.py
"""

import gzip
import json
import sys
from pathlib import Path

SAVED_DIR = Path(__file__).resolve().parent.parent / "backend" / "saved_networks"


def split_one(path: Path) -> None:
    year = int(path.stem.split("_")[-2])
    out_dir = SAVED_DIR / path.stem.removesuffix("_network")
    out_dir.mkdir(exist_ok=True)

    with open(path) as f:
        data = json.load(f)

    for month in range(1, 13):
        nodes = [n for n in data["nodes"] if n["year"] == year and n["month"] == month]
        edges = [
            e for e in data["edges"]
            if e["source_year"] == year and e["source_month"] == month
        ]
        for kind, rows in (("nodes", nodes), ("edges", edges)):
            payload = json.dumps(rows, separators=(",", ":")).encode()
            # mtime=0 keeps the output byte-identical across runs (clean diffs)
            with open(out_dir / f"{month:02d}_{kind}.json.gz", "wb") as f:
                f.write(gzip.compress(payload, compresslevel=9, mtime=0))

    print(f"{path.name}: {len(data['nodes'])} nodes, {len(data['edges'])} edges -> {out_dir.name}/")


def main() -> None:
    files = sorted(SAVED_DIR.glob("*_network.json"))
    if not files:
        sys.exit(f"No *_network.json files found in {SAVED_DIR}")
    for path in files:
        split_one(path)


if __name__ == "__main__":
    main()
