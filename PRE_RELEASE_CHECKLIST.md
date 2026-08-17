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

- [x] Impact records validated: unique ids, coordinates inside Iowa, end date
      not before start date, severity 0-3, confidence A-C
- [x] Every news/agency source logged in sources_news.csv, including rejected
      and unreachable ones
- [ ] Precision audit before the data paper: score a random sample of records
      Accurate / Approximate / Partial / Wrong against their text_span and
      report the shares (GroundSource rubric, see impact_schema.md)

## Website

- [x] Filters verified headless: baseline episode count unchanged, crowdsource
      filter and impacts slider work, no console errors from the add-on
- [ ] After merge: check GitHub Pages serves the updated docs/index.html and
      spot-check one episode popup and one download bundle in a browser

## Publication

- [x] README with data layers, run order, and attribution
- [x] requirements.txt, LICENSE, CITATION.cff
- [ ] Mint a DOI (Zenodo or the university library) at the first release tag
- [ ] Add the compendium URL and DOI to the conference abstract and paper
