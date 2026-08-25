"""Roll satellite overpasses up to one row per episode.

Reads data/episode_overpasses.csv and writes data/episode_imagery_index.csv,
the table that drives the website's imagery filter and the per-episode
download bundles.

Grading
-------
Almost every episode has some satellite passing overhead within the search
window, so a plain "has imagery" flag would be true nearly everywhere and
would not help anyone choose an episode. Two things actually matter for a
flood inundation mapping experiment:

1. A FLOOD OBSERVATION: an image taken while the water was up, meaning during
   the episode or in the 48 hours after it ended, covering enough of the
   flooded counties, and either clear enough to see through or radar, which
   does not care about cloud. Flood days are usually cloudy, which is why
   Sentinel-1 radar is tracked separately.
2. A BASELINE: a clear image from the 48 hours before the episode began, so
   the flooded extent can be differenced against normal conditions.

Each episode is graded on the flood observation:

  clear   clear optical image during or after the flood
  radar   radar image during or after the flood (cloud does not matter)
  cloudy  passes happened, but only cloudy or low coverage optical ones
  none    no pass at all in the window

and separately flagged for whether a clear pre-flood baseline exists. An
episode graded clear or radar can validate a simulated flood map; one that
also has a baseline supports change detection.
"""
from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
NOAA_CSV = REPO / "AFinal" / "locations" / "noaa_21-25_with_huc_08.csv"
OVERPASSES = HERE / "data" / "episode_overpasses.csv"
OUT = HERE / "data" / "episode_imagery_index.csv"

MIN_COVERAGE = 0.30      # fraction of the episode AOI a pass must cover
MAX_CLOUD_CLEAR = 35.0   # percent cloud below which optical counts as clear


def main() -> None:
    ov = pd.read_csv(OVERPASSES)
    ov["cloud_pct"] = pd.to_numeric(ov.cloud_pct, errors="coerce")
    episodes = (pd.read_csv(NOAA_CSV, encoding="utf-8-sig")
                .NEW_EPISODE_ID.unique())

    rows = []
    for eid in sorted(episodes):
        sub = ov[ov.episode_id == eid]
        good = sub[sub.aoi_coverage >= MIN_COVERAGE]
        flood = good[good.window_label.isin(["during", "post"])]
        pre = good[good.window_label == "pre"]

        radar = flood[flood.sensor_type == "radar"]
        clear = flood[(flood.sensor_type == "optical")
                      & (flood.cloud_pct < MAX_CLOUD_CLEAR)]
        baseline = pre[(pre.sensor_type == "radar")
                       | (pre.cloud_pct < MAX_CLOUD_CLEAR)]

        if len(clear):
            grade = "clear"
            best = clear.sort_values(
                ["cloud_pct", "aoi_coverage"], ascending=[True, False]).iloc[0]
        elif len(radar):
            grade = "radar"
            best = radar.sort_values("aoi_coverage", ascending=False).iloc[0]
        elif len(sub):
            grade = "cloudy"
            best = sub.sort_values("aoi_coverage", ascending=False).iloc[0]
        else:
            grade = "none"
            best = None

        base_best = (baseline.sort_values("aoi_coverage", ascending=False).iloc[0]
                     if len(baseline) else None)

        rows.append({
            "episode_id": eid,
            "imagery_grade": grade,
            "has_flood_imagery": grade in ("clear", "radar"),
            "has_baseline_imagery": bool(len(baseline)),
            "n_overpasses": len(sub),
            "n_flood_window": len(flood),
            "n_during_episode": int((sub.window_label == "during").sum()),
            "n_radar_flood": len(radar),
            "n_clear_optical_flood": len(clear),
            "platforms": ";".join(sorted(sub.platform.unique())),
            "best_overpass_utc": best.overpass_utc if best is not None else "",
            "best_platform": best.platform if best is not None else "",
            "best_window_label": best.window_label if best is not None else "",
            "best_coverage": best.aoi_coverage if best is not None else "",
            "best_cloud_pct": ("" if best is None or pd.isna(best.cloud_pct)
                               else best.cloud_pct),
            "best_hours_from_begin": (best.hours_from_begin
                                      if best is not None else ""),
            "baseline_overpass_utc": (base_best.overpass_utc
                                      if base_best is not None else ""),
            "baseline_platform": (base_best.platform
                                  if base_best is not None else ""),
            "first_overpass_utc": (sub.overpass_utc.min() if len(sub) else ""),
            "last_overpass_utc": (sub.overpass_utc.max() if len(sub) else ""),
        })

    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    counts = pd.Series([r["imagery_grade"] for r in rows]).value_counts()
    usable = sum(1 for r in rows if r["has_flood_imagery"])
    both = sum(1 for r in rows
               if r["has_flood_imagery"] and r["has_baseline_imagery"])
    print(f"{len(rows)} episodes -> {OUT.name}")
    print(counts.to_string())
    print(f"{usable} episodes imaged during or just after the flood")
    print(f"{both} of those also have a clear pre-flood baseline")


if __name__ == "__main__":
    main()
