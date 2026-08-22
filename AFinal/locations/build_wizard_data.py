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
        },
        "precip_total_mm": n, "precip_median_mm": n, "precip_max_mm": n,
        "precip_max_intensity_mm": n | null,
        "region_intensity_mm": {"county": {"<fips>": n, ...}, "huc8": {"<huc8>": n, ...}},
        "flash_most_severe": {"ari_years": n, "duration": "3h", "datetime_utc": "..."} | null
      }, ...
    },
    "county_geometry": <geojson FeatureCollection, from by_county.geojson>,
    "huc8_geometry":   <geojson FeatureCollection, from by_huc8.geojson>
  }
"""
import base64
import gzip
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from pathlib import Path

import pandas as pd

from episode_sensor_extract import load_noaa_events, match_episode_sensors, match_sensors, SENSOR_TABLES, EPISODES_DIR, get_sensor_code
from episode_hwm_extract import extract_episode_hwms
from episode_fema_extract import extract_episode_fema_claims
from episode_inundation_extract import extract_episode_inundation
from episode_precip_extract import extract_episode_precip
from episode_flash_extract import extract_episode_flash
from episode_rma_extract import extract_episode_crop_loss
from choropleth_data import build_county_choropleth, build_huc8_choropleth, OUT_DIR

BASE_DIR = Path(__file__).parent
FEMA_PREFETCH_WORKERS = 6
SENSOR_WINDOW_PAD_HOURS = 3  # matches build_embedded_episode_data.py's padding
EMBEDDED_MRMS_GRIDS_PATH = BASE_DIR / 'choropleth' / 'embedded_mrms_grids.b64.txt'
REGION_INTENSITY_PATH = BASE_DIR / 'choropleth' / 'region_intensity.json'


def _load_mrms_max_intensity_lookup():
    """episode_id -> max_intensity_mm (peak single-hour, single-cell rain
    rate anywhere in the episode's matched region), read from the grid
    payload build_embedded_mrms_grids.py already computed -- avoids
    redoing the GRIB-decode/region-bbox work here just for one scalar.
    Requires build_embedded_mrms_grids.py to have been run first; returns
    an empty lookup (every episode gets None) if it hasn't."""
    if not EMBEDDED_MRMS_GRIDS_PATH.exists():
        print(f'[!] {EMBEDDED_MRMS_GRIDS_PATH.name} not found -- precip_max_intensity_mm will be null for every episode '
              f'until build_embedded_mrms_grids.py has been run.')
        return {}
    b64 = EMBEDDED_MRMS_GRIDS_PATH.read_text(encoding='ascii')
    payload = json.loads(gzip.decompress(base64.b64decode(b64)))
    return {eid: grid.get('max_intensity_mm') for eid, grid in payload.items()}


def _load_region_intensity_lookup():
    """episode_id -> {'county': {fips: mm, ...}, 'huc8': {huc8: mm, ...}} --
    peak single-hour rain rate within EACH region the episode touches
    individually (precisely polygon-clipped), read from
    build_embedded_mrms_grids.py's region_intensity.json. Requires that
    script to have been run first; returns an empty lookup otherwise (every
    episode gets {'county': {}, 'huc8': {}})."""
    if not REGION_INTENSITY_PATH.exists():
        print(f'[!] {REGION_INTENSITY_PATH.name} not found -- region_intensity_mm will be empty for every episode '
              f'until build_embedded_mrms_grids.py has been run.')
        return {}
    return json.loads(REGION_INTENSITY_PATH.read_text(encoding='utf-8'))

# Whether a matched ifc_river/ifc_hydrostation sensor actually reported any
# reading during the episode's padded window -- these two sensor types are
# spatially matched (in range of the episode's watershed/county) regardless
# of whether the station existed/was reporting yet, so without this check
# the map would show pins for stations that error out with "no data" the
# moment you click them (see IFC hydrostation network growth: a third of all
# 58 stations only came online in mid-to-late 2024). USGS sensors are left
# unfiltered -- they're long-running federal gauges where this almost never
# happens.  Reads each archive file's datetime column ONCE (cached across
# all 135 episodes) rather than re-reading per episode.
_ARCHIVE_DATETIME_CACHE = {}


def _sensor_reports_in_window(sensor_type, code, start_dt, end_dt):
    key = (sensor_type, code)
    if key not in _ARCHIVE_DATETIME_CACHE:
        _, archive_dir = SENSOR_TABLES[sensor_type]
        safe_code = str(code).replace('/', '_').replace('\\', '_')
        path = archive_dir / f'{safe_code}.csv'
        if not path.exists():
            _ARCHIVE_DATETIME_CACHE[key] = None
        else:
            df = pd.read_csv(path, usecols=['datetime'], low_memory=False)
            _ARCHIVE_DATETIME_CACHE[key] = df['datetime']
    series = _ARCHIVE_DATETIME_CACHE[key]
    if series is None or series.empty:
        return False
    if sensor_type == 'usgs':
        keycol = series.str.slice(0, 19)
        start_str, end_str = start_dt.strftime('%Y-%m-%dT%H:%M:%S'), end_dt.strftime('%Y-%m-%dT%H:%M:%S')
    else:
        keycol = series
        start_str, end_str = start_dt.strftime('%Y-%m-%d %H:%M:%S'), end_dt.strftime('%Y-%m-%d %H:%M:%S')
    return bool(((keycol >= start_str) & (keycol <= end_str)).any())


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
    mrms_intensity_lookup = _load_mrms_max_intensity_lookup()
    region_intensity_lookup = _load_region_intensity_lookup()

    print(f'Computing sensor availability for {len(episode_ids)} episodes...')

    episodes = {}
    for i, episode_id in enumerate(episode_ids):
        episode_rows = events_df[events_df['NEW_EPISODE_ID'] == episode_id]
        counties = sorted(episode_rows['FIPS_5'].unique().tolist())
        huc8s = sorted([h for h in episode_rows['HUC8_clean'].unique().tolist() if h != '00000000'])
        pad_start_dt = episode_rows['BEGIN_DT'].min() - timedelta(hours=SENSOR_WINDOW_PAD_HOURS)
        pad_end_dt = episode_rows['END_DT'].max() + timedelta(hours=SENSOR_WINDOW_PAD_HOURS)

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
                    code = get_sensor_code(row, sensor_type)
                    # n_sensors (used for "minimum sensors available" filtering)
                    # still counts spatial matches regardless of data -- this
                    # check only controls which pins actually render once
                    # you've drilled into an episode on the map.
                    if sensor_type in ('ifc_river', 'ifc_hydrostation') and not _sensor_reports_in_window(sensor_type, code, pad_start_dt, pad_end_dt):
                        continue
                    points.append({
                        'type': sensor_type,
                        'lat': float(row['lat']), 'lng': float(row['lng']),
                        'description': row.get('description'),
                        # Matches the filename get_sensor_code produces for this
                        # sensor's archive CSV -- lets the wizard page look up the
                        # matching entry in the embedded per-sensor time series
                        # (embedded_episode_data.b64.txt) when a marker is clicked.
                        'code': code,
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
        # Real dollars paid out on NFIP flood claims (building + contents +
        # increased-cost-of-compliance), vs. just a bare claim count -- the
        # headline "FEMA" number the wizard filters/ranks on.
        fema_payout_usd = float(
            fema_df.get('amountPaidOnBuildingClaim', pd.Series(dtype=float)).fillna(0).sum()
            + fema_df.get('amountPaidOnContentsClaim', pd.Series(dtype=float)).fillna(0).sum()
            + fema_df.get('amountPaidOnIncreasedCostOfComplianceClaim', pd.Series(dtype=float)).fillna(0).sum()
        )

        inundation_df = extract_episode_inundation(episode_id, events_df=events_df)
        n_inundation_layers = len(inundation_df)
        inundation_communities = sorted(inundation_df['community_name'].unique().tolist()) if not inundation_df.empty else []
        n_inundation_communities = len(inundation_communities)
        precip = extract_episode_precip(episode_id, events_df=events_df)
        # Most severe (max) FLASH QPE ARI cell across the episode's raw,
        # unpadded hour-by-hour window -- see episode_flash_extract.py's
        # docstring. The full hourly table isn't included here (it lives in
        # embedded_episode_data.b64.txt alongside sensor/precip CSVs) --
        # this is just the one headline cell for the details modal/filters.
        _, flash_most_severe = extract_episode_flash(episode_id, events_df=events_df)

        # NOAA's own reported impact -- already present in the base storm
        # events table (INJURIES_DIRECT/INDIRECT, DEATHS_DIRECT/INDIRECT,
        # DAMAGE_PROPERTY), just summed across the episode's event rows.
        # DAMAGE_PROPERTY is parsed from NOAA's '30.00K'/'1.50M'-style
        # strings into USD by load_noaa_events(). Crop loss uses USDA RMA's
        # Cause of Loss data instead of NOAA's own DAMAGE_CROPS estimate --
        # see episode_rma_extract.py's docstring for why, and for the
        # county-month granularity mismatch this implies: two episodes
        # sharing a county in the same month show the SAME crop indemnity
        # total, since RMA doesn't report per-storm.
        n_injuries = int(episode_rows['INJURIES_DIRECT'].sum() + episode_rows['INJURIES_INDIRECT'].sum())
        n_deaths = int(episode_rows['DEATHS_DIRECT'].sum() + episode_rows['DEATHS_INDIRECT'].sum())
        damage_property_usd = float(episode_rows['DAMAGE_PROPERTY_USD'].sum())
        crop_indemnity_usd, _ = extract_episode_crop_loss(episode_id, events_df=events_df)

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
            'fema_points': fema_points,
            'fema_payout_usd': fema_payout_usd,
            'n_inundation_layers': n_inundation_layers,
            'n_inundation_communities': n_inundation_communities,
            'inundation_communities': inundation_communities,
            'precip_total_mm': precip['total_mm'],
            'precip_median_mm': precip['median_mm'],
            'precip_max_mm': precip['max_mm'],
            'precip_max_intensity_mm': mrms_intensity_lookup.get(str(episode_id)),
            'region_intensity_mm': region_intensity_lookup.get(str(episode_id), {'county': {}, 'huc8': {}}),
            'flash_most_severe': flash_most_severe,
            'n_injuries': n_injuries,
            'n_deaths': n_deaths,
            'damage_property_usd': damage_property_usd,
            'crop_indemnity_usd': crop_indemnity_usd,
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
