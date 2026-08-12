"""
Builds the real time-series payload that gets embedded (gzip-compressed,
base64-encoded) directly in wizard.html, so the "Download ZIP" buttons
produce actual sensor readings and MRMS rainfall instead of just point
locations.

For each of the 135 episodes: the union of sensors matched under either
HUC8 or county granularity (a sensor's raw time series doesn't depend on
which granularity view you were filtering under -- only which sensors count
as "matched" does, so exporting the union covers both), each sliced to that
episode's padded window via the same load_archive_slice used everywhere else
in this pipeline, plus an hourly MRMS precipitation series over the same
window.

Output shape (before compression):
  {
    "<episode_id>": {
      "usgs_sensors/<code>.csv": "<csv text>",
      "ifc_hydrostations/<code>.csv": "<csv text>",
      "ifc_river_sensors/<code>.csv": "<csv text>",
      "mrms_precipitation/hourly_precip.csv": "<csv text>"
    }, ...
  }

That JSON is gzip-compressed and base64-encoded; build_wizard_html.py embeds
the resulting string as a JS constant, and the page decompresses it lazily
(only when a download is first requested) using the browser's native
DecompressionStream -- no extra JS library needed for that half.

Measured via estimate_embed_size.py before building this for real: ~116MB
raw across all episodes, which is why this goes through gzip rather than
being embedded as plain text.
"""
import base64
import gzip
import json
import time
from pathlib import Path

from episode_sensor_extract import load_noaa_events, match_sensors, load_archive_slice, get_sensor_code, SENSOR_TABLES
from episode_precip_extract import extract_episode_precip_timeseries

BASE_DIR = Path(__file__).parent
OUT_PATH = BASE_DIR / 'choropleth' / 'embedded_episode_data.b64.txt'

FOLDER_NAMES = {
    'usgs': 'usgs_sensors',
    'ifc_hydrostation': 'ifc_hydrostations',
    'ifc_river': 'ifc_river_sensors',
}


def build_episode_payload(episode_id, episode_rows):
    from datetime import timedelta
    start_dt = episode_rows['BEGIN_DT'].min() - timedelta(hours=3)
    end_dt = episode_rows['END_DT'].max() + timedelta(hours=3)

    files = {}
    for sensor_type in SENSOR_TABLES:
        folder = FOLDER_NAMES[sensor_type]
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
                    safe_code = str(code).replace('/', '_').replace('\\', '_')
                    files[f'{folder}/{safe_code}.csv'] = sliced.to_csv(index=False)

    precip_df = extract_episode_precip_timeseries(episode_id, events_df=None)
    if not precip_df.empty:
        files['mrms_precipitation/hourly_precip.csv'] = precip_df.to_csv(index=False)

    return files


def main():
    events_df = load_noaa_events()
    episode_ids = sorted(events_df['NEW_EPISODE_ID'].dropna().unique().tolist())

    payload = {}
    t0 = time.time()
    for i, episode_id in enumerate(episode_ids):
        episode_rows = events_df[events_df['NEW_EPISODE_ID'] == episode_id]
        payload[episode_id] = build_episode_payload(episode_id, episode_rows)
        if (i + 1) % 20 == 0 or i == len(episode_ids) - 1:
            elapsed = time.time() - t0
            n_files = sum(len(v) for v in payload.values())
            print(f'  {i+1}/{len(episode_ids)}  ({elapsed:.0f}s elapsed, {n_files} files so far)')

    raw_json = json.dumps(payload)
    raw_bytes = raw_json.encode('utf-8')
    compressed = gzip.compress(raw_bytes, compresslevel=9)
    b64 = base64.b64encode(compressed).decode('ascii')

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(b64, encoding='ascii')

    print()
    print(f'Raw JSON:        {len(raw_bytes)/1e6:.1f} MB')
    print(f'Gzip compressed: {len(compressed)/1e6:.1f} MB')
    print(f'Base64 encoded:  {len(b64)/1e6:.1f} MB  (this is what gets embedded -- base64 adds ~33% over raw compressed)')
    print(f'Wrote {OUT_PATH}')


if __name__ == '__main__':
    main()
