"""
Stages USGS High-Water Marks per episode, same pattern as
episode_fema_extract.py: filters the local hwm_archive.csv cache (fetched
once by hwm_archive.py) to the episode's bounding box + date window and
writes episodes/<id>/hwms.csv. Warm reads are a plain local CSV read.

Bounding box logic (min_lat from BEGIN_LAT, max_lat from END_LAT, etc.) and
the 0.1-degree spatial pad + date_buffer_days=30 are carried over unchanged
from pipeline (11).ipynb's query_usgs_hwms, just reading from the local
archive instead of re-fetching the live endpoint per episode.

HWMs are bbox-scoped, not HUC8/county-scoped, so -- like FEMA claims -- this
caches at the episode level, independent of the sensor-matching granularity
toggle.
"""
from datetime import timedelta
from pathlib import Path

import pandas as pd

from episode_sensor_extract import EPISODES_DIR, get_episode
from hwm_archive import load_hwm_archive

HWM_NO_RECORDS_MARKER = '__no_hwms__'


def extract_episode_hwms(episode_id, date_buffer_days=30, spatial_pad_deg=0.1, events_df=None, force=False):
    safe_id = str(episode_id).replace('/', '_')
    out_dir = EPISODES_DIR / safe_id
    out_path = out_dir / 'hwms.csv'

    if out_path.exists() and not force:
        first_line = out_path.read_text(encoding='utf-8').splitlines()[0] if out_path.stat().st_size > 0 else ''
        if first_line.strip() == HWM_NO_RECORDS_MARKER:
            return pd.DataFrame()
        return pd.read_csv(out_path)

    episode_rows = get_episode(episode_id, events_df)
    min_lat = episode_rows['BEGIN_LAT'].min()
    max_lat = episode_rows['END_LAT'].max()
    min_lon = episode_rows['BEGIN_LON'].min()
    max_lon = episode_rows['END_LON'].max()
    start_date = episode_rows['BEGIN_DT'].min()
    end_date = episode_rows['END_DT'].max()

    archive = load_hwm_archive()
    pad = spatial_pad_deg
    mask_sp = ((archive['latitude_dd'] >= min_lat - pad) & (archive['latitude_dd'] <= max_lat + pad) &
               (archive['longitude_dd'] >= min_lon - pad) & (archive['longitude_dd'] <= max_lon + pad))
    if 'date_parsed' in archive.columns:
        s = start_date - timedelta(days=2)
        e = end_date + timedelta(days=date_buffer_days)
        mask_t = (archive['date_parsed'] >= s) & (archive['date_parsed'] <= e)
        hwms_df = archive[mask_sp & mask_t]
    else:
        hwms_df = archive[mask_sp]

    out_dir.mkdir(parents=True, exist_ok=True)
    if hwms_df.empty:
        out_path.write_text(HWM_NO_RECORDS_MARKER + '\n', encoding='utf-8')
    else:
        hwms_df.to_csv(out_path, index=False, encoding='utf-8')
    return hwms_df


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('episode_id', nargs='?', default='191899_0')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()

    df = extract_episode_hwms(args.episode_id, force=args.force)
    if df.empty:
        print(f'Episode {args.episode_id}: no HWMs found.')
    else:
        print(f'Episode {args.episode_id}: {len(df)} HWM(s).')
        cols = [c for c in ['hwm_id', 'waterbody', 'elev_ft', 'survey_date'] if c in df.columns]
        print(df[cols].head(10).to_string(index=False))
