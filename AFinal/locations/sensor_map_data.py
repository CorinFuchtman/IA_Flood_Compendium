"""
Builds the wizard's "sensors" step: how many of each sensor type exist,
where they are, and how they break down per county/HUC-8 -- the natural
screen after the episode choropleth, before narrowing to one episode.

Outputs into choropleth/ alongside the episode choropleth files:
  sensor_points.geojson   -- one Point feature per sensor (603 total), with
                              sensor_type/code/description/active properties,
                              for plotting exact locations on a map.
  sensors_by_county.geojson / sensors_by_huc8.geojson -- same boundary
                              geometry as by_county.geojson/by_huc8.geojson,
                              with n_ifc_river/n_ifc_hydrostation/n_usgs/
                              n_sensors_total properties instead of episode
                              counts, covering all 99 Iowa counties / all 56
                              Iowa-touching HUC-8s (0 where no sensor of a
                              given type falls in that region).
"""
import json
from pathlib import Path

import pandas as pd

from choropleth_data import build_county_choropleth, build_huc8_choropleth, OUT_DIR

BASE_DIR = Path(__file__).parent
SENSOR_DIR = BASE_DIR / 'sensor_csv_huc08'

SENSOR_FILES = {
    'ifc_river':        SENSOR_DIR / 'ifis_stream_sensors_huc08_with_county.csv',
    'ifc_hydrostation': SENSOR_DIR / 'ifis_hydrostations_huc08_with_county.csv',
    'usgs':             SENSOR_DIR / 'usgs_stream_sensors_huc08_with_foreign_id1_with_county.csv',
}

SENSOR_LABELS = {
    'ifc_river': 'IFC River Sensor',
    'ifc_hydrostation': 'IFC Hydrostation',
    'usgs': 'USGS Sensor',
}


def _normalize_huc(series):
    return series.fillna('').astype(str).str.replace(r'\.0$', '', regex=True).str.strip().str.zfill(8)


def print_sensor_totals():
    print(f"{'Sensor type':20s} {'Total':>6s} {'Active':>7s} {'In Iowa':>8s} {'Out of state':>13s}")
    grand_total = 0
    for sensor_type, path in SENSOR_FILES.items():
        df = pd.read_csv(path)
        total = len(df)
        active = (df['active'] == 't').sum()
        in_ia = df['county_fips'].astype(str).str.zfill(5).str.startswith('19').sum()
        grand_total += total
        print(f"{SENSOR_LABELS[sensor_type]:20s} {total:6d} {active:7d} {in_ia:8d} {total-in_ia:13d}")
    print(f"{'TOTAL':20s} {grand_total:6d}")


def build_sensor_points(force=False):
    out_path = OUT_DIR / 'sensor_points.geojson'
    if out_path.exists() and not force:
        return out_path

    features = []
    for sensor_type, path in SENSOR_FILES.items():
        df = pd.read_csv(path)
        code_col = 'foreign_id1' if sensor_type != 'ifc_river' else 'foreign_id'
        for _, row in df.iterrows():
            lat, lng = row.get('lat'), row.get('lng')
            if pd.isna(lat) or pd.isna(lng):
                continue
            features.append({
                'type': 'Feature',
                'geometry': {'type': 'Point', 'coordinates': [float(lng), float(lat)]},
                'properties': {
                    'sensor_type': sensor_type,
                    'sensor_type_label': SENSOR_LABELS[sensor_type],
                    'code': str(row.get(code_col, row.get('id'))),
                    'description': row.get('description'),
                    'active': row.get('active') == 't',
                    'county_name': row.get('county_name'),
                    'huc8': row.get('HUC8'),
                },
            })

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({'type': 'FeatureCollection', 'features': features}))
    print(f'[OK] wrote {out_path} ({len(features)} sensor points)')
    return out_path


def build_sensors_by_county(force=False):
    out_path = OUT_DIR / 'sensors_by_county.geojson'
    if out_path.exists() and not force:
        return out_path

    county_geo_path = build_county_choropleth()
    boundaries = json.loads(Path(county_geo_path).read_text())

    counts = {}  # fips -> {type: n}
    for sensor_type, path in SENSOR_FILES.items():
        df = pd.read_csv(path)
        by_fips = df['county_fips'].dropna().astype(int).astype(str).str.zfill(5).value_counts()
        for fips, n in by_fips.items():
            counts.setdefault(fips, {}).update({sensor_type: int(n)})

    for feat in boundaries['features']:
        fips = feat['properties']['county_fips']
        c = counts.get(fips, {})
        n_river = c.get('ifc_river', 0)
        n_hydro = c.get('ifc_hydrostation', 0)
        n_usgs = c.get('usgs', 0)
        feat['properties']['n_ifc_river'] = n_river
        feat['properties']['n_ifc_hydrostation'] = n_hydro
        feat['properties']['n_usgs'] = n_usgs
        feat['properties']['n_sensors_total'] = n_river + n_hydro + n_usgs

    out_path.write_text(json.dumps(boundaries))
    print(f'[OK] wrote {out_path}')
    return out_path


def build_sensors_by_huc8(force=False):
    out_path = OUT_DIR / 'sensors_by_huc8.geojson'
    if out_path.exists() and not force:
        return out_path

    huc8_geo_path = build_huc8_choropleth()
    boundaries = json.loads(Path(huc8_geo_path).read_text())

    counts = {}  # huc8 -> {type: n}
    for sensor_type, path in SENSOR_FILES.items():
        df = pd.read_csv(path)
        by_huc8 = _normalize_huc(df['HUC8']).value_counts()
        for huc8, n in by_huc8.items():
            if huc8 == '00000000':
                continue
            counts.setdefault(huc8, {}).update({sensor_type: int(n)})

    for feat in boundaries['features']:
        huc8 = feat['properties']['huc8']
        c = counts.get(huc8, {})
        n_river = c.get('ifc_river', 0)
        n_hydro = c.get('ifc_hydrostation', 0)
        n_usgs = c.get('usgs', 0)
        feat['properties']['n_ifc_river'] = n_river
        feat['properties']['n_ifc_hydrostation'] = n_hydro
        feat['properties']['n_usgs'] = n_usgs
        feat['properties']['n_sensors_total'] = n_river + n_hydro + n_usgs

    out_path.write_text(json.dumps(boundaries))
    print(f'[OK] wrote {out_path}')
    return out_path


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()

    print_sensor_totals()
    print()
    build_sensor_points(force=args.force)
    build_sensors_by_county(force=args.force)
    build_sensors_by_huc8(force=args.force)
