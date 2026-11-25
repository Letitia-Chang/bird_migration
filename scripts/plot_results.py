"""Draws docs/results/fragility_curves_2025.png from experiment_results.py output."""
import json
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "docs" / "results"
curves = json.load(open(RES / "fragility_curves_2025.json"))

SURFACE, INK, MUTED, GRID = "#fcfcfb", "#1f1f1e", "#6b6b68", "#e6e5e1"
SERIES = [  # (key, label, color, linestyle)
    ("brightest", "Brightest sites removed first", "#eb6834", "-"),
    ("random", "Random sites (avg)", "#2a78d6", "--"),
    ("activity_matched", "Random sites with the same bird activity (avg)", "#1baf7a", ":"),
]
ORDER = ["Swainson's Thrush", "Magnolia Warbler", "Ruby-throated Hummingbird", "Song Sparrow"]

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
fig, axes = plt.subplots(1, 4, figsize=(15, 4.1), sharey=True, facecolor=SURFACE)
for i, (ax, sp) in enumerate(zip(axes, ORDER)):
    pts = curves[sp]
    x = [p["pct_removed"] * 100 for p in pts]
    ax.set_facecolor(SURFACE)
    for key, label, color, ls in SERIES:
        ax.plot(x, [p[key] * 100 for p in pts], color=color, lw=2, ls=ls,
                marker="o", ms=3.5, label=label)
    ax.set_title(sp, color=INK, fontsize=11, loc="left")
    ax.set_xlim(0, 100); ax.set_ylim(0, 100)
    ax.set_xticks([0, 30, 60, 90])
    ax.axvline(30, color=GRID, lw=1, zorder=0)
    ax.grid(axis="y", color=GRID, lw=0.8); ax.set_axisbelow(True)
    for s in ("top", "right"): ax.spines[s].set_visible(False)
    for s in ("left", "bottom"): ax.spines[s].set_color(GRID)
    ax.tick_params(colors=MUTED)
    ax.set_xlabel("% of sites removed", color=MUTED)
    if i == 0:
        ax.set_ylabel("Largest connected network (% of sites)", color=MUTED)

handles, labels = axes[0].get_legend_handles_labels()
fig.legend(handles, labels, loc="upper center", ncol=3, frameon=False,
           bbox_to_anchor=(0.5, 1.02), labelcolor=INK)
fig.text(0.01, -0.04,
         "Full-year 2025 migration networks. Random and activity-matched = mean of 50 draws per step. "
         "Gray line marks the 30% removal level used in the summary table.",
         color=MUTED, fontsize=8.5)
fig.tight_layout(rect=(0, 0, 1, 0.92))
fig.savefig(RES / "fragility_curves_2025.png", dpi=150, bbox_inches="tight", facecolor=SURFACE)
print("saved")
