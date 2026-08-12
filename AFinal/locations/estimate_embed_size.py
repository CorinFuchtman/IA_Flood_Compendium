"""
One-off estimate: if every episode's matched sensor time series (union of
county+huc8 matches) plus its MRMS hourly precip series were embedded
directly in wizard.html instead of fetched, how big would that data be?

Reuses match_sensors/load_archive_slice from episode_sensor_extract.py and
the MRMS hour cache -- no new fetches, just measuring what's already local.
"""
import time
from pathlib import Path

import pandas as pd

from episode_sensor_extract import load_noaa_events, match_sensors, load_archive_slice, get_sensor_code, SENSOR_TABLES
from episode_precip_extract import _episode_utc_window
from mrms_hour_cache import load_hour_grid

BASE_DIR = Path(__file__).parent


def episode_window(episode_rows, pad_hours=3):
    from datetime import timedelta
    start = episode_rows['BEGIN_DT'].min() - timedelta(hours=pad_hours)
    end = episode_rows['END_DT'].max() + timedelta(hours=pad_hours)
    return start, end


def main():
    events_df = load_noaa_events()
    episode_ids = sorted(events_df['NEW_EPISODE_ID'].dropna().unique().tolist())

    total_sensor_bytes = 0
    total_mrms_bytes = 0
    total_sensor_files = 0
    per_episode = []

    t0 = time.time()
    for i, episode_id in enumerate(episode_ids):
        episode_rows = events_df[events_df['NEW_EPISODE_ID'] == episode_id]
        start_dt, end_dt = episode_window(episode_rows)

        # Union of sensors matched under either granularity, per type.
        ep_sensor_bytes = 0
        ep_sensor_files = 0
        for sensor_type in SENSOR_TABLES:
            codes_seen = set()
            for granularity in ['county', 'huc8']:
                matched = match_sensors(episode_rows, sensor_type, granularity=granularity)
                for _, row in matched.iterrows():
                    code = get_sensor_code(row, sensor_type)
                    if code in codes_seen:
                        continue
                    codes_seen.add(code)
                    sliced = load_archive_slice(sensor_type, code, start_dt, end_dt)
                    if sliced is not None and not sliced.empty:
                        csv_bytes = sliced.to_csv(index=False).encode('utf-8')
                        ep_sensor_bytes += len(csv_bytes)
                        ep_sensor_files += 1

        # MRMS hourly series: one row per hour in window with cached data.
        start_utc, end_utc = _episode_utc_window(episode_rows)
        hours = pd.date_range(start_utc, end_utc, freq='h')
        n_hours_with_data = sum(1 for hr in hours if load_hour_grid(hr.to_pydatetime()) is not None)
        ep_mrms_bytes = n_hours_with_data * 40  # ~40 bytes/row: datetime,mean_mm,max_mm

        total_sensor_bytes += ep_sensor_bytes
        total_mrms_bytes += ep_mrms_bytes
        total_sensor_files += ep_sensor_files
        per_episode.append((episode_id, ep_sensor_bytes, ep_sensor_files, ep_mrms_bytes))

        if (i + 1) % 20 == 0 or i == len(episode_ids) - 1:
            elapsed = time.time() - t0
            print(f'  {i+1}/{len(episode_ids)}  ({elapsed:.0f}s elapsed)  '
                  f'running sensor total: {total_sensor_bytes/1e6:.1f} MB')

    print()
    print(f'TOTAL sensor CSV bytes (raw, uncompressed): {total_sensor_bytes/1e6:.1f} MB across {total_sensor_files} sensor-episode files')
    print(f'TOTAL MRMS CSV bytes (raw, uncompressed):   {total_mrms_bytes/1e6:.1f} MB')
    print(f'GRAND TOTAL raw:                            {(total_sensor_bytes+total_mrms_bytes)/1e6:.1f} MB')
    print()
    per_episode.sort(key=lambda x: -x[1])
    print('Top 10 heaviest episodes (sensor data):')
    for ep_id, sb, sf, mb in per_episode[:10]:
        print(f'  {ep_id}: {sb/1e6:.2f} MB sensors ({sf} files), {mb/1e3:.1f} KB mrms')

    # Existing wizard.html size for comparison
    wiz_path = BASE_DIR / 'choropleth' / 'wizard.html'
    if wiz_path.exists():
        print()
        print(f'Current wizard.html size (lightweight, no time series): {wiz_path.stat().st_size/1e6:.2f} MB')


if __name__ == '__main__':
    main()
