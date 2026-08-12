"""
Builds the wizard's landing-page choropleth data: episode counts per county
and per HUC-8 watershed, each merged with real boundary geometry so they can
be rendered directly as a choropleth (Folium/Leaflet GeoJSON with a
`episode_count` property per feature).

Covers ALL 99 Iowa counties and all 56 HUC-8 watersheds touching Iowa, not
just the ones that had a NOAA-reported episode -- regions with no episodes
get episode_count=0/event_count=0 rather than being omitted, so the map
reads as a complete state picture instead of only the "hit" areas.

Counts distinct NEW_EPISODE_ID values touching each region, not raw event
rows -- one episode can span multiple counties/HUC8s (multiple rows in the
NOAA events table), and a raw row count would overweight episodes that
happened to generate more reported events.

Geometry sources (same ArcGIS REST pattern pipeline (11) already uses for
HUC8 boundaries, extended here to counties):
  - HUC8:    USGS National Map WBD MapServer (hydro.nationalmap.gov)
  - Counties: Census TIGERweb State_County MapServer

Output:
  choropleth/by_county.geojson  -- one feature per county, county_fips/
                                    county_name/episode_count properties
  choropleth/by_huc8.geojson    -- one feature per HUC8, huc8/name/
                                    episode_count properties

Geometry is fetched once and cached to these files; re-run with force=True
(or delete the files) to refresh after the NOAA events table changes.

Geometry is simplified (shapely, tolerance ~0.003 deg ~= 300m) before being
cached -- the raw ArcGIS geometry is full surveyed-boundary resolution,
which is total overkill for a state-zoom choropleth and made the combined
geometry ~30MB (mostly HUC8, which has more vertices than county lines).
Simplifying cut that to <1MB combined with no visible loss at this zoom
level, which matters because this geometry gets embedded directly in the
wizard's HTML (no server to fetch it from).
"""
from pathlib import Path

import pandas as pd
import requests
from shapely.geometry import shape, mapping

SIMPLIFY_TOLERANCE_DEG = 0.003

BASE_DIR = Path(__file__).parent
NOAA_EVENTS_FILE = BASE_DIR / 'noaa_21-25_with_huc_08.csv'
OUT_DIR = BASE_DIR / 'choropleth'

HUC8_QUERY_URL = 'https://hydro.nationalmap.gov/arcgis/rest/services/wbd/MapServer/4/query'
COUNTY_QUERY_URL = 'https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/11/query'


def _load_events():
    df = pd.read_csv(NOAA_EVENTS_FILE)
    df.columns = [c.upper().strip() for c in df.columns]
    df['FIPS_5'] = df['STATE_FIPS'].astype(str).str.replace(r'\.0$', '', regex=True).str.zfill(2) + \
                   df['CZ_FIPS'].astype(str).str.replace(r'\.0$', '', regex=True).str.zfill(3)
    df['HUC8_clean'] = df['HUC8'].fillna('').astype(str).str.replace(r'\.0$', '', regex=True).str.zfill(8)
    return df


def _county_episode_counts(df):
    # episode_count = distinct NEW_EPISODE_ID (storms), event_count = raw
    # NOAA event rows (a single episode/storm often logs several individual
    # events -- e.g. flash flood + flood + heavy rain -- against the same
    # county), so the two numbers answer different questions: "how many
    # separate storms hit this county" vs. "how many things got reported".
    counts = df.groupby('FIPS_5').agg(
        episode_count=('NEW_EPISODE_ID', 'nunique'),
        event_count=('EVENT_ID', 'count'),
        county_name=('CZ_NAME', 'first'),
    ).reset_index()
    return counts[counts['FIPS_5'] != '00000']


def _huc8_episode_counts(df):
    counts = df.groupby('HUC8_clean').agg(
        episode_count=('NEW_EPISODE_ID', 'nunique'),
        event_count=('EVENT_ID', 'count'),
        huc8_name=('NAME', 'first'),
    ).reset_index()
    return counts[counts['HUC8_clean'] != '00000000']


def _simplify_features(features, tolerance=SIMPLIFY_TOLERANCE_DEG):
    for feat in features:
        geom = shape(feat['geometry']).simplify(tolerance, preserve_topology=True)
        feat['geometry'] = mapping(geom)
    return features


def _batched(items, batch_size=40):
    items = list(items)
    for i in range(0, len(items), batch_size):
        yield items[i:i + batch_size]


def _fetch_county_geometries(fips_list):
    features = []
    for batch in _batched(fips_list):
        where = 'GEOID IN (' + ','.join(f"'{f}'" for f in batch) + ')'
        r = requests.get(COUNTY_QUERY_URL, params={
            'where': where, 'outFields': 'GEOID,NAME,BASENAME', 'f': 'geojson', 'outSR': '4326',
        }, timeout=30)
        r.raise_for_status()
        features.extend(r.json().get('features', []))
    return features


def _fetch_huc8_geometries(huc8_list):
    features = []
    for batch in _batched(huc8_list):
        where = 'huc8 IN (' + ','.join(f"'{h}'" for h in batch) + ')'
        r = requests.get(HUC8_QUERY_URL, params={
            'where': where, 'outFields': 'huc8,name', 'f': 'geojson', 'outSR': '4326', 'returnGeometry': 'true',
        }, timeout=30)
        r.raise_for_status()
        features.extend(r.json().get('features', []))
    return features


def _fetch_all_iowa_counties():
    """All 99 Iowa counties, not just the ones with a NOAA-reported episode."""
    r = requests.get(COUNTY_QUERY_URL, params={
        'where': "STATE='19'", 'outFields': 'GEOID,NAME,BASENAME', 'f': 'geojson', 'outSR': '4326',
    }, timeout=30)
    r.raise_for_status()
    return r.json().get('features', [])


def _fetch_all_iowa_huc8():
    """All HUC-8 watersheds whose 'states' field includes Iowa (56 basins) --
    these extend into neighboring states where the watershed itself does,
    same as the sensor lookup tables' HUC8 coverage."""
    r = requests.get(HUC8_QUERY_URL, params={
        'where': "states LIKE '%IA%'", 'outFields': 'huc8,name,states,areasqkm', 'f': 'geojson', 'outSR': '4326', 'returnGeometry': 'true',
    }, timeout=30)
    r.raise_for_status()
    return r.json().get('features', [])


def build_county_choropleth(force=False):
    out_path = OUT_DIR / 'by_county.geojson'
    if out_path.exists() and not force:
        return out_path

    df = _load_events()
    counts = _county_episode_counts(df)
    count_lookup = counts.set_index('FIPS_5')[['episode_count', 'event_count', 'county_name']].to_dict('index')

    from svi_data import svi_by_county_fips
    svi_lookup = svi_by_county_fips()

    print('Fetching geometry for all Iowa counties...')
    features = _simplify_features(_fetch_all_iowa_counties())
    for feat in features:
        geoid = feat['properties'].get('GEOID')
        info = count_lookup.get(geoid, {})
        feat['properties']['county_fips'] = geoid
        feat['properties']['episode_count'] = info.get('episode_count', 0)
        feat['properties']['event_count'] = info.get('event_count', 0)
        feat['properties']['county_name'] = info.get('county_name', feat['properties'].get('NAME'))
        feat['properties']['svi_overall'] = svi_lookup.get(geoid, {}).get('svi_overall')

    covered = sum(1 for f in features if f['properties']['episode_count'] > 0)
    print(f'  [OK] {len(features)} counties total, {covered} with at least one episode')

    import json
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({'type': 'FeatureCollection', 'features': features}))
    print(f'  [OK] wrote {out_path} ({len(features)} features)')
    return out_path


def build_huc8_choropleth(force=False):
    out_path = OUT_DIR / 'by_huc8.geojson'
    if out_path.exists() and not force:
        return out_path

    df = _load_events()
    counts = _huc8_episode_counts(df)
    count_lookup = counts.set_index('HUC8_clean')[['episode_count', 'event_count', 'huc8_name']].to_dict('index')

    print('Fetching geometry for all Iowa-touching HUC-8 basins...')
    features = _simplify_features(_fetch_all_iowa_huc8())
    for feat in features:
        huc8 = feat['properties'].get('huc8')
        info = count_lookup.get(huc8, {})
        feat['properties']['episode_count'] = info.get('episode_count', 0)
        feat['properties']['event_count'] = info.get('event_count', 0)
        feat['properties']['huc8_name'] = info.get('huc8_name', feat['properties'].get('name'))
        feat['properties']['area_sqkm'] = feat['properties'].get('areasqkm')

    covered = sum(1 for f in features if f['properties']['episode_count'] > 0)
    print(f'  [OK] {len(features)} HUC-8 basins total, {covered} with at least one episode')

    import json
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({'type': 'FeatureCollection', 'features': features}))
    print(f'  [OK] wrote {out_path} ({len(features)} features)')
    return out_path


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    build_county_choropleth(force=args.force)
    build_huc8_choropleth(force=args.force)
