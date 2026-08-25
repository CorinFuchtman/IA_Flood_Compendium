"""Find satellite overpasses that fall inside each flood episode's window.

For every compendium episode this asks: which satellites flew over the flooded
counties between 48 hours before the episode started and 48 hours after it
ended, and exactly when. Only the overpass timing and footprint coverage are
recorded here, no pixels are downloaded.

Why this matters: a flood inundation mapping experiment needs an image taken
while the water was still on the ground. Knowing which episodes have an
overpass inside the flood window, and how many hours it sits from the episode
start, tells you which episodes can be validated against imagery at all.

Providers
---------
* Public STAC (no credentials needed, https://earth-search.aws.element84.com):
  Sentinel-2 L2A optical, Sentinel-1 GRD radar, Landsat Collection 2 L2.
  Sentinel-1 matters most for floods because radar sees through cloud, and
  flood days are usually cloudy.
* Planet (needs a key): PSScene, SkySatCollect, REOrthoTile through the Data
  API quick-search, reusing the search contract of the floodbench tool in
  Planet/floodbench. Set PL_API_KEY in the environment, or create
  ~/.pl_api_key, then re-run with --planet. Without a key the Planet provider
  is skipped and the run reports it as skipped rather than failing.

Method
------
1. AOI: dissolve the counties NOAA lists for the episode into one polygon
   (from the compendium's own county choropleth), then search its bounding box.
   Coverage is measured against the dissolved polygon, not the box.
2. Window: [episode begin - 48h, episode end + 48h]. NOAA stores Iowa event
   times as CST-6 all year, so UTC = local + 6 hours (see TZ_OFFSET_HOURS).
3. Scenes from one platform acquired within GROUP_MINUTES of each other are
   collapsed into a single overpass, since one pass drops many tiles. The
   overpass keeps the median acquisition time, the union coverage of the AOI,
   and the scene count.

Usage
-----
    python episode_satellite_overpasses.py                # STAC providers
    python episode_satellite_overpasses.py --planet       # add Planet
    python episode_satellite_overpasses.py --episodes 191899_0 193311_0
    python episode_satellite_overpasses.py --window-hours 72

Outputs data/episode_overpasses.csv (one row per overpass) and, through
build_imagery_index.py, the per-episode index that drives the website filter.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import time
from datetime import timedelta
from pathlib import Path

import pandas as pd
import requests
from pyproj import Geod
from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
NOAA_CSV = REPO / "AFinal" / "locations" / "noaa_21-25_with_huc_08.csv"
COUNTY_GEOJSON = REPO / "AFinal" / "locations" / "choropleth" / "by_county.geojson"
OUT_DIR = HERE / "data"
CACHE_DIR = HERE / "_cache"

STAC_URL = "https://earth-search.aws.element84.com/v1/search"
PLANET_URL = "https://api.planet.com/data/v1/quick-search"

# NOAA Storm Events stores Iowa times as CST-6 year round (CZ_TIMEZONE column).
TZ_OFFSET_HOURS = 6
WINDOW_HOURS = 48          # before episode begin and after episode end
GROUP_MINUTES = 20         # scenes this close from one platform = one overpass
STAC_PAGE = 100
STAC_MAX_PAGES = 10
SLEEP_S = 0.25

STAC_COLLECTIONS = {
    "sentinel-2-l2a": ("Sentinel-2", "optical"),
    "sentinel-1-grd": ("Sentinel-1", "radar"),
    "landsat-c2-l2": ("Landsat", "optical"),
}
PLANET_ITEM_TYPES = ("PSScene", "SkySatCollect", "REOrthoTile")

GEOD = Geod(ellps="WGS84")


def geodesic_area_m2(geom) -> float:
    if geom.is_empty:
        return 0.0
    return abs(GEOD.geometry_area_perimeter(geom)[0])


def load_api_key() -> str:
    """PL_API_KEY environment variable first, then ~/.pl_api_key."""
    key = os.environ.get("PL_API_KEY", "").strip()
    if not key:
        keyfile = Path.home() / ".pl_api_key"
        if keyfile.exists():
            key = keyfile.read_text().strip()
    return key


def load_episodes(window_hours: int) -> pd.DataFrame:
    """One row per episode: UTC search window and dissolved county AOI."""
    df = pd.read_csv(NOAA_CSV, encoding="utf-8-sig")
    df["fips"] = (df.STATE_FIPS.astype(int).astype(str).str.zfill(2)
                  + df.CZ_FIPS.astype(int).astype(str).str.zfill(3))
    df["b"] = pd.to_datetime(df.BEGIN_DATE_TIME)
    df["e"] = pd.to_datetime(df.END_DATE_TIME)

    counties = {}
    for f in json.loads(COUNTY_GEOJSON.read_text())["features"]:
        fips = str(f["properties"].get("county_fips")
                   or f["properties"].get("GEOID"))
        counties[fips] = shape(f["geometry"])

    rows = []
    off = timedelta(hours=TZ_OFFSET_HOURS)
    pad = timedelta(hours=window_hours)
    for eid, sub in df.groupby("NEW_EPISODE_ID"):
        geoms = [counties[f] for f in sub.fips.unique() if f in counties]
        if not geoms:
            print(f"warning: {eid} has no county geometry, skipped")
            continue
        aoi = unary_union(geoms)
        begin_utc = sub.b.min() + off
        end_utc = sub.e.max() + off
        rows.append({
            "episode_id": eid,
            "begin_utc": begin_utc,
            "end_utc": end_utc,
            "search_start": begin_utc - pad,
            "search_end": end_utc + pad,
            "counties": "; ".join(sorted(set(sub.CZ_NAME.str.title()))),
            "aoi": aoi,
            "aoi_area_m2": geodesic_area_m2(aoi),
        })
    return pd.DataFrame(rows)


def request_with_retry(method: str, url: str, max_attempts: int = 5, **kw):
    """Backoff on 429 and 5xx, honouring Retry-After; raise on final failure."""
    kw.setdefault("timeout", 90)
    last = None
    for attempt in range(max_attempts):
        r = requests.request(method, url, **kw)
        if r.status_code in (429, 500, 502, 503, 504):
            last = r
            wait = float(r.headers.get("Retry-After") or min(2 ** attempt, 30))
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r
    raise RuntimeError(f"{url} kept failing ({last.status_code if last else '?'}) "
                       f"after {max_attempts} attempts")


def iso(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


def search_stac(collection: str, bounds, start, end) -> list[dict]:
    """Paginated STAC item search; returns scene dicts."""
    body = {
        "collections": [collection],
        "bbox": [round(v, 5) for v in bounds],
        "datetime": f"{iso(start)}/{iso(end)}",
        "limit": STAC_PAGE,
    }
    out, url, payload = [], STAC_URL, body
    for _ in range(STAC_MAX_PAGES):
        r = request_with_retry("POST", url, json=payload)
        js = r.json()
        for f in js.get("features", []):
            p = f.get("properties", {})
            platform, sensor = STAC_COLLECTIONS[collection]
            out.append({
                "scene_id": f.get("id", ""),
                "acquired_utc": p.get("datetime") or p.get("start_datetime"),
                "platform": platform,
                "sensor_type": sensor,
                "instrument": p.get("platform") or p.get("constellation") or "",
                "cloud_pct": p.get("eo:cloud_cover"),
                "collection": collection,
                "geometry": f.get("geometry"),
            })
        nxt = [l for l in js.get("links", []) if l.get("rel") == "next"]
        if not nxt:
            break
        url = nxt[0]["href"]
        payload = nxt[0].get("body", {})
        if nxt[0].get("method", "POST").upper() == "GET":
            payload = None
    return out


def search_planet(api_key: str, aoi_geom: dict, start, end) -> list[dict]:
    """Planet Data API quick-search, same filter contract as floodbench."""
    body = {
        "item_types": list(PLANET_ITEM_TYPES),
        "filter": {"type": "AndFilter", "config": [
            {"type": "GeometryFilter", "field_name": "geometry",
             "config": aoi_geom},
            {"type": "DateRangeFilter", "field_name": "acquired",
             "config": {"gte": iso(start), "lte": iso(end)}}]},
    }
    headers = {"Authorization": f"api-key {api_key}"}
    out, url, payload = [], f"{PLANET_URL}?_page_size=250", body
    capped = True
    for _ in range(8):
        r = (request_with_retry("POST", url, json=payload, headers=headers)
             if payload is not None
             else request_with_retry("GET", url, headers=headers))
        js = r.json()
        for f in js.get("features", []):
            p = f.get("properties", {})
            cloud = p.get("cloud_percent")
            if cloud is None and p.get("cloud_cover") is not None:
                cloud = float(p["cloud_cover"]) * 100.0
            out.append({
                "scene_id": f.get("id", ""),
                "acquired_utc": p.get("acquired"),
                "platform": "Planet",
                "sensor_type": "optical",
                "instrument": p.get("instrument") or p.get("item_type") or "",
                "cloud_pct": cloud,
                "collection": p.get("item_type", ""),
                "geometry": f.get("geometry"),
            })
        nxt = js.get("_links", {}).get("_next")
        if not nxt:
            capped = False
            break
        url, payload = nxt, None
    if capped:
        print("  note: Planet paging cap reached (2000 scenes); overpass times "
              "are still correct but the scene counts undercount this window")
    return out


def group_overpasses(scenes: list[dict], ep) -> list[dict]:
    """Collapse scenes of one platform acquired close in time into one pass."""
    rows = []
    df = pd.DataFrame(scenes)
    if df.empty:
        return rows
    df = df[df.acquired_utc.notna()].copy()
    df["t"] = pd.to_datetime(df.acquired_utc, format="mixed", utc=True)
    df = df.sort_values(["platform", "t"])
    aoi, aoi_area = ep.aoi, ep.aoi_area_m2
    gap = pd.Timedelta(minutes=GROUP_MINUTES)

    for platform, sub in df.groupby("platform"):
        cluster_id = (sub.t.diff() > gap).cumsum()
        for _, grp in sub.groupby(cluster_id):
            geoms = []
            for g in grp.geometry:
                if not g:
                    continue
                try:
                    geoms.append(shape(g))
                except Exception:
                    continue
            cov = 0.0
            if geoms and aoi_area:
                inter = unary_union(geoms).intersection(aoi)
                cov = round(geodesic_area_m2(inter) / aoi_area, 4)
            t_mid = grp.t.median()
            begin_utc = pd.Timestamp(ep.begin_utc, tz="UTC")
            end_utc = pd.Timestamp(ep.end_utc, tz="UTC")
            during = begin_utc <= t_mid <= end_utc
            # pre  = before the flood started, usable as a dry baseline
            # during / post = the flood itself, usable to map inundation
            window_label = ("pre" if t_mid < begin_utc
                            else "during" if during else "post")
            clouds = grp.cloud_pct.dropna()
            rows.append({
                "episode_id": ep.episode_id,
                "overpass_utc": t_mid.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "platform": platform,
                "sensor_type": grp.sensor_type.iloc[0],
                "collection": grp.collection.iloc[0],
                "n_scenes": len(grp),
                "aoi_coverage": cov,
                "cloud_pct": (round(float(clouds.mean()), 1)
                              if len(clouds) else ""),
                "hours_from_begin": round(
                    (t_mid - begin_utc).total_seconds() / 3600.0, 2),
                "hours_after_end": round(
                    (t_mid - end_utc).total_seconds() / 3600.0, 2),
                "during_episode": during,
                "window_label": window_label,
                "scene_ids": ";".join(list(grp.scene_id)[:12]),
            })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--planet", action="store_true",
                    help="also query the Planet Data API (needs PL_API_KEY)")
    ap.add_argument("--window-hours", type=int, default=WINDOW_HOURS)
    ap.add_argument("--episodes", nargs="*", default=None,
                    help="limit to these episode ids")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    eps = load_episodes(args.window_hours)
    if args.episodes:
        eps = eps[eps.episode_id.isin(args.episodes)]

    planet_key = load_api_key() if args.planet else ""
    if args.planet and not planet_key:
        print("note: --planet requested but no PL_API_KEY or ~/.pl_api_key "
              "found; Planet provider skipped, STAC providers still run")

    all_rows: list[dict] = []
    for i, ep in enumerate(eps.itertuples(index=False), 1):
        cache = CACHE_DIR / f"{ep.episode_id}_{args.window_hours}h.json"
        if cache.exists() and not args.no_cache:
            all_rows.extend(json.loads(cache.read_text()))
            print(f"[{i}/{len(eps)}] {ep.episode_id}: cached")
            continue

        scenes: list[dict] = []
        bounds = ep.aoi.bounds
        for coll in STAC_COLLECTIONS:
            try:
                scenes += search_stac(coll, bounds, ep.search_start,
                                      ep.search_end)
            except Exception as exc:
                print(f"  {ep.episode_id} {coll} failed: {exc}")
            time.sleep(SLEEP_S)
        if planet_key:
            try:
                scenes += search_planet(planet_key,
                                        mapping(box(*bounds)),
                                        ep.search_start, ep.search_end)
            except Exception as exc:
                print(f"  {ep.episode_id} Planet failed: {exc}")
            time.sleep(SLEEP_S)

        rows = group_overpasses(scenes, ep)
        cache.write_text(json.dumps(rows))
        all_rows.extend(rows)
        plats = sorted({r["platform"] for r in rows})
        print(f"[{i}/{len(eps)}] {ep.episode_id}: {len(rows)} overpasses "
              f"from {len(scenes)} scenes {plats}")

    all_rows.sort(key=lambda r: (r["episode_id"], r["overpass_utc"]))
    out = OUT_DIR / "episode_overpasses.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        w.writeheader()
        w.writerows(all_rows)
    n_ep = len({r["episode_id"] for r in all_rows})
    print(f"\n{len(all_rows)} overpasses across {n_ep} episodes -> {out.name}")


if __name__ == "__main__":
    main()
