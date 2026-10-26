# Satellite Overpasses for Flood Episodes

Which satellites flew over each flooded area while the water was up, and
exactly when. Only overpass timing and footprint coverage are recorded, no
pixels are downloaded, so the whole layer is small and fast to rebuild.

The point is episode selection: a flood inundation mapping experiment can only
be validated against an image taken during or shortly after the flood. This
layer tells you, before you commit to an episode, whether such an image exists.

## Files

| File | Content |
|---|---|
| `episode_satellite_overpasses.py` | Queries the catalogues and writes the overpass table |
| `build_imagery_index.py` | Rolls overpasses up to one row per episode and grades them |
| `augment_site_imagery.py` | Embeds the results in the website and injects the add-on |
| `imagery_site_addon.js` | The website panel, filter, detail readout and ZIP export |
| `data/episode_overpasses.csv` | One row per overpass |
| `data/episode_imagery_index.csv` | One row per episode: grade, best overpass, baseline |

## Method

1. **Area**: the counties NOAA lists for the episode are dissolved into one
   polygon. Its bounding box is searched; coverage is measured against the
   dissolved polygon, so `aoi_coverage` means the fraction of the flooded
   counties a pass actually saw.
2. **Window**: episode begin minus 48 hours to episode end plus 48 hours.
   NOAA stores Iowa event times as CST-6 year round, so UTC is local plus six
   hours. Getting this wrong would shift every overpass by six hours.
3. **Grouping**: one satellite pass drops many tiles seconds apart, so scenes
   from the same platform within 20 minutes are collapsed into a single
   overpass with the median time, the union coverage, and the scene count.
4. **Labelling**: `pre` is before the flood began (a dry baseline for change
   detection), `during` and `post` are the flood itself.

## Sources

| Source | Needs a key | What it adds |
|---|---|---|
| Sentinel-2 L2A | no | 10 m optical, about 5 day revisit |
| Sentinel-1 GRD | no | radar, sees through cloud, the reliable flood sensor |
| Landsat Collection 2 L2 | no | 30 m optical, independent revisit cycle |
| Planet (PSScene, SkySatCollect, REOrthoTile) | yes | daily 3 m optical |

The three public catalogues come from the same STAC endpoint and need no
credentials, so `python episode_satellite_overpasses.py` reproduces the shipped
table from scratch.

### Adding Planet

Planet needs an account key. Set it once, then re-run with `--planet`:

```
Windows PowerShell:   $env:PL_API_KEY = "<your key>"
Linux or macOS:       export PL_API_KEY="<your key>"

python episode_satellite_overpasses.py --planet --no-cache
python build_imagery_index.py
python augment_site_imagery.py
```

The key is read from `PL_API_KEY` or `~/.pl_api_key` and is never written to
any output file. The search uses the same filter contract as the floodbench
tool in the Planet folder: a GeometryFilter on the episode box plus a
DateRangeFilter on `acquired`, over PSScene, SkySatCollect and REOrthoTile.
Without a key the Planet provider is skipped and the run still completes on the
public catalogues.

## Grading

An episode is graded on whether it was imaged during or just after the flood:

| Grade | Meaning |
|---|---|
| `clear` | clear optical image of the flood (cloud below 35 percent, at least 30 percent of the area) |
| `radar` | Sentinel-1 radar image of the flood, usable whatever the cloud did |
| `cloudy` | passes happened, but only cloudy or low coverage optical |
| `none` | no pass in the window |

`has_baseline_imagery` separately flags a clear image from the 48 hours before
the flood, which is what change detection needs.

Current run: 764 overpasses across 134 of 135 episodes. 78 episodes were
imaged during or just after the flood (59 clear optical, 19 radar), and 33 of
those also have a clear pre-flood baseline.

Cloud is the limiting factor, as expected: flood days are cloudy days. In the
June 2024 northwest Iowa flood, Sentinel-2 covered the whole area on 22 June
but at 98 percent cloud, while Landsat on 25 June covered it at 25 percent
cloud and Sentinel-1 radar imaged it on 1 July regardless of cloud.

## On the website

A "Satellite imagery" panel filters episodes three ways: any, imaged during
the flood, or imaged during the flood with a baseline as well. Each episode's
detail panel lists its overpass times with platform, coverage, cloud and
whether the pass was before, during or after the flood, and every per-episode
download ZIP now carries `satellite_overpasses.csv`.
