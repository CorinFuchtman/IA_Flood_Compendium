"""
Bulk historical sensor data downloader.

Pulls full water-year 2021-2025 (2020-10-01 through 2025-09-30) time series
for every sensor in the three HUC08 sensor lookup tables:
  - sensor_csv_huc08/ifis_stream_sensors_huc08.csv           (IFC river sensors)
  - sensor_csv_huc08/ifis_hydrostations_huc08.csv             (IFC hydrostations)
  - sensor_csv_huc08/usgs_stream_sensors_huc08_with_foreign_id1.csv (USGS sensors)

Writes one raw, native-resolution CSV per sensor into
sensor_data_wy2021_2025/{ifc_river_sensors,ifc_hydrostations,usgs_sensors}/.

Fetch logic (candidate IDs, endpoints, fallbacks) is carried over unchanged
from pipeline (11).ipynb's per-episode sensor helpers -- only the date
window changed, from a short storm window to the full 5-water-year range.
Testing showed all three APIs accept that full range in a single request
per sensor, so no per-year chunking is needed.

Resumable: skips any sensor whose output CSV already exists (non-empty)
unless --force is passed. Safe to re-run after an interruption.
"""
import argparse
import csv
import logging
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

BASE_DIR   = Path(__file__).parent
SENSOR_DIR = BASE_DIR / 'sensor_csv_huc08'
OUT_DIR    = BASE_DIR / 'sensor_data_wy2021_2025'
LOG_DIR    = OUT_DIR / 'logs'

IFC_RIVER_FILE   = SENSOR_DIR / 'ifis_stream_sensors_huc08.csv'
IFC_HYDRO_FILE   = SENSOR_DIR / 'ifis_hydrostations_huc08.csv'
USGS_FILE        = SENSOR_DIR / 'usgs_stream_sensors_huc08_with_foreign_id1.csv'

START_DT = pd.Timestamp('2020-10-01T00:00:00')
END_DT   = pd.Timestamp('2025-09-30T23:59:59')

MAX_WORKERS          = 6
MAX_RETRIES          = 3
RETRY_DELAY_SECONDS  = 5
REQUEST_TIMEOUT       = 90

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-7s %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / f'download_{datetime.now():%Y%m%d_%H%M%S}.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger('bulk_sensor_download')


# ── Fetch helpers (carried over from pipeline (11).ipynb, unchanged logic) ──

def resolve_hydrostation_id(row):
    for col in ['foreign_id1', 'ifc_id', 'station_id', 'hydro_id', 'ifis_id', 'ifc_id.1', 'Sensor_ID']:
        val = row.get(col)
        if pd.notna(val):
            try:
                n = int(float(val))
                if n > 4000:
                    return str(n)
            except ValueError:
                pass
    raw = row.get('id')
    if pd.notna(raw):
        try:
            return str(int(float(raw)))
        except ValueError:
            pass
    return None


# ── Chunked-retry infrastructure ─────────────────────────────────────────────
# hydroiowa.org's endpoint (IFC river sensors + hydrostations) intermittently
# 500s on a full 5-year request for a subset of sensors -- confirmed by
# re-requesting the same sensor at smaller windows and getting HTTP 200 for
# most of them. A bare non-200/exception was previously indistinguishable
# from a genuine "no data" response (HTTP 200 with an empty/null observed
# payload), so failed requests were silently misreported as empty sensors.
# This retries a failing window at progressively finer granularity --
# water-year, then quarter -- so a server hiccup on one slice doesn't blank
# out five years of otherwise-good data for that sensor.
WATER_YEAR_CHUNKS = [
    (pd.Timestamp('2020-10-01T00:00:00'), pd.Timestamp('2021-09-30T23:59:59')),
    (pd.Timestamp('2021-10-01T00:00:00'), pd.Timestamp('2022-09-30T23:59:59')),
    (pd.Timestamp('2022-10-01T00:00:00'), pd.Timestamp('2023-09-30T23:59:59')),
    (pd.Timestamp('2023-10-01T00:00:00'), pd.Timestamp('2024-09-30T23:59:59')),
    (pd.Timestamp('2024-10-01T00:00:00'), pd.Timestamp('2025-09-30T23:59:59')),
]


def _quarter_chunks(start_dt, end_dt):
    chunks = []
    cur = start_dt
    while cur < end_dt:
        nxt = min(cur + pd.DateOffset(months=3), end_dt)
        chunks.append((cur, nxt))
        cur = nxt + pd.Timedelta(seconds=1)
    return chunks


def _fetch_windowed(fetch_window_fn, start_dt, end_dt, depth=0):
    """
    fetch_window_fn(s, e) -> (success, df). success=False means the request
    itself failed (non-200 / exception) and should be retried at finer
    granularity; success=True with an empty df means the server responded
    normally with genuinely no data for that window (no retry needed).
    Returns (combined_df, had_unrecoverable_gap).
    """
    success, df = fetch_window_fn(start_dt, end_dt)
    if success:
        return df, False

    if depth == 0:
        subchunks = [w for w in WATER_YEAR_CHUNKS if w[0] < end_dt and w[1] > start_dt]
    elif depth == 1:
        subchunks = _quarter_chunks(start_dt, end_dt)
    else:
        return pd.DataFrame(), True  # already at quarter granularity and still failing

    frames, any_gap = [], False
    for s, e in subchunks:
        sub_df, gap = _fetch_windowed(fetch_window_fn, s, e, depth=depth + 1)
        any_gap = any_gap or gap
        if sub_df is not None and not sub_df.empty:
            frames.append(sub_df)
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return combined, any_gap


def _fetch_ifc_river_window(candidate, start_dt, end_dt):
    start_str, end_str = start_dt.strftime('%Y%m%d'), end_dt.strftime('%Y%m%d')
    try:
        res = requests.get(f'https://hydroiowa.org/api/riversensor/{candidate}/data/{start_str}/{end_str}', timeout=REQUEST_TIMEOUT)
    except Exception as e:
        log.debug(f'IFC river {candidate} {start_str}-{end_str} exception: {e}')
        return False, pd.DataFrame()
    if res.status_code != 200:
        return False, pd.DataFrame()
    observed = res.json().get('observed', [])
    if not observed:
        return True, pd.DataFrame()
    df = pd.DataFrame(observed)
    if 'validTime' in df.columns:
        # utc=True avoids pandas' "Mixed timezones detected" error, which a plain
        # format='mixed' parse raises once the range spans a DST transition (every
        # one of these multi-year pulls does; the original per-episode pipeline
        # never hit this because storm windows were only a few days).
        df['validTime'] = pd.to_datetime(df['validTime'], format='mixed', errors='coerce', utc=True)
        df = df.dropna(subset=['validTime'])
        df['validTime'] = df['validTime'].dt.tz_convert(None)
        df = df[(df['validTime'] >= start_dt) & (df['validTime'] <= end_dt)].copy()
    return True, df


def fetch_ifc_sensor_data(sensor_row, start_dt, end_dt):
    candidates = [c for c in [sensor_row.get('foreign_id'), sensor_row.get('id')] if pd.notna(c)]
    for c in candidates:
        df, had_gap = _fetch_windowed(lambda s, e, c=c: _fetch_ifc_river_window(c, s, e), start_dt, end_dt)
        if not df.empty:
            return df, c, had_gap
    return pd.DataFrame(), None, False


def _fetch_ifc_hydro_window(nid, start_dt, end_dt):
    start_str, end_str = start_dt.strftime('%Y%m%d'), end_dt.strftime('%Y%m%d')
    hdrs = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    params = ['rain', 'wind', 'soil', 'well', 'groundwell', 'stage']
    urls = [f"https://hydroiowa.org/api/hydrostation/{nid}/data/{start_str}/{end_str}/?{'&'.join(params)}",
            f'https://hydroiowa.org/api/hydrostation/{nid}/data/{start_str}/{end_str}']
    request_failed = True
    for url in urls:
        try:
            res = requests.get(url, headers=hdrs, timeout=REQUEST_TIMEOUT)
        except Exception as e:
            log.debug(f'Hydrostation {nid} {start_str}-{end_str} exception: {e}')
            continue
        if res.status_code != 200:
            continue
        request_failed = False
        obs = res.json().get('observed', {})
        frames = []
        if isinstance(obs, dict):
            for k, v in obs.items():
                if isinstance(v, list) and v:
                    p = pd.DataFrame(v)
                    p['parameter_type'] = k
                    frames.append(p)
        elif isinstance(obs, list) and obs:
            frames.append(pd.DataFrame(obs))
        if frames:
            df = pd.concat(frames, ignore_index=True)
            if 'validTime' in df.columns:
                df['validTime'] = pd.to_datetime(df['validTime'], format='mixed', errors='coerce', utc=True)
                df = df.dropna(subset=['validTime'])
                df['validTime'] = df['validTime'].dt.tz_convert(None)
                df = df[(df['validTime'] >= start_dt) & (df['validTime'] <= end_dt)]
            return True, df
        return True, pd.DataFrame()  # HTTP 200, genuinely no observations
    return not request_failed, pd.DataFrame()


def fetch_ifc_hydrostation_data(station_row, start_dt, end_dt):
    nid = resolve_hydrostation_id(station_row)
    if not nid:
        return pd.DataFrame(), None, False
    # Unlike the river-sensor endpoint, the hydrostation endpoint was found to
    # silently return HTTP 200 with an EMPTY body for a full 5-year request on
    # some stations (confirmed reproducible across repeated attempts), rather
    # than erroring -- so a full-range attempt can't be trusted to mean
    # "genuinely no data" here. Skip straight to water-year chunking, which
    # was confirmed to reliably retrieve the same station's real data.
    frames, any_gap = [], False
    for s, e in WATER_YEAR_CHUNKS:
        sub_df, gap = _fetch_windowed(lambda ss, ee: _fetch_ifc_hydro_window(nid, ss, ee), s, e, depth=1)
        any_gap = any_gap or gap
        if sub_df is not None and not sub_df.empty:
            frames.append(sub_df)
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return df, nid, any_gap


def fetch_nws_lid_data(nws_id, start_dt, end_dt):
    nws_id = str(nws_id).strip().upper()
    try:
        res = requests.get(f'https://api.water.noaa.gov/v1/gauges/{nws_id}/stageflow/observed',
                            headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}, timeout=REQUEST_TIMEOUT)
        if res.status_code == 200:
            records = []
            for obs in res.json().get('data', []):
                t = pd.to_datetime(obs.get('validTime'))
                if t.tzinfo:
                    t = t.tz_localize(None)
                if start_dt <= t <= end_dt:
                    records.append({'datetime': t, 'parameter': 'Stage / Observed', 'value': obs.get('primary'), 'unit': obs.get('primaryUnit', 'ft')})
            if records:
                return pd.DataFrame(records).sort_values('datetime')
    except Exception as e:
        log.debug(f'NWS LID {nws_id} failed: {e}')
    return pd.DataFrame()


def fetch_usgs_sensor_data(usgs_row, start_dt, end_dt):
    candidates, nws_lids = [], []
    for key in ['foreign_id1', 'foreign_id', 'usgs_site_no_revised', 'usgs_site_no', 'id']:
        val = usgs_row.get(key)
        if pd.notna(val):
            cv = str(val).split('.')[0].strip().upper()
            if cv and cv != 'NAN' and cv not in candidates:
                candidates.append(cv)
                if not cv.isdigit():
                    nws_lids.append(cv)
    if not candidates:
        return pd.DataFrame(), None

    strategies = []
    for sid in candidates:
        if sid.isdigit():
            strategies += [('sites', sid.zfill(8)), ('nwsLids', sid)]
        else:
            strategies += [('nwsLids', sid), ('sites', sid)]

    url = 'https://waterservices.usgs.gov/nwis/iv/'
    hdrs = {'User-Agent': 'Mozilla/5.0 HydrologicalPipeline/2.0'}
    for param_type, qval in strategies:
        for pcd in ['00065', '00060', None]:
            params = {'format': 'json', param_type: qval,
                      'startDT': start_dt.strftime('%Y-%m-%dT%H:%M:%S.000Z'),
                      'endDT': end_dt.strftime('%Y-%m-%dT%H:%M:%S.000Z')}
            if pcd:
                params['parameterCd'] = pcd
            try:
                res = requests.get(url, params=params, headers=hdrs, timeout=REQUEST_TIMEOUT)
                if res.status_code != 200:
                    continue
                records = []
                for ts in res.json().get('value', {}).get('timeSeries', []):
                    pname = ts.get('variable', {}).get('variableName', 'Unknown')
                    unit = ts.get('variable', {}).get('unit', {}).get('unitCode', 'N/A')
                    for v in ts.get('values', [{}])[0].get('value', []):
                        records.append({'datetime': v.get('dateTime'), 'parameter': pname, 'value': v.get('value'), 'unit': unit})
                if records:
                    df = pd.DataFrame(records)
                    other_pcd = {'00060': '00065', '00065': '00060'}.get(pcd)
                    if other_pcd:
                        try:
                            other_params = dict(params, parameterCd=other_pcd)
                            other_res = requests.get(url, params=other_params, headers=hdrs, timeout=REQUEST_TIMEOUT)
                            if other_res.status_code == 200:
                                other_records = []
                                for ts in other_res.json().get('value', {}).get('timeSeries', []):
                                    pname2 = ts.get('variable', {}).get('variableName', 'Unknown')
                                    unit2 = ts.get('variable', {}).get('unit', {}).get('unitCode', 'N/A')
                                    for v in ts.get('values', [{}])[0].get('value', []):
                                        other_records.append({'datetime': v.get('dateTime'), 'parameter': pname2, 'value': v.get('value'), 'unit': unit2})
                                if other_records:
                                    df = pd.concat([df, pd.DataFrame(other_records)], ignore_index=True)
                        except Exception:
                            pass
                    return df, qval
            except Exception as e:
                log.debug(f'USGS {qval}/{pcd} attempt failed: {e}')
                continue
    for lid in nws_lids:
        df_n = fetch_nws_lid_data(lid, start_dt, end_dt)
        if df_n is not None and not df_n.empty:
            return df_n, lid
    return pd.DataFrame(), (candidates[0] if candidates else None)


def _first_present(row, keys):
    # Series.get(key, default) only falls back when the COLUMN is missing,
    # not when the cell value is NaN -- with these lookup CSVs the column
    # always exists, so a naive chained .get() silently returns NaN instead
    # of trying the next candidate. This walks candidates checking pd.notna,
    # same pattern as resolve_hydrostation_id above. Falls back to the row's
    # 'id' (always populated, unique per source table) so two different
    # sensors never collide on the same output filename.
    for k in keys:
        val = row.get(k)
        if pd.notna(val) and str(val).strip() and str(val).strip().lower() != 'nan':
            return val
    return row.get('id', 'unknown')


def get_sensor_code(row, sensor_type):
    if sensor_type == 'ifc_river':
        return str(_first_present(row, ['foreign_id', 'id'])).split('.')[0]
    elif sensor_type == 'ifc_hydrostation':
        return str(_first_present(row, ['foreign_id1', 'id'])).split('.')[0].replace(' ', '_')
    elif sensor_type == 'usgs':
        return str(_first_present(row, ['foreign_id1', 'usgs_site_no_revised', 'foreign_id', 'id'])).split('.')[0].strip().upper()
    return str(row.get('id', 'unknown'))


# ── Wide-format reshaping ────────────────────────────────────────────────────
# All three sources report every parameter on the same timestamp grid for a
# given sensor (confirmed against live data: USGS gage-height/discharge share
# one 15-min IV grid per site; IFC hydrostation rain/soil/wind/well readings
# share one 15-min validTime grid per station -- verified via
# groupby('parameter_type') row counts and sample timestamps lining up).
# Because the grids genuinely align, pivoting to one row per timestamp with a
# column per parameter is safe here. If a sensor type turns out NOT to share
# a common grid, these functions fall back to returning the data unreshaped
# rather than forcing a misleading merge.

def _slugify(name):
    return re.sub(r'[^a-z0-9]+', '_', str(name).lower()).strip('_')


def reshape_ifc_river(df):
    # API already returns one record per validTime with both elevation and
    # discharge as separate fields -- already wide, just standardize the
    # time column name.
    return df.rename(columns={'validTime': 'datetime'})


def reshape_ifc_hydrostation(df):
    if 'parameter_type' not in df.columns or df['parameter_type'].nunique() <= 1:
        return df.rename(columns={'validTime': 'datetime'})
    exclude = {'sensor_code', 'sensor_description', 'validTime', 'parameter_type'}
    merged = None
    for pt, sub in df.groupby('parameter_type'):
        data_cols = [c for c in sub.columns if c not in exclude and sub[c].notna().any()]
        if not data_cols:
            continue
        piece = sub[['validTime'] + data_cols].drop_duplicates(subset='validTime').copy()
        piece = piece.rename(columns={c: f'{pt}_{c}' for c in data_cols})
        merged = piece if merged is None else pd.merge(merged, piece, on='validTime', how='outer')
    if merged is None:
        return df.rename(columns={'validTime': 'datetime'})
    return merged.rename(columns={'validTime': 'datetime'}).sort_values('datetime')


def reshape_usgs(df):
    if 'parameter' not in df.columns or 'value' not in df.columns:
        return df
    df = df.copy()

    def clean_param(p):
        pl = str(p).lower()
        if 'gage height' in pl:
            return 'gage_height_ft'
        if 'discharge' in pl or 'streamflow' in pl or 'stream flow' in pl:
            return 'discharge_cfs'
        return _slugify(p)

    df['param_clean'] = df['parameter'].map(clean_param)
    if df.duplicated(subset=['datetime', 'param_clean']).any():
        df = df.drop_duplicates(subset=['datetime', 'param_clean'], keep='first')
    piv = df.pivot(index='datetime', columns='param_clean', values='value').reset_index()
    piv.columns.name = None
    return piv


# ── Worker ────────────────────────────────────────────────────────────────

def process_sensor(sensor_type, row, out_dir, force):
    code = get_sensor_code(row, sensor_type)
    safe_code = code.replace('/', '_').replace('\\', '_')
    out_path = out_dir / f'{safe_code}.csv'

    if out_path.exists() and out_path.stat().st_size > 0 and not force:
        return sensor_type, code, 'skipped (already exists)', 0

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            had_gap = False
            if sensor_type == 'ifc_river':
                df, resolved_id, had_gap = fetch_ifc_sensor_data(row, START_DT, END_DT)
            elif sensor_type == 'ifc_hydrostation':
                df, resolved_id, had_gap = fetch_ifc_hydrostation_data(row, START_DT, END_DT)
            elif sensor_type == 'usgs':
                df, resolved_id = fetch_usgs_sensor_data(row, START_DT, END_DT)
            else:
                raise ValueError(sensor_type)

            if df is None or df.empty:
                # HTTP 200 with genuinely nothing returned -- not an error.
                # (For ifc_river/ifc_hydrostation, _fetch_windowed already
                # retried failed requests down to quarter granularity before
                # giving up, so this means every window came back empty, not
                # that a request merely failed.)
                status = 'no data' if not had_gap else 'no data (unrecoverable server errors on all windows)'
                return sensor_type, code, status, 0

            if sensor_type == 'ifc_river':
                df = reshape_ifc_river(df)
            elif sensor_type == 'ifc_hydrostation':
                df = reshape_ifc_hydrostation(df)
            elif sensor_type == 'usgs':
                df = reshape_usgs(df)

            df.insert(0, 'sensor_code', code)
            if 'description' in row and pd.notna(row.get('description')):
                df.insert(1, 'sensor_description', row.get('description'))
            df.to_csv(out_path, index=False, quoting=csv.QUOTE_MINIMAL)
            status = 'ok' if not had_gap else 'ok (partial -- some windows unrecoverable, see log)'
            return sensor_type, code, status, len(df)
        except Exception as e:
            last_err = e
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)
    return sensor_type, code, f'FAILED: {last_err}', 0


def load_rows(csv_path):
    df = pd.read_csv(csv_path)
    return [row for _, row in df.iterrows()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true', help='Re-download even if output CSV already exists')
    parser.add_argument('--only', choices=['ifc_river', 'ifc_hydrostation', 'usgs'], help='Restrict to one sensor type')
    args = parser.parse_args()

    jobs = []
    if not args.only or args.only == 'ifc_river':
        for row in load_rows(IFC_RIVER_FILE):
            jobs.append(('ifc_river', row, OUT_DIR / 'ifc_river_sensors'))
    if not args.only or args.only == 'ifc_hydrostation':
        for row in load_rows(IFC_HYDRO_FILE):
            jobs.append(('ifc_hydrostation', row, OUT_DIR / 'ifc_hydrostations'))
    if not args.only or args.only == 'usgs':
        for row in load_rows(USGS_FILE):
            jobs.append(('usgs', row, OUT_DIR / 'usgs_sensors'))

    log.info(f'Water year range: {START_DT} -> {END_DT}')
    log.info(f'Total sensors queued: {len(jobs)}')

    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_sensor, stype, row, out_dir, args.force): (stype, row) for stype, row, out_dir in jobs}
        done_count = 0
        for fut in as_completed(futures):
            stype, code, status, n = fut.result()
            done_count += 1
            log.info(f'[{done_count}/{len(jobs)}] {stype:18s} {code:15s} {status}  (rows={n})')
            results.append((stype, code, status, n))

    summary_path = OUT_DIR / 'logs' / f'summary_{datetime.now():%Y%m%d_%H%M%S}.csv'
    pd.DataFrame(results, columns=['sensor_type', 'sensor_code', 'status', 'rows']).to_csv(summary_path, index=False)

    ok = sum(1 for r in results if r[2] == 'ok')
    nodata = sum(1 for r in results if r[2] == 'no data')
    skipped = sum(1 for r in results if r[2].startswith('skipped'))
    failed = sum(1 for r in results if r[2].startswith('FAILED'))
    log.info('=' * 60)
    log.info(f'DONE. ok={ok} no_data={nodata} skipped={skipped} failed={failed} total={len(results)}')
    log.info(f'Summary written to {summary_path}')


if __name__ == '__main__':
    main()
