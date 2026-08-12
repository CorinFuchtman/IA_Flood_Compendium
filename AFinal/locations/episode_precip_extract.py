"""
Stages MRMS accumulated precipitation per episode, reading from
mrms_hour_cache.py's shared local cache of unique UTC hours -- no live
network call once that cache is built (same "fetch shared resource once,
slice per-episode locally" pattern as HWMs/sensors).

Bbox (episode_rows' raw BEGIN_LAT/END_LAT/BEGIN_LON/END_LON, no spatial pad)
and UTC conversion (via CZ_TIMEZONE) match pipeline (11).ipynb's master
pipeline MRMS step exactly -- same numbers this replaces, just sourced from
the local hour cache instead of a live per-episode S3 fetch.

The single number this produces -- total accumulated rainfall in mm, summed
across the episode's padded window -- is the "episode total (mean-of-grid)
precipitation" pipeline (11) already prints, kept as one representative
scalar per episode for consistency with every other wizard filter (sensor
counts, HWm counts, FEMA claims, inundation presence).
"""
import json
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from episode_sensor_extract import EPISODES_DIR, get_episode
from mrms_hour_cache import load_hour_grid


def _episode_utc_window(episode_rows, pad_hours=3):
    tz_offset_hours = None
    tz_vals = episode_rows['CZ_TIMEZONE'].dropna().unique() if 'CZ_TIMEZONE' in episode_rows.columns else []
    if len(tz_vals) > 0 and '-' in str(tz_vals[0]):
        try:
            tz_offset_hours = -int(str(tz_vals[0]).split('-')[-1])
        except ValueError:
            tz_offset_hours = None
    shift = -tz_offset_hours if tz_offset_hours is not None else 0  # local -> UTC

    start_local = episode_rows['BEGIN_DT'].min() - timedelta(hours=pad_hours)
    end_local = episode_rows['END_DT'].max() + timedelta(hours=pad_hours)
    return (start_local + timedelta(hours=shift)).floor('h'), (end_local + timedelta(hours=shift)).floor('h')


def _episode_bbox(episode_rows):
    min_lat, max_lat = episode_rows['BEGIN_LAT'].min(), episode_rows['END_LAT'].max()
    min_lon, max_lon = episode_rows['BEGIN_LON'].min(), episode_rows['END_LON'].max()
    return min_lat, max_lat, min_lon % 360, max_lon % 360


def _hourly_bbox_mean(grid, min_lat, max_lat, lon_min_360, lon_max_360):
    """Returns (mean_mm, max_mm) for one hour's cached grid, or (None, None)
    if nothing usable. Falls back to the nearest single cell when the bbox
    is narrower than the ~0.01deg grid spacing -- see extract_episode_precip's
    docstring/comment for why this fallback exists (40 of 135 episodes hit it)."""
    values, lats, lons = grid
    lat_idx = np.where((lats >= min_lat) & (lats <= max_lat))[0]
    lon_idx = np.where((lons >= lon_min_360) & (lons <= lon_max_360))[0]
    if lat_idx.size == 0:
        lat_idx = np.array([np.argmin(np.abs(lats - (min_lat + max_lat) / 2))])
    if lon_idx.size == 0:
        lon_idx = np.array([np.argmin(np.abs(lons - (lon_min_360 + lon_max_360) / 2))])
    sub = values[np.ix_(lat_idx, lon_idx)]
    valid = sub[~np.isnan(sub)]
    if valid.size == 0:
        return None, None
    return float(valid.mean()), float(valid.max())


def extract_episode_precip(episode_id, pad_hours=3, events_df=None, force=False):
    """
    Returns {'total_mm': float, 'hours_with_data': int, 'hours_total': int}.
    total_mm is the bbox-mean accumulated precipitation across the episode's
    padded UTC window; hours_with_data/hours_total is a QC signal (a low
    ratio means the hour cache is missing/incomplete for that window, not
    necessarily that it was dry).
    """
    safe_id = str(episode_id).replace('/', '_')
    out_dir = EPISODES_DIR / safe_id
    out_path = out_dir / 'precip_summary.json'

    if out_path.exists() and not force:
        return json.loads(out_path.read_text(encoding='utf-8'))

    episode_rows = get_episode(episode_id, events_df)
    min_lat, max_lat, lon_min_360, lon_max_360 = _episode_bbox(episode_rows)
    start_utc, end_utc = _episode_utc_window(episode_rows, pad_hours=pad_hours)
    hours = pd.date_range(start_utc, end_utc, freq='h')

    total_mm = 0.0
    hours_with_data = 0
    for hr in hours:
        grid = load_hour_grid(hr.to_pydatetime())
        if grid is None:
            continue
        mean_mm, _ = _hourly_bbox_mean(grid, min_lat, max_lat, lon_min_360, lon_max_360)
        if mean_mm is None:
            continue
        total_mm += mean_mm
        hours_with_data += 1

    result = {'total_mm': round(total_mm, 2), 'hours_with_data': hours_with_data, 'hours_total': len(hours)}
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result), encoding='utf-8')
    return result


def extract_episode_precip_timeseries(episode_id, pad_hours=3, events_df=None):
    """
    Returns a DataFrame with one row per UTC hour in the episode's padded
    window: datetime_utc, mean_mm, max_mm. Rows for hours with no cached
    data are omitted (not zero-filled, so a gap reads as "no data" rather
    than misleadingly "no rain"). Not cached to disk like extract_episode_precip
    -- this is only used for the bulk per-episode ZIP export, computed once
    up front there rather than repeatedly.
    """
    episode_rows = get_episode(episode_id, events_df)
    min_lat, max_lat, lon_min_360, lon_max_360 = _episode_bbox(episode_rows)
    start_utc, end_utc = _episode_utc_window(episode_rows, pad_hours=pad_hours)
    hours = pd.date_range(start_utc, end_utc, freq='h')

    rows = []
    for hr in hours:
        grid = load_hour_grid(hr.to_pydatetime())
        if grid is None:
            continue
        mean_mm, max_mm = _hourly_bbox_mean(grid, min_lat, max_lat, lon_min_360, lon_max_360)
        if mean_mm is None:
            continue
        rows.append({'datetime_utc': hr, 'mean_mm': round(mean_mm, 3), 'max_mm': round(max_mm, 3)})
    return pd.DataFrame(rows, columns=['datetime_utc', 'mean_mm', 'max_mm'])


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('episode_id', nargs='?', default='191899_0')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()

    result = extract_episode_precip(args.episode_id, force=args.force)
    print(f"Episode {args.episode_id}: {result['total_mm']} mm "
          f"({result['hours_with_data']}/{result['hours_total']} hours had data)")
