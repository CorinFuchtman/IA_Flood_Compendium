# Iowa Flood Compendium

A statewide, episode-based collection of flood observations for Iowa,
2021-2025, built to make flood modeling experiments easy: pick an episode,
download everything needed to force, calibrate, and evaluate a model, and
check simulated flooding against what people actually reported.

Interactive site: https://corinfuchtman.github.io/IA_Flood_Compendium/
(built to `docs/index.html`). Filter 135 flood episodes
(485 NOAA Flood and Flash Flood events) by sensor coverage, high water marks,
FEMA claims, rainfall, social vulnerability, damages, and flood impact
reports, then download per-episode data bundles.

## What is in the compendium

| Layer | Source | Code | Data |
|---|---|---|---|
| Flood episodes and events, 2021-2025 | NOAA Storm Events Database (Iowa, Flood + Flash Flood), grouped into 135 episodes with county and HUC8 joins | `notebooks/noaa_storm_events.ipynb` | `data/noaa/` |
| Stream sensors | Iowa Flood Center bridge sensors and hydrostations, USGS gauges (locations + water year 2021-2025 records) | `bulk_sensor_download.py`, `episode_sensor_extract.py` | `data/sensors/`, ownCloud archive |
| Rainfall | NOAA MRMS hourly grids cropped to Iowa per episode | `mrms_hour_cache.py`, `episode_precip_extract.py` | `cache/` (rebuilt locally) |
| Rainfall recurrence | NOAA/NSSL FLASH QPE average recurrence interval (ARI) per episode | `flash_ari_hour_cache.py`, `episode_flash_extract.py` | `cache/` (rebuilt locally) |
| High water marks | USGS STN archive filtered per episode | `hwm_archive.py`, `episode_hwm_extract.py` | `cache/` (rebuilt locally) |
| Flood insurance claims | FEMA OpenFEMA NFIP claims per county and date window | `episode_fema_extract.py` | `cache/` (rebuilt locally) |
| Crop insurance payouts | USDA Risk Management Agency (RMA) Cause of Loss, flood-related indemnities | `episode_rma_extract.py` | `data/support/rma_cause_of_loss/` |
| Inundation maps | Iowa Flood Center community inundation map layers | `ifc_community_layers.py`, `episode_inundation_extract.py` | `data/support/` |
| Social vulnerability | CDC/ATSDR SVI 2022, county level | `svi_data.py` | `data/support/` |
| Flood impact reports | NOAA narratives + local news mined with large language models; geo-referenced records of flooded and closed streets, rescues, evacuations, flooded homes | `pipeline/impacts/` | `data/impacts/` |
| Satellite overpasses | Sentinel-2, Sentinel-1 radar and Landsat passes within 48 hours of each episode, with acquisition time, coverage and cloud; Planet optional with a key | `pipeline/imagery/` | `data/imagery/` |

Code paths without a folder are in `pipeline/`.

## Repository layout

```
pipeline/          Python scripts that build the dataset and the website
  impacts/         flood impact records (NOAA narratives + news)
  imagery/         satellite overpass layer
data/
  noaa/            NOAA Storm Events tables (the episode list everything joins to)
  sensors/         raw sensor location lists, and the same lists joined to HUC8 and county
  support/         SVI, IFC inundation layer counts, IFIS community ids, RMA crop loss
  impacts/         impact records, data dictionary, schema, audit and source logs
  imagery/         satellite overpass tables
  site_build/      intermediate files the website is built from
notebooks/         notebooks that built the NOAA and sensor tables (outputs cleared)
archive/           superseded notebooks and scripts, earlier NOAA and sensor tables,
                   and sensor shapefiles; nothing in pipeline/ reads from here
docs/              the published website (GitHub Pages serves docs/index.html)
cache/             not in git: large local downloads the scripts rebuild
```

## The impacts layer

`pipeline/impacts/` turns unstructured text into structured, auditable impact
records, following GroundSource (news to flood data with large language
models) and HANZE (curated flood impact database) practice. See
`data/impacts/impact_schema.md` for the full schema, controlled vocabulary,
inclusion rules, and known biases. Current coverage: 562 records (417 NOAA
narrative + 145 news/agency), of which 560 are matched to an episode and shown
on the site; 104 of 135 episodes with impact records, 33 episodes with
crowdsourced reports, 135 sources logged.

Files below are in `data/impacts/`; scripts are in `pipeline/impacts/`.

* `impacts_noaa.csv` and `.geojson`: records mined from NOAA event
  narratives with documented regex rules. Reproducible:
  `python pipeline/impacts/episode_impacts_extract.py`.
* `impacts_news.csv` and `.geojson`: records extracted from local news
  and agency event summaries with large language models, using the published
  prompt in `news_extraction_prompt.md`. Every article consulted is logged in
  `sources_news.csv`, including rejected and unreachable ones, and every
  per-episode source hunt is logged in `search_log.csv` so "searched,
  nothing found" episodes are separated from "not yet searched".
* `impacts_master.csv` and `.geojson` (+ `impacts_data_dictionary.csv`):
  the database-ready union of both sources with episode metadata joined.
  Load it directly, e.g. DuckDB `SELECT * FROM
  read_csv_auto('impacts_master.csv')` or SQLite `.import --csv
  impacts_master.csv impacts`.
* `episode_impact_index.csv`: one row per episode with counts by source
  and impact type, max severity, and a `has_crowdsource` flag. This is the
  quick filter table: episodes with crowdsourced impact data are the best
  candidates for street-scale model evaluation case studies.
* `episode_source_leads.csv`: the student worksheet - one row per
  episode with prebuilt search links and a status column (todo / searched /
  covered) so coverage work is a checklist, not archaeology.
* `validate_impacts.py`: schema validation gate; `quality_audit.csv`:
  seeded audit rounds (currently 97 percent Accurate or Approximate).

The site itself loads these records natively: `pipeline/build_wizard_data.py`
reads `impacts_noaa.csv` and `impacts_news.csv` directly (no separate
"augment" pass needed), and every episode in `wizard_data.json` carries
`n_impacts`/`n_impacts_crowd`/`has_crowdsource`/`impact_points`.
`build_wizard_html.py` renders those as the "Flood impact reports" section of
the Impacts filter group: a severity-colored map layer, one-click preset
chips (All, Crowdsourced, Street-level, Severe), impact-type toggles, live
result counts, and a minimum-records slider that composes with every other
filter on the page. Per-episode download ZIPs and `manifest.csv` include
impact counts and `impacts.csv`.

Episodes without crowdsourced data stay in the compendium. The impact layer
is a filter and an evaluation target, not an inclusion criterion.

## The satellite imagery layer

`pipeline/imagery/` records which satellites passed over each episode's counties
within 48 hours either side of the flood, and exactly when. 764 overpasses
across 134 of 135 episodes: 78 episodes were imaged during or just after the
flood (59 clear optical, 19 Sentinel-1 radar), and 33 of those also have a
clear pre-flood baseline for change detection. Only timing and footprint
coverage are stored, no pixels, so the layer is small and quick to rebuild.

The website filters on this, so an episode that can actually be validated
against an image is one click away, and each episode's overpass times appear
in its detail panel and download bundle. See `data/imagery/README.md`,
including how to add Planet scenes once a Planet key is set.

## Reproducing the pipeline

Python 3.10+. Install dependencies: `pip install -r requirements.txt`

Run each script from the repository root, e.g. `python pipeline/svi_data.py`.
Large downloads go to `cache/`, which is not in git. If you have caches from
the old `AFinal/locations/` layout, move them into `cache/` to avoid
re-downloading: `mrms_hour_cache/`, `flash_ari_hour_cache/`,
`sensor_data_wy2021_2025/`, `episodes/` and `hwm_archive.csv`.

1. Support layers: `pipeline/hwm_archive.py`, `pipeline/svi_data.py`,
   `pipeline/ifc_community_layers.py`, `pipeline/episode_rma_extract.py`
   (needs the RMA Cause of Loss files unzipped into
   `data/support/rma_cause_of_loss/raw/` first), `pipeline/choropleth_data.py`
2. Sensors: `pipeline/bulk_sensor_download.py` then
   `pipeline/enrich_sensors_with_county.py`. Per-episode extraction syncs the
   sensor archive from ownCloud and needs `OWNCLOUD_SHARE_TOKEN` set in the
   environment.
3. Rainfall: `pipeline/mrms_hour_cache.py` and `pipeline/flash_ari_hour_cache.py`
   (large downloads), then `pipeline/build_embedded_mrms_grids.py`
4. Impacts: `pipeline/impacts/episode_impacts_extract.py`,
   `build_news_impacts.py`, `build_impact_index.py`,
   `build_master_impacts.py`, `validate_impacts.py`, `generate_source_leads.py`
5. Imagery: `pipeline/imagery/episode_satellite_overpasses.py` then
   `build_imagery_index.py` (add `--planet` with a Planet key set to include
   Planet scenes)
6. Episode data for the site: `pipeline/build_wizard_data.py` (runs the
   per-episode HWM, FEMA, rainfall, FLASH, RMA and inundation extracts and
   reads the impact records from step 4) then
   `pipeline/build_embedded_episode_data.py`
7. Website: `pipeline/imagery/augment_site_imagery.py` (adds the imagery
   fields to `data/site_build/wizard_data.json`), then
   `pipeline/build_wizard_html.py`, then copy `data/site_build/wizard.html` to
   `docs/index.html` to publish.

## Data sources and attribution

NOAA Storm Events Database (NCEI); NOAA MRMS (rainfall) and NOAA/NSSL FLASH
(rainfall recurrence); Iowa Flood Center / IIHR (bridge sensors,
hydrostations, community inundation maps, IFIS); USGS (NWIS gauges, STN high
water marks); FEMA (OpenFEMA NFIP claims); USDA RMA (Cause of Loss crop
insurance indemnities); CDC/ATSDR (Social Vulnerability Index); US Census
TIGERweb and USGS WBD (boundaries); ESA Copernicus Sentinel-1 and Sentinel-2
and USGS Landsat via the Element 84 Earth Search STAC catalogue, and Planet
Labs where a key is supplied; Esri Light Gray Canvas and USGS The National
Map hydrography (website basemap); local news outlets credited per record in
`data/impacts/sources_news.csv`. All third-party
data remain under their providers' terms.

## Citing

See `CITATION.cff`. A data-paper style reference for the impact layer method:
GroundSource (Google Research, 2026) and HANZE v2.1 (Paprotny et al., ESSD,
2024).

## Team

Iowa Flood Center / IIHR Hydroscience and Engineering, University of Iowa.
Contact: Corin Fuchtman (corin-fuchtman@uiowa.edu).
