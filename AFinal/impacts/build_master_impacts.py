"""Build the database-ready master impact dataset.

Concatenates impacts_noaa.csv and impacts_news.csv into one table in the
shared schema, joins per-episode metadata (window, counties) so each row is
self-describing, and writes:

  data/impacts_master.csv       - one row per impact observation (all sources)
  data/impacts_master.geojson   - the same records as WGS84 points
  data/impacts_data_dictionary.csv - column name, type, definition, vocabulary

The master table is the file to load into a database. Examples:

  DuckDB:   SELECT * FROM read_csv_auto('impacts_master.csv');
  SQLite:   .import --csv impacts_master.csv impacts
  Python:   pd.read_csv('impacts_master.csv', dtype={'admin_fips': str})

Run after episode_impacts_extract.py and build_news_impacts.py.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
DATA = HERE / "data"
NOAA_CSV = REPO / "AFinal" / "locations" / "noaa_21-25_with_huc_08.csv"

DICTIONARY = [
    ("impact_id", "text (UUID)", "Stable unique identifier (uuid5 of source and content)", ""),
    ("episode_id", "text", "Compendium NEW_EPISODE_ID the record belongs to; blank if no episode matched", ""),
    ("event_id", "integer or blank", "NOAA EVENT_ID when the record comes from one storm event", ""),
    ("source_dataset", "text", "Which compendium file the row came from", "noaa | news"),
    ("source_type", "text", "Kind of source text", "noaa_narrative | local_news | agency_web"),
    ("source_ref", "text", "NOAA event reference or article URL", ""),
    ("pub_date", "date", "Publication date of the source (news only)", "YYYY-MM-DD"),
    ("start_date", "date", "First day the impact was observed", "YYYY-MM-DD"),
    ("end_date", "date", "Last day the impact was observed", "YYYY-MM-DD"),
    ("date_precision", "text", "How precisely the dates are known", "day | range | month"),
    ("location_name", "text", "Canonical place (City, County, IA)", ""),
    ("location_raw", "text", "Verbatim location words incl. street names", ""),
    ("lat", "real", "WGS84 latitude of the record point", ""),
    ("lon", "real", "WGS84 longitude of the record point", ""),
    ("geom_type", "text", "What the point represents", "event_point | place_centroid | county_centroid"),
    ("admin_fips", "text (5 chars)", "County FIPS; every record resolves at least to a county", ""),
    ("huc8", "text", "HUC8 watershed when known", ""),
    ("impact_type", "text", "Controlled impact vocabulary", "road_flooded | road_closed | bridge_damaged | rescue | evacuation | home_flooded | business_flooded | agriculture | infrastructure | injury | fatality | river_overbank | other"),
    ("quantity", "real or blank", "Number stated in the source (homes, people, feet, cfs)", ""),
    ("quantity_unit", "text", "Unit for quantity", ""),
    ("severity", "integer 0-3", "0 overbank only, 1 minor, 2 moderate, 3 major", "0 | 1 | 2 | 3"),
    ("confidence", "text", "A explicit and precisely located; B degraded precision; C single uncorroborated or inferred", "A | B | C"),
    ("flood_type", "text", "Flood process", "flash | river | pluvial | unknown"),
    ("mention_count", "integer", "Matching sentences (NOAA records); 1 for news", ""),
    ("text_span", "text", "Verbatim sentence supporting the record", ""),
    ("notes", "text", "Discrepancies, corroboration, proxies used", ""),
    ("episode_begin", "date", "First day of the parent episode (from NOAA events)", "YYYY-MM-DD"),
    ("episode_end", "date", "Last day of the parent episode", "YYYY-MM-DD"),
    ("episode_counties", "text", "Counties in the parent episode, semicolon separated", ""),
]


def main() -> None:
    noaa = pd.read_csv(DATA / "impacts_noaa.csv")
    news = pd.read_csv(DATA / "impacts_news.csv")
    noaa["source_dataset"] = "noaa"
    news["source_dataset"] = "news"
    master = pd.concat([noaa, news], ignore_index=True)

    ev = pd.read_csv(NOAA_CSV, encoding="utf-8-sig")
    ev["b"] = pd.to_datetime(ev.BEGIN_DATE_TIME)
    ev["e"] = pd.to_datetime(ev.END_DATE_TIME)
    meta = ev.groupby("NEW_EPISODE_ID").agg(
        episode_begin=("b", lambda s: s.min().strftime("%Y-%m-%d")),
        episode_end=("e", lambda s: s.max().strftime("%Y-%m-%d")),
        episode_counties=("CZ_NAME", lambda s: "; ".join(
            sorted(set(s.str.title())))),
    ).reset_index().rename(columns={"NEW_EPISODE_ID": "episode_id"})
    master = master.merge(meta, on="episode_id", how="left")

    cols = [d[0] for d in DICTIONARY]
    master = master[[c for c in cols if c in master.columns]]
    master = master.sort_values(
        ["start_date", "episode_id", "impact_type"]).reset_index(drop=True)

    out_csv = DATA / "impacts_master.csv"
    master.to_csv(out_csv, index=False)

    feats = []
    for r in master.itertuples(index=False):
        props = {k: (None if pd.isna(v) else v)
                 for k, v in r._asdict().items() if k not in ("lat", "lon")}
        feats.append({"type": "Feature",
                      "geometry": {"type": "Point",
                                   "coordinates": [r.lon, r.lat]},
                      "properties": props})
    (DATA / "impacts_master.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": feats}),
        encoding="utf-8")

    with (DATA / "impacts_data_dictionary.csv").open(
            "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["column", "type", "definition", "allowed_values"])
        w.writerows(DICTIONARY)

    n_ep = master.episode_id.replace("", pd.NA).nunique(dropna=True)
    print(f"{len(master)} master records across {n_ep} episodes -> "
          f"{out_csv.name}, impacts_master.geojson, "
          f"impacts_data_dictionary.csv")
    print(master.source_dataset.value_counts().to_string())


if __name__ == "__main__":
    main()
