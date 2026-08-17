# Iowa Flood Compendium

A statewide, episode-based collection of flood observations for Iowa,
2021-2025, built to make flood modeling experiments easy: pick an episode,
download everything needed to force, calibrate, and evaluate a model, and
check simulated flooding against what people actually reported.

Interactive site: `docs/index.html` (GitHub Pages). Filter 135 flood episodes
(485 NOAA Flood and Flash Flood events) by sensor coverage, high water marks,
FEMA claims, rainfall, social vulnerability, damages, and flood impact
reports, then download per-episode data bundles.

## What is in the compendium

| Layer | Source | Where |
|---|---|---|
| Flood episodes and events, 2021-2025 | NOAA Storm Events Database (Iowa, Flood + Flash Flood), grouped into 135 episodes with county and HUC8 joins | `AFinal/storm_events_NOAA/`, `AFinal/locations/noaa_21-25_with_huc_08.csv` |
| Stream sensors | Iowa Flood Center bridge sensors and hydrostations, USGS gauges (locations + water year 2021-2025 records) | `AFinal/locations/sensor_csv_huc08/`, synced archive |
| Rainfall | MRMS hourly grids cropped to Iowa per episode | `mrms_hour_cache.py`, `episode_precip_extract.py` |
| High water marks | USGS STN archive filtered per episode | `episode_hwm_extract.py` |
| FEMA claims | OpenFEMA NFIP claims per county and date window | `episode_fema_extract.py` |
| Inundation maps | Iowa Flood Center community inundation map layers | `episode_inundation_extract.py`, `ifc_community_layers.csv` |
| Social vulnerability | CDC/ATSDR SVI 2022, county level | `svi_iowa_county_2022.csv` |
| Flood impact reports | NOAA narratives + local news mined with large language models; geo-referenced records of flooded and closed streets, rescues, evacuations, flooded homes | `AFinal/impacts/` |

## The impacts layer (new)

`AFinal/impacts/` turns unstructured text into structured, auditable impact
records, following GroundSource (news to flood data with large language
models) and HANZE (curated flood impact database) practice. See
`AFinal/impacts/impact_schema.md` for the full schema, controlled vocabulary,
inclusion rules, and known biases.

* `data/impacts_noaa.csv` and `.geojson`: 418 records mined from NOAA event
  narratives with documented regex rules (98 of 135 episodes). Reproducible:
  `python AFinal/impacts/episode_impacts_extract.py`.
* `data/impacts_news.csv` and `.geojson`: records extracted from local news
  and agency event summaries with large language models, using the published
  prompt in `news_extraction_prompt.md`. Every article consulted is logged in
  `data/sources_news.csv`, including rejected and unreachable ones.
* `data/episode_impact_index.csv`: one row per episode with counts by source
  and impact type, max severity, and a `has_crowdsource` flag. This is the
  quick filter table: episodes with crowdsourced impact data are the best
  candidates for street-scale model evaluation case studies.
* `augment_site.py` embeds the records in the website and adds the "Flood
  impact reports" panel: a map layer colored by severity plus two filters
  (only episodes with crowdsourced reports, minimum impact records).

Episodes without crowdsourced data stay in the compendium. The impact layer
is a filter and an evaluation target, not an inclusion criterion.

## Reproducing the pipeline

Python 3.10+. Install dependencies: `pip install -r requirements.txt`

Run order (from `AFinal/locations/` unless noted):

1. `bulk_sensor_download.py` then `enrich_sensors_with_county.py` (sensor
   locations and records; needs `OWNCLOUD_SHARE_TOKEN` in the environment)
2. `hwm_archive.py`, `svi_data.py`, `ifc_community_layers.py`,
   `choropleth_data.py` (support layers)
3. `mrms_hour_cache.py` (rainfall grids; large download)
4. `../impacts/episode_impacts_extract.py`, `../impacts/build_news_impacts.py`,
   `../impacts/build_impact_index.py` (impact records)
5. `build_wizard_data.py` then `build_embedded_episode_data.py` then
   `build_wizard_html.py` (site build)
6. `../impacts/augment_site.py` (adds the impacts layer to the built site and
   to `docs/index.html`)
7. Copy `choropleth/wizard.html` to `docs/index.html` to publish.

## Data sources and attribution

NOAA Storm Events Database (NCEI); Iowa Flood Center / IIHR (bridge sensors,
hydrostations, community inundation maps, IFIS); USGS (NWIS gauges, STN high
water marks); FEMA (OpenFEMA NFIP claims); NOAA MRMS (rainfall); CDC/ATSDR
(Social Vulnerability Index); US Census TIGERweb and USGS WBD (boundaries);
local news outlets credited per record in `sources_news.csv`. All third-party
data remain under their providers' terms.

## Citing

See `CITATION.cff`. A data-paper style reference for the impact layer method:
GroundSource (Google Research, 2026) and HANZE v2.1 (Paprotny et al., ESSD,
2024).

## Team

Iowa Flood Center / IIHR Hydroscience and Engineering, University of Iowa.
Contact: Mohamed Abdelkader (mohamed-abdelkader@uiowa.edu).
