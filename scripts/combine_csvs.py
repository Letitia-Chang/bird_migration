import pandas as pd
from pathlib import Path

# ===== EDIT THIS =====
DATA_DIR = Path(".")  # folder where your CSVs are
OUTPUT_FILE = "combined_nightlight.csv"
# =====================

def main():
    all_files = sorted(DATA_DIR.glob("*.csv"))

    dfs = []

    for file in all_files:
        # Expect format: YYYY_MM.csv
        try:
            name = file.stem  # e.g. "2024_09"
            year_str, month_str = name.split("_")
            year = int(year_str)
            month = int(month_str)
        except Exception:
            print(f"Skipping file (bad name format): {file.name}")
            continue

        print(f"Processing {file.name}...")

        df = pd.read_csv(file)

        # Force correct year/month (overwrite if already exists)
        df["year"] = year
        df["month"] = month

        dfs.append(df)

    if not dfs:
        raise ValueError("No valid CSV files found.")

    combined = pd.concat(dfs, ignore_index=True)

    print("Writing combined CSV...")
    combined.to_csv(OUTPUT_FILE, index=False)

    print(f"Done. Saved to {OUTPUT_FILE}")
    print(f"Total rows: {len(combined)}")


if __name__ == "__main__":
    main()