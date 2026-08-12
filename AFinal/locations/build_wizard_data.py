"""
Precomputes everything the interactive filter wizard needs to run entirely
client-side: for all 135 episodes, sensor-availability counts (both HUC8 and
county granularity) plus the region geometry, bundled into one JSON file.

This is what makes filtering feel instant in the browser -- the wizard never
calls back to Python. Moving a "minimum sensors" slider just re-filters this
already-computed episode list and re-tallies episode_count per region
client-side. Only sensor counts are covered yet (this is the same shape
future filters -- FEMA claim counts, HWM counts, etc. -- would extend), since
that's the dimension asked for first.

Output: choropleth/wizard_data.json
  {
    "episodes": {
      "<episode_id>": {
        "event_types": [...], "begin_date": "...", "end_date": "...",
        "counties": ["19103", ...], "huc8s": ["07080209", ...],
        "n_sensors": {
          "huc8":   {"ifc_river": n, "ifc_hydrostation": n, "usgs": n, "total": n},
          "county": {"ifc_river": n, "ifc_hydrostation": n, "usgs": n, "total": n}
        }
      }, ...
    },
    "county_geometry": <geojson FeatureCollection, from by_county.geojson>,
    "huc8_geometry":   <geojson FeatureCollection, from by_huc8.geojson>
  }
"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from episode_sensor_extract import load_noaa_events, match_episode_sensors, match_sensors, SENSOR_TABLES, EPISODES_DIR
from episode_hwm_extract import extract_episode_hwms
from episode_fema_extract import extract_episode_fema_claims
from episode_inundation_extract import extract_episode_inundation
from episode_precip_extract import extract_episode_precip
from choropleth_data import build_county_choropleth, build_huc8_choropleth, OUT_DIR

BASE_DIR = Path(__file__).parent
FEMA_PREFETCH_WORKERS = 6


def _prefetch_fema_claims(episode_ids, events_df):
    """
    FEMA claims are a live OpenFEMA call per episode (unlike sensors/HWMs,
    which read local caches), so an uncached first build would otherwise
    make 135 sequential ~2s API calls (~5 min) in the main loop below.
    Fetching them in parallel first means the main loop -- which needs to
    stay a straightforward sequential pass for the other per-episode stats
    -- always hits a warm cache.
    """
    uncached = [
        eid for eid in episode_ids
        if not (EPISODES_DIR / str(eid).replace('/', '_') / 'fema_claims.csv').exists()
    ]
    if not uncached:
        return
    print(f'Pre-fetching FEMA claims for {len(uncached)} uncached episodes ({FEMA_PREFETCH_WORKERS} threads)...')
    with ThreadPoolExecutor(max_workers=FEMA_PREFETCH_WORKERS) as executor:
        futures = {executor.submit(extract_episode_fema_claims, eid, events_df=events_df): eid for eid in uncached}
        for i, future in enumerate(as_completed(futures)):
            future.result()
            if (i + 1) % 20 == 0 or i == len(uncached) - 1:
                print(f'  {i+1}/{len(uncached)}')


def build():
    events_df = load_noaa_events()
    episode_ids = sorted(events_df['NEW_EPISODE_ID'].dropna().unique().tolist())

    _prefetch_fema_claims(episode_ids, events_df)

    print(f'Computing sensor availability for {len(episode_ids)} episodes...')

    episodes = {}
    for i, episode_id in enumerate(episode_ids):
        episode_rows = events_df[events_df['NEW_EPISODE_ID'] == episode_id]
        counties = sorted(episode_rows['FIPS_5'].unique().tolist())
        huc8s = sorted([h for h in episode_rows['HUC8_clean'].unique().tolist() if h != '00000000'])

        n_sensors = {}
        sensor_points = {}
        for granularity in ['huc8', 'county']:
            counts = {'ifc_river': 0, 'ifc_hydrostation': 0, 'usgs': 0}
            points = []
            for sensor_type in SENSOR_TABLES:
                matched = match_sensors(episode_rows, sensor_type, granularity=granularity)
                counts[sensor_type] = len(matched)
                for _, row in matched.iterrows():
                    if pd.isna(row.get('lat')) or pd.isna(row.get('lng')):
                        continue
                    points.append({
                        'type': sensor_type,
                        'lat': float(row['lat']), 'lng': float(row['lng']),
                        'description': row.get('description'),
                    })
            counts['total'] = sum(counts.values())
            n_sensors[granularity] = counts
            sensor_points[granularity] = points

        hwms_df = extract_episode_hwms(episode_id, events_df=events_df)
        hwm_points = [
            {'lat': float(r['latitude_dd']), 'lon': float(r['longitude_dd']),
             'waterbody': r.get('waterbody'), 'elev_ft': None if pd.isna(r.get('elev_ft')) else float(r['elev_ft'])}
            for _, r in hwms_df.iterrows() if pd.notna(r.get('latitude_dd')) and pd.notna(r.get('longitude_dd'))
        ]

        fema_df = extract_episode_fema_claims(episode_id, events_df=events_df)
        fema_points = [
            {'lat': float(r['latitude']), 'lon': float(r['longitude']),
             'amount': None if pd.isna(r.get('amountPaidOnBuildingClaim')) else float(r['amountPaidOnBuildingClaim']),
             'date': str(r.get('dateOfLoss'))[:10]}
            for _, r in fema_df.iterrows() if pd.notna(r.get('latitude')) and pd.notna(r.get('longitude'))
        ]

        inundation_df = extract_episode_inundation(episode_id, events_df=events_df)
        n_inundation_layers = len(inundation_df)
        n_inundation_communities = inundation_df['community_name'].nunique() if not inundation_df.empty else 0
        precip = extract_episode_precip(episode_id, events_df=events_df)

        # NOAA's own reported impact -- already present in the base storm
        # events table (INJURIES_DIRECT/INDIRECT, DEATHS_DIRECT/INDIRECT,
        # DAMAGE_PROPERTY, DAMAGE_CROPS), just summed across the episode's
        # event rows. DAMAGE_PROPERTY/DAMAGE_CROPS are parsed from NOAA's
        # '30.00K'/'1.50M'-style strings into USD by load_noaa_events().
        n_injuries = int(episode_rows['INJURIES_DIRECT'].sum() + episode_rows['INJURIES_INDIRECT'].sum())
        n_deaths = int(episode_rows['DEATHS_DIRECT'].sum() + episode_rows['DEATHS_INDIRECT'].sum())
        damage_property_usd = float(episode_rows['DAMAGE_PROPERTY_USD'].sum())
        damage_crops_usd = float(episode_rows['DAMAGE_CROPS_USD'].sum())

        episodes[str(episode_id)] = {
            'event_types': sorted(episode_rows['EVENT_TYPE'].dropna().unique().tolist()),
            'begin_date': str(episode_rows['BEGIN_DT'].min()),
            'end_date': str(episode_rows['END_DT'].max()),
            'counties': counties,
            'huc8s': huc8s,
            'n_sensors': n_sensors,
            'sensor_points': sensor_points,
            'n_hwms': len(hwm_points),
            'hwm_points': hwm_points,
            'n_fema_claims': len(fema_points),
            'fema_points': fema_points,
            'n_inundation_layers': n_inundation_layers,
            'n_inundation_communities': n_inundation_communities,
            'precip_total_mm': precip['total_mm'],
            'n_injuries': n_injuries,
            'n_deaths': n_deaths,
            'damage_property_usd': damage_property_usd,
            'damage_crops_usd': damage_crops_usd,
        }
        if (i + 1) % 20 == 0 or i == len(episode_ids) - 1:
            print(f'  {i+1}/{len(episode_ids)}')

    county_geo = json.loads(Path(build_county_choropleth()).read_text())
    huc8_geo = json.loads(Path(build_huc8_choropleth()).read_text())

    # Raw per-event point geometry (485 rows -- one per NOAA-reported event,
    # several per episode) for the "show event locations" map toggle. Kept
    # separate from the 'episodes' dict above, which is one aggregated
    # record per episode, not per event.
    events = []
    for _, row in events_df.iterrows():
        events.append({
            'episode_id': str(row['NEW_EPISODE_ID']),
            'event_type': row['EVENT_TYPE'],
            'begin_date': str(row['BEGIN_DT']),
            'begin_lat': float(row['BEGIN_LAT']), 'begin_lon': float(row['BEGIN_LON']),
            'end_lat': float(row['END_LAT']), 'end_lon': float(row['END_LON']),
        })

    out = {
        'episodes': episodes,
        'events': events,
        'county_geometry': county_geo,
        'huc8_geometry': huc8_geo,
    }
    out_path = OUT_DIR / 'wizard_data.json'
    out_path.write_text(json.dumps(out))
    print(f'[OK] wrote {out_path} ({out_path.stat().st_size / 1e6:.1f} MB)')
    return out_path


if __name__ == '__main__':
    build()
