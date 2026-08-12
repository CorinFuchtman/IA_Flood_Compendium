"""
Adds county FIPS/name to each sensor lookup table via the Census Bureau's
free geocoder (reverse geocode lat/lng -> county), so sensors can be matched
to a NOAA episode at COUNTY granularity in addition to the existing HUC8
granularity. NOAA episodes are already tagged with county FIPS
(STATE_FIPS+CZ_FIPS, see load_noaa_events in pipeline (11).ipynb), so this
uses the same 5-digit GEOID format for a direct join.

Writes *_with_county.csv alongside each source file; does not modify the
originals.
"""
import time
from pathlib import Path

import pandas as pd
import requests

BASE_DIR = Path(__file__).parent
SENSOR_DIR = BASE_DIR / 'sensor_csv_huc08'

FILES = [
    SENSOR_DIR / 'ifis_stream_sensors_huc08.csv',
    SENSOR_DIR / 'ifis_hydrostations_huc08.csv',
    SENSOR_DIR / 'usgs_stream_sensors_huc08_with_foreign_id1.csv',
]

GEOCODE_URL = 'https://geocoding.geo.census.gov/geocoder/geographies/coordinates'


def geocode_county(lat, lon, retries=3):
    params = {
        'x': lon, 'y': lat,
        'benchmark': 'Public_AR_Current',
        'vintage': 'Current_Current',
        'layers': 'Counties',
        'format': 'json',
    }
    for attempt in range(retries):
        try:
            r = requests.get(GEOCODE_URL, params=params, timeout=15)
            if r.status_code == 200:
                counties = r.json().get('result', {}).get('geographies', {}).get('Counties', [])
                if counties:
                    c = counties[0]
                    return c.get('GEOID'), c.get('NAME')
                return None, None
        except Exception:
            pass
        time.sleep(1)
    return None, None


def main():
    # Cache lookups across files -- many lat/lng values are shared/close and
    # some sensors appear in more than one table's neighborhood.
    cache = {}
    for f in FILES:
        df = pd.read_csv(f)
        fips_col, name_col = [], []
        for i, row in df.iterrows():
            lat, lon = row.get('lat'), row.get('lng')
            if pd.isna(lat) or pd.isna(lon):
                fips_col.append(None)
                name_col.append(None)
                continue
            key = (round(float(lat), 5), round(float(lon), 5))
            if key not in cache:
                cache[key] = geocode_county(lat, lon)
            fips, name = cache[key]
            fips_col.append(fips)
            name_col.append(name)
            if (i + 1) % 25 == 0:
                print(f'  {f.name}: {i+1}/{len(df)}')
        df['county_fips'] = fips_col
        df['county_name'] = name_col
        out_path = f.with_name(f.stem + '_with_county.csv')
        df.to_csv(out_path, index=False)
        n_missing = df['county_fips'].isna().sum()
        print(f'{f.name} -> {out_path.name}  ({len(df)} rows, {n_missing} unresolved)')


if __name__ == '__main__':
    main()
