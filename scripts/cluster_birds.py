import pandas as pd
import numpy as np
import hdbscan

file_path = "birds.txt"
chunk_size = 50000

target_species = None

all_chunks = []

for chunk in pd.read_csv(
    file_path,
    sep="\t",
    chunksize=chunk_size,
    low_memory=False,
    usecols=["COMMON NAME", "LATITUDE", "LONGITUDE"]
):
    chunk = chunk.dropna(subset=["LATITUDE", "LONGITUDE"])
    if target_species is not None:
        chunk = chunk[chunk["COMMON NAME"] == target_species]
    all_chunks.append(chunk[["LATITUDE", "LONGITUDE"]])

df = pd.concat(all_chunks, ignore_index=True)

print(f"Raw points kept: {len(df)}")

grid_size = 0.1

df["lat_bin"] = (df["LATITUDE"] / grid_size).round() * grid_size
df["lon_bin"] = (df["LONGITUDE"] / grid_size).round() * grid_size

grid = (
    df.groupby(["lat_bin", "lon_bin"])
      .size()
      .reset_index(name="n")
)

print(f"Grid cells after aggregation: {len(grid)}")
min_points_per_cell = 5
grid = grid[grid["n"] >= min_points_per_cell].copy()
print(f"Grid cells after minimum count filter: {len(grid)}")

coords = grid[["lat_bin", "lon_bin"]].to_numpy()
coords_rad = np.radians(coords)
clusterer = hdbscan.HDBSCAN(
    min_cluster_size=5,
    metric="haversine"
)
labels = clusterer.fit_predict(coords_rad)
grid["cluster"] = labels

cluster_coords = []

for cluster_id in sorted(grid["cluster"].unique()):
    if cluster_id == -1:
        continue
    cluster_points = grid[grid["cluster"] == cluster_id]
    weights = cluster_points["n"].to_numpy()
    lats = cluster_points["lat_bin"].to_numpy()
    lons = cluster_points["lon_bin"].to_numpy()
    centroid_lat = np.average(lats, weights=weights)
    centroid_lon = np.average(lons, weights=weights)
    cluster_coords.append((cluster_id, centroid_lat, centroid_lon, weights.sum()))

print("\nCluster centroids (lat, lon, total points):")
for cid, lat, lon, total in cluster_coords:
    print(f"Cluster {cid}: ({lat:.6f}, {lon:.6f})  sightings={total}")

import folium

m = folium.Map(location=[39.5, -98.35], zoom_start=4)

for cid, lat, lon, total in cluster_coords:
    folium.Marker(
        location=[lat, lon],
        popup=f"Cluster {cid} | sightings={total}"
    ).add_to(m)

m.save("bird_clusters_centroids_only.html")
print("Saved bird_clusters_centroids_only.html")