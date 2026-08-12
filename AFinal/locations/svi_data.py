"""
Caches CDC/ATSDR Social Vulnerability Index (SVI) county-level data for
Iowa, sourced directly from svi2.cdc.gov -- no NASA Earthdata Login needed.
The user pointed at a NASA Earthdata Search collection ("U.S. Social
Vulnerability Index Grids") for this, which is the same underlying CDC/ATSDR
SVI data but as a 1km CONUS raster requiring an Earthdata Login account
(which Claude can't create/log into on the user's behalf) plus spatial
aggregation to county boundaries just to get back to what this endpoint
already provides directly: one SVI score per county, no login, no raster
processing.

Endpoint discovered by reading the SVI download tool's own JS
(svi2.cdc.gov/data-downloads/assets/main-*.js) -- not a NASA Earthdata
collection; it's the CDC/ATSDR SVI team's public download API:
  https://svi2.cdc.gov/webapi/Documents/download
    ?year=2022&type=csv&category=states_counties&name=IOWA_COUNTY

RPL_THEMES is the overall SVI percentile rank (0 = least vulnerable,
1 = most vulnerable, ranked against other Iowa counties). RPL_THEME1-4 are
the four sub-theme percentile ranks (Socioeconomic; Household Composition &
Disability; Minority Status & Language; Housing Type & Transportation), kept
in the cache for reference even though only the overall score drives the
wizard filter.
"""
from pathlib import Path

import pandas as pd
import requests

BASE_DIR = Path(__file__).parent
CACHE_PATH = BASE_DIR / 'svi_iowa_county_2022.csv'

SVI_YEAR = 2022
SVI_DOWNLOAD_URL = (
    f'https://svi2.cdc.gov/webapi/Documents/download'
    f'?year={SVI_YEAR}&type=csv&category=states_counties&name=IOWA_COUNTY'
)


def fetch_and_cache_svi(force=False):
    if CACHE_PATH.exists() and not force:
        return CACHE_PATH

    print(f'Fetching CDC/ATSDR SVI {SVI_YEAR} county data for Iowa...')
    r = requests.get(SVI_DOWNLOAD_URL, timeout=30)
    r.raise_for_status()
    CACHE_PATH.write_bytes(r.content)
    df = pd.read_csv(CACHE_PATH, encoding='utf-8-sig')
    print(f'[OK] cached {len(df)} Iowa counties to {CACHE_PATH}')
    return CACHE_PATH


def load_svi():
    if not CACHE_PATH.exists():
        fetch_and_cache_svi()
    df = pd.read_csv(CACHE_PATH, encoding='utf-8-sig')
    df['county_fips'] = df['FIPS'].astype(str).str.zfill(5)
    return df


def svi_by_county_fips():
    """Returns {county_fips: {'svi_overall': RPL_THEMES, 'svi_theme1'..4: ...}}"""
    df = load_svi()
    out = {}
    for _, row in df.iterrows():
        out[row['county_fips']] = {
            'svi_overall': None if pd.isna(row['RPL_THEMES']) else float(row['RPL_THEMES']),
            'svi_theme1_socioeconomic': None if pd.isna(row['RPL_THEME1']) else float(row['RPL_THEME1']),
            'svi_theme2_household': None if pd.isna(row['RPL_THEME2']) else float(row['RPL_THEME2']),
            'svi_theme3_minority_language': None if pd.isna(row['RPL_THEME3']) else float(row['RPL_THEME3']),
            'svi_theme4_housing_transport': None if pd.isna(row['RPL_THEME4']) else float(row['RPL_THEME4']),
        }
    return out


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    fetch_and_cache_svi(force=args.force)
    lookup = svi_by_county_fips()
    print(f'{len(lookup)} counties loaded, e.g. Johnson (19103): {lookup.get("19103")}')
