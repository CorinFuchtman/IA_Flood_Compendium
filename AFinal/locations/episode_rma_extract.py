"""
USDA RMA Summary of Business -- Cause of Loss extraction. Replaces NOAA
Storm Events' DAMAGE_CROPS field (a rough estimate NOAA's own storm
surveyors attach to an event) with the actual indemnities paid out under
federal crop insurance for flood-related causes, per RMA's own
peril-of-loss classification -- a more authoritative number, but at
different granularity than NOAA's.

Source: https://www.rma.usda.gov/tools-reports/summary-of-business/cause-loss
Record layout: https://pubfs-rma.fpac.usda.gov/pub/Web_Data_Files/Summary_of_Business/cause_of_loss/COL_Summary_of_Business_with_Month_All_Years.pdf

Downloaded once as colsom_<year>.zip for 2020-2025 (2020 included so
episodes early in water-year-2021, i.e. Oct-Dec 2020, wouldn't silently be
missing coverage even though none of the 135 episodes in this dataset
currently start before May 2021) and cached filtered to Iowa
(state_abbrev == 'IA') in iowa_cause_of_loss_2020_2025.csv -- the raw
national files run ~30-45MB/year and are almost entirely non-Iowa rows, so
re-parsing them in full on every run would be wasteful.

Granularity mismatch with NOAA episodes, and how it's handled here:
RMA reports one row per county x commodity x insurance-plan x cause x
MONTH -- there's no date-of-loss finer than month, and no link back to a
specific NOAA episode. An episode is matched against every RMA row whose
county FIPS is one the episode touches (same FIPS_5 NOAA's episodes already
carry) AND whose (year_of_loss, month_of_loss) falls within the episode's
[BEGIN_DT, END_DT] window, filtered to FLOOD_CAUSES, summed.

Because RMA's number is a whole-county-month total rather than a per-storm
total, two NOAA episodes landing in the same county in the same month will
report the SAME total -- there is no way to split an insurance claim's
cause between two different storms that both happened that month. This is
intentional (the alternative is fabricating a split with no basis in the
data), not a bug -- build_wizard_data.py surfaces this as a caveat in the
wizard UI.
"""
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).parent
RMA_DIR = BASE_DIR / 'rma_cause_of_loss'
RAW_DIR = RMA_DIR / 'raw'
CACHE_PATH = RMA_DIR / 'iowa_cause_of_loss_2020_2025.csv'
YEARS = [2020, 2021, 2022, 2023, 2024, 2025]

RAW_COLUMNS = [
    'commodity_year', 'state_code', 'state_abbrev', 'county_code', 'county_name',
    'commodity_code', 'commodity_name', 'insurance_plan_code', 'insurance_plan_abbrev',
    'coverage_category', 'stage_code', 'cause_of_loss_code', 'cause_of_loss_desc',
    'month_of_loss', 'month_of_loss_name', 'year_of_loss', 'policies_earning_premium',
    'policies_indemnified', 'net_planted_qty', 'net_endorsed_acres', 'liability',
    'total_premium', 'producer_paid_premium', 'subsidy', 'state_private_subsidy',
    'additional_subsidy', 'efa_premium_discount', 'net_determined_qty',
    'indemnity_amount', 'loss_ratio',
]
KEEP_COLUMNS = ['county_code', 'county_name', 'commodity_name', 'cause_of_loss_desc', 'month_of_loss', 'year_of_loss', 'indemnity_amount']

# Iowa's full cause-of-loss list also includes Drought, Hail, Wind/Excess
# Wind, Frost, Freeze, Cold Wet Weather, Insects, Plant Disease, etc. -- these
# two are the ones that correspond to flooding.
FLOOD_CAUSES = ['Flood', 'Excess Moisture/Precipitation/Rain']


def build_iowa_cache(force=False):
    if CACHE_PATH.exists() and not force:
        return
    frames = []
    for year in YEARS:
        raw_path = RAW_DIR / f'colsom_{year}.txt'
        if not raw_path.exists():
            raise FileNotFoundError(
                f'{raw_path} missing -- download colsom_{year}.zip from '
                f'https://pubfs-rma.fpac.usda.gov/pub/Web_Data_Files/Summary_of_Business/cause_of_loss/ '
                f'and unzip it into {RAW_DIR}.'
            )
        df = pd.read_csv(raw_path, sep='|', header=None, names=RAW_COLUMNS, dtype=str)
        frames.append(df[df['state_abbrev'].str.strip() == 'IA'][KEEP_COLUMNS].copy())

    combined = pd.concat(frames, ignore_index=True)
    combined['county_code'] = combined['county_code'].str.strip().str.zfill(3)
    combined['county_fips'] = '19' + combined['county_code']
    combined['county_name'] = combined['county_name'].str.strip()
    combined['cause_of_loss_desc'] = combined['cause_of_loss_desc'].str.strip()
    combined['commodity_name'] = combined['commodity_name'].str.strip()
    combined['indemnity_amount'] = pd.to_numeric(combined['indemnity_amount'], errors='coerce').fillna(0.0)
    combined['month_of_loss'] = pd.to_numeric(combined['month_of_loss'], errors='coerce').astype('Int64')
    combined['year_of_loss'] = pd.to_numeric(combined['year_of_loss'], errors='coerce').astype('Int64')

    RMA_DIR.mkdir(parents=True, exist_ok=True)
    combined.to_csv(CACHE_PATH, index=False)


_cache_df = None


def load_iowa_cause_of_loss():
    global _cache_df
    if _cache_df is None:
        build_iowa_cache()
        df = pd.read_csv(CACHE_PATH, dtype={'county_fips': str})
        df['year_month'] = df['year_of_loss'] * 100 + df['month_of_loss']
        _cache_df = df
    return _cache_df


def months_spanned(start_dt, end_dt):
    """List of (year, month) tuples the [start_dt, end_dt] window touches --
    almost always a single month, occasionally two for an episode that
    straddles a month boundary."""
    months = []
    cur = start_dt.replace(day=1)
    end_marker = end_dt.replace(day=1)
    while cur <= end_marker:
        months.append((cur.year, cur.month))
        cur = cur + pd.DateOffset(months=1)
    return months


def extract_episode_crop_loss(episode_id, events_df=None):
    """
    Returns (indemnity_usd, detail_df): the flood-related RMA indemnity
    total across every county this episode touches, for every (year, month)
    its [BEGIN_DT, END_DT] window spans, plus the matched detail rows
    (county, commodity, cause, month, indemnity) for drill-down/CSV export.
    """
    from episode_sensor_extract import get_episode  # noqa: E402 -- avoids a circular import at module load

    episode_rows = get_episode(episode_id, events_df)
    fips_list = episode_rows['FIPS_5'].unique().tolist()
    start_dt = episode_rows['BEGIN_DT'].min()
    end_dt = episode_rows['END_DT'].max()
    year_months = {y * 100 + m for y, m in months_spanned(start_dt, end_dt)}

    df = load_iowa_cause_of_loss()
    matched = df[
        df['county_fips'].isin(fips_list)
        & df['cause_of_loss_desc'].isin(FLOOD_CAUSES)
        & df['year_month'].isin(year_months)
    ]
    return float(matched['indemnity_amount'].sum()), matched


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('episode_id', nargs='?', default='191899_0')
    args = parser.parse_args()

    total, detail = extract_episode_crop_loss(args.episode_id)
    print(f'Episode {args.episode_id}: ${total:,.0f} flood-related RMA crop indemnity')
    if not detail.empty:
        print(detail.groupby(['county_name', 'cause_of_loss_desc'])['indemnity_amount'].sum().to_string())
