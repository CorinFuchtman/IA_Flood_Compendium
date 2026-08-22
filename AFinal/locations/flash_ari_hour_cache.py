"""
Caches one cropped FLASH QPE ARI (Average Recurrence Interval) grid per
unique UTC hour x duration needed across all 135 NOAA episodes -- same
"fetch the shared resource once, slice per-episode locally many times"
pattern as mrms_hour_cache.py, applied to NOAA/NSSL's FLASH ARI product
instead of the plain hourly QPE accumulation.

FLASH ARI compares real-time MRMS QPE accumulations against static NOAA
Atlas 14 precipitation-frequency tables to estimate, at each grid cell, how
rare that accumulation is -- expressed as a recurrence interval in years
(e.g. "this is a 100-year rainfall here"). NSSL publishes one product per
accumulation duration; this module caches the five durations the wizard's
episode detail table needs (30 min, 1h, 3h, 6h, 24h). Confirmed via the
noaa-mrms-pds S3 bucket listing that each duration is its own top-level
MRMS product, with files every 2 minutes and data available back through
2021 (covers this project's full 2021-2025 episode set):

  CONUS/FLASH_QPE_ARI30M_00.00/<YYYYMMDD>/MRMS_FLASH_QPE_ARI30M_00.00_<YYYYMMDD>-<HHMMSS>.grib2.gz
  CONUS/FLASH_QPE_ARI01H_00.00/...
  CONUS/FLASH_QPE_ARI03H_00.00/...
  CONUS/FLASH_QPE_ARI06H_00.00/...
  CONUS/FLASH_QPE_ARI24H_00.00/...

HHMMSS steps by 2 minutes (000000, 000200, ..., 235800), so the top-of-hour
file (HH0000) exists for every hour -- one file per hour per duration is
enough, matching the QPE cache's "one file = one hour" granularity.

Unlike mrms_hour_cache.py, the hours cached here are NOT padded +-3h around
each episode's window -- the per-episode FLASH table (episode_flash_extract.py)
uses the raw NOAA BEGIN_DT-END_DT span, one row per hour, so a 72-hour event
produces exactly 72 rows. See compute_required_utc_hours() below.

Same GRIB2/cfgrib decode approach as mrms_hour_cache.py, and the same
ProcessPoolExecutor pattern for the same reason: eccodes isn't thread-safe,
so separate worker processes are required for real parallelism. With 5
durations instead of 1, this is roughly 5x the one-time cost of the QPE hour
cache build.
"""
import gzip
import os
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from mrms_hour_cache import CROP_MIN_LAT, CROP_MAX_LAT, CROP_MIN_LON_360, CROP_MAX_LON_360

BASE_DIR = Path(__file__).parent
CACHE_DIR = BASE_DIR / 'flash_ari_hour_cache'
NOAA_EVENTS_FILE = BASE_DIR / 'noaa_21-25_with_huc_08.csv'

MRMS_BUCKET = 'noaa-mrms-pds'
FLASH_PRODUCTS = {
    '30m': 'FLASH_QPE_ARI30M_00.00',
    '1h': 'FLASH_QPE_ARI01H_00.00',
    '3h': 'FLASH_QPE_ARI03H_00.00',
    '6h': 'FLASH_QPE_ARI06H_00.00',
    '24h': 'FLASH_QPE_ARI24H_00.00',
}


def compute_required_utc_hours(events_df=None):
    """All unique UTC hours needed to cover every episode's RAW (unpadded)
    NOAA window -- one hour per row of the per-episode FLASH table, so a
    72-hour event needs exactly 72 hours here. Same local->UTC conversion
    (via CZ_TIMEZONE) as mrms_hour_cache.compute_required_utc_hours, just
    without the +-3h pad."""
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

        start_utc = (row['begin'] + pd.Timedelta(hours=shift)).floor('h')
        end_utc = (row['end'] + pd.Timedelta(hours=shift)).floor('h')
        all_hours.update(pd.date_range(start_utc, end_utc, freq='h'))

    return sorted(all_hours)


def _hour_cache_path(dt_utc, duration_key):
    return CACHE_DIR / f"{dt_utc.strftime('%Y%m%d_%H')}_{duration_key}.npz"


def _fetch_and_crop_one_hour(dt_utc, duration_key):
    """Runs in a worker PROCESS (not thread) -- must be picklable/import-safe,
    no shared state with the parent. Returns 'ok' / 'no_data' / 'FAILED: ...'."""
    out_path = _hour_cache_path(dt_utc, duration_key)
    if out_path.exists():
        return 'skipped'

    import s3fs
    import xarray as xr

    product = FLASH_PRODUCTS[duration_key]
    fs = s3fs.S3FileSystem(anon=True)
    date_str = dt_utc.strftime('%Y%m%d')
    hour_str = dt_utc.strftime('%H')
    key = f'{MRMS_BUCKET}/CONUS/{product}/{date_str}/MRMS_{product}_{date_str}-{hour_str}0000.grib2.gz'

    tmp_path = None
    try:
        if not fs.exists(key):
            np.savez_compressed(out_path, no_data=True)
            return 'no_data'

        with fs.open(key, 'rb') as gz_file:
            raw = gzip.decompress(gz_file.read())

        fd, tmp_path = tempfile.mkstemp(suffix='.grib2')
        with os.fdopen(fd, 'wb') as tmp_file:
            tmp_file.write(raw)

        ds = xr.load_dataset(tmp_path, engine='cfgrib', decode_timedelta=False,
                              backend_kwargs={'indexpath': ''})
        var_name = list(ds.data_vars)[0]
        # ARI values are years, so negative/sentinel fill values are masked
        # the same way mrms_hour_cache.py masks QPE's own fill value.
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


def build_hour_cache(max_workers=None, events_df=None, durations=None):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    hours = compute_required_utc_hours(events_df)
    keys = list(durations) if durations else list(FLASH_PRODUCTS)
    jobs = [(hr, dur) for hr in hours for dur in keys]
    workers = max_workers or min(24, os.cpu_count() or 8)
    print(f'{len(hours)} unique UTC hours x {len(keys)} durations = {len(jobs)} grids needed. '
          f'Fetching with {workers} worker processes...')

    results = {'ok': 0, 'no_data': 0, 'skipped': 0, 'failed': 0}
    sample_stats = []
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_fetch_and_crop_one_hour, hr.to_pydatetime(), dur): (hr, dur) for hr, dur in jobs}
        for i, future in enumerate(as_completed(futures)):
            status = future.result()
            key = status if status in results else 'failed'
            results[key] += 1
            if status == 'ok' and len(sample_stats) < 5:
                hr, dur = futures[future]
                grid = load_hour_grid(hr.to_pydatetime(), dur)
                if grid is not None:
                    values = grid[0]
                    valid = values[~np.isnan(values)]
                    if valid.size:
                        sample_stats.append((dur, float(valid.min()), float(np.median(valid)), float(valid.max())))
            if (i + 1) % 200 == 0 or i == len(jobs) - 1:
                print(f'  {i+1}/{len(jobs)}  ok={results["ok"]} no_data={results["no_data"]} '
                      f'skipped={results["skipped"]} failed={results["failed"]}')

    if sample_stats:
        print('\nSanity check -- observed ARI value range (years) on first decoded grids:')
        for dur, lo, med, hi in sample_stats:
            print(f'  {dur}: min={lo:.2f} median={med:.2f} max={hi:.2f}')
        print('(values should look like plausible recurrence-interval years, not raw mm)')

    print(f'\nDONE. {results}')
    return results


def load_hour_grid(dt_utc, duration_key):
    """Returns (values, lats, lons) or None if no data / not cached."""
    path = _hour_cache_path(dt_utc, duration_key)
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
    parser.add_argument('--durations', nargs='*', default=None, choices=list(FLASH_PRODUCTS))
    args = parser.parse_args()
    build_hour_cache(max_workers=args.workers, durations=args.durations)
