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


def _bbox_cell_indices(lats, lons, min_lat, max_lat, lon_min_360, lon_max_360):
    """Shared lat/lon index selection for cropping one hour's cached grid to
    an episode bbox, with the same nearest-single-cell fallback used when the
    bbox is narrower than the ~0.01deg grid spacing (40 of 135 episodes hit
    this -- see extract_episode_precip's docstring/comment)."""
    lat_idx = np.where((lats >= min_lat) & (lats <= max_lat))[0]
    lon_idx = np.where((lons >= lon_min_360) & (lons <= lon_max_360))[0]
    if lat_idx.size == 0:
        lat_idx = np.array([np.argmin(np.abs(lats - (min_lat + max_lat) / 2))])
    if lon_idx.size == 0:
        lon_idx = np.array([np.argmin(np.abs(lons - (lon_min_360 + lon_max_360) / 2))])
    return lat_idx, lon_idx


def _hourly_bbox_mean(grid, min_lat, max_lat, lon_min_360, lon_max_360):
    """Returns (mean_mm, max_mm) for one hour's cached grid, or (None, None)
    if nothing usable."""
    values, lats, lons = grid
    lat_idx, lon_idx = _bbox_cell_indices(lats, lons, min_lat, max_lat, lon_min_360, lon_max_360)
    sub = values[np.ix_(lat_idx, lon_idx)]
    valid = sub[~np.isnan(sub)]
    if valid.size == 0:
        return None, None
    return float(valid.mean()), float(valid.max())


def _hourly_bbox_grid(grid, min_lat, max_lat, lon_min_360, lon_max_360):
    """Returns the cropped 2D sub-array for one hour's cached grid (same
    cropping as _hourly_bbox_mean, but keeps per-cell values instead of
    collapsing to an aggregate) -- used to accumulate a per-pixel total
    across an episode's hours, so median/max can be computed per-pixel
    rather than per-hour."""
    values, lats, lons = grid
    lat_idx, lon_idx = _bbox_cell_indices(lats, lons, min_lat, max_lat, lon_min_360, lon_max_360)
    return values[np.ix_(lat_idx, lon_idx)]


def extract_episode_precip(episode_id, pad_hours=3, events_df=None, force=False):
    """
    Returns {'total_mm', 'median_mm', 'max_mm', 'hours_with_data', 'hours_total'}.
    total_mm is the bbox-mean accumulated precipitation across the episode's
    padded UTC window (unchanged computation, kept as its own pass so this
    number never shifts). median_mm/max_mm instead accumulate a per-pixel
    total across the same hours/bbox (nansum per cell, same all-NaN masking
    as build_embedded_mrms_grids.build_episode_grid) and take the median/max
    over that per-pixel grid -- i.e. "the typical/worst single cell in the
    basin got this much rain over the whole episode", not a per-hour max.
    hours_with_data/hours_total is a QC signal (a low ratio means the hour
    cache is missing/incomplete for that window, not necessarily that it was
    dry).
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
    pixel_sum = None
    pixel_all_nan = None
    for hr in hours:
        grid = load_hour_grid(hr.to_pydatetime())
        if grid is None:
            continue
        mean_mm, _ = _hourly_bbox_mean(grid, min_lat, max_lat, lon_min_360, lon_max_360)
        if mean_mm is not None:
            total_mm += mean_mm
            hours_with_data += 1

        sub = _hourly_bbox_grid(grid, min_lat, max_lat, lon_min_360, lon_max_360)
        hour_nan = np.isnan(sub)
        hour_contrib = np.where(hour_nan, 0.0, sub)
        if pixel_sum is None:
            pixel_sum = hour_contrib.copy()
            pixel_all_nan = hour_nan.copy()
        else:
            pixel_sum += hour_contrib
            pixel_all_nan &= hour_nan

    if pixel_sum is not None:
        valid_pixels = pixel_sum[~pixel_all_nan]
    else:
        valid_pixels = np.array([])
    median_mm = float(np.median(valid_pixels)) if valid_pixels.size else None
    max_mm = float(np.max(valid_pixels)) if valid_pixels.size else None

    result = {
        'total_mm': round(total_mm, 2),
        'median_mm': round(median_mm, 2) if median_mm is not None else None,
        'max_mm': round(max_mm, 2) if max_mm is not None else None,
        'hours_with_data': hours_with_data,
        'hours_total': len(hours),
    }
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
    print(f"Episode {args.episode_id}: avg {result['total_mm']} mm, "
          f"median (pixel) {result['median_mm']} mm, max (pixel) {result['max_mm']} mm "
          f"({result['hours_with_data']}/{result['hours_total']} hours had data)")
