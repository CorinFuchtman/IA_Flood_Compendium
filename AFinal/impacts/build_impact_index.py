"""Roll impact records up to one row per compendium episode.

Reads data/impacts_noaa.csv and data/impacts_news.csv, writes
data/episode_impact_index.csv with, per episode: counts by source, counts by
impact type, max severity, and has_crowdsource - the flag that drives the
website's "episodes with crowdsourced impact data" filter and makes episode
selection for modeling experiments a one-liner.
"""
from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
NOAA_CSV = REPO / "AFinal" / "locations" / "noaa_21-25_with_huc_08.csv"

IMPACT_TYPES = ["road_flooded", "road_closed", "bridge_damaged", "rescue",
                "evacuation", "home_flooded", "business_flooded",
                "agriculture", "infrastructure", "injury", "fatality",
                "river_overbank", "other"]


def main() -> None:
    noaa_imp = pd.read_csv(HERE / "data" / "impacts_noaa.csv")
    news_imp = pd.read_csv(HERE / "data" / "impacts_news.csv")
    allimp = pd.concat([noaa_imp, news_imp], ignore_index=True)
    allimp = allimp[allimp.episode_id.notna() & (allimp.episode_id != "")]

    episodes = pd.read_csv(NOAA_CSV, encoding="utf-8-sig") \
        .groupby("NEW_EPISODE_ID").size().index

    rows = []
    for eid in episodes:
        sub = allimp[allimp.episode_id == eid]
        news_sub = sub[sub.source_type.isin(["local_news", "agency_web"])]
        row = {
            "episode_id": eid,
            "n_impacts_total": len(sub),
            "n_impacts_noaa": int((sub.source_type == "noaa_narrative").sum()),
            "n_impacts_crowd": len(news_sub),
            "has_crowdsource": bool(len(news_sub)),
            "max_severity": int(sub.severity.max()) if len(sub) else 0,
            "impact_types": ";".join(sorted(sub.impact_type.unique())),
            "n_sources": sub.source_ref.nunique(),
        }
        for t in IMPACT_TYPES:
            row[f"n_{t}"] = int((sub.impact_type == t).sum())
        rows.append(row)

    out = HERE / "data" / "episode_impact_index.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    n_any = sum(1 for r in rows if r["n_impacts_total"])
    n_crowd = sum(1 for r in rows if r["has_crowdsource"])
    print(f"{len(rows)} episodes -> {out.name}: "
          f"{n_any} with impact records, {n_crowd} with crowdsourced records")


if __name__ == "__main__":
    main()
