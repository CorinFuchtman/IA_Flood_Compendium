# Pre-release checklist

Run through this before merging to main or announcing the repository.

## Code and secrets

- [x] No credentials in code: ownCloud share token now read from the
      OWNCLOUD_SHARE_TOKEN environment variable (episode_sensor_extract.py)
- [ ] Commit the newer wizard generator: docs/index.html was produced by a
      version of the build pipeline (FLASH recurrence, episode_rma_extract.py)
      that is not in the repository yet; Corin should commit it
- [ ] Remove or archive superseded files: pipeline (9/10/11).ipynb naming,
      duplicate noaa CSVs, stale noaa_episodes/ folder, personal paths in
      AFinal/.claude/launch.json and notebook outputs

## Data quality

- [x] Impact records validated by script (validate_impacts.py): unique ids,
      coordinates inside Iowa, end date not before start date, severity 0-3,
      confidence A-C, controlled vocabularies, sources all logged
- [x] Every news/agency source logged in sources_news.csv, including rejected
      and unreachable ones (135 sources); per-episode search outcomes in
      search_log.csv
- [x] Precision audit rounds recorded in data/quality_audit.csv: round 1
      (seed 42, 24 records, 96% Accurate+Approximate) and round 2 (seed 43,
      12 round-4 records re-checked against re-fetched sources, 100%
      Accurate+Approximate; one pub date corrected). Repeat with a fresh
      seed before the data paper submission.
- [x] Database-ready master dataset built (impacts_master.csv/.geojson +
      impacts_data_dictionary.csv)

## Website

- [x] Filters verified headless (round 4): baseline 135 episodes and 560
      points; crowdsource preset narrows to 33 episodes; impact-type chips,
      live counts, and reset behave; no add-on console errors; impacts.csv
      still appended to episode ZIP downloads
- [ ] After merge: check GitHub Pages serves the updated docs/index.html and
      spot-check one episode popup, the preset chips, and one download bundle
      in a browser

## Publication

- [x] README with data layers, run order, attribution, and database loading
      instructions
- [x] requirements.txt, LICENSE, CITATION.cff
- [ ] Mint a DOI (Zenodo or the university library) at the first release tag
- [ ] Add the compendium URL and DOI to the conference abstract and paper
