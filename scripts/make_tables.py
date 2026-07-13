import os
import re
from datetime import datetime
import h5py
import numpy as np
import pandas as pd
import h3
from collections import Counter

# =========================
# CONFIG
# =========================

H3_RESOLUTION = 6
INPUT_DIR = "."   # current directory
OUTPUT_DIR = "chunked_night_light_data"

BASE_PATH = "HDFEOS/GRIDS/VIIRS_Grid_DNB_2d/Data Fields"
LIGHT_DATASET = f"{BASE_PATH}/AllAngle_Composite_Snow_Free"
LAT_DATASET = f"{BASE_PATH}/lat"
LON_DATASET = f"{BASE_PATH}/lon"

QUALITY_DATASET = f"{BASE_PATH}/AllAngle_Composite_Snow_Free_Quality"
NUM_DATASET = f"{BASE_PATH}/AllAngle_Composite_Snow_Free_Num"
LAND_WATER_DATASET = f"{BASE_PATH}/Land_Water_Mask"

os.makedirs(OUTPUT_DIR, exist_ok=True)

# =========================
# DATE PARSING
# =========================

def extract_year_month(filename):
    match = re.search(r"A(\d{4})(\d{3})", filename)
    if not match:
        return None, None

    year = int(match.group(1))
    doy = int(match.group(2))

    date = datetime.strptime(f"{year}-{doy}", "%Y-%j")
    return date.year, date.month


# =========================
# HELPERS
# =========================

def attr_to_scalar(value):
    if value is None:
        return None

    if isinstance(value, np.ndarray):
        if value.size == 0:
            return None
        value = value.flat[0]

    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore").strip()

    if isinstance(value, str):
        try:
            return float(value)
        except:
            return None

    return value


def apply_scaling_and_mask(dataset):
    arr = dataset[:].astype("float64")
    attrs = dataset.attrs

    # Fill values
    for key in ["_FillValue", "fillvalue", "FillValue", "missing_value"]:
        if key in attrs:
            fill_val = attr_to_scalar(attrs[key])
            if fill_val is not None:
                arr[arr == fill_val] = np.nan

    # Valid min/max
    valid_min = attr_to_scalar(attrs.get("valid_min"))
    valid_max = attr_to_scalar(attrs.get("valid_max"))

    if valid_min is not None:
        arr[arr < valid_min] = np.nan
    if valid_max is not None:
        arr[arr > valid_max] = np.nan

    # Scaling
    scale_factor = attr_to_scalar(attrs.get("scale_factor")) or 1.0
    add_offset = attr_to_scalar(attrs.get("add_offset")) or 0.0

    arr = arr * scale_factor + add_offset

    return arr


def read_optional_dataset(f, path):
    if path in f:
        return apply_scaling_and_mask(f[path])
    return None


def safe_mode(values):
    clean = [v for v in values if pd.notna(v)]
    if not clean:
        return np.nan
    return Counter(clean).most_common(1)[0][0]


# =========================
# CORE PROCESSING
# =========================

def process_file(filepath):
    filename = os.path.basename(filepath)
    year, month = extract_year_month(filename)

    if year is None:
        print(f"Skipping (no date found): {filename}")
        return

    print(f"Processing {filename} → {year}-{month:02d}")

    with h5py.File(filepath, "r") as f:
        light = apply_scaling_and_mask(f[LIGHT_DATASET])
        lat = f[LAT_DATASET][:].astype("float64")
        lon = f[LON_DATASET][:].astype("float64")

        quality = read_optional_dataset(f, QUALITY_DATASET)
        num_obs = read_optional_dataset(f, NUM_DATASET)
        land_water = read_optional_dataset(f, LAND_WATER_DATASET)

    # Build grid
    lon_grid, lat_grid = np.meshgrid(lon, lat)

    flat = pd.DataFrame({
        "lat": lat_grid.ravel(),
        "lon": lon_grid.ravel(),
        "nightlight": light.ravel(),
    })

    if quality is not None:
        flat["quality"] = quality.ravel()

    if num_obs is not None:
        flat["num_obs"] = num_obs.ravel()

    if land_water is not None:
        flat["land_water"] = land_water.ravel()

    flat = flat.dropna(subset=["lat", "lon", "nightlight"])

    # H3 mapping
    flat["h3_cell"] = flat.apply(
        lambda r: h3.latlng_to_cell(r["lat"], r["lon"], H3_RESOLUTION),
        axis=1
    )

    grouped_rows = []

    for cell, grp in flat.groupby("h3_cell"):
        row = {
            "h3_cell": cell,
            "year": year,
            "month": month,
            "pixel_count": len(grp),
            "nightlight_mean": grp["nightlight"].mean(),
            "nightlight_median": grp["nightlight"].median(),
            "nightlight_std": grp["nightlight"].std(),
            "nightlight_min": grp["nightlight"].min(),
            "nightlight_max": grp["nightlight"].max(),
        }

        if "quality" in grp:
            row["quality_mean"] = grp["quality"].mean()

        if "num_obs" in grp:
            row["num_obs_sum"] = grp["num_obs"].sum()

        if "land_water" in grp:
            row["land_water_mode"] = safe_mode(grp["land_water"].tolist())

        lat_c, lon_c = h3.cell_to_latlng(cell)
        row["cell_center_lat"] = lat_c
        row["cell_center_lon"] = lon_c

        grouped_rows.append(row)

    out = pd.DataFrame(grouped_rows)

    # Save
    output_file = os.path.join(OUTPUT_DIR, f"{year}_{month:02d}.csv")
    out.to_csv(output_file, index=False)

    print(f"Saved: {output_file} ({len(out)} rows)\n")


# =========================
# RUN ALL FILES
# =========================

def main():
    files = [f for f in os.listdir(INPUT_DIR) if f.endswith(".h5")]

    for file in files:
        process_file(os.path.join(INPUT_DIR, file))


if __name__ == "__main__":
    main()