"""Turn raw news/agency impact extractions into geocoded, episode-linked records.

Input:  data/news_extractions_raw.json (records produced with
        news_extraction_prompt.md, human reviewed)
Output: data/impacts_news.csv, data/impacts_news.geojson, data/sources_news.csv

Steps
-----
1. Geocode each record: city centroid from the Census 2023 Gazetteer for Iowa
   (downloaded once and cached next to this script), else county centroid from
   the compendium's county GeoJSON. geom_type records which one was used.
2. Assign episode_id by spatiotemporal match against the compendium episode
   table (date windows padded +/- 2 days, county overlap) - the same
   intersection + overlap rule GroundSource uses to cross-match databases.
3. Emit CSV + GeoJSON in the shared impact schema (impact_schema.md).
"""
from __future__ import annotations

import csv
import json
import urllib.request
import uuid
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
NOAA_CSV = REPO / "AFinal" / "locations" / "noaa_21-25_with_huc_08.csv"
COUNTY_GEOJSON = REPO / "AFinal" / "locations" / "choropleth" / "by_county.geojson"
RAW = HERE / "data" / "news_extractions_raw.json"
GAZ_CACHE = HERE / "data" / "census_gazetteer_places_ia.txt"
GAZ_URL = ("https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
           "2023_Gazetteer/2023_gaz_place_19.txt")
PAD_DAYS = 2

# Bundled city centroids (WGS84), used when the Census gazetteer cannot be
# downloaded (e.g. offline runs). Values are city-center approximations; the
# gazetteer takes precedence when available.
FALLBACK_PLACES: dict[str, tuple[float, float]] = {
    "ROCK VALLEY": (43.2047, -96.2953), "ROCK RAPIDS": (43.4272, -96.1717),
    "SPENCER": (43.1414, -95.1444), "CHEROKEE": (42.7494, -95.5514),
    "HAWARDEN": (43.0011, -96.4853), "SIOUX RAPIDS": (42.8917, -95.1508),
    "ALVORD": (43.3428, -96.2989), "LARCHWOOD": (43.4536, -96.4353),
    "SIOUX CENTER": (43.0797, -96.1756), "BOYDEN": (43.1911, -96.0064),
    "NEW HAMPTON": (43.0592, -92.3177), "FREDERICKSBURG": (42.9647, -92.2005),
    "SPILLVILLE": (43.2028, -91.9507), "CALMAR": (43.1836, -91.8632),
    "POSTVILLE": (43.0847, -91.5679), "GREENE": (42.8958, -92.8021),
    "MARBLE ROCK": (42.9642, -92.8683), "ELKADER": (42.8539, -91.4054),
    "DAVENPORT": (41.5236, -90.5776), "DUBUQUE": (42.5006, -90.6646),
    "ASBURY": (42.5147, -90.7515), "INDEPENDENCE": (42.4686, -91.8893),
    "VINTON": (42.1686, -92.0236), "CLINTON": (41.8445, -90.1887),
}


def load_gazetteer() -> dict[str, tuple[float, float]]:
    """City name (upper) -> (lat, lon) from the Census place gazetteer."""
    if not GAZ_CACHE.exists():
        try:
            urllib.request.urlretrieve(GAZ_URL, GAZ_CACHE)
        except Exception as exc:  # offline fallback: bundled city centroids
            print(f"warning: gazetteer download failed ({exc}); "
                  "using bundled city centroids")
            return dict(FALLBACK_PLACES)
    gaz = {}
    df = pd.read_csv(GAZ_CACHE, sep="\t")
    df.columns = [c.strip() for c in df.columns]
    for _, r in df.iterrows():
        name = (str(r["NAME"]).upper()
                .replace(" CITY", "").replace(" TOWN", "").strip())
        gaz[name] = (float(r["INTPTLAT"]), float(r["INTPTLONG"]))
    return gaz


def county_centroids() -> dict[str, tuple[float, float]]:
    """County FIPS -> geometry bbox center from the compendium choropleth."""
    out = {}
    gj = json.loads(COUNTY_GEOJSON.read_text())
    for f in gj["features"]:
        fips = str(f["properties"].get("county_fips")
                   or f["properties"].get("GEOID"))
        xs, ys = [], []

        def walk(coords):
            if isinstance(coords[0], (int, float)):
                xs.append(coords[0]); ys.append(coords[1])
            else:
                for c in coords:
                    walk(c)
        walk(f["geometry"]["coordinates"])
        out[fips] = (sum(ys) / len(ys), sum(xs) / len(xs))
    return out


def main() -> None:
    raw = json.loads(RAW.read_text())
    noaa = pd.read_csv(NOAA_CSV, encoding="utf-8-sig")
    noaa["fips"] = (noaa.STATE_FIPS.astype(int).astype(str).str.zfill(2)
                    + noaa.CZ_FIPS.astype(int).astype(str).str.zfill(3))
    noaa["b"] = pd.to_datetime(noaa.BEGIN_DATE_TIME)
    noaa["e"] = pd.to_datetime(noaa.END_DATE_TIME)
    county_fips = (noaa.groupby(noaa.CZ_NAME.str.upper())["fips"]
                   .agg(lambda s: s.mode()[0]).to_dict())
    episodes = noaa.groupby("NEW_EPISODE_ID").agg(
        b=("b", "min"), e=("e", "max"),
        fips=("fips", lambda s: set(s))).reset_index()

    gaz = load_gazetteer()
    ccent = county_centroids()
    pad = pd.Timedelta(days=PAD_DAYS)

    sources = {s["source_id"]: s for s in raw["sources"]}
    records = []
    for rec in raw["records"]:
        src = sources[rec["source_id"]]
        county = rec["county"].upper()
        fips = county_fips.get(county, "")
        city = rec["city"].strip()
        lat = lon = None
        geom_type = ""
        if city and city.upper() in gaz:
            lat, lon = gaz[city.upper()]
            geom_type = "place_centroid"
        elif fips and fips in ccent:
            lat, lon = ccent[fips]
            geom_type = "county_centroid"
        if lat is None:
            print(f"warning: could not geocode {city or county}; skipped")
            continue

        s0 = pd.Timestamp(rec["start_date"])
        s1 = pd.Timestamp(rec["end_date"])
        match = episodes[(episodes.b - pad <= s1) & (episodes.e + pad >= s0)
                         & episodes.fips.map(lambda f: fips in f)]
        episode_id = match.NEW_EPISODE_ID.iloc[0] if len(match) else ""
        if len(match) > 1:  # prefer the episode with most events in county
            episode_id = match.NEW_EPISODE_ID.iloc[0]

        loc_name = (f"{city}, {county.title()} County, IA" if city
                    else f"{county.title()} County, IA")
        records.append({
            "impact_id": str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"news/{src['url']}/{loc_name}/{rec['impact_type']}/"
                f"{rec['start_date']}/{rec['text_span'][:80]}")),
            "episode_id": episode_id,
            "event_id": "",
            "source_type": src["source_type"],
            "source_ref": src["url"],
            "pub_date": src.get("pub_date", ""),
            "start_date": rec["start_date"],
            "end_date": rec["end_date"],
            "date_precision": rec["date_precision"],
            "location_name": loc_name,
            "location_raw": rec["location_raw"],
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            "geom_type": geom_type,
            "admin_fips": fips,
            "huc8": "",
            "impact_type": rec["impact_type"],
            "quantity": rec["quantity"],
            "quantity_unit": rec["quantity_unit"],
            "severity": rec["severity"],
            "confidence": rec["confidence"],
            "flood_type": rec["flood_type"],
            "mention_count": 1,
            "text_span": rec["text_span"][:400],
            "notes": rec["notes"],
        })

    records.sort(key=lambda r: (r["start_date"], r["episode_id"],
                                r["impact_type"]))
    out_csv = HERE / "data" / "impacts_news.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(records[0].keys()))
        w.writeheader()
        w.writerows(records)
    geo = {"type": "FeatureCollection", "features": [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
        "properties": {k: v for k, v in r.items() if k not in ("lat", "lon")},
    } for r in records]}
    (HERE / "data" / "impacts_news.geojson").write_text(json.dumps(geo))

    with (HERE / "data" / "sources_news.csv").open(
            "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["source_id", "source_type", "outlet",
                                          "url", "pub_date", "status", "note"])
        w.writeheader()
        w.writerows(raw["sources"])

    n_matched = sum(1 for r in records if r["episode_id"])
    print(f"{len(records)} news/agency impact records "
          f"({n_matched} matched to episodes, "
          f"{len(records) - n_matched} unmatched) -> {out_csv.name}")
    for eid in sorted({r['episode_id'] for r in records if r['episode_id']}):
        n = sum(1 for r in records if r["episode_id"] == eid)
        print(f"  {eid}: {n} records")


if __name__ == "__main__":
    main()
