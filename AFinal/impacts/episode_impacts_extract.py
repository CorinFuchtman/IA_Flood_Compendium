"""Extract geo-referenced flood impact records from NOAA Storm Events narratives.

Reads the compendium's NOAA events table (one row per Flood / Flash Flood event,
2021-2025, all with BEGIN_LAT/BEGIN_LON) and mines EVENT_NARRATIVE text for
concrete impact mentions: flooded streets, closed roads, washed-out bridges,
water rescues, evacuations, flooded homes and businesses, and more.

Output: one row per (event, impact_type) in the schema documented in
AFinal/impacts/impact_schema.md (GroundSource / HANZE inspired).

Design rules
------------
* Deterministic and reproducible: pure regex rules, no network, no ML at this
  stage. The companion news pipeline (see news_extraction_prompt.md) uses
  large language models and writes to a separate file with its own provenance.
* Only explicit impact statements are extracted; forecasts, warnings, and
  "no damage" statements are skipped.
* Every record keeps the verbatim sentence (text_span) that produced it, the
  NOAA event coordinates (geom_type = event_point), county FIPS, and HUC8, so
  each record is auditable and joinable to any model footprint.

Usage
-----
    python episode_impacts_extract.py            # writes data/impacts_noaa.csv/.geojson
    python episode_impacts_extract.py --csv PATH # alternate input table
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import uuid
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
DEFAULT_INPUT = REPO / "AFinal" / "locations" / "noaa_21-25_with_huc_08.csv"
OUT_DIR = HERE / "data"

# ---------------------------------------------------------------------------
# Controlled vocabulary: impact_type -> (regex, severity 0-3)
# Severity scale (impact_schema.md): 0 nuisance / overbank only, 1 minor,
# 2 moderate (closures, structures, rescues), 3 major (loss of life,
# evacuations, destroyed structures).
# ---------------------------------------------------------------------------
RULES: list[tuple[str, re.Pattern, int]] = [
    ("fatality", re.compile(
        r"(drown|fatalit|died|death|body was recovered|killed)", re.I), 3),
    ("injury", re.compile(r"injur", re.I), 2),
    ("evacuation", re.compile(r"evacuat", re.I), 3),
    ("rescue", re.compile(
        r"(rescue|stranded (motorist|vehicle|driver|person)|pulled from|"
        r"swept (away|off|into)|submerged vehicle|vehicle.{0,40}stall)", re.I), 2),
    ("bridge_damaged", re.compile(
        r"bridge.{0,60}(washed out|damage|closed|impassable|out of service)|"
        r"(washed out|damage to).{0,40}bridge", re.I), 2),
    ("road_closed", re.compile(
        r"((?<!rail)road|street|highway|hwy|avenue|intersection|lane|us \d+|ia \d+|"
        r"interstate)[^.]{0,80}(closed|closure|barricad|impassable|"
        r"washed out|shut down)|closed?[^.]{0,50}((?<!rail)road|street|highway|due to "
        r"(flood|high )?water)", re.I), 2),
    ("road_flooded", re.compile(
        r"(water[^.]{0,60}(over|across|covering|covered|on) [^.]{0,50}"
        r"((?<!rail)road|street|highway|hwy|avenue|intersection|viaduct|underpass)|"
        r"((?<!rail)road|(?<!rail)roads|street|streets|highway|intersection|underpass|viaduct)"
        r"[^.]{0,60}(flood|inundat|under ?water|covered (by|in|with) water|"
        r"water covered))", re.I), 1),
    ("home_flooded", re.compile(
        r"(basement|home|house|residence|apartment|mobile home)[^.]{0,60}"
        r"(flood|inundat|water damage|under ?water|took on water)|"
        r"(flood|water)[^.]{0,40}(basement|home|house|residence)", re.I), 2),
    ("business_flooded", re.compile(
        r"(business|store|shop|restaurant|school|hospital|campus|church)"
        r"[^.]{0,60}(flood|inundat|water damage|under ?water|closed)", re.I), 2),
    ("agriculture", re.compile(
        r"(farmland|cropland|crops?|corn|soybean|field)[^.]{0,60}"
        r"(flood|inundat|under ?water|drowned out|damage|loss)", re.I), 1),
    ("infrastructure", re.compile(
        r"(levee|dam |dam\.|spillway|wastewater|water (treatment|plant)|"
        r"sewer|railroad|rail line|power (outage|lines?)|culvert)"
        r"[^.]{0,60}(breach|fail|overtop|washed out|damage|flood|inundat|"
        r"compromis|clos)", re.I), 2),
    ("river_overbank", re.compile(
        r"(out of (its |their )?banks?|overflow|over its banks|"
        r"(major|moderate|record) flood(ing)? (stage|crest)|"
        r"crested? (at|above)|exceed(ed)? (its )?flood stage)", re.I), 0),
]

ROAD_NAME = re.compile(
    r"\b((?:U\.?S\.?|Iowa|IA|State )?(?:Highway|Hwy\.?|Route) ?\d+[A-Z]?|"
    r"Interstate ?\d+|I-\d+|"
    r"(?:[A-Z][a-z0-9']+ ){0,3}(?:[A-Z][a-z0-9']+|\d+(?:st|nd|rd|th))\s"
    r"(?:Street|St\.?|Avenue|Ave\.?|Road|Rd\.?|Drive|Dr\.?|Boulevard|Blvd\.?|"
    r"Lane|Ln\.?|Trail|Parkway|Pkwy)\b|"
    r"\b[A-Z]\d{2,3}\b)")  # county roads like C13, L40

QUANTITY = re.compile(
    r"\b(\d{1,4})\s+(homes?|houses?|residences?|basements?|businesses?|"
    r"people|persons|residents|vehicles?|cars?|roads?|streets?|rescues?)\b",
    re.I)

NEGATION = re.compile(r"\b(no (flood|damage|impact)|did not|without (any )?"
                      r"(flood|damage)|time (was )?estimated|estimated based on"
                      r"|based on radar)\b", re.I)

SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def county_fips(row) -> str:
    try:
        return f"{int(row.STATE_FIPS):02d}{int(row.CZ_FIPS):03d}"
    except Exception:
        return ""


def norm_date(s: str) -> str:
    """NOAA 'M/D/YYYY H:MM' -> YYYY-MM-DD."""
    try:
        return pd.to_datetime(s).strftime("%Y-%m-%d")
    except Exception:
        return ""


def extract_from_row(row) -> list[dict]:
    text = row.EVENT_NARRATIVE
    if not isinstance(text, str) or not text.strip():
        return []
    records: dict[str, dict] = {}
    for sentence in SENT_SPLIT.split(text.strip()):
        if NEGATION.search(sentence):
            continue
        for impact_type, pattern, severity in RULES:
            if not pattern.search(sentence):
                continue
            roads = ROAD_NAME.findall(sentence)
            qty = QUANTITY.search(sentence)
            rec = records.get(impact_type)
            if rec is None:
                rec = records[impact_type] = {
                    "impact_id": str(uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"noaa/{row.EVENT_ID}/{impact_type}")),
                    "episode_id": row.NEW_EPISODE_ID,
                    "event_id": int(row.EVENT_ID),
                    "source_type": "noaa_narrative",
                    "source_ref": f"NOAA StormEvents EVENT_ID {row.EVENT_ID}",
                    "pub_date": "",
                    "start_date": norm_date(row.BEGIN_DATE_TIME),
                    "end_date": norm_date(row.END_DATE_TIME),
                    "date_precision": "day",
                    "location_name": f"{str(row.CZ_NAME).title()} County, IA",
                    "location_raw": "",
                    "lat": round(float(row.BEGIN_LAT), 5),
                    "lon": round(float(row.BEGIN_LON), 5),
                    "geom_type": "event_point",
                    "admin_fips": county_fips(row),
                    "huc8": str(row.HUC8) if pd.notna(row.HUC8) else "",
                    "impact_type": impact_type,
                    "quantity": "",
                    "quantity_unit": "",
                    "severity": severity,
                    "confidence": "A",
                    "flood_type": ("flash" if row.EVENT_TYPE == "Flash Flood"
                                   else "river"),
                    "mention_count": 0,
                    "text_span": sentence.strip()[:400],
                    "notes": "",
                }
            rec["mention_count"] += 1
            if roads and not rec["location_raw"]:
                names = sorted({(r[0] if isinstance(r, tuple) else r).strip()
                                for r in roads if str(r).strip()})
                rec["location_raw"] = "; ".join(names)[:200]
            if qty and not rec["quantity"]:
                rec["quantity"] = qty.group(1)
                rec["quantity_unit"] = qty.group(2).lower()
    return list(records.values())


def to_geojson(records: list[dict]) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [r["lon"], r["lat"]]},
            "properties": {k: v for k, v in r.items()
                           if k not in ("lat", "lon")},
        } for r in records],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = ap.parse_args()

    df = pd.read_csv(args.csv, encoding="utf-8-sig")
    records: list[dict] = []
    for row in df.itertuples(index=False):
        records.extend(extract_from_row(row))
    records.sort(key=lambda r: (r["start_date"], r["episode_id"],
                                r["event_id"], r["impact_type"]))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_csv = args.out_dir / "impacts_noaa.csv"
    fields = list(records[0].keys())
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(records)
    out_geo = args.out_dir / "impacts_noaa.geojson"
    out_geo.write_text(json.dumps(to_geojson(records)), encoding="utf-8")

    n_ep = len({r["episode_id"] for r in records})
    n_ev = len({r["event_id"] for r in records})
    print(f"{len(records)} impact records from {n_ev} events "
          f"in {n_ep} episodes -> {out_csv.name}, {out_geo.name}")
    by_type = pd.Series([r["impact_type"] for r in records]).value_counts()
    print(by_type.to_string())


if __name__ == "__main__":
    main()
