"""
Stages the FLASH QPE ARI (Average Recurrence Interval) table per episode,
reading from flash_ari_hour_cache.py's shared local cache of unique UTC
hours -- no live network call once that cache is built (same "fetch shared
resource once, slice per-episode locally" pattern as every other
per-episode extractor in this pipeline).

One row per UTC hour of the episode's RAW (unpadded) NOAA BEGIN_DT-END_DT
window -- a 72-hour event produces exactly 72 rows -- with one column per
FLASH duration (30m/1h/3h/6h/24h). Each cell is the MOST SEVERE (max) ARI
value found anywhere in the episode's own bbox at that hour: a higher
recurrence-interval-in-years means rarer/more severe rainfall, so the worst
single cell is the number worth surfacing, not a bbox average.

Uses the same narrow bbox as episode_precip_extract.py's _episode_bbox (the
episode's own storm-report point bbox, no spatial pad) -- kept consistent
with the median/max pixel rainfall stats added alongside it, and reuses
that module's _hourly_bbox_mean() for the per-hour bbox-max lookup (the
second element of its (mean, max) return).
"""
import json
from pathlib import Path

import pandas as pd

from episode_sensor_extract import EPISODES_DIR, get_episode
from episode_precip_extract import _episode_bbox, _episode_utc_window, _hourly_bbox_mean
from flash_ari_hour_cache import load_hour_grid, FLASH_PRODUCTS

COLUMN_FOR_DURATION = {'30m': 'ari_30m', '1h': 'ari_1h', '3h': 'ari_3h', '6h': 'ari_6h', '24h': 'ari_24h'}


def extract_episode_flash(episode_id, events_df=None, force=False):
    """
    Returns (table_df, summary_dict_or_None).
    table_df: one row per UTC hour -- datetime_utc plus one ari_<duration>
    column per FLASH duration, values in years, None where no cached grid
    exists for that hour/duration.
    summary_dict: {'ari_years', 'duration', 'datetime_utc'} for the single
    most severe cell across the whole table, or None if the episode has no
    FLASH data cached at all.
    Cached to episodes/<id>/flash_recurrence.csv and flash_summary.json.
    """
    safe_id = str(episode_id).replace('/', '_')
    out_dir = EPISODES_DIR / safe_id
    table_path = out_dir / 'flash_recurrence.csv'
    summary_path = out_dir / 'flash_summary.json'

    if table_path.exists() and summary_path.exists() and not force:
        table_df = pd.read_csv(table_path, encoding='utf-8')
        summary = json.loads(summary_path.read_text(encoding='utf-8'))
        return table_df, summary

    episode_rows = get_episode(episode_id, events_df)
    min_lat, max_lat, lon_min_360, lon_max_360 = _episode_bbox(episode_rows)
    start_utc, end_utc = _episode_utc_window(episode_rows, pad_hours=0)
    hours = pd.date_range(start_utc, end_utc, freq='h')

    rows = []
    most_severe = None  # (ari_years, duration_key, hour_timestamp)
    for hr in hours:
        row = {'datetime_utc': hr}
        for duration_key in FLASH_PRODUCTS:
            col = COLUMN_FOR_DURATION[duration_key]
            grid = load_hour_grid(hr.to_pydatetime(), duration_key)
            if grid is None:
                row[col] = None
                continue
            _, max_ari = _hourly_bbox_mean(grid, min_lat, max_lat, lon_min_360, lon_max_360)
            row[col] = round(max_ari, 2) if max_ari is not None else None
            if max_ari is not None and (most_severe is None or max_ari > most_severe[0]):
                most_severe = (max_ari, duration_key, hr)
        rows.append(row)

    table_df = pd.DataFrame(rows, columns=['datetime_utc'] + list(COLUMN_FOR_DURATION.values()))

    summary = None
    if most_severe is not None:
        ari_years, duration_key, hr = most_severe
        summary = {'ari_years': round(ari_years, 2), 'duration': duration_key, 'datetime_utc': hr.isoformat()}

    out_dir.mkdir(parents=True, exist_ok=True)
    table_df.to_csv(table_path, index=False, encoding='utf-8')
    summary_path.write_text(json.dumps(summary), encoding='utf-8')
    return table_df, summary


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('episode_id', nargs='?', default='191899_0')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()

    table_df, summary = extract_episode_flash(args.episode_id, force=args.force)
    print(f'Episode {args.episode_id}: {len(table_df)} hourly rows')
    if summary:
        print(f"Most severe: {summary['ari_years']}-yr ARI ({summary['duration']}) at {summary['datetime_utc']} UTC")
    else:
        print('No FLASH ARI data cached for this episode.')
    print(table_df.head(10).to_string(index=False))
