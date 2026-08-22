"""
Builds a per-episode accumulated MRMS precipitation grid (gzip-compressed,
base64-encoded), embedded in wizard.html for the "Show MRMS accumulated
precipitation" map toggle -- same lazy-decompress-on-first-use pattern as
build_embedded_episode_data.py's sensor time series.

Unlike episode_precip_extract.py's extract_episode_precip() (one bbox-mean
scalar per episode, for the "minimum rainfall" filter slider), this keeps
the full spatial grid -- summed across every hour in the episode's padded
UTC window -- so the map can actually draw a rainfall heatmap instead of
just filtering on a single number.

Bbox: the UNION of (a) the episode's own event-point bbox (same as
episode_precip_extract's _episode_bbox) and (b) the full polygon bounding
box of every county AND every HUC8 watershed the episode touches -- not
just (a) alone, which was the first cut at this and turned out too narrow:
NOAA storm reports are point locations along a path, so an episode can be
spatially matched to a HUC8 watershed (any polygon overlap counts) while
its actual reported points only fall in one small corner of that
watershed. Using only the point bbox meant the "This Region" view for a
large watershed only had real grid data over a sliver of it. Padded by
MRMS_GRID_PAD_DEG on top of that union, then clamped to the hour cache's
own crop bounds (mrms_hour_cache.CROP_MIN/MAX_LAT/LON) since that's all
that's locally cached. Grid stays at MRMS's native ~0.01deg resolution --
even the widest episode's polygon union is at most a few degrees across,
so this is at most a few hundred cells per side, not worth downsampling.

A cell missing from EVERY hour in the window stays null (no data, not
"zero rain"). A cell missing from only SOME hours is summed over the hours
it does have (np.nansum) rather than propagating NaN and blanking out the
whole cell over one bad hour -- matches episode_precip_extract's own
per-hour mean, which already ignores individual NaN cells rather than
treating one as poisoning the whole bbox.

Output shape (before compression):
  {
    "<episode_id>": {
      "lat_min": .., "lat_max": .., "lon_min": .., "lon_max": ..,  (lon in -180..180)
      "nlat": .., "nlon": ..,
      "values": [[mm or null, ...], ...]   -- nlat rows x nlon cols,
                                               row 0 = lat_max (north), col 0 = lon_min (west)
      "max_intensity_mm": mm or null   -- peak single-hour, single-cell value
                                           anywhere in the region during the
                                           episode (a rate, not accumulated)
    }, ...
  }
Episodes with zero cached hours in their window (no local MRMS data at all)
are simply omitted -- the frontend treats a missing episode key as "no MRMS
data available" rather than an all-null grid.

Also writes choropleth/region_intensity.json: per (episode, county-or-HUC8)
peak single-hour rain rate, precisely polygon-clipped to that region alone
(not the whole episode's matched-region union that max_intensity_mm above
covers). Unlike the spatial grid, counties/HUC8s are a small fixed set
(~99 + ~56), so this doesn't need shipping another grid -- it's cheap
per-episode scalars, read directly (not lazily) by build_wizard_data.py so
the Map & Filters detail panel and the region-ranked leaderboards can look
up "how intense was the rain in just this county/watershed" synchronously.
"""
import base64
import gzip
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
from shapely import contains_xy
from shapely.geometry import shape

from episode_sensor_extract import load_noaa_events
from episode_precip_extract import _episode_bbox, _episode_utc_window
from mrms_hour_cache import load_hour_grid, CROP_MIN_LAT, CROP_MAX_LAT, CROP_MIN_LON, CROP_MAX_LON
from choropleth_data import build_county_choropleth, build_huc8_choropleth

BASE_DIR = Path(__file__).parent
OUT_PATH = BASE_DIR / 'choropleth' / 'embedded_mrms_grids.b64.txt'
REGION_INTENSITY_PATH = BASE_DIR / 'choropleth' / 'region_intensity.json'
MRMS_GRID_PAD_DEG = 0.35


def _walk_coords(coords):
    """Yields every [lon, lat] leaf pair out of a Polygon's or MultiPolygon's
    nested coordinate list, regardless of nesting depth."""
    if not coords:
        return
    if isinstance(coords[0], (int, float)):
        yield coords
        return
    for c in coords:
        yield from _walk_coords(c)


def _geometry_bbox(geometry):
    """(min_lat, max_lat, min_lon_360, max_lon_360) bounding a GeoJSON
    Polygon/MultiPolygon geometry -- lon converted to the same 0-360 form
    the MRMS hour cache stores its own longitudes in."""
    lats, lons_360 = [], []
    for lon, lat in _walk_coords(geometry['coordinates']):
        lats.append(lat)
        lons_360.append(lon % 360)
    return min(lats), max(lats), min(lons_360), max(lons_360)


def _build_region_bbox_lookups():
    """county_fips -> bbox and huc8 -> bbox, from the already-built
    choropleth geometry (same files build_wizard_data.py reads)."""
    county_geo = json.loads(Path(build_county_choropleth()).read_text())
    huc8_geo = json.loads(Path(build_huc8_choropleth()).read_text())
    county_bbox = {f['properties']['county_fips']: _geometry_bbox(f['geometry']) for f in county_geo['features']}
    huc8_bbox = {f['properties']['huc8']: _geometry_bbox(f['geometry']) for f in huc8_geo['features']}
    return county_bbox, huc8_bbox


def _build_region_geom_lookups():
    """county_fips -> shapely geometry and huc8 -> shapely geometry, for the
    precise per-region polygon clip compute_region_intensities() needs
    (a bbox alone would over-include cells just outside the county/HUC8's
    actual boundary). Coordinates stay in native GeoJSON -180..180 form --
    the mesh built from grid_lats/grid_lons is converted to match at the
    call site, since the MRMS cache itself stores longitude 0..360."""
    county_geo = json.loads(Path(build_county_choropleth()).read_text())
    huc8_geo = json.loads(Path(build_huc8_choropleth()).read_text())
    county_geom = {f['properties']['county_fips']: shape(f['geometry']) for f in county_geo['features']}
    huc8_geom = {f['properties']['huc8']: shape(f['geometry']) for f in huc8_geo['features']}
    return county_geom, huc8_geom


def compute_region_intensities(episode_rows, arr, grid_lats, grid_lons, county_bbox, county_geom, huc8_bbox, huc8_geom):
    """Peak single-hour, single-cell rain rate (mm/hr) within EACH county and
    HUC8 this episode touches individually -- as opposed to max_intensity_mm
    (the peak anywhere across the whole union region). Reuses the per-hour
    stack `arr` (shape hours x nlat x nlon) already loaded for the episode's
    union grid -- no re-fetching hour data, just slicing+masking a subset of
    what's already in memory. Returns {'county': {fips: mm, ...}, 'huc8': {huc8: mm, ...}},
    omitting any region with no cached data or no cells actually inside its
    polygon (as opposed to just inside its bbox)."""
    region_intensity = {'county': {}, 'huc8': {}}
    county_ids = episode_rows['FIPS_5'].unique().tolist()
    huc8_ids = [h for h in episode_rows['HUC8_clean'].unique() if h != '00000000']

    for kind, ids, bbox_lookup, geom_lookup in [('county', county_ids, county_bbox, county_geom), ('huc8', huc8_ids, huc8_bbox, huc8_geom)]:
        for region_id in ids:
            bbox = bbox_lookup.get(region_id)
            geom = geom_lookup.get(region_id)
            if bbox is None or geom is None:
                continue
            r_min_lat, r_max_lat, r_min_lon, r_max_lon = bbox
            lat_idx = np.where((grid_lats >= r_min_lat) & (grid_lats <= r_max_lat))[0]
            lon_idx = np.where((grid_lons >= r_min_lon) & (grid_lons <= r_max_lon))[0]
            if lat_idx.size == 0 or lon_idx.size == 0:
                continue
            sub_arr = arr[:, lat_idx, :][:, :, lon_idx]  # hours x sub-lat x sub-lon
            lon_mesh, lat_mesh = np.meshgrid(grid_lons[lon_idx], grid_lats[lat_idx])  # each sub-lat x sub-lon
            lon_mesh_180 = np.where(lon_mesh > 180, lon_mesh - 360, lon_mesh)
            mask = contains_xy(geom, lon_mesh_180, lat_mesh)  # sub-lat x sub-lon, True = inside the actual polygon
            if not mask.any():
                continue
            masked = np.where(mask[None, :, :], sub_arr, np.nan)
            if np.all(np.isnan(masked)):
                continue
            region_intensity[kind][region_id] = round(float(np.nanmax(masked)), 1)

    return region_intensity


def build_episode_grid(episode_rows, county_bbox, huc8_bbox, county_geom, huc8_geom, pad_hours=3):
    """Returns (grid_dict_or_None, region_intensity_dict). region_intensity_dict
    is {} when there's no cached hour data at all (grid_dict is None too in
    that case)."""
    min_lat, max_lat, lon_min_360, lon_max_360 = _episode_bbox(episode_rows)

    # Union in every touched county's and HUC8's own polygon bbox -- see
    # module docstring for why the point-only bbox above isn't enough.
    region_ids = list(episode_rows['FIPS_5'].unique()) + \
        [h for h in episode_rows['HUC8_clean'].unique() if h != '00000000']
    for region_id in region_ids:
        bbox = county_bbox.get(region_id) or huc8_bbox.get(region_id)
        if bbox is None:
            continue
        r_min_lat, r_max_lat, r_min_lon, r_max_lon = bbox
        min_lat, max_lat = min(min_lat, r_min_lat), max(max_lat, r_max_lat)
        lon_min_360, lon_max_360 = min(lon_min_360, r_min_lon), max(lon_max_360, r_max_lon)

    min_lat = max(min_lat - MRMS_GRID_PAD_DEG, CROP_MIN_LAT)
    max_lat = min(max_lat + MRMS_GRID_PAD_DEG, CROP_MAX_LAT)
    lon_min_360 = max(lon_min_360 - MRMS_GRID_PAD_DEG, CROP_MIN_LON % 360)
    lon_max_360 = min(lon_max_360 + MRMS_GRID_PAD_DEG, CROP_MAX_LON % 360)

    start_utc, end_utc = _episode_utc_window(episode_rows, pad_hours=pad_hours)
    hours = pd.date_range(start_utc, end_utc, freq='h')

    stack = []
    grid_lats = grid_lons = None
    for hr in hours:
        grid = load_hour_grid(hr.to_pydatetime())
        if grid is None:
            continue
        values, lats, lons = grid
        lat_idx = np.where((lats >= min_lat) & (lats <= max_lat))[0]
        lon_idx = np.where((lons >= lon_min_360) & (lons <= lon_max_360))[0]
        if lat_idx.size == 0 or lon_idx.size == 0:
            continue
        sub = values[np.ix_(lat_idx, lon_idx)]
        if grid_lats is None:
            grid_lats, grid_lons = lats[lat_idx], lons[lon_idx]
        stack.append(sub)

    if not stack:
        return None, {}

    arr = np.stack(stack)
    all_nan_mask = np.all(np.isnan(arr), axis=0)
    total = np.nansum(arr, axis=0)
    total[all_nan_mask] = np.nan

    # Peak single-HOUR, single-CELL value anywhere in the stack -- i.e. the
    # most intense hour this region saw, in mm/hr, as opposed to `total`
    # which is the accumulated sum across the whole window. Distinct from
    # episode_precip_extract.py's own max_mm (that one is the max of the
    # per-pixel ACCUMULATED total, not a single hour's rate).
    max_intensity = np.nanmax(arr) if not np.all(np.isnan(arr)) else np.nan
    max_intensity_mm = None if np.isnan(max_intensity) else round(float(max_intensity), 1)

    region_intensity = compute_region_intensities(episode_rows, arr, grid_lats, grid_lons, county_bbox, county_geom, huc8_bbox, huc8_geom)

    lon_out_min = float(grid_lons.min())
    lon_out_max = float(grid_lons.max())
    if lon_out_min > 180:
        lon_out_min -= 360
    if lon_out_max > 180:
        lon_out_max -= 360

    values_list = [[None if np.isnan(v) else round(float(v), 1) for v in row] for row in total]

    grid_dict = {
        'lat_min': float(grid_lats.min()), 'lat_max': float(grid_lats.max()),
        'lon_min': lon_out_min, 'lon_max': lon_out_max,
        'nlat': int(total.shape[0]), 'nlon': int(total.shape[1]),
        'values': values_list,
        'max_intensity_mm': max_intensity_mm,
    }
    return grid_dict, region_intensity


def main():
    events_df = load_noaa_events()
    episode_ids = sorted(events_df['NEW_EPISODE_ID'].dropna().unique().tolist())
    county_bbox, huc8_bbox = _build_region_bbox_lookups()
    county_geom, huc8_geom = _build_region_geom_lookups()

    payload = {}
    region_intensity_payload = {}
    skipped = 0
    t0 = time.time()
    for i, episode_id in enumerate(episode_ids):
        episode_rows = events_df[events_df['NEW_EPISODE_ID'] == episode_id]
        grid, region_intensity = build_episode_grid(episode_rows, county_bbox, huc8_bbox, county_geom, huc8_geom)
        if grid is None:
            skipped += 1
        else:
            payload[episode_id] = grid
        if region_intensity['county'] or region_intensity['huc8']:
            region_intensity_payload[episode_id] = region_intensity
        if (i + 1) % 20 == 0 or i == len(episode_ids) - 1:
            print(f'  {i+1}/{len(episode_ids)}  ({time.time()-t0:.0f}s elapsed, {skipped} skipped so far)')

    raw_json = json.dumps(payload)
    raw_bytes = raw_json.encode('utf-8')
    compressed = gzip.compress(raw_bytes, compresslevel=9)
    b64 = base64.b64encode(compressed).decode('ascii')

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(b64, encoding='ascii')

    region_intensity_json = json.dumps(region_intensity_payload)
    REGION_INTENSITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGION_INTENSITY_PATH.write_text(region_intensity_json, encoding='utf-8')

    print()
    print(f'Episodes with a grid: {len(payload)} / {len(episode_ids)} ({skipped} skipped -- no cached hour data in window)')
    print(f'Raw JSON:        {len(raw_bytes)/1e6:.1f} MB')
    print(f'Gzip compressed: {len(compressed)/1e6:.1f} MB')
    print(f'Base64 encoded:  {len(b64)/1e6:.1f} MB')
    print(f'Wrote {OUT_PATH}')
    print(f'Wrote {REGION_INTENSITY_PATH} ({len(region_intensity_json)/1e3:.1f} KB, {len(region_intensity_payload)} episodes)')


if __name__ == '__main__':
    main()
