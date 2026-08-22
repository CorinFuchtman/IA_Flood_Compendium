"""Validate the impact record files against the schema (impact_schema.md).

Checks, per file (impacts_noaa.csv, impacts_news.csv):
  - impact_id present and globally unique
  - coordinates inside an Iowa bounding box (with a small margin)
  - start_date <= end_date, ISO formatted; news end_date never after pub_date
    is NOT enforced here (pub_date can legitimately precede a range end when
    a closure persisted; the extraction rule applies at capture time)
  - severity in 0-3, confidence in A-C
  - impact_type, source_type, geom_type, flood_type, date_precision in the
    controlled vocabularies
  - every news record's source_id-equivalent (source_ref URL) appears in
    sources_news.csv
Exit code 1 on any failure; prints a summary either way.

Usage: python validate_impacts.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

IOWA = dict(lat=(40.2, 43.7), lon=(-96.8, -89.9))  # bbox with margin
IMPACT_TYPES = {"road_flooded", "road_closed", "bridge_damaged", "rescue",
                "evacuation", "home_flooded", "business_flooded",
                "agriculture", "infrastructure", "injury", "fatality",
                "river_overbank", "other"}
SOURCE_TYPES = {"noaa_narrative", "local_news", "agency_web"}
GEOM_TYPES = {"event_point", "place_centroid", "county_centroid"}
FLOOD_TYPES = {"flash", "river", "pluvial", "unknown"}
PRECISIONS = {"day", "range", "month"}


def check(df: pd.DataFrame, name: str, errors: list[str]) -> None:
    def bad(mask, msg):
        n = int(mask.sum())
        if n:
            errors.append(f"{name}: {n} records {msg}")

    bad(df.impact_id.isna() | (df.impact_id == ""), "missing impact_id")
    bad(~df.lat.between(*IOWA["lat"]), "latitude outside Iowa bbox")
    bad(~df.lon.between(*IOWA["lon"]), "longitude outside Iowa bbox")
    d0 = pd.to_datetime(df.start_date, errors="coerce")
    d1 = pd.to_datetime(df.end_date, errors="coerce")
    bad(d0.isna() | d1.isna(), "unparseable dates")
    bad(d1 < d0, "end_date before start_date")
    bad(~df.severity.isin([0, 1, 2, 3]), "severity outside 0-3")
    bad(~df.confidence.isin(list("ABC")), "confidence outside A-C")
    bad(~df.impact_type.isin(IMPACT_TYPES), "impact_type not in vocabulary")
    bad(~df.source_type.isin(SOURCE_TYPES), "source_type not in vocabulary")
    bad(~df.geom_type.isin(GEOM_TYPES), "geom_type not in vocabulary")
    bad(~df.flood_type.isin(FLOOD_TYPES), "flood_type not in vocabulary")
    bad(~df.date_precision.isin(PRECISIONS), "date_precision not in vocabulary")
    bad(df.text_span.isna() | (df.text_span.astype(str).str.strip() == ""),
        "empty text_span")
    bad(df.admin_fips.isna(), "missing county FIPS")


def main() -> int:
    errors: list[str] = []
    noaa = pd.read_csv(DATA / "impacts_noaa.csv")
    news = pd.read_csv(DATA / "impacts_news.csv")
    check(noaa, "impacts_noaa", errors)
    check(news, "impacts_news", errors)

    ids = pd.concat([noaa.impact_id, news.impact_id])
    dup = ids[ids.duplicated()]
    if len(dup):
        errors.append(f"{len(dup)} duplicate impact_ids across files")

    sources = pd.read_csv(DATA / "sources_news.csv")
    known = set(sources.url)
    missing = news[~news.source_ref.isin(known)]
    if len(missing):
        errors.append(f"{len(missing)} news records whose source_ref is not "
                      "in sources_news.csv")

    n_matched = int((news.episode_id.notna() & (news.episode_id != "")).sum())
    print(f"impacts_noaa: {len(noaa)} records | impacts_news: {len(news)} "
          f"({n_matched} episode-matched) | sources logged: {len(sources)}")
    if errors:
        print("VALIDATION FAILED:")
        for e in errors:
            print(" -", e)
        return 1
    print("All validation checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
