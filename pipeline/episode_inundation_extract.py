"""
Stages IFC inundation map availability per episode: which IFIS communities
match the episode (by county name / event narrative text), and how many
inundation layers (stage maps + flood-frequency maps) those communities have,
per ifc_community_layers.py's pre-fetched cache.

match_communities_for_episode is carried over unchanged from pipeline
(11).ipynb -- word-boundary matches of known community names against each
row's CZ_NAME + EVENT_NARRATIVE, widened by COUNTY_TO_CITIES for counties
with multiple mapped communities (e.g. Black Hawk -> Waterloo + Cedar Falls).

Unlike sensors/HWMs/FEMA, there's no "granularity" axis here -- inundation
maps are matched to specific named communities via text, not HUC8/county
membership, so this caches at the episode level like FEMA/HWMs.

This stage is entirely local (no live network call) once
ifc_community_layers.py's cache exists, since community matching is text-only
and layer availability is already cached per community.
"""
import re
from pathlib import Path

import pandas as pd

from episode_sensor_extract import EPISODES_DIR, get_episode
from ifc_community_layers import load_ifis_community_lookup, load_community_layers

COUNTY_TO_CITIES = {
    'BLACK HAWK': ['WATERLOO', 'CEDAR FALLS'],
    'JOHNSON':    ['IOWA CITY'],
    'LINN':       ['CEDAR RAPIDS'],
    'POLK':       ['DES MOINES'],
}

NO_MATCH_MARKER = '__no_communities_matched__'


def match_communities_for_episode(episode_rows, community_lookup, county_to_cities=None):
    county_to_cities = county_to_cities or {}
    candidate_names = set(community_lookup.keys())
    matched_ids = set()
    matched_names = set()

    for _, row in episode_rows.iterrows():
        cz = str(row.get('CZ_NAME', '')).upper().strip()
        narr = str(row.get('EVENT_NARRATIVE', '')).upper()
        text = f'{cz} {narr}'

        for name in candidate_names:
            if re.search(rf'\b{re.escape(name)}\b', text):
                matched_ids.add(community_lookup[name])
                matched_names.add(name)

        county_key = cz.replace(' CO.', '').replace(' COUNTY', '').strip()
        for cities in (county_to_cities.get(county_key, []), county_to_cities.get(cz, [])):
            for city in cities:
                if city in community_lookup:
                    matched_ids.add(community_lookup[city])
                    matched_names.add(city)

    return matched_ids, matched_names


def extract_episode_inundation(episode_id, events_df=None, force=False):
    """
    Returns a DataFrame of matched inundation layers (community_name,
    ifis_id, kmz_url, extent_name) -- empty if no community matched, or a
    matched community has no layers. Caches to
    episodes/<id>/inundation_layers.csv.
    """
    safe_id = str(episode_id).replace('/', '_')
    out_dir = EPISODES_DIR / safe_id
    out_path = out_dir / 'inundation_layers.csv'

    if out_path.exists() and not force:
        first_line = out_path.read_text(encoding='utf-8').splitlines()[0] if out_path.stat().st_size > 0 else ''
        if first_line.strip() == NO_MATCH_MARKER:
            return pd.DataFrame()
        return pd.read_csv(out_path, encoding='utf-8')

    episode_rows = get_episode(episode_id, events_df)
    community_lookup = load_ifis_community_lookup()
    matched_ids, matched_names = match_communities_for_episode(episode_rows, community_lookup, COUNTY_TO_CITIES)

    out_dir.mkdir(parents=True, exist_ok=True)
    if not matched_names:
        out_path.write_text(NO_MATCH_MARKER + '\n', encoding='utf-8')
        return pd.DataFrame()

    all_layers = load_community_layers()
    matched_layers = all_layers[all_layers['community_name'].isin(matched_names)].dropna(subset=['kmz_url'])

    if matched_layers.empty:
        out_path.write_text(NO_MATCH_MARKER + '\n', encoding='utf-8')
        return pd.DataFrame()

    matched_layers.to_csv(out_path, index=False, encoding='utf-8')
    return matched_layers


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('episode_id', nargs='?', default='191899_0')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()

    df = extract_episode_inundation(args.episode_id, force=args.force)
    if df.empty:
        print(f'Episode {args.episode_id}: no inundation layers matched.')
    else:
        print(f"Episode {args.episode_id}: {len(df)} layer(s) across {df['community_name'].nunique()} communities.")
        print(df[['community_name', 'extent_name']].to_string(index=False))
