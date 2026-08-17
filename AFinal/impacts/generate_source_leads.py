"""Generate a per-episode source-hunting worksheet.

Emits data/episode_source_leads.csv: one row per compendium episode with its
dates, counties, NOAA place names, current impact coverage, and ready-made
news search links. This turns "check every episode for online resources" into
a checklist a student can work through top to bottom: open the link, apply
news_extraction_prompt.md to anything found, append the results to
news_extractions_raw.json, and re-run the pipeline.

Sorted by priority score (event count + damage) so the biggest uncovered
episodes come first.
"""
from __future__ import annotations

import csv
import re
import urllib.parse
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
NOAA_CSV = REPO / "AFinal" / "locations" / "noaa_21-25_with_huc_08.csv"
INDEX_CSV = HERE / "data" / "episode_impact_index.csv"
OUT = HERE / "data" / "episode_source_leads.csv"

MONTHS = ["", "January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def parse_damage(s) -> float:
    m = re.match(r"([\d.]+)([KMB]?)", str(s)) if pd.notna(s) else None
    return (float(m.group(1)) * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[m.group(2)]
            if m else 0.0)


def main() -> None:
    df = pd.read_csv(NOAA_CSV, encoding="utf-8-sig")
    df["dmg"] = df.DAMAGE_PROPERTY.map(parse_damage) + \
        df.DAMAGE_CROPS.map(parse_damage)
    df["b"] = pd.to_datetime(df.BEGIN_DATE_TIME)
    idx = pd.read_csv(INDEX_CSV).set_index("episode_id")

    rows = []
    for eid, sub in df.groupby("NEW_EPISODE_ID"):
        b, e = sub.b.min(), pd.to_datetime(sub.END_DATE_TIME).max()
        counties = sorted(set(sub.CZ_NAME.str.title()))
        places = sorted({str(p).title() for p in sub.BEGIN_LOCATION.dropna()
                         if str(p).strip()})[:5]
        month = f"{MONTHS[b.month]} {b.year}"
        q = f"Iowa flooding {counties[0]} County {month}"
        gnews = ("https://news.google.com/search?q=" +
                 urllib.parse.quote(q + " when:" + str(b.year)))
        gweb = ("https://www.google.com/search?q=" +
                urllib.parse.quote(f"{' OR '.join(places[:3]) or counties[0]} "
                                   f"Iowa flooding {month}"))
        meta = idx.loc[eid] if eid in idx.index else None
        rows.append({
            "episode_id": eid,
            "begin": b.strftime("%Y-%m-%d"),
            "end": e.strftime("%Y-%m-%d"),
            "n_events": len(sub),
            "counties": "; ".join(counties),
            "noaa_places": "; ".join(places),
            "damage_usd": int(sub.dmg.sum()),
            "deaths": int(sub.DEATHS_DIRECT.sum()),
            "n_impacts_noaa": int(meta.n_impacts_noaa) if meta is not None else 0,
            "n_impacts_crowd": int(meta.n_impacts_crowd) if meta is not None else 0,
            "status": ("covered" if meta is not None and meta.has_crowdsource
                       else "todo"),
            "priority": round(len(sub) + sub.dmg.sum() / 1e6
                              + 50 * sub.DEATHS_DIRECT.sum(), 1),
            "google_news_link": gnews,
            "google_search_link": gweb,
            "suggested_query": q,
        })

    rows.sort(key=lambda r: (-{"todo": 1, "covered": 0}[r["status"]],
                             -r["priority"]))
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    n_todo = sum(1 for r in rows if r["status"] == "todo")
    print(f"{len(rows)} episodes -> {OUT.name} "
          f"({n_todo} todo, {len(rows) - n_todo} covered)")


if __name__ == "__main__":
    main()
