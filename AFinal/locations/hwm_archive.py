"""
Caches the full USGS STN High-Water Marks dataset locally, one-time, so
per-episode HWM lookups are a local filter instead of a live API call.

query_usgs_hwms in pipeline (11).ipynb calls
https://stn.wim.usgs.gov/STNServices/HWMs.json fresh on every episode -- but
that endpoint returns the ENTIRE nationwide HWM dataset every time (39,769
records, ~26MB, ~8s), not something scoped to a bbox/date range server-side.
Re-fetching that per episode (135 episodes) would mean 135 x 26MB downloads
for data that's identical every time. Same "fetch the big dataset once,
slice locally many times" pattern as the sensor archive and the choropleth
geometry.
"""
from pathlib import Path

import pandas as pd
import requests

BASE_DIR = Path(__file__).parent
CACHE_PATH = BASE_DIR / 'hwm_archive.csv'

HWM_URL = 'https://stn.wim.usgs.gov/STNServices/HWMs.json'


def fetch_and_cache_hwm_archive(force=False):
    if CACHE_PATH.exists() and not force:
        return CACHE_PATH

    print('Fetching full USGS STN HWM archive (nationwide, ~26MB)...')
    r = requests.get(HWM_URL, timeout=60)
    r.raise_for_status()
    hwms = r.json()
    df = pd.DataFrame(hwms)

    lat_col = 'latitude_dd' if 'latitude_dd' in df.columns else 'latitude'
    lon_col = 'longitude_dd' if 'longitude_dd' in df.columns else 'longitude'
    for col in ['flagDate', 'surveyDate', 'approvalDate', 'flag_date', 'survey_date', 'approval_date']:
        if col in df.columns:
            df['date_parsed'] = pd.to_datetime(df[col], errors='coerce')
            break

    # Drop the nested 'files' column (photo attachment metadata) for the CSV
    # cache -- it's a list-of-dicts per row, not CSV-friendly, and isn't
    # needed for count/filter purposes. Anything that needs photo URLs
    # should hit the live API for that one HWM, not this archive.
    if 'files' in df.columns:
        df = df.drop(columns=['files'])

    df.to_csv(CACHE_PATH, index=False, encoding='utf-8')
    print(f'[OK] cached {len(df)} HWM records to {CACHE_PATH} (lat_col={lat_col}, lon_col={lon_col})')
    return CACHE_PATH


def load_hwm_archive():
    if not CACHE_PATH.exists():
        fetch_and_cache_hwm_archive()
    df = pd.read_csv(CACHE_PATH, low_memory=False, encoding='utf-8')
    if 'date_parsed' in df.columns:
        df['date_parsed'] = pd.to_datetime(df['date_parsed'], errors='coerce')
    return df


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    fetch_and_cache_hwm_archive(force=args.force)
