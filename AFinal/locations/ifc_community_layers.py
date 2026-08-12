"""
Caches, once, how many IFC inundation map layers (stage maps + flood-frequency
maps) each known IFIS community has available -- so per-episode matching
never needs a live call.

ifis_community_ids.csv (already present in this repo) is the {community
name: ifis_id} lookup pipeline (11).ipynb's discover_ifis_communities() built
by scanning ~700 candidate IDs, a one-time few-minute scan. There are only 37
verified communities in it. get_ifis_kmz_items(ifis_id) (carried over
unchanged from pipeline (11)) hits
https://ifis.iowafloodcenter.org/ifis/app/inc/inc_get_inundata.php?id=<id>
and returns that community's available (kmz_url, extent_name) layers --
~0.2s per community, so caching all 37 up front is a few seconds total,
versus re-fetching per episode for however many episodes touch that
community.
"""
import ast
from pathlib import Path

import pandas as pd
import requests

BASE_DIR = Path(__file__).parent
COMMUNITY_LOOKUP_FILE = BASE_DIR / 'ifis_community_ids.csv'
CACHE_PATH = BASE_DIR / 'ifc_community_layers.csv'

IFC_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
    'Referer': 'https://ifis.iowafloodcenter.org/ifis/app/?snap_view=fmap',
}


def load_ifis_community_lookup():
    s = pd.read_csv(COMMUNITY_LOOKUP_FILE, index_col=0).iloc[:, 0]
    return s.to_dict()


def get_ifis_kmz_items(ifis_id):
    url = f'https://ifis.iowafloodcenter.org/ifis/app/inc/inc_get_inundata.php?id={ifis_id}'
    try:
        res = requests.get(url, headers=IFC_HEADERS, timeout=10)
        res.raise_for_status()
        data = ast.literal_eval(res.text)
        items = []
        if len(data) > 2 and data[2] and data[2][0]:
            for mi in data[2][0][0]:
                items.append((mi[0], f'stage_{mi[1]}ft'))
        if len(data) > 2 and len(data[2]) > 1 and data[2][1]:
            for mi in data[2][1][0]:
                items.append((mi[0], f'flood_{mi[3]}yr'))
        return items
    except Exception as e:
        print(f'  [-] IFIS ID {ifis_id}: {e}')
        return []


def fetch_and_cache_community_layers(force=False):
    if CACHE_PATH.exists() and not force:
        return CACHE_PATH

    lookup = load_ifis_community_lookup()
    print(f'Fetching inundation layer lists for {len(lookup)} IFIS communities...')
    rows = []
    for name, ifis_id in lookup.items():
        items = get_ifis_kmz_items(ifis_id)
        for kmz_url, extent_name in items:
            rows.append({'community_name': name, 'ifis_id': ifis_id, 'kmz_url': kmz_url, 'extent_name': extent_name})
        if not items:
            rows.append({'community_name': name, 'ifis_id': ifis_id, 'kmz_url': None, 'extent_name': None})

    df = pd.DataFrame(rows)
    df.to_csv(CACHE_PATH, index=False, encoding='utf-8')
    n_communities_with_layers = df.dropna(subset=['kmz_url'])['community_name'].nunique()
    print(f'[OK] wrote {CACHE_PATH} ({len(df)} layer rows, {n_communities_with_layers}/{len(lookup)} communities have >=1 layer)')
    return CACHE_PATH


def load_community_layers():
    if not CACHE_PATH.exists():
        fetch_and_cache_community_layers()
    return pd.read_csv(CACHE_PATH, encoding='utf-8')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    fetch_and_cache_community_layers(force=args.force)
