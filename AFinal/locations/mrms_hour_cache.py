"""
Caches one cropped MRMS hourly precipitation grid per unique UTC hour needed
across ALL 135 NOAA episodes -- the same "fetch the shared resource once,
slice per-episode locally many times" pattern as hwm_archive.py and the
sensor archive, applied to MRMS.

Why this needs its own module instead of reusing pipeline (11)'s
query_mrms_precipitation() directly: that function is scoped to ONE episode
per call and re-fetches every hour fresh. Across 135 episodes the padded
windows total 9,183 episode-hours, but only 4,814 are actually UNIQUE
(episodes cluster in time) -- so fetching per-unique-hour once instead of
per-episode avoids ~2x redundant work on top of everything else here.

The real cost driver is GRIB2 decoding, not download: eccodes/cfgrib decode
took ~7.7s/hour in testing (download+decompress was <1s), and pipeline
(11)'s own code notes eccodes isn't thread-safe for concurrent decode --
its ThreadPoolExecutor pattern serializes all decoding behind one lock, so
threading doesn't actually parallelize the bottleneck. At 4,814 hours x
7.7s serial, that's ~10 hours. This uses ProcessPoolExecutor instead --
separate OS processes each get independent eccodes state, no shared lock
needed, so decode genuinely parallelizes across this machine's 24 cores
(~30-60 min realistic, accounting for process overhead and I/O).

This is a one-time cost paid ONCE by whoever builds the wizard data, not by
every person who opens wizard.html -- same as the sensor archive download
and HWM archive fetch. Once cached, per-episode precip extraction
(episode_precip_extract.py) is a fast local array slice.

Each hour's grid is cropped to a padded Iowa bounding box (not full CONUS)
before caching, to keep the ~4,814 cache files small -- full CONUS grids
would be wasteful when every episode's own bbox falls inside this region.
"""
import gzip
import os
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).parent
CACHE_DIR = BASE_DIR / 'mrms_hour_cache'
NOAA_EVENTS_FILE = BASE_DIR / 'noaa_21-25_with_huc_08.csv'

MRMS_BUCKET = 'noaa-mrms-pds'
MRMS_PRODUCT = 'MultiSensor_QPE_01H_Pass2_00.00'

# Padded around the actual BEGIN_LAT/END_LAT/BEGIN_LON/END_LON range seen
# across all 135 NOAA episodes (40.41-43.48 lat, -96.63--90.18 lon) plus a
# 0.5 deg margin for the per-episode 0.1 deg bbox pads used elsewhere.
CROP_MIN_LAT, CROP_MAX_LAT = 39.8, 44.1
CROP_MIN_LON, CROP_MAX_LON = -97.3, -89.5

CROP_MIN_LON_360 = CROP_MIN_LON % 360
CROP_MAX_LON_360 = CROP_MAX_LON % 360


def compute_required_utc_hours(events_df=None):
    """All unique UTC hours needed to cover every episode's padded (+-3h)
    window, converting NOAA's local CZ_TIMEZONE (CST-6 for all 485 rows in
    this dataset) to UTC -- same conversion pipeline (11)'s master pipeline
    cell does per-episode, applied once globally here."""
    df = events_df if events_df is not None else pd.read_csv(NOAA_EVENTS_FILE)
    if 'BEGIN_DT' not in df.columns:
        df.columns = [c.upper().strip() for c in df.columns]
        df['BEGIN_DT'] = pd.to_datetime(df['BEGIN_DATE_TIME'])
        df['END_DT'] = pd.to_datetime(df['END_DATE_TIME'])

    grp = df.groupby('NEW_EPISODE_ID').agg(
        begin=('BEGIN_DT', 'min'), end=('END_DT', 'max'), tz=('CZ_TIMEZONE', 'first'))

    all_hours = set()
    for _, row in grp.iterrows():
        tz_offset_hours = None
        tz_str = str(row['tz'])
        if '-' in tz_str:
            try:
                tz_offset_hours = -int(tz_str.split('-')[-1])
            except ValueError:
                tz_offset_hours = None
        shift = -tz_offset_hours if tz_offset_hours is not None else 0  # local -> UTC

        start_utc = (row['begin'] - timedelta(hours=3) + timedelta(hours=shift)).floor('h')
        end_utc = (row['end'] + timedelta(hours=3) + timedelta(hours=shift)).floor('h')
        all_hours.update(pd.date_range(start_utc, end_utc, freq='h'))

    return sorted(all_hours)


def _hour_cache_path(dt_utc):
    return CACHE_DIR / f"{dt_utc.strftime('%Y%m%d_%H')}.npz"


def _fetch_and_crop_one_hour(dt_utc):
    """Runs in a worker PROCESS (not thread) -- must be picklable/import-safe,
    no shared state with the parent. Returns 'ok' / 'no_data' / 'FAILED: ...'."""
    out_path = _hour_cache_path(dt_utc)
    if out_path.exists():
        return 'skipped'

    import s3fs
    import xarray as xr

    fs = s3fs.S3FileSystem(anon=True)
    date_str = dt_utc.strftime('%Y%m%d')
    hour_str = dt_utc.strftime('%H')
    prefix = f'{MRMS_BUCKET}/CONUS/{MRMS_PRODUCT}/{date_str}/'
    pattern = f'{prefix}MRMS_{MRMS_PRODUCT}_{date_str}-{hour_str}*.grib2.gz'

    tmp_path = None
    try:
        matches = sorted(fs.glob(pattern))
        if not matches:
            np.savez_compressed(out_path, no_data=True)
            return 'no_data'

        with fs.open(matches[0], 'rb') as gz_file:
            raw = gzip.decompress(gz_file.read())

        fd, tmp_path = tempfile.mkstemp(suffix='.grib2')
        with os.fdopen(fd, 'wb') as tmp_file:
            tmp_file.write(raw)

        ds = xr.load_dataset(tmp_path, engine='cfgrib', decode_timedelta=False,
                              backend_kwargs={'indexpath': ''})
        var_name = list(ds.data_vars)[0]
        da = ds[var_name].where(ds[var_name] >= 0)

        sub = da.sel(latitude=slice(CROP_MAX_LAT, CROP_MIN_LAT), longitude=slice(CROP_MIN_LON_360, CROP_MAX_LON_360))
        values = sub.values.astype('float32')
        lats = sub['latitude'].values.astype('float32')
        lons = sub['longitude'].values.astype('float32')

        np.savez_compressed(out_path, no_data=False, values=values, lats=lats, lons=lons)
        return 'ok'
    except Exception as e:
        return f'FAILED: {e}'
    finally:
        if tmp_path and os.path.exists(tmp_path):
            tmp_dir = os.path.dirname(tmp_path)
            tmp_name = os.path.basename(tmp_path)
            for f in os.listdir(tmp_dir):
                if f.startswith(tmp_name):
                    try:
                        os.remove(os.path.join(tmp_dir, f))
                    except OSError:
                        pass


def build_hour_cache(max_workers=None, events_df=None):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    hours = compute_required_utc_hours(events_df)
    workers = max_workers or min(24, os.cpu_count() or 8)
    print(f'{len(hours)} unique UTC hours needed. Fetching with {workers} worker processes...')

    results = {'ok': 0, 'no_data': 0, 'skipped': 0, 'failed': 0}
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_fetch_and_crop_one_hour, hr.to_pydatetime()): hr for hr in hours}
        for i, future in enumerate(as_completed(futures)):
            status = future.result()
            key = status if status in results else 'failed'
            results[key] += 1
            if (i + 1) % 50 == 0 or i == len(hours) - 1:
                print(f'  {i+1}/{len(hours)}  ok={results["ok"]} no_data={results["no_data"]} '
                      f'skipped={results["skipped"]} failed={results["failed"]}')

    print(f'DONE. {results}')
    return results


def load_hour_grid(dt_utc):
    """Returns (values, lats, lons) or None if no data / not cached."""
    path = _hour_cache_path(dt_utc)
    if not path.exists():
        return None
    with np.load(path) as d:
        if bool(d['no_data']):
            return None
        return d['values'], d['lats'], d['lons']


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--workers', type=int, default=None)
    args = parser.parse_args()
    build_hour_cache(max_workers=args.workers)
