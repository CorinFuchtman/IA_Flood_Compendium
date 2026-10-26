"""
Stages FEMA NFIP claims per episode, same pattern as episode_sensor_extract.py's
match/extract stages: the first call for an episode hits OpenFEMA's live API
and writes the result to episodes/<id>/fema_claims.csv (or an empty marker
file if there were no matching claims); every later call for that episode is
a plain local CSV read, no network call.

FEMA claims are inherently county-scoped (the API only filters by
countyCode), independent of whether sensor matching is done at HUC8 or
county granularity -- so this caches at the episode level, not nested under
a granularity folder like the sensor stages are.

query_fema_claims_multi_fips is carried over unchanged from pipeline
(11).ipynb's FEMA helper cell.
"""
from datetime import timedelta
from pathlib import Path

import pandas as pd
import requests

from episode_sensor_extract import EPISODES_DIR, get_episode  # noqa: E402

FEMA_NO_CLAIMS_MARKER = '__no_claims__'


def query_fema_claims_multi_fips(fips_list, start_date, end_date, date_buffer_days=14):
    base_url = 'https://www.fema.gov/api/open/v2/FimaNfipClaims'
    search_start = (start_date - timedelta(days=2)).strftime('%Y-%m-%d')
    search_end = (end_date + timedelta(days=date_buffer_days)).strftime('%Y-%m-%d')
    fips_cond = ' or '.join([f"countyCode eq '{f}'" for f in fips_list])
    fema_filter = (f'({fips_cond}) and '
                   f'dateOfLoss ge {search_start}T00:00:00.000Z and '
                   f'dateOfLoss le {search_end}T23:59:59.000Z')
    try:
        r = requests.get(base_url, params={'$filter': fema_filter, '$top': 10000}, timeout=30)
        r.raise_for_status()
        return pd.DataFrame(r.json().get('FimaNfipClaims', []))
    except Exception as e:
        print(f'Error querying OpenFEMA API: {e}')
        return pd.DataFrame()


def extract_episode_fema_claims(episode_id, date_buffer_days=14, events_df=None, force=False):
    """
    Stage: FEMA claims for one episode. Returns a DataFrame (empty if no
    claims matched). Caches to episodes/<id>/fema_claims.csv; an episode with
    genuinely zero claims is marked with a one-line sentinel file so a
    "no data" result doesn't get silently re-queried forever.
    """
    safe_id = str(episode_id).replace('/', '_')
    out_dir = EPISODES_DIR / safe_id
    out_path = out_dir / 'fema_claims.csv'

    if out_path.exists() and not force:
        first_line = out_path.read_text(encoding='utf-8').splitlines()[0] if out_path.stat().st_size > 0 else ''
        if first_line.strip() == FEMA_NO_CLAIMS_MARKER:
            return pd.DataFrame()
        return pd.read_csv(out_path, encoding='utf-8')

    episode_rows = get_episode(episode_id, events_df)
    fips_list = episode_rows['FIPS_5'].unique().tolist()
    start_date = episode_rows['BEGIN_DT'].min()
    end_date = episode_rows['END_DT'].max()

    claims_df = query_fema_claims_multi_fips(fips_list, start_date, end_date, date_buffer_days=date_buffer_days)

    out_dir.mkdir(parents=True, exist_ok=True)
    if claims_df.empty:
        out_path.write_text(FEMA_NO_CLAIMS_MARKER + '\n', encoding='utf-8')
    else:
        claims_df.to_csv(out_path, index=False, encoding='utf-8')
    return claims_df


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('episode_id', nargs='?', default='191899_0')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()

    df = extract_episode_fema_claims(args.episode_id, force=args.force)
    if df.empty:
        print(f'Episode {args.episode_id}: no FEMA claims found.')
    else:
        print(f'Episode {args.episode_id}: {len(df)} FEMA claim(s).')
        cols = [c for c in ['dateOfLoss', 'countyCode', 'amountPaidOnBuildingClaim', 'amountPaidOnContentsClaim'] if c in df.columns]
        print(df[cols].head(10).to_string(index=False))
