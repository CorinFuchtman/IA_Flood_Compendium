# Flood Impact Records: Schema and Method

This module adds a geo-referenced flood impact layer to the Iowa Flood
Compendium. It follows two published models: GroundSource (Google Research,
news reports turned into flood observations with large language models) and
HANZE (ESSD, a curated European flood impact database with explicit inclusion
rules and per-record provenance).

One row = one impact observation of one type, at one place, tied to one
compendium episode. Records are auditable: every row keeps the verbatim text
that produced it and a link back to its source.

## Files

| File | Content |
|---|---|
| `data/impacts_noaa.csv` / `.geojson` | Impacts mined from NOAA Storm Events narratives (rule based, reproducible, run `episode_impacts_extract.py`) |
| `data/impacts_news.csv` / `.geojson` | Impacts mined from local news coverage with large language models (see `news_extraction_prompt.md`) |
| `data/sources_news.csv` | Registry of every news article consulted (URL, outlet, publication date, episode) |
| `data/episode_impact_index.csv` | Per-episode rollup used by the website filter (counts by source and type, has_crowdsource flag) |

## Fields

| Field | Type | Definition |
|---|---|---|
| `impact_id` | UUID | Stable unique identifier |
| `episode_id` | string | Compendium `NEW_EPISODE_ID` (e.g. `191899_0`); assigned to news records by date window + county overlap |
| `event_id` | int or blank | NOAA `EVENT_ID` when the record comes from a specific event |
| `source_type` | enum | `noaa_narrative`, `local_news`, `agency_web` (NWS event summary pages) (future: `nws_lsr`, `globe_observer`, `social_media`) |
| `source_ref` | string | NOAA event reference or article URL |
| `pub_date` | date | Publication date of the source (news only) |
| `start_date`, `end_date` | date | Days the impact was observed. News dates are anchored to the publication date and never exceed it |
| `date_precision` | enum | `day`, `range`, `month` |
| `location_name` | string | Canonical place: "City, County, IA" or "County, IA" |
| `location_raw` | string | Verbatim location text (street or road names as written) |
| `lat`, `lon` | float | WGS84 point |
| `geom_type` | enum | `event_point` (NOAA event coordinates), `place_centroid` (geocoded city or town), `county_centroid` |
| `admin_fips` | string | 5-digit county FIPS; every record resolves at least to a county |
| `huc8` | string | HUC8 watershed when known |
| `impact_type` | enum | Controlled vocabulary below |
| `quantity`, `quantity_unit` | number, string | e.g. 40 homes, 2 rescues; blank when the source gives none |
| `severity` | 0-3 | 0 overbank only, 1 minor, 2 moderate (closures, structures, rescues), 3 major (fatalities, evacuations, destroyed structures) |
| `confidence` | A-C | A explicit and precisely located; B usable with degraded precision (county level or date within 1 day); C single uncorroborated source or inferred fields |
| `flood_type` | enum | `flash`, `river`, `pluvial`, `unknown` |
| `mention_count` | int | Number of matching sentences (NOAA records) |
| `text_span` | string | Verbatim sentence supporting the record |
| `notes` | string | Discrepancies, co-occurring hazards, proxies used |

## Controlled vocabulary: impact_type

`road_flooded`, `road_closed`, `bridge_damaged`, `rescue`, `evacuation`,
`home_flooded`, `business_flooded`, `agriculture`, `infrastructure`,
`injury`, `fatality`, `river_overbank`, `other`

## Inclusion rules

1. Only explicit impact statements. Warnings, forecasts, preparedness stories,
   and "no damage" statements are excluded.
2. Minimum metadata: a date resolved at least to a day range, a county, and an
   impact_type.
3. Dates extracted from news are anchored to the article publication date;
   vague references ("recently") are rejected, never guessed.
4. One row per (location, impact_type). Consecutive-day reports of the same
   impact at the same location merge into one row with a date range.
5. Every record carries its `text_span` and source reference.

## Known biases (state these when using the data)

* Reporting probability rises with severity: minor street flooding is
  underreported in both NOAA narratives and news.
* Media density varies by county; metro flooding is covered more than rural.
* NOAA narrative impacts are located at the official event point, not at the
  named street; treat `geom_type = event_point` records as within-county
  locations unless `location_raw` names a road you can match.

## Intended use

The impact layer supports the compendium's purpose: flood modeling experiments
along the forecasting chain (rainfall input, simulated flows, street-scale
inundation maps, impacts). Typical uses: selecting well-observed episodes for
case studies, validating simulated inundation against reported flooded and
closed streets, and linking model output to reported impacts to estimate flood
severity categories at ungauged locations.
