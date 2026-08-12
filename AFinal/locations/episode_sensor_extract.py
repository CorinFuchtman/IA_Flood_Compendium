"""
Per-episode sensor extraction, sourced from ownCloud.

Replaces pipeline (11).ipynb's live, short-window sensor fetch
(fetch_ifc_sensor_data/fetch_ifc_hydrostation_data/fetch_usgs_sensor_data,
called with a +-3h padded window around one NOAA episode) with a slice pulled
out of the 2021-2025 archive stored on the shared ownCloud folder: no live API
calls, no missing data from a fetch failing mid-episode, and any padding
window is free since the full 5-year record is already available.

ownCloud is the source of truth, not the local sensor_data_wy2021_2025/
folder -- that folder is only a local cache of the three zip bundles
(IFC_river_sensors_WY2021-2025.zip, IFC_hydrostations_WY2021-2025.zip,
USGS_sensors_WY2021-2025.zip) uploaded there. sync_from_owncloud() checks
each zip's ETag via WebDAV PROPFIND and only re-downloads/re-extracts when
the remote file actually changed, so repeated runs don't re-pull ~440MB
every time. Call it explicitly (or rely on build_episode_sensor_bundle's
default sync=True) before reading from the cache.

Adds a second sensor-matching mode alongside pipeline (11)'s existing HUC8
matching: county-level, using the county FIPS added by
enrich_sensors_with_county.py (geocoded via the Census Bureau's coordinate
geocoder). NOAA episodes are already tagged with county FIPS (see
load_noaa_events below, FIPS_5 = STATE_FIPS+CZ_FIPS), so county matching is a
direct join -- no new geocoding needed at extraction time.

Usage:
    from episode_sensor_extract import build_episode_sensor_bundle
    bundle = build_episode_sensor_bundle('191899_0', granularity='huc8')
    bundle = build_episode_sensor_bundle('191899_0', granularity='county', pad_hours=72)
"""
import json
import shutil
import zipfile
from datetime import timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

import pandas as pd
import requests

BASE_DIR = Path(__file__).parent
SENSOR_DIR = BASE_DIR / 'sensor_csv_huc08'
ARCHIVE_DIR = BASE_DIR / 'sensor_data_wy2021_2025'  # local cache of the ownCloud zips
NOAA_EVENTS_FILE = BASE_DIR / 'noaa_21-25_with_huc_08.csv'

SENSOR_TABLES = {
    'ifc_river':        (SENSOR_DIR / 'ifis_stream_sensors_huc08_with_county.csv',                    ARCHIVE_DIR / 'ifc_river_sensors'),
    'ifc_hydrostation': (SENSOR_DIR / 'ifis_hydrostations_huc08_with_county.csv',                      ARCHIVE_DIR / 'ifc_hydrostations'),
    'usgs':             (SENSOR_DIR / 'usgs_stream_sensors_huc08_with_foreign_id1_with_county.csv',    ARCHIVE_DIR / 'usgs_sensors'),
}

# ── ownCloud sync ────────────────────────────────────────────────────────────
OWNCLOUD_SHARE_TOKEN = 'pLMo9aGbKKsrJNh'
OWNCLOUD_WEBDAV_BASE = 'https://transit.engineering.uiowa.edu/owncloud/public.php/webdav/'
OWNCLOUD_ZIP_MANIFEST = {
    'ifc_river':        'IFC_river_sensors_WY2021-2025.zip',
    'ifc_hydrostation': 'IFC_hydrostations_WY2021-2025.zip',
    'usgs':             'USGS_sensors_WY2021-2025.zip',
}
SYNC_STATE_FILE = ARCHIVE_DIR / '.owncloud_sync_state.json'
_DAV_NS = {'d': 'DAV:'}


def _webdav_etag(zip_name):
    resp = requests.request(
        'PROPFIND', OWNCLOUD_WEBDAV_BASE + zip_name,
        auth=(OWNCLOUD_SHARE_TOKEN, ''), headers={'Depth': '0'}, timeout=30,
    )
    if resp.status_code != 207:
        return None
    root = ET.fromstring(resp.content)
    etag_el = root.find('.//d:getetag', _DAV_NS)
    return etag_el.text.strip('"') if etag_el is not None and etag_el.text else None


def sync_from_owncloud(force=False):
    """
    Ensures the local cache under ARCHIVE_DIR matches what's currently on
    ownCloud. Compares each zip's WebDAV ETag against the last-synced value
    recorded in SYNC_STATE_FILE; only downloads+extracts a zip whose ETag
    changed (or is missing/forced). Returns the set of sensor_types that were
    actually refreshed.
    """
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    state = json.loads(SYNC_STATE_FILE.read_text()) if SYNC_STATE_FILE.exists() else {}
    refreshed = set()

    for sensor_type, zip_name in OWNCLOUD_ZIP_MANIFEST.items():
        target_dir = SENSOR_TABLES[sensor_type][1]
        remote_etag = _webdav_etag(zip_name)
        cache_looks_present = target_dir.exists() and any(target_dir.iterdir())

        if not force and cache_looks_present and remote_etag is not None and state.get(sensor_type) == remote_etag:
            continue  # cache already matches ownCloud

        print(f'[sync] {zip_name}: downloading (cache {"stale" if cache_looks_present else "missing"})...')
        r = requests.get(OWNCLOUD_WEBDAV_BASE + zip_name, auth=(OWNCLOUD_SHARE_TOKEN, ''), timeout=600)
        r.raise_for_status()
        zip_path = ARCHIVE_DIR / zip_name
        zip_path.write_bytes(r.content)

        shutil.rmtree(target_dir, ignore_errors=True)
        target_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(target_dir)
        zip_path.unlink()

        state[sensor_type] = remote_etag
        refreshed.add(sensor_type)
        print(f'[sync] {zip_name}: extracted to {target_dir}')

    SYNC_STATE_FILE.write_text(json.dumps(state))
    return refreshed


# get_sensor_code drives archive filenames -- same logic bulk_sensor_download.py
# used to write them, so lookups here have to match exactly.
import sys
sys.path.insert(0, str(BASE_DIR))
from bulk_sensor_download import get_sensor_code  # noqa: E402


def normalize_huc(series):
    return series.fillna('').astype(str).str.replace(r'\.0$', '', regex=True).str.strip().str.zfill(8)


def parse_noaa_damage(s):
    """NOAA Storm Events stores damage as strings like '30.00K', '1.50M',
    '2.15B' -- converts to a plain USD float. Blank/NaN/unparseable -> 0.0."""
    if pd.isna(s):
        return 0.0
    s = str(s).strip().upper()
    if not s:
        return 0.0
    mult = 1.0
    if s.endswith('K'):
        mult, s = 1_000.0, s[:-1]
    elif s.endswith('M'):
        mult, s = 1_000_000.0, s[:-1]
    elif s.endswith('B'):
        mult, s = 1_000_000_000.0, s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return 0.0


def load_noaa_events(csv_path=NOAA_EVENTS_FILE):
    df = pd.read_csv(csv_path)
    df.columns = [c.upper().strip() for c in df.columns]
    df['FIPS_5'] = df['STATE_FIPS'].astype(str).str.replace(r'\.0$', '', regex=True).str.zfill(2) + \
                   df['CZ_FIPS'].astype(str).str.replace(r'\.0$', '', regex=True).str.zfill(3)
    df['HUC8_clean'] = normalize_huc(df['HUC8'])
    df['BEGIN_DT'] = pd.to_datetime(df['BEGIN_DATE_TIME'])
    df['END_DT'] = pd.to_datetime(df['END_DATE_TIME'])
    df['DAMAGE_PROPERTY_USD'] = df['DAMAGE_PROPERTY'].apply(parse_noaa_damage)
    df['DAMAGE_CROPS_USD'] = df['DAMAGE_CROPS'].apply(parse_noaa_damage)
    return df


def get_episode(episode_id, events_df=None):
    df = events_df if events_df is not None else load_noaa_events()
    rows = df[df['NEW_EPISODE_ID'].astype(str) == str(episode_id)]
    if rows.empty:
        raise ValueError(f"Episode '{episode_id}' not found in {NOAA_EVENTS_FILE.name}")
    return rows


def match_sensors(episode_rows, sensor_type, granularity='huc8'):
    """granularity: 'huc8' or 'county'. Returns the matched rows of that
    sensor type's lookup table (now including county_fips/county_name)."""
    lookup_path, _ = SENSOR_TABLES[sensor_type]
    sensors = pd.read_csv(lookup_path)

    if granularity == 'huc8':
        huc8_codes = [h for h in episode_rows['HUC8_clean'].unique().tolist() if h != '00000000']
        return sensors[normalize_huc(sensors['HUC8']).isin(huc8_codes)]
    elif granularity == 'county':
        fips_list = episode_rows['FIPS_5'].unique().tolist()
        sensor_fips = sensors['county_fips'].fillna(0).astype(float).astype(int).astype(str).str.zfill(5)
        return sensors[sensor_fips.isin(fips_list)]
    else:
        raise ValueError("granularity must be 'huc8' or 'county'")


def load_archive_slice(sensor_type, code, start_dt, end_dt):
    """
    Pulls one sensor's full-archive CSV (up to ~175k rows covering all 5
    water years) and slices it to [start_dt, end_dt]. This is the expensive
    step in the pipeline -- callers should go through extract_episode_sensors
    below rather than calling this directly, so the slice gets materialized
    to disk once instead of re-parsing the full archive on every call.

    format='mixed' (pandas' per-row format sniffer) was the original
    implementation; parsing every row into a real Timestamp (even with an
    exact format string) is still the dominant cost at this row count.
    Filtering instead by plain string comparison on the fixed-width,
    lexicographically-sortable datetime prefix is vectorized and needs no
    per-row parsing at all -- this cut a ~99-sensor episode extraction from
    ~35s to a few seconds.

    This also fixes a real correctness bug the earlier utc=True/
    tz_convert(None) version had for USGS: that path converted each local
    wall-clock reading (e.g. '2022-10-01T01:00:00.000-05:00') to UTC before
    comparing against start_dt/end_dt -- but start_dt/end_dt come from NOAA's
    BEGIN_DT/END_DT, which are naive LOCAL time, not UTC. That silently
    shifted the comparison by the site's UTC offset (5-6h for Iowa).
    Comparing the raw local-time string prefix directly against
    start_dt/end_dt (also local) avoids the mismatch entirely.
    """
    _, archive_dir = SENSOR_TABLES[sensor_type]
    safe_code = str(code).replace('/', '_').replace('\\', '_')
    path = archive_dir / f'{safe_code}.csv'
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path, low_memory=False)
    if 'datetime' not in df.columns:
        return df
    if sensor_type == 'usgs':
        # e.g. '2022-10-01T01:00:00.000-05:00' -- first 19 chars are the
        # sortable local-time prefix 'YYYY-MM-DDTHH:MM:SS', offset stripped.
        key = df['datetime'].str.slice(0, 19)
        start_str = start_dt.strftime('%Y-%m-%dT%H:%M:%S')
        end_str = end_dt.strftime('%Y-%m-%dT%H:%M:%S')
    else:
        # ifc_river / ifc_hydrostation: already 'YYYY-MM-DD HH:MM:SS', naive local.
        key = df['datetime']
        start_str = start_dt.strftime('%Y-%m-%d %H:%M:%S')
        end_str = end_dt.strftime('%Y-%m-%d %H:%M:%S')
    mask = (key >= start_str) & (key <= end_str)
    return df[mask]


# ── Staged, materialized filters ─────────────────────────────────────────────
# Mirrors runoff's pipeline shape: each stage consumes the PREVIOUS stage's
# (small) output and writes its own result to disk under episodes/<id>/...,
# rather than every stage re-scanning the full raw archive. First run through
# an episode pays the real I/O cost once; every later run -- or every later
# UI click for that same episode/granularity/window -- just reads the
# already-materialized files, which is fast regardless of archive size.
EPISODES_DIR = BASE_DIR / 'episodes'


def episode_dir(episode_id, granularity):
    return EPISODES_DIR / str(episode_id).replace('/', '_') / granularity


def match_episode_sensors(episode_id, granularity='huc8', events_df=None, force=False):
    """
    Stage 1 -- fast filter. Matches sensors to the episode by HUC8 or county
    (only touches the small ~600-row lookup tables, never the archive) and
    writes episodes/<id>/<granularity>/matched_sensors.csv. Returns
    (episode_rows, manifest_df).
    """
    episode_rows = get_episode(episode_id, events_df)
    out_dir = episode_dir(episode_id, granularity)
    manifest_path = out_dir / 'matched_sensors.csv'

    if manifest_path.exists() and not force:
        return episode_rows, pd.read_csv(manifest_path)

    frames = []
    for sensor_type in SENSOR_TABLES:
        matched = match_sensors(episode_rows, sensor_type, granularity=granularity).copy()
        if matched.empty:
            continue
        matched['sensor_type'] = sensor_type
        matched['sensor_code'] = matched.apply(lambda r: get_sensor_code(r, sensor_type), axis=1)
        keep_cols = [c for c in ['sensor_type', 'sensor_code', 'description', 'lat', 'lng', 'HUC8', 'county_fips', 'county_name'] if c in matched.columns]
        frames.append(matched[keep_cols])
    manifest = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=['sensor_type', 'sensor_code', 'description', 'lat', 'lng'])

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest.to_csv(manifest_path, index=False)
    return episode_rows, manifest


def extract_episode_sensors(episode_id, granularity='huc8', pad_hours=3, events_df=None, force=False):
    """
    Stage 2 -- the slow-on-first-run filter. For each sensor in stage 1's
    manifest, writes a small pre-sliced CSV to
    episodes/<id>/<granularity>/pad<h>h/sensors/<type>/<code>.csv if it
    doesn't already exist. Only sensors missing their materialized file pay
    the full-archive read; everything already extracted is a plain
    pd.read_csv of a file that's typically a few hundred rows.

    Returns the same shape as build_episode_sensor_bundle.
    """
    episode_rows, manifest = match_episode_sensors(episode_id, granularity, events_df)
    start_dt = episode_rows['BEGIN_DT'].min() - timedelta(hours=pad_hours)
    end_dt = episode_rows['END_DT'].max() + timedelta(hours=pad_hours)
    sensors_out_dir = episode_dir(episode_id, granularity) / f'pad{pad_hours}h' / 'sensors'

    sensors = {t: {} for t in SENSOR_TABLES}
    match_counts = manifest.groupby('sensor_type').size().to_dict() if not manifest.empty else {}

    for _, row in manifest.iterrows():
        sensor_type, code = row['sensor_type'], row['sensor_code']
        dest = sensors_out_dir / sensor_type / f'{code}.csv'
        if dest.exists() and not force:
            df = pd.read_csv(dest, low_memory=False)
        else:
            df = load_archive_slice(sensor_type, code, start_dt, end_dt)
            if not df.empty:
                dest.parent.mkdir(parents=True, exist_ok=True)
                df.to_csv(dest, index=False)
        if df is not None and not df.empty:
            sensors[sensor_type][code] = df

    for sensor_type in SENSOR_TABLES:
        match_counts.setdefault(sensor_type, 0)

    return {'episode_rows': episode_rows, 'window': (start_dt, end_dt), 'sensors': sensors, 'match_counts': match_counts}


def build_episode_sensor_bundle(episode_id, granularity='huc8', pad_hours=3, events_df=None, sync=False):
    """
    Returns a dict:
      {
        'episode_rows': DataFrame,
        'window': (start_dt, end_dt),
        'sensors': {
          'ifc_river':        {code: DataFrame, ...},
          'ifc_hydrostation': {code: DataFrame, ...},
          'usgs':             {code: DataFrame, ...},
        },
        'match_counts': {'ifc_river': n, 'ifc_hydrostation': n, 'usgs': n},
      }
    Only sensors with a non-empty slice in the window are included in
    'sensors'; match_counts reflects how many were matched by
    HUC8/county before filtering to non-empty results.

    sync=False (default) -- this function ONLY reads local files (the
    ownCloud cache plus its own per-episode materialized folders under
    episodes/), no network calls, so it's safe to call per-request from a
    web backend without adding ownCloud round-trip latency to every request.
    Sync is a separate concern: call sync_from_owncloud() once at process
    startup (or on a schedule / manual "refresh data" trigger), not inline
    per request. Pass sync=True only for one-off/script usage where you want
    each call to self-check freshness.

    This is a thin wrapper around the two staged filters below
    (match_episode_sensors, extract_episode_sensors) -- call those directly
    if you want to run/inspect one stage at a time (e.g. a UI that shows
    matched sensors before committing to extracting their time series).
    """
    if sync:
        sync_from_owncloud()
    return extract_episode_sensors(episode_id, granularity=granularity, pad_hours=pad_hours, events_df=events_df)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('episode_id', nargs='?', default='191899_0')
    parser.add_argument('--granularity', choices=['huc8', 'county'], default='huc8')
    parser.add_argument('--pad-hours', type=float, default=3)
    args = parser.parse_args()

    bundle = build_episode_sensor_bundle(args.episode_id, granularity=args.granularity, pad_hours=args.pad_hours)
    print(f"Episode {args.episode_id}  granularity={args.granularity}  window={bundle['window'][0]} -> {bundle['window'][1]}")
    for sensor_type, n_matched in bundle['match_counts'].items():
        n_with_data = len(bundle['sensors'][sensor_type])
        print(f'  {sensor_type:18s} matched={n_matched:4d}  with data in window={n_with_data}')
