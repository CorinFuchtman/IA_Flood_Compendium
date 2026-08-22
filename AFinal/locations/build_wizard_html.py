"""
Generates choropleth/wizard.html: the interactive filter wizard. Embeds
wizard_data.json directly into the page (avoids file:// fetch/CORS issues
when just double-clicked open locally) so all filtering happens client-side
in JS -- no server, no Python round-trip per filter change.

Current filters: HUC8/county granularity toggle, minimum total sensors
available per episode. Built so additional filters (FEMA claim count, HWM
count, etc.) can be added the same way later: each is just another property
already present (or addable) on the per-episode record in wizard_data.json,
combined with AND into the same `passesFilters` check.
"""
import json
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_PATH = BASE_DIR / 'choropleth' / 'wizard_data.json'
EMBEDDED_EPISODE_DATA_PATH = BASE_DIR / 'choropleth' / 'embedded_episode_data.b64.txt'
OUT_PATH = BASE_DIR / 'choropleth' / 'wizard.html'

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Iowa Flood Compendium — Data Wizard</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/jszip@3.10.1/dist/jszip.min.js"></script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Antonio:wght@400;600;700&family=Roboto:wght@400;500;700&family=Roboto+Condensed:wght@400;600;700&family=Zilla+Slab:wght@500;700&display=swap" rel="stylesheet">
<style>
  /* Iowa Hawkeye black & gold, official UIowa brand typefaces -- IFC is a
     University of Iowa center. Per brand.uiowa.edu/fonts: Antonio for
     headlines/display, Roboto/Roboto Condensed for body & UI text, Zilla
     Slab as an accent face for visual hierarchy. All three are free on
     Google Fonts, unlike Sentinel/Sentinel-adjacent faces some university
     brand systems use, which need a paid license. */
  :root {
    --uiowa-black: #000000;
    --uiowa-black-soft: #16130a;
    --uiowa-gold: #FFCD00;
    --uiowa-gold-dark: #B8960C;
    --font-display: 'Antonio', Arial Narrow, sans-serif;
    --font-body: 'Roboto', -apple-system, Segoe UI, Arial, sans-serif;
    --font-condensed: 'Roboto Condensed', Arial Narrow, sans-serif;
    --font-accent: 'Zilla Slab', Georgia, serif;
  }
  html, body { margin:0; padding:0; height:100%; font-family: var(--font-body); }
  #app { display:flex; height:100%; }
  #panel { width:320px; flex-shrink:0; padding:16px; box-sizing:border-box; background:#fafaf7; border-right:1px solid #ddd; overflow-y:auto; }
  #map { flex:1; position:relative; }
  .detail-panel {
    position:absolute; top:16px; right:16px; width:320px; max-height:calc(100% - 32px);
    overflow-y:auto; background:#fff; border-radius:8px; box-shadow:0 4px 20px rgba(0,0,0,0.3);
    padding:16px; z-index:1000; border-top:4px solid var(--uiowa-gold);
  }
  .detail-close { position:absolute; top:10px; right:10px; border:none; background:none; font-size:20px; line-height:1; cursor:pointer; color:#888; }
  .detail-close:hover { color:#000; }
  .detail-panel h3 { font-family:var(--font-display); margin:0 24px 12px 0; font-size:19px; color:var(--uiowa-black); font-weight:700; text-transform:uppercase; letter-spacing:0.01em; }
  .detail-nav { display:flex; align-items:center; justify-content:center; gap:12px; margin-bottom:12px; }
  .detail-nav button { background:var(--uiowa-black); color:var(--uiowa-gold); border:none; border-radius:5px; width:32px; height:28px; cursor:pointer; font-size:14px; }
  .detail-nav button:disabled { opacity:0.3; cursor:default; }
  .detail-nav span { font-size:12px; font-weight:600; color:#333; min-width:70px; text-align:center; }
  .detail-episode-info { font-size:12px; line-height:1.6; margin-bottom:12px; }
  .detail-episode-info b { font-size:13px; }
  .detail-legend { font-size:11px; color:#555; border-top:1px solid #eee; padding-top:10px; }
  .detail-legend div { margin:3px 0; }
  .detail-legend .dot { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:6px; vertical-align:middle; }
  .detail-legend .sq { display:inline-block; width:9px; height:9px; margin-right:6px; vertical-align:middle; border:1px solid #000; }
  .detail-legend .tri { display:inline-block; width:0; height:0; margin-right:6px; vertical-align:middle; border-left:5px solid transparent; border-right:5px solid transparent; border-bottom:9px solid #000; }
  .detail-legend .excl { display:inline-block; width:10px; margin-right:6px; text-align:center; font-weight:900; color:#FFCD00; text-shadow:-1px -1px 0 #000,1px -1px 0 #000,-1px 1px 0 #000,1px 1px 0 #000; }
  .detail-hint { font-size:11px; color:#888; margin-top:6px; font-style:italic; }
  h1 { font-family:var(--font-display); font-size:22px; margin:0 0 6px 0; font-weight:700; letter-spacing:0.02em; text-transform:uppercase; color:var(--uiowa-black); border-bottom:3px solid var(--uiowa-gold); display:inline-block; padding-bottom:4px; }
  .subtitle { font-family:var(--font-accent); font-size:12px; color:#666; margin-bottom:16px; margin-top:8px; font-style:italic; }
  .section { margin-bottom:20px; padding-bottom:16px; border-bottom:1px solid #e0e0e0; }
  .section:last-child { border-bottom:none; }
  .section label { font-family:var(--font-condensed); font-size:12px; font-weight:600; color:#333; display:block; margin-bottom:6px; letter-spacing:0.01em; }
  .toggle-group { display:flex; border:1px solid #ccc; border-radius:6px; overflow:hidden; }
  .toggle-group button { font-family:var(--font-condensed); flex:1; padding:7px 0; border:none; background:#fff; cursor:pointer; font-size:13px; font-weight:600; }
  .toggle-group button.active { background:var(--uiowa-black); color:var(--uiowa-gold); font-weight:700; }
  input[type=range] { width:100%; accent-color: var(--uiowa-gold); }
  .slider-value { font-size:13px; color:var(--uiowa-gold); font-weight:700; text-shadow: 0 0 1px rgba(0,0,0,0.5); }
  .checkbox-row { display:flex; align-items:center; gap:8px; }
  .checkbox-row input[type=checkbox] { width:16px; height:16px; accent-color: var(--uiowa-black); }
  .checkbox-row label { margin-bottom:0; }
  .stat { font-size:13px; margin:4px 0; }
  .stat b { color:#111; }
  .legend { font-size:11px; color:#555; margin-top:8px; }
  .legend-swatch { display:inline-block; width:12px; height:12px; margin-right:4px; vertical-align:middle; border:1px solid #999; }
  .episode-list { max-height:220px; overflow-y:auto; font-size:11px; border:1px solid #e0e0e0; border-radius:4px; padding:4px 8px; background:#fff; }
  .episode-list div { padding:2px 0; border-bottom:1px solid #f0f0f0; }
  .leaflet-tooltip { font-size:12px; }
  .region-label { background: none; border: none; box-shadow: none; font-weight:700; font-size:11px; text-shadow: 0 0 3px #fff, 0 0 3px #fff, 0 0 3px #fff, 0 0 3px #fff; text-align:center; }

  #top-nav { display:flex; align-items:center; gap:4px; padding:10px 20px; background:var(--uiowa-black-soft); border-bottom:3px solid var(--uiowa-gold); box-shadow:0 2px 8px rgba(0,0,0,0.25); }
  #top-nav .nav-title { font-family:var(--font-display); color:var(--uiowa-gold); font-weight:700; font-size:19px; letter-spacing:0.03em; margin-right:20px; text-transform:uppercase; }
  #top-nav button { font-family:var(--font-condensed); padding:7px 16px; border:none; border-radius:6px; background:transparent; color:#ddd; cursor:pointer; font-size:13px; font-weight:600; transition:background 0.15s; }
  #top-nav button:hover { background:rgba(255,205,0,0.15); }
  #top-nav button.active { background:var(--uiowa-gold); color:var(--uiowa-black); }
  #page-map, #page-leaderboard, #page-download { height:calc(100vh - 46px); }
  #page-leaderboard, #page-download { display:none; overflow-y:auto; padding:20px; box-sizing:border-box; background:#fafaf7; }
  .leaderboard-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(320px, 1fr)); gap:16px; }
  .leaderboard-card { background:#fff; border:1px solid #e0e0e0; border-left:4px solid var(--uiowa-gold); border-radius:8px; padding:14px 16px; box-shadow:0 1px 3px rgba(0,0,0,0.06); }
  .leaderboard-card h3 { font-family:var(--font-display); font-size:17px; margin:0 0 2px 0; color:var(--uiowa-black); font-weight:700; letter-spacing:0.01em; text-transform:uppercase; }
  .lb-sublabel { font-size:10px; color:#999; font-style:italic; margin-bottom:8px; }
  .leaderboard-card table { width:100%; border-collapse:collapse; font-size:12px; }
  .leaderboard-card th { text-align:left; color:#888; font-weight:600; padding:2px 4px; border-bottom:1px solid #eee; }
  .leaderboard-card td { padding:4px 4px; border-bottom:1px solid #f5f5f5; }
  .leaderboard-card td.value { text-align:right; font-weight:700; color:var(--uiowa-gold-dark); white-space:nowrap; }
  .leaderboard-card td.rank { color:#aaa; width:18px; }
  .lb-controls { margin-bottom:16px; }

  /* ── Download-data staged wizard ── */
  .dl-wrap { max-width:640px; margin:0 auto; }
  .dl-progress { display:flex; gap:6px; margin-bottom:20px; }
  .dl-progress-step { flex:1; height:6px; border-radius:3px; background:#ddd; }
  .dl-progress-step.done { background:var(--uiowa-gold-dark); }
  .dl-progress-step.current { background:var(--uiowa-gold); }
  .dl-step-label { font-family:var(--font-condensed); font-size:12px; color:#888; margin-bottom:4px; text-transform:uppercase; letter-spacing:0.03em; }
  .dl-stage-title { font-family:var(--font-display); font-size:26px; color:var(--uiowa-black); text-transform:uppercase; margin:0 0 4px 0; }
  .dl-running-count { background:#fff; border:1px solid #e0e0e0; border-radius:8px; padding:12px 16px; margin:16px 0; font-size:14px; }
  .dl-running-count b { font-family:var(--font-display); font-size:22px; color:var(--uiowa-black); }
  .dl-stage-card { background:#fff; border:1px solid #e0e0e0; border-radius:8px; padding:20px; margin-bottom:16px; transition:box-shadow 0.2s, border-color 0.2s; }
  .dl-stage-card.dl-culprit { border-color:#c0392b; box-shadow:0 0 0 2px rgba(192,57,43,0.25); }
  .dl-warning { display:none; background:#fdecea; border:1px solid #c0392b; color:#8b1f14; border-radius:6px; padding:10px 12px; font-size:12px; margin-top:12px; }
  .dl-warning.show { display:block; }
  .dl-culprit .dl-warning { display:block; }
  .dl-closest-list { margin-top:8px; }
  .dl-closest-list .dl-closest-row { display:flex; justify-content:space-between; padding:4px 0; border-bottom:1px solid #f6d9d5; font-size:12px; }
  .dl-nav { display:flex; justify-content:space-between; margin-top:16px; }
  .dl-nav button { font-family:var(--font-condensed); font-weight:700; font-size:13px; padding:10px 22px; border-radius:6px; border:none; cursor:pointer; }
  .dl-nav .dl-btn-back { background:#eee; color:#333; }
  .dl-nav .dl-btn-next { background:var(--uiowa-black); color:var(--uiowa-gold); }
  .dl-nav button:disabled { opacity:0.4; cursor:default; }
  .dl-sensor-advanced { display:none; margin-top:12px; padding-top:12px; border-top:1px solid #eee; }
  .dl-sensor-advanced.show { display:block; }
  .dl-results-table { width:100%; border-collapse:collapse; font-size:12px; background:#fff; }
  .dl-results-table th { text-align:left; background:#f5f5f0; padding:6px 8px; border-bottom:2px solid var(--uiowa-gold); position:sticky; top:0; }
  .dl-results-table td { padding:6px 8px; border-bottom:1px solid #eee; }
  .dl-results-table tr:hover { background:#fffbe6; }
  .dl-download-btn { font-family:var(--font-condensed); font-weight:700; background:var(--uiowa-gold); color:var(--uiowa-black); border:none; border-radius:6px; padding:10px 20px; font-size:13px; cursor:pointer; margin-bottom:12px; }
  .dl-download-btn:disabled { opacity:0.5; cursor:default; }

  /* ── Leaderboard row click -> summary/download popup ── */
  .leaderboard-card tbody tr { cursor:pointer; }
  .leaderboard-card tbody tr:hover { background:#fffbe6; }
  .lb-modal-overlay { display:none; position:fixed; inset:0; background:rgba(0,0,0,0.5); z-index:2000; align-items:center; justify-content:center; }
  .lb-modal { background:#fff; border-radius:10px; border-top:5px solid var(--uiowa-gold); width:380px; max-width:90vw; max-height:80vh; overflow-y:auto; padding:22px; position:relative; box-shadow:0 10px 40px rgba(0,0,0,0.3); }
  .lb-modal h3 { font-family:var(--font-display); font-size:20px; margin:0 22px 10px 0; color:var(--uiowa-black); text-transform:uppercase; }
  .lb-modal-body { font-size:13px; line-height:1.7; color:#333; }
  .lb-modal-body .lb-loc { color:#888; margin:4px 0 10px 0; }
</style>
</head>
<body>
<div id="top-nav">
  <div class="nav-title">Iowa Flood Compendium</div>
  <button id="nav-map" class="active">Map &amp; Filters</button>
  <button id="nav-leaderboard">Top 5 Leaderboards</button>
  <button id="nav-download">Download Data</button>
</div>

<div id="page-map">
<div id="app">
  <div id="panel">
    <h1>Iowa Flood Compendium</h1>
    <div class="subtitle">Step 1: choose a region view and filter episodes</div>

    <div class="section">
      <label>Region view</label>
      <div class="toggle-group">
        <button id="btn-county" class="active">County</button>
        <button id="btn-huc8">HUC-8 watershed</button>
      </div>
    </div>

    <!-- ── Toggles ── -->
    <div class="section">
      <div class="checkbox-row">
        <input type="checkbox" id="show-events">
        <label for="show-events" style="margin-bottom:0">Show raw NOAA event locations</label>
      </div>
      <div class="legend">
        For episodes currently matching your filters: a line per individual reported event (start to end location), plus one gold buffer circle per episode centered on and enclosing all of that episode's events.
      </div>
      <div class="legend" id="events-status" style="font-weight:600; color:#333;"></div>
    </div>

    <div class="section">
      <div class="checkbox-row">
        <input type="checkbox" id="inundation-present">
        <label for="inundation-present" style="margin-bottom:0">Only episodes with IFC inundation maps available</label>
      </div>
      <div class="legend">
        51 of 135 episodes matched a mapped IFIS community (stage + flood-frequency map layers).
      </div>
    </div>

    <div class="section">
      <label>Basin size (HUC-8 view only)</label>
      <div class="toggle-group">
        <button id="btn-basin-all" class="active">All</button>
        <button id="btn-basin-above">Above 3,000 km&sup2;</button>
        <button id="btn-basin-below">Below 3,000 km&sup2;</button>
      </div>
      <div class="legend">
        23 of 56 Iowa-touching basins are below 3,000 km&sup2;, 33 above. Has no effect in County view.
      </div>
    </div>

    <!-- ── Sliders: instrumentation → ground-truth evidence → impact → context ── -->
    <div class="section">
      <label>Minimum IFC river sensors: <span class="slider-value" id="ifc_river-min-value">0</span></label>
      <input type="range" id="ifc_river-min" min="0" max="0" value="0" step="1" data-sensor-type="ifc_river">
    </div>

    <div class="section">
      <label>Minimum IFC hydrostations: <span class="slider-value" id="ifc_hydrostation-min-value">0</span></label>
      <input type="range" id="ifc_hydrostation-min" min="0" max="0" value="0" step="1" data-sensor-type="ifc_hydrostation">
    </div>

    <div class="section">
      <label>Minimum USGS sensors: <span class="slider-value" id="usgs-min-value">0</span></label>
      <input type="range" id="usgs-min" min="0" max="0" value="0" step="1" data-sensor-type="usgs">
    </div>

    <div class="section">
      <label>Minimum USGS High-Water Marks: <span class="slider-value" id="hwm-min-value">0</span></label>
      <input type="range" id="hwm-min" min="0" max="0" value="0" step="1">
      <div class="legend">
        Only 7 of 135 episodes have any HWM records at all, so most of this slider's range returns nothing — that's a real gap in HWM coverage, not a bug.
      </div>
    </div>

    <div class="section">
      <label>Minimum FEMA NFIP claims: <span class="slider-value" id="fema-min-value">0</span></label>
      <input type="range" id="fema-min" min="0" max="0" value="0" step="1">
      <div class="legend">
        42 of 135 episodes have at least one FEMA claim.
      </div>
    </div>

    <div class="section">
      <label>Minimum accumulated rainfall (mm): <span class="slider-value" id="precip-min-value">0</span></label>
      <input type="range" id="precip-min" min="0" max="0" value="0" step="1">
      <div class="legend">
        126 of 135 episodes have MRMS precipitation data (mean-of-grid accumulation over the episode's padded window). Max observed: 225 mm.
      </div>
    </div>

    <div class="section">
      <label>Minimum county social vulnerability (SVI, county view only): <span class="slider-value" id="svi-min-value">0.0</span></label>
      <input type="range" id="svi-min" min="0" max="1" value="0" step="0.05">
      <div class="legend">
        CDC/ATSDR SVI overall percentile rank (0 = least vulnerable, 1 = most vulnerable, ranked among Iowa's 99 counties, 2022 data). Has no effect in HUC-8 view.
      </div>
    </div>

    <div class="section">
      <label>Minimum direct + indirect injuries: <span class="slider-value" id="injuries-min-value">0</span></label>
      <input type="range" id="injuries-min" min="0" max="0" value="0" step="1">
      <div class="legend">
        From NOAA's own storm reports. None of the 135 episodes have any recorded injuries in this dataset.
      </div>
    </div>

    <div class="section">
      <label>Minimum direct + indirect deaths: <span class="slider-value" id="deaths-min-value">0</span></label>
      <input type="range" id="deaths-min" min="0" max="0" value="0" step="1">
    </div>

    <div class="section">
      <label>Minimum property damage ($): <span class="slider-value" id="propdmg-min-value">0</span></label>
      <input type="range" id="propdmg-min" min="0" max="0" value="0" step="1000">
      <div class="legend">
        23 of 135 episodes have any recorded property damage (max ~$60.8M).
      </div>
    </div>

    <div class="section">
      <label>Minimum crop damage ($): <span class="slider-value" id="cropdmg-min-value">0</span></label>
      <input type="range" id="cropdmg-min" min="0" max="0" value="0" step="1000">
      <div class="legend">
        10 of 135 episodes have any recorded crop damage (max ~$8.9M).
      </div>
      <div class="legend">
        Only episodes meeting ALL filters above (for the selected region view) are counted on the map.
      </div>
    </div>

    <div class="section">
      <div class="stat"><b id="stat-matching">135</b> / <span id="stat-total">135</span> episodes match current filters</div>
      <div class="stat">Regions with &ge;1 matching episode: <b id="stat-regions">-</b></div>
    </div>

    <div class="section">
      <label>Matching episodes</label>
      <div class="episode-list" id="episode-list"></div>
    </div>

    <div class="section legend">
      <div><span class="legend-swatch" style="background:#f7f7f7"></span>0 episodes</div>
      <div><span class="legend-swatch" style="background:#fee8c8"></span>low</div>
      <div><span class="legend-swatch" style="background:#fdbb84"></span>medium</div>
      <div><span class="legend-swatch" style="background:#e34a33"></span>high</div>
    </div>
  </div>
  <div id="map">
    <div id="region-detail-panel" class="detail-panel" style="display:none;">
      <button id="detail-close" class="detail-close">&times;</button>
      <h3 id="detail-region-name"></h3>
      <div class="detail-nav">
        <button id="detail-prev">&larr;</button>
        <span id="detail-episode-counter"></span>
        <button id="detail-next">&rarr;</button>
      </div>
      <div class="toggle-group" style="margin-bottom:12px;">
        <button id="detail-scope-region" class="active">This Region</button>
        <button id="detail-scope-episode">Entire Episode</button>
      </div>
      <div class="detail-episode-info" id="detail-episode-info"></div>
      <div class="detail-legend">
        <div><span class="dot" style="background:#1f78b4"></span>IFC River Sensor</div>
        <div><span class="dot" style="background:#33a02c"></span>USGS Sensor</div>
        <div><span class="sq" style="background:#e31a1c"></span>IFC Hydrostation</div>
        <div><span class="tri"></span>USGS High-Water Mark</div>
        <div><span class="excl">!</span>FEMA NFIP Claim</div>
      </div>
      <div class="detail-hint">Inundation map layers aren't plotted here yet (only counted) — that's flood-extent polygon data we haven't downloaded.</div>
    </div>
  </div>
</div>
</div>

<div id="page-leaderboard">
  <div class="lb-controls">
    <label style="font-size:12px; font-weight:600; color:#333; margin-right:8px;">Sensor counts use:</label>
    <div class="toggle-group" style="display:inline-flex; width:220px;">
      <button id="lb-btn-county" class="active">County</button>
      <button id="lb-btn-huc8">HUC-8 watershed</button>
    </div>
  </div>
  <div class="leaderboard-grid" id="leaderboard-grid"></div>
</div>

<div id="page-download">
  <div class="dl-wrap">
    <div class="dl-progress" id="dl-progress"></div>
    <div class="dl-step-label" id="dl-step-label">Step 1 of 5</div>
    <h2 class="dl-stage-title" id="dl-stage-title">Region Type</h2>

    <div class="dl-running-count">
      <b id="dl-running-count">135</b> of 135 episodes match so far
    </div>

    <!-- Stage 0: Region Type -->
    <div class="dl-stage-card" id="dl-stage-0">
      <label style="font-family:var(--font-condensed); font-weight:600; font-size:13px; display:block; margin-bottom:8px;">Filter by watershed (HUC-8) or by county?</label>
      <div class="toggle-group">
        <button id="dl-btn-county" class="active">County</button>
        <button id="dl-btn-huc8">HUC-8 Watershed</button>
      </div>
      <div class="legend" style="margin-top:10px;">This decides which boundary sensor coverage and a couple of later filters (basin size, SVI) get matched against. You can't mix both at once, same as the main map.</div>
      <div class="dl-warning"></div>
    </div>

    <!-- Stage 1: Sensors -->
    <div class="dl-stage-card" id="dl-stage-1" style="display:none;">
      <label style="font-family:var(--font-condensed); font-weight:600; font-size:13px; display:block; margin-bottom:8px;">Minimum total sensors: <span class="slider-value" id="dl-sensor-total-value">0</span></label>
      <input type="range" id="dl-sensor-total" min="0" max="0" value="0" step="1">
      <div class="checkbox-row" style="margin-top:12px;">
        <input type="checkbox" id="dl-sensor-advanced-toggle">
        <label for="dl-sensor-advanced-toggle" style="margin-bottom:0;">Advanced: set USGS / IFC bridge / IFC hydrostation minimums separately</label>
      </div>
      <div class="dl-sensor-advanced" id="dl-sensor-advanced">
        <label>Minimum USGS sensors: <span class="slider-value" id="dl-sensor-usgs-value">0</span></label>
        <input type="range" id="dl-sensor-usgs" min="0" max="0" value="0" step="1">
        <label style="margin-top:10px;">Minimum IFC river (bridge) sensors: <span class="slider-value" id="dl-sensor-river-value">0</span></label>
        <input type="range" id="dl-sensor-river" min="0" max="0" value="0" step="1">
        <label style="margin-top:10px;">Minimum IFC hydrostations: <span class="slider-value" id="dl-sensor-hydro-value">0</span></label>
        <input type="range" id="dl-sensor-hydro" min="0" max="0" value="0" step="1">
      </div>
      <div class="dl-warning"></div>
    </div>

    <!-- Stage 2: HWM & FEMA -->
    <div class="dl-stage-card" id="dl-stage-2" style="display:none;">
      <label>Minimum USGS High-Water Marks: <span class="slider-value" id="dl-hwm-value">0</span></label>
      <input type="range" id="dl-hwm" min="0" max="0" value="0" step="1">
      <label style="margin-top:12px;">Minimum FEMA NFIP claims: <span class="slider-value" id="dl-fema-value">0</span></label>
      <input type="range" id="dl-fema" min="0" max="0" value="0" step="1">
      <div class="dl-warning"></div>
    </div>

    <!-- Stage 3: Rainfall -->
    <div class="dl-stage-card" id="dl-stage-3" style="display:none;">
      <label>Minimum accumulated rainfall (mm): <span class="slider-value" id="dl-precip-value">0</span></label>
      <input type="range" id="dl-precip" min="0" max="0" value="0" step="1">
      <div class="dl-warning"></div>
    </div>

    <!-- Stage 4: Everything Else -->
    <div class="dl-stage-card" id="dl-stage-4" style="display:none;">
      <div class="checkbox-row">
        <input type="checkbox" id="dl-inundation">
        <label for="dl-inundation" style="margin-bottom:0;">Only episodes with IFC inundation maps available</label>
      </div>

      <label style="margin-top:14px;">Basin size (HUC-8 view only)</label>
      <div class="toggle-group">
        <button id="dl-basin-all" class="active">All</button>
        <button id="dl-basin-above">Above 3,000 km&sup2;</button>
        <button id="dl-basin-below">Below 3,000 km&sup2;</button>
      </div>

      <label style="margin-top:14px;">Minimum county SVI (county view only): <span class="slider-value" id="dl-svi-value">0.0</span></label>
      <input type="range" id="dl-svi" min="0" max="1" value="0" step="0.05">

      <label style="margin-top:14px;">Minimum injuries: <span class="slider-value" id="dl-injuries-value">0</span></label>
      <input type="range" id="dl-injuries" min="0" max="0" value="0" step="1">

      <label style="margin-top:14px;">Minimum deaths: <span class="slider-value" id="dl-deaths-value">0</span></label>
      <input type="range" id="dl-deaths" min="0" max="0" value="0" step="1">

      <label style="margin-top:14px;">Minimum property damage ($): <span class="slider-value" id="dl-propdmg-value">0</span></label>
      <input type="range" id="dl-propdmg" min="0" max="0" value="0" step="1000">

      <label style="margin-top:14px;">Minimum crop damage ($): <span class="slider-value" id="dl-cropdmg-value">0</span></label>
      <input type="range" id="dl-cropdmg" min="0" max="0" value="0" step="1000">
      <div class="dl-warning"></div>
    </div>

    <!-- Results -->
    <div class="dl-stage-card" id="dl-stage-results" style="display:none;">
      <p style="font-size:13px; color:#555;">These are the episodes matching every filter you set. Check/uncheck to choose exactly which ones go in the ZIP — each gets its own folder with real sensor time series (USGS, IFC hydrostations, IFC river), hourly MRMS rainfall, HWM and FEMA claim locations, and a summary. Everything is embedded in this page and built entirely in your browser — no server, no internet needed once the page is loaded.</p>
      <button class="dl-download-btn" id="dl-download-btn">Download ZIP (<span id="dl-download-count">0</span> selected)</button>
      <div style="max-height:420px; overflow-y:auto; border:1px solid #eee; border-radius:6px;">
        <table class="dl-results-table">
          <thead><tr><th><input type="checkbox" id="dl-select-all" checked></th><th>Episode</th><th>Date</th><th>Region(s)</th><th>Sensors</th><th>HWM</th><th>FEMA</th><th>Rain (mm)</th></tr></thead>
          <tbody id="dl-results-body"></tbody>
        </table>
      </div>
    </div>

    <div class="dl-nav">
      <button class="dl-btn-back" id="dl-btn-back">&larr; Back</button>
      <button class="dl-btn-next" id="dl-btn-next">Next &rarr;</button>
    </div>
  </div>
</div>

<div id="lb-modal-overlay" class="lb-modal-overlay">
  <div class="lb-modal">
    <button id="lb-modal-close" class="detail-close">&times;</button>
    <h3 id="lb-modal-title"></h3>
    <div class="lb-modal-body" id="lb-modal-body"></div>
    <button class="dl-download-btn" id="lb-modal-download" style="margin-top:14px;">Download Data</button>
  </div>
</div>

<script>
const WIZARD_DATA = __WIZARD_DATA_JSON__;
// Real per-episode sensor time series + MRMS rainfall, gzip-compressed then
// base64-encoded at build time (see build_embedded_episode_data.py). Decoded
// lazily by getEpisodeDataPayload() on first download, not at page load.
const EMBEDDED_EPISODE_DATA_B64 = "__EMBEDDED_EPISODE_DATA_B64__";

const map = L.map('map').setView([42.0, -93.5], 7);
L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
  attribution: '&copy; OpenStreetMap contributors &copy; CARTO', maxZoom: 12
}).addTo(map);

let granularity = 'county';
const sensorMin = { ifc_river: 0, ifc_hydrostation: 0, usgs: 0 };
let hwmMin = 0;
let femaMin = 0;
let inundationRequired = false;
let basinSizeFilter = 'all';  // 'all' | 'above' | 'below'
const BASIN_SIZE_THRESHOLD_SQKM = 3000;
let precipMin = 0;
let sviMin = 0;
let injuriesMin = 0;
let deathsMin = 0;
let propDmgMin = 0;
let cropDmgMin = 0;
let geoLayer = null;
let labelLayer = L.layerGroup().addTo(map);

const huc8AreaLookup = {};
for (const feat of WIZARD_DATA.huc8_geometry.features) {
  huc8AreaLookup[feat.properties.huc8] = feat.properties.area_sqkm;
}
const countySviLookup = {};
for (const feat of WIZARD_DATA.county_geometry.features) {
  countySviLookup[feat.properties.county_fips] = feat.properties.svi_overall;
}

function regionIdField() { return granularity === 'county' ? 'county_fips' : 'huc8'; }
function regionNameField() { return granularity === 'county' ? 'county_name' : 'huc8_name'; }
function episodeRegionKey() { return granularity === 'county' ? 'counties' : 'huc8s'; }

function episodePassesBasinSizeFilter(ep) {
  if (basinSizeFilter === 'all' || granularity !== 'huc8') return true;
  return ep.huc8s.some((h) => {
    const area = huc8AreaLookup[h];
    if (area == null) return false;
    return basinSizeFilter === 'above' ? area > BASIN_SIZE_THRESHOLD_SQKM : area <= BASIN_SIZE_THRESHOLD_SQKM;
  });
}

function episodePassesSviFilter(ep) {
  if (sviMin <= 0 || granularity !== 'county') return true;
  return ep.counties.some((c) => {
    const svi = countySviLookup[c];
    return svi != null && svi >= sviMin;
  });
}

function episodePassesFilter(ep) {
  const counts = ep.n_sensors[granularity];
  return counts.ifc_river >= sensorMin.ifc_river
      && counts.ifc_hydrostation >= sensorMin.ifc_hydrostation
      && counts.usgs >= sensorMin.usgs
      && ep.n_hwms >= hwmMin
      && ep.n_fema_claims >= femaMin
      && (!inundationRequired || ep.n_inundation_layers > 0)
      && episodePassesBasinSizeFilter(ep)
      && ep.precip_total_mm >= precipMin
      && episodePassesSviFilter(ep)
      && ep.n_injuries >= injuriesMin
      && ep.n_deaths >= deathsMin
      && ep.damage_property_usd >= propDmgMin
      && ep.damage_crops_usd >= cropDmgMin;
}

function colorForCount(n) {
  if (n === 0) return '#f7f7f7';
  if (n <= 1) return '#fee8c8';
  if (n <= 3) return '#fdbb84';
  if (n <= 6) return '#fc8d59';
  return '#e34a33';
}

function recompute() {
  const episodes = WIZARD_DATA.episodes;
  const regionCounts = {};
  let matchingCount = 0;
  const matchingIds = [];

  for (const [epId, ep] of Object.entries(episodes)) {
    if (!episodePassesFilter(ep)) continue;
    matchingCount++;
    matchingIds.push(epId);
    for (const regionId of ep[episodeRegionKey()]) {
      regionCounts[regionId] = (regionCounts[regionId] || 0) + 1;
    }
  }

  document.getElementById('stat-matching').textContent = matchingCount;
  document.getElementById('stat-total').textContent = Object.keys(episodes).length;
  document.getElementById('stat-regions').textContent = Object.keys(regionCounts).length;

  const listEl = document.getElementById('episode-list');
  listEl.innerHTML = matchingIds.length
    ? matchingIds.map(id => {
        const ep = episodes[id];
        const c = ep.n_sensors[granularity];
        return `<div><b>${id}</b> — ${ep.event_types.join(', ')} (${ep.begin_date.slice(0,10)})<br>`
             + `&nbsp;&nbsp;river: ${c.ifc_river}, hydrostation: ${c.ifc_hydrostation}, USGS: ${c.usgs}, HWMs: ${ep.n_hwms}, FEMA claims: ${ep.n_fema_claims}, inundation maps: ${ep.n_inundation_layers > 0 ? 'yes ('+ep.n_inundation_layers+' layers)' : 'no'}, rainfall: ${ep.precip_total_mm} mm<br>`
             + `&nbsp;&nbsp;injuries: ${ep.n_injuries}, deaths: ${ep.n_deaths}, property damage: $${ep.damage_property_usd.toLocaleString()}, crop damage: $${ep.damage_crops_usd.toLocaleString()}</div>`;
      }).join('')
    : '<div style="color:#999">No episodes match current filters</div>';

  return { regionCounts, matchingIds };
}

// Dedicated pane above the default overlayPane (z-index 400, where the
// choropleth's GeoJSON polygons render) so event markers/lines stay on top
// regardless of which layer was most recently redrawn -- without this,
// vector layers share one pane and the last one redrawn wins the stacking
// order, which was the choropleth on every filter change.
map.createPane('eventsPane');
map.getPane('eventsPane').style.zIndex = 450;

let eventsLayer = L.layerGroup().addTo(map);
let showEvents = false;

function renderEventsLayer(matchingIds) {
  eventsLayer.clearLayers();
  const statusEl = document.getElementById('events-status');

  if (!showEvents) {
    statusEl.textContent = '';
    return;
  }
  statusEl.textContent = `Showing ${matchingIds.length} episode(s).`;

  const matchingSet = new Set(matchingIds);
  const byEpisode = {};
  for (const ev of WIZARD_DATA.events) {
    if (!matchingSet.has(ev.episode_id)) continue;
    (byEpisode[ev.episode_id] = byEpisode[ev.episode_id] || []).push(ev);
  }

  // Pass 1: one buffer circle per episode (not per event) -- centered on
  // the centroid of all that episode's event begin/end points, radius
  // sized to enclose all of them (+15% padding, 2km floor so a
  // single-point episode still shows a visible buffer instead of a dot).
  // Drawn BEFORE the event lines (pass 2) so the lines render on top,
  // since layers added later within the same pane stack above earlier ones.
  for (const [epId, evs] of Object.entries(byEpisode)) {
    const points = [];
    for (const ev of evs) {
      points.push([ev.begin_lat, ev.begin_lon]);
      points.push([ev.end_lat, ev.end_lon]);
    }
    const centerLat = points.reduce((s, p) => s + p[0], 0) / points.length;
    const centerLon = points.reduce((s, p) => s + p[1], 0) / points.length;
    const center = L.latLng(centerLat, centerLon);
    const maxDist = points.reduce((m, p) => Math.max(m, center.distanceTo(L.latLng(p[0], p[1]))), 0);
    const radius = Math.max(maxDist * 1.15, 2000);

    L.circle(center, {
      pane: 'eventsPane', radius, color: '#000', weight: 1.5, fillColor: '#FFCD00', fillOpacity: 0.15,
    }).bindTooltip(`Episode ${epId} — ${evs.length} event(s)`, { sticky: true }).addTo(eventsLayer);
  }

  // Pass 2: individual event lines, drawn on top of the buffer circles.
  for (const ev of WIZARD_DATA.events) {
    if (!matchingSet.has(ev.episode_id)) continue;
    const samePoint = Math.abs(ev.begin_lat - ev.end_lat) < 1e-6 && Math.abs(ev.begin_lon - ev.end_lon) < 1e-6;
    if (samePoint) continue;
    L.polyline([[ev.begin_lat, ev.begin_lon], [ev.end_lat, ev.end_lon]], {
      pane: 'eventsPane', color: '#C9A227', weight: 2, opacity: 0.9,
    }).bindTooltip(`${ev.event_type} — ${ev.begin_date.slice(0, 10)} (${ev.episode_id})`, { sticky: true })
      .addTo(eventsLayer);
  }
}

function renderMap() {
  const { regionCounts, matchingIds } = recompute();
  lastMatchingIds = matchingIds;
  renderEventsLayer(currentEventsScopeIds());
  const geo = granularity === 'county' ? WIZARD_DATA.county_geometry : WIZARD_DATA.huc8_geometry;
  const idField = regionIdField();
  const nameField = regionNameField();

  if (geoLayer) map.removeLayer(geoLayer);
  labelLayer.clearLayers();

  geoLayer = L.geoJSON(geo, {
    style: (feature) => {
      const count = regionCounts[feature.properties[idField]] || 0;
      return { fillColor: colorForCount(count), fillOpacity: 0.75, color: '#666', weight: 0.6 };
    },
    onEachFeature: (feature, layer) => {
      const count = regionCounts[feature.properties[idField]] || 0;
      layer.bindTooltip(`${feature.properties[nameField]}: ${count} matching episode(s)`, { sticky: true });
      layer.on('click', () => {
        openRegionDetail(feature.properties[idField], feature.properties[nameField], layer.getBounds(), feature.geometry);
      });

      const center = layer.getBounds().getCenter();
      const label = L.marker(center, {
        icon: L.divIcon({ className: 'region-label', html: String(count), iconSize: [30, 16] }),
        interactive: false,
      });
      labelLayer.addLayer(label);
    },
  }).addTo(map);

  // If a region's already selected (drilled into), keep it in sync with
  // filter changes -- the episode list for that region can shrink/grow/
  // clamp as filters move, rather than silently going stale.
  if (detailMode) refreshDetailForCurrentFilters();
}

// ── Region drill-down: select a region, flip through its matching episodes ──
// USGS = green circle, IFC river = blue circle, IFC hydrostation = red
// square, HWM = black triangle, FEMA claim = gold exclamation point.
// Leaflet has no built-in square/triangle/exclamation marker, so those
// three are divIcons (small styled HTML) instead of circleMarkers.
const SENSOR_STYLE = {
  ifc_river: { shape: 'circle', color: '#1f78b4' },
  usgs: { shape: 'circle', color: '#33a02c' },
  ifc_hydrostation: { shape: 'square', color: '#e31a1c' },
};

function squareIcon(color) {
  return L.divIcon({
    className: '', iconSize: [12, 12], iconAnchor: [6, 6],
    html: `<div style="width:10px;height:10px;background:${color};border:1px solid #000;"></div>`,
  });
}
function triangleIcon(color) {
  return L.divIcon({
    className: '', iconSize: [14, 12], iconAnchor: [7, 10],
    html: `<div style="width:0;height:0;border-left:6px solid transparent;border-right:6px solid transparent;border-bottom:11px solid ${color};filter:drop-shadow(0 0 1px #fff);"></div>`,
  });
}
function exclamationIcon(color) {
  return L.divIcon({
    className: '', iconSize: [14, 18], iconAnchor: [7, 16],
    html: `<div style="color:${color};font-weight:900;font-size:17px;line-height:1;text-shadow:-1px -1px 0 #000,1px -1px 0 #000,-1px 1px 0 #000,1px 1px 0 #000;">!</div>`,
  });
}
let detailMode = false;
let detailRegionId = null;
let detailRegionGeometry = null;
let detailEpisodeIds = [];
let detailEpisodeIndex = 0;
let detailScope = 'region';  // 'region' | 'episode'
let detailLayer = L.layerGroup().addTo(map);
let lastMatchingIds = [];

// While a specific episode is drilled into (detail panel open), the "show
// event locations" layer should reflect ONLY that one episode -- not the
// full filtered set -- since seeing every other matching episode's buffer
// at the same time defeats the point of having drilled into one of them.
function currentEventsScopeIds() {
  return (detailMode && detailEpisodeIds.length > 0) ? [detailEpisodeIds[detailEpisodeIndex]] : lastMatchingIds;
}

// Ray-casting point-in-polygon, GeoJSON coordinate order [lng, lat].
// Handles Polygon (with holes) and MultiPolygon -- the only two geometry
// types the county/HUC8 layers ever produce.
function pointInRing(lng, lat, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0], yi = ring[i][1];
    const xj = ring[j][0], yj = ring[j][1];
    const intersect = ((yi > lat) !== (yj > lat)) && (lng < (xj - xi) * (lat - yi) / (yj - yi) + xi);
    if (intersect) inside = !inside;
  }
  return inside;
}
function pointInPolygonRings(lng, lat, rings) {
  if (!pointInRing(lng, lat, rings[0])) return false;
  for (let k = 1; k < rings.length; k++) {
    if (pointInRing(lng, lat, rings[k])) return false;  // inside a hole
  }
  return true;
}
function pointInGeometry(lat, lng, geometry) {
  if (!geometry) return true;
  if (geometry.type === 'Polygon') return pointInPolygonRings(lng, lat, geometry.coordinates);
  if (geometry.type === 'MultiPolygon') return geometry.coordinates.some((poly) => pointInPolygonRings(lng, lat, poly));
  return true;
}

function episodesTouchingRegion(regionId) {
  const key = episodeRegionKey();
  return Object.entries(WIZARD_DATA.episodes)
    .filter(([, ep]) => episodePassesFilter(ep) && ep[key].includes(regionId))
    .map(([id]) => id);
}

function openRegionDetail(regionId, regionName, bounds, geometry) {
  const ids = episodesTouchingRegion(regionId);
  detailRegionId = regionId;
  detailRegionGeometry = geometry;
  detailEpisodeIds = ids;
  detailEpisodeIndex = 0;
  detailMode = true;
  document.getElementById('detail-region-name').textContent = regionName;
  document.getElementById('detail-scope-region').textContent = regionName;
  document.getElementById('region-detail-panel').style.display = 'block';
  map.fitBounds(bounds, { padding: [40, 40] });
  renderDetailEpisode();
}

function refreshDetailForCurrentFilters() {
  const ids = episodesTouchingRegion(detailRegionId);
  detailEpisodeIds = ids;
  detailEpisodeIndex = Math.min(detailEpisodeIndex, Math.max(0, ids.length - 1));
  renderDetailEpisode();
}

function closeRegionDetail() {
  detailMode = false;
  detailRegionId = null;
  document.getElementById('region-detail-panel').style.display = 'none';
  detailLayer.clearLayers();
  renderEventsLayer(currentEventsScopeIds());
}

function renderDetailEpisode() {
  const counterEl = document.getElementById('detail-episode-counter');
  const infoEl = document.getElementById('detail-episode-info');
  document.getElementById('detail-prev').disabled = detailEpisodeIds.length <= 1;
  document.getElementById('detail-next').disabled = detailEpisodeIds.length <= 1;
  detailLayer.clearLayers();
  renderEventsLayer(currentEventsScopeIds());

  if (detailEpisodeIds.length === 0) {
    counterEl.textContent = '0 of 0';
    infoEl.innerHTML = '<span style="color:#999">No episodes matching current filters touch this region.</span>';
    return;
  }

  const epId = detailEpisodeIds[detailEpisodeIndex];
  const ep = WIZARD_DATA.episodes[epId];
  counterEl.textContent = `${detailEpisodeIndex + 1} of ${detailEpisodeIds.length}`;

  const scoped = detailScope === 'region';
  const allSensors = ep.sensor_points[granularity] || [];
  const sensors = scoped ? allSensors.filter((p) => pointInGeometry(p.lat, p.lng, detailRegionGeometry)) : allSensors;
  const hwms = scoped ? ep.hwm_points.filter((h) => pointInGeometry(h.lat, h.lon, detailRegionGeometry)) : ep.hwm_points;
  const femas = scoped ? ep.fema_points.filter((f) => pointInGeometry(f.lat, f.lon, detailRegionGeometry)) : ep.fema_points;

  const scopeNote = scoped
    ? `<span style="color:#888">(within this region — ${allSensors.length - sensors.length} sensor(s)/${ep.hwm_points.length - hwms.length} HWM(s)/${ep.fema_points.length - femas.length} claim(s) outside it, hidden)</span>`
    : `<span style="color:#888">(entire episode, all regions it touches)</span>`;

  infoEl.innerHTML = `
    <b>${epId}</b> — ${ep.event_types.join(', ')}<br>
    ${ep.begin_date.slice(0, 10)} to ${ep.end_date.slice(0, 10)}<br>
    Rainfall: ${ep.precip_total_mm} mm (whole episode) &nbsp;|&nbsp; Inundation layers: ${ep.n_inundation_layers}<br>
    Injuries: ${ep.n_injuries} &nbsp;|&nbsp; Deaths: ${ep.n_deaths} &nbsp;|&nbsp; Property: $${ep.damage_property_usd.toLocaleString()} &nbsp;|&nbsp; Crops: $${ep.damage_crops_usd.toLocaleString()}<br>
    Shown here: ${sensors.length} sensor(s), ${hwms.length} HWM(s), ${femas.length} FEMA claim(s)<br>
    ${scopeNote}
  `;

  for (const p of sensors) {
    const style = SENSOR_STYLE[p.type];
    const marker = style.shape === 'circle'
      ? L.circleMarker([p.lat, p.lng], { pane: 'eventsPane', radius: 5, color: '#000', weight: 1, fillColor: style.color, fillOpacity: 1 })
      : L.marker([p.lat, p.lng], { pane: 'eventsPane', icon: squareIcon(style.color) });
    marker.bindTooltip(`${p.description}`, { sticky: true }).addTo(detailLayer);
  }
  for (const h of hwms) {
    L.marker([h.lat, h.lon], { pane: 'eventsPane', icon: triangleIcon('#000') })
      .bindTooltip(`HWM: ${h.waterbody}${h.elev_ft != null ? ` (${h.elev_ft.toFixed(1)} ft)` : ''}`, { sticky: true }).addTo(detailLayer);
  }
  for (const f of femas) {
    L.marker([f.lat, f.lon], { pane: 'eventsPane', icon: exclamationIcon('#FFCD00') })
      .bindTooltip(`FEMA claim: ${f.amount != null ? '$' + f.amount.toLocaleString() : '?'} (${f.date})`, { sticky: true }).addTo(detailLayer);
  }
}

document.getElementById('detail-scope-region').addEventListener('click', () => {
  detailScope = 'region';
  document.getElementById('detail-scope-region').classList.add('active');
  document.getElementById('detail-scope-episode').classList.remove('active');
  renderDetailEpisode();
});
document.getElementById('detail-scope-episode').addEventListener('click', () => {
  detailScope = 'episode';
  document.getElementById('detail-scope-episode').classList.add('active');
  document.getElementById('detail-scope-region').classList.remove('active');
  renderDetailEpisode();
});

document.getElementById('detail-prev').addEventListener('click', () => {
  if (detailEpisodeIds.length === 0) return;
  detailEpisodeIndex = (detailEpisodeIndex - 1 + detailEpisodeIds.length) % detailEpisodeIds.length;
  renderDetailEpisode();
});
document.getElementById('detail-next').addEventListener('click', () => {
  if (detailEpisodeIds.length === 0) return;
  detailEpisodeIndex = (detailEpisodeIndex + 1) % detailEpisodeIds.length;
  renderDetailEpisode();
});
document.getElementById('detail-close').addEventListener('click', closeRegionDetail);

document.getElementById('btn-county').addEventListener('click', () => {
  granularity = 'county';
  document.getElementById('btn-county').classList.add('active');
  document.getElementById('btn-huc8').classList.remove('active');
  renderMap();
});
document.getElementById('btn-huc8').addEventListener('click', () => {
  granularity = 'huc8';
  document.getElementById('btn-huc8').classList.add('active');
  document.getElementById('btn-county').classList.remove('active');
  renderMap();
});

const basinButtons = { all: 'btn-basin-all', above: 'btn-basin-above', below: 'btn-basin-below' };
Object.entries(basinButtons).forEach(([value, id]) => {
  document.getElementById(id).addEventListener('click', () => {
    basinSizeFilter = value;
    Object.values(basinButtons).forEach((bid) => document.getElementById(bid).classList.remove('active'));
    document.getElementById(id).classList.add('active');
    renderMap();
  });
});

['ifc_river', 'ifc_hydrostation', 'usgs'].forEach((sensorType) => {
  const slider = document.getElementById(`${sensorType}-min`);
  const sliderValue = document.getElementById(`${sensorType}-min-value`);
  const maxForType = Math.max(...Object.values(WIZARD_DATA.episodes).map(
    ep => Math.max(ep.n_sensors.county[sensorType], ep.n_sensors.huc8[sensorType])
  ));
  slider.max = maxForType;
  slider.addEventListener('input', () => {
    sensorMin[sensorType] = parseInt(slider.value, 10);
    sliderValue.textContent = sensorMin[sensorType];
    renderMap();
  });
});

const hwmSlider = document.getElementById('hwm-min');
const hwmSliderValue = document.getElementById('hwm-min-value');
hwmSlider.max = Math.max(...Object.values(WIZARD_DATA.episodes).map(ep => ep.n_hwms));
hwmSlider.addEventListener('input', () => {
  hwmMin = parseInt(hwmSlider.value, 10);
  hwmSliderValue.textContent = hwmMin;
  renderMap();
});

const femaSlider = document.getElementById('fema-min');
const femaSliderValue = document.getElementById('fema-min-value');
femaSlider.max = Math.max(...Object.values(WIZARD_DATA.episodes).map(ep => ep.n_fema_claims));
femaSlider.addEventListener('input', () => {
  femaMin = parseInt(femaSlider.value, 10);
  femaSliderValue.textContent = femaMin;
  renderMap();
});

const inundationCheckbox = document.getElementById('inundation-present');
inundationCheckbox.addEventListener('change', () => {
  inundationRequired = inundationCheckbox.checked;
  renderMap();
});

const showEventsCheckbox = document.getElementById('show-events');
showEventsCheckbox.addEventListener('change', () => {
  showEvents = showEventsCheckbox.checked;
  renderMap();
});

const precipSlider = document.getElementById('precip-min');
const precipSliderValue = document.getElementById('precip-min-value');
precipSlider.max = Math.ceil(Math.max(...Object.values(WIZARD_DATA.episodes).map(ep => ep.precip_total_mm)));
precipSlider.addEventListener('input', () => {
  precipMin = parseFloat(precipSlider.value);
  precipSliderValue.textContent = precipMin;
  renderMap();
});

const sviSlider = document.getElementById('svi-min');
const sviSliderValue = document.getElementById('svi-min-value');
sviSlider.addEventListener('input', () => {
  sviMin = parseFloat(sviSlider.value);
  sviSliderValue.textContent = sviMin.toFixed(2);
  renderMap();
});

const injuriesSlider = document.getElementById('injuries-min');
const injuriesSliderValue = document.getElementById('injuries-min-value');
injuriesSlider.max = Math.max(...Object.values(WIZARD_DATA.episodes).map(ep => ep.n_injuries));
injuriesSlider.addEventListener('input', () => {
  injuriesMin = parseInt(injuriesSlider.value, 10);
  injuriesSliderValue.textContent = injuriesMin;
  renderMap();
});

const deathsSlider = document.getElementById('deaths-min');
const deathsSliderValue = document.getElementById('deaths-min-value');
deathsSlider.max = Math.max(...Object.values(WIZARD_DATA.episodes).map(ep => ep.n_deaths));
deathsSlider.addEventListener('input', () => {
  deathsMin = parseInt(deathsSlider.value, 10);
  deathsSliderValue.textContent = deathsMin;
  renderMap();
});

const propDmgSlider = document.getElementById('propdmg-min');
const propDmgSliderValue = document.getElementById('propdmg-min-value');
propDmgSlider.max = Math.ceil(Math.max(...Object.values(WIZARD_DATA.episodes).map(ep => ep.damage_property_usd)));
propDmgSlider.addEventListener('input', () => {
  propDmgMin = parseFloat(propDmgSlider.value);
  propDmgSliderValue.textContent = '$' + propDmgMin.toLocaleString();
  renderMap();
});

const cropDmgSlider = document.getElementById('cropdmg-min');
const cropDmgSliderValue = document.getElementById('cropdmg-min-value');
cropDmgSlider.max = Math.ceil(Math.max(...Object.values(WIZARD_DATA.episodes).map(ep => ep.damage_crops_usd)));
cropDmgSlider.addEventListener('input', () => {
  cropDmgMin = parseFloat(cropDmgSlider.value);
  cropDmgSliderValue.textContent = '$' + cropDmgMin.toLocaleString();
  renderMap();
});

renderMap();

// ── Top 5 Leaderboards page ──────────────────────────────────────────────
let lbGranularity = 'county';

// "Documentation" = how much data exists for an episode across every
// source tracked (sensors + HWMs + FEMA claims + inundation layers).
// Rainfall isn't included -- it's a intensity measure, not a count of
// documentation artifacts, so it'd skew the composite toward wet episodes
// regardless of how much else was actually recorded about them.
function docScore(ep, gran) {
  return ep.n_sensors[gran].total + ep.n_hwms + ep.n_fema_claims + ep.n_inundation_layers;
}

const LEADERBOARD_CATEGORIES = [
  { label: 'IFC River Sensors', getValue: (ep) => ep.n_sensors[lbGranularity].ifc_river },
  { label: 'IFC Hydrostations', getValue: (ep) => ep.n_sensors[lbGranularity].ifc_hydrostation },
  { label: 'USGS Sensors', getValue: (ep) => ep.n_sensors[lbGranularity].usgs },
  { label: 'Total Sensors (all types)', getValue: (ep) => ep.n_sensors[lbGranularity].total },
  { label: 'USGS High-Water Marks', getValue: (ep) => ep.n_hwms },
  { label: 'FEMA NFIP Claims', getValue: (ep) => ep.n_fema_claims },
  { label: 'IFC Inundation Map Layers', getValue: (ep) => ep.n_inundation_layers },
  { label: 'Accumulated Rainfall', unit: ' mm', getValue: (ep) => ep.precip_total_mm },
  {
    label: 'Most Vulnerable Counties Touched (SVI)',
    getValue: (ep) => ep.counties.reduce((max, c) => Math.max(max, countySviLookup[c] ?? 0), 0),
    format: (v) => v.toFixed(2),
  },
  { label: 'Most Injuries (direct + indirect)', getValue: (ep) => ep.n_injuries },
  { label: 'Most Deaths (direct + indirect)', getValue: (ep) => ep.n_deaths },
  { label: 'Most Property Damage', getValue: (ep) => ep.damage_property_usd, format: (v) => '$' + v.toLocaleString() },
  { label: 'Most Crop Damage', getValue: (ep) => ep.damage_crops_usd, format: (v) => '$' + v.toLocaleString() },
  {
    label: 'Most Documented Episodes',
    subLabel: 'sum of sensors + HWMs + FEMA claims + inundation layers',
    getValue: (ep) => docScore(ep, lbGranularity),
  },
];

const countyNameLookup = {};
for (const f of WIZARD_DATA.county_geometry.features) countyNameLookup[f.properties.county_fips] = f.properties.county_name;
const huc8NameLookup = {};
for (const f of WIZARD_DATA.huc8_geometry.features) huc8NameLookup[f.properties.huc8] = f.properties.huc8_name;

function truncatedNames(ids, lookup, max = 2) {
  const names = ids.map((id) => lookup[id] || id);
  if (names.length <= max) return names.join(', ');
  return names.slice(0, max).join(', ') + ` +${names.length - max} more`;
}
function episodeLocationLabel(ep) {
  const counties = truncatedNames(ep.counties, countyNameLookup);
  const huc8s = truncatedNames(ep.huc8s, huc8NameLookup);
  return `${counties} &nbsp;|&nbsp; ${huc8s}`;
}

// Region-level "most documented" -- sums docScore across every episode
// touching that region, at that region's own fixed granularity (a
// watershed rollup should count huc8-matched sensors, not whatever the
// page-wide toggle happens to be set to, and vice versa for counties).
function computeRegionLeaderboard(regionKey, geometryFeatures, idField, nameField, fixedGranularity) {
  const scores = {};
  const episodeIdsByRegion = {};
  for (const [epId, ep] of Object.entries(WIZARD_DATA.episodes)) {
    const score = docScore(ep, fixedGranularity);
    for (const regionId of ep[regionKey]) {
      scores[regionId] = (scores[regionId] || 0) + score;
      (episodeIdsByRegion[regionId] = episodeIdsByRegion[regionId] || []).push(epId);
    }
  }
  const nameLookup = {};
  for (const f of geometryFeatures) nameLookup[f.properties[idField]] = f.properties[nameField];

  return Object.entries(scores)
    .map(([id, value]) => ({ id, name: nameLookup[id] || id, value, episodeIds: episodeIdsByRegion[id], episodeCount: episodeIdsByRegion[id].length }))
    .sort((a, b) => b.value - a.value)
    .slice(0, 5);
}

function renderLeaderboard() {
  const grid = document.getElementById('leaderboard-grid');
  grid.innerHTML = '';
  for (const cat of LEADERBOARD_CATEGORIES) {
    const ranked = Object.entries(WIZARD_DATA.episodes)
      .map(([id, ep]) => ({ id, ep, value: cat.getValue(ep) }))
      .sort((a, b) => b.value - a.value)
      .slice(0, 5);

    const rows = ranked.map((r, i) => `
      <tr>
        <td class="rank">${i + 1}</td>
        <td><b>${r.id}</b><br><span style="color:#888">${r.ep.event_types.join(', ')} — ${r.ep.begin_date.slice(0, 10)}</span><br><span style="color:#888">${episodeLocationLabel(r.ep)}</span></td>
        <td class="value">${cat.format ? cat.format(r.value) : r.value}${cat.unit || ''}</td>
      </tr>`).join('');

    const card = document.createElement('div');
    card.className = 'leaderboard-card';
    const subLabelHtml = cat.subLabel ? `<div class="lb-sublabel">Counts: ${cat.subLabel}</div>` : '';
    card.innerHTML = `<h3>${cat.label}</h3>${subLabelHtml}<table><tbody>${rows}</tbody></table>`;
    card.querySelectorAll('tbody tr').forEach((tr, i) => {
      tr.addEventListener('click', () => openEpisodeModal(ranked[i].id, lbGranularity));
    });
    grid.appendChild(card);
  }

  const regionBoards = [
    {
      label: 'Most Documented Watersheds (HUC-8)',
      subLabel: 'sum of (sensors + HWMs + FEMA claims + inundation layers) across every episode touching that watershed',
      key: 'huc8s', geo: WIZARD_DATA.huc8_geometry.features, idField: 'huc8', nameField: 'huc8_name', gran: 'huc8',
    },
    {
      label: 'Most Documented Counties',
      subLabel: 'sum of (sensors + HWMs + FEMA claims + inundation layers) across every episode touching that county',
      key: 'counties', geo: WIZARD_DATA.county_geometry.features, idField: 'county_fips', nameField: 'county_name', gran: 'county',
    },
  ];
  for (const rb of regionBoards) {
    const ranked = computeRegionLeaderboard(rb.key, rb.geo, rb.idField, rb.nameField, rb.gran);
    const rows = ranked.map((r, i) => `
      <tr>
        <td class="rank">${i + 1}</td>
        <td><b>${r.name}</b><br><span style="color:#888">${r.episodeCount} episode(s) touching it</span></td>
        <td class="value">${r.value}</td>
      </tr>`).join('');
    const card = document.createElement('div');
    card.className = 'leaderboard-card';
    card.innerHTML = `<h3>${rb.label}</h3><div class="lb-sublabel">Counts: ${rb.subLabel}</div><table><tbody>${rows}</tbody></table>`;
    card.querySelectorAll('tbody tr').forEach((tr, i) => {
      tr.addEventListener('click', () => openRegionModal(ranked[i].name, ranked[i].episodeIds, rb.gran));
    });
    grid.appendChild(card);
  }
}

function switchPage(activeName) {
  const pages = { map: 'page-map', leaderboard: 'page-leaderboard', download: 'page-download' };
  const navs = { map: 'nav-map', leaderboard: 'nav-leaderboard', download: 'nav-download' };
  for (const name of Object.keys(pages)) {
    document.getElementById(pages[name]).style.display = name === activeName ? (name === 'map' ? '' : 'block') : 'none';
    document.getElementById(navs[name]).classList.toggle('active', name === activeName);
  }
  if (activeName === 'map') map.invalidateSize();  // Leaflet mis-renders after its container was display:none
}
document.getElementById('nav-map').addEventListener('click', () => switchPage('map'));
document.getElementById('nav-leaderboard').addEventListener('click', () => {
  switchPage('leaderboard');
  renderLeaderboard();
});
document.getElementById('nav-download').addEventListener('click', () => {
  switchPage('download');
  renderDownloadWizard();
});
document.getElementById('lb-btn-county').addEventListener('click', () => {
  lbGranularity = 'county';
  document.getElementById('lb-btn-county').classList.add('active');
  document.getElementById('lb-btn-huc8').classList.remove('active');
  renderLeaderboard();
});
document.getElementById('lb-btn-huc8').addEventListener('click', () => {
  lbGranularity = 'huc8';
  document.getElementById('lb-btn-huc8').classList.add('active');
  document.getElementById('lb-btn-county').classList.remove('active');
  renderLeaderboard();
});

// ── Download Data: staged filter wizard ──────────────────────────────────
// Mirrors runoff's step-by-step pipeline shape: one filter group per stage
// instead of everything at once. Each stage shows a running "N of M match
// so far" count computed from every stage up to and including this one; if
// THIS stage's own setting is what drops the count to zero (i.e. the prior
// stage's count was > 0), the stage card is outlined red with a warning and
// a "closest episodes" list -- episodes passing every prior stage, ranked
// by how small a change to this stage's threshold(s) would let them in.
let dlGranularity = 'county';
let dlSensorAdvanced = false;
let dlSensorMin = { total: 0, usgs: 0, ifc_river: 0, ifc_hydrostation: 0 };
let dlHwmMin = 0;
let dlFemaMin = 0;
let dlPrecipMin = 0;
let dlInundationRequired = false;
let dlBasinSizeFilter = 'all';
let dlSviMin = 0;
let dlInjuriesMin = 0;
let dlDeathsMin = 0;
let dlPropDmgMin = 0;
let dlCropDmgMin = 0;
let dlStage = 0;  // 0-4 filter stages, 5 = results
let dlSlidersInitialized = false;

const DL_STAGE_TITLES = ['Region Type', 'Sensor Coverage', 'High-Water Marks & FEMA Claims', 'Rainfall', 'Everything Else'];

function dlBasinFilterActive() { return dlGranularity === 'huc8' && dlBasinSizeFilter !== 'all'; }
function dlBasinPassValue(ep) {
  return ep.huc8s.some((h) => {
    const area = huc8AreaLookup[h];
    if (area == null) return false;
    return dlBasinSizeFilter === 'above' ? area > BASIN_SIZE_THRESHOLD_SQKM : area <= BASIN_SIZE_THRESHOLD_SQKM;
  }) ? 1 : 0;
}
function dlSviFilterActive() { return dlGranularity === 'county' && dlSviMin > 0; }
function dlSviValue(ep) { return ep.counties.reduce((max, c) => Math.max(max, countySviLookup[c] ?? 0), 0); }

function dlStageFilters(stageIndex) {
  switch (stageIndex) {
    case 0:
      return [];
    case 1:
      return dlSensorAdvanced
        ? [
            { label: 'USGS Sensors', getValue: (ep) => ep.n_sensors[dlGranularity].usgs, threshold: () => dlSensorMin.usgs },
            { label: 'IFC River (Bridge) Sensors', getValue: (ep) => ep.n_sensors[dlGranularity].ifc_river, threshold: () => dlSensorMin.ifc_river },
            { label: 'IFC Hydrostations', getValue: (ep) => ep.n_sensors[dlGranularity].ifc_hydrostation, threshold: () => dlSensorMin.ifc_hydrostation },
          ]
        : [{ label: 'Total Sensors', getValue: (ep) => ep.n_sensors[dlGranularity].total, threshold: () => dlSensorMin.total }];
    case 2:
      return [
        { label: 'USGS High-Water Marks', getValue: (ep) => ep.n_hwms, threshold: () => dlHwmMin },
        { label: 'FEMA NFIP Claims', getValue: (ep) => ep.n_fema_claims, threshold: () => dlFemaMin },
      ];
    case 3:
      return [{ label: 'Accumulated Rainfall (mm)', getValue: (ep) => ep.precip_total_mm, threshold: () => dlPrecipMin }];
    case 4: {
      const fs = [
        { label: 'Injuries', getValue: (ep) => ep.n_injuries, threshold: () => dlInjuriesMin },
        { label: 'Deaths', getValue: (ep) => ep.n_deaths, threshold: () => dlDeathsMin },
        { label: 'Property Damage ($)', getValue: (ep) => ep.damage_property_usd, threshold: () => dlPropDmgMin },
        { label: 'Crop Damage ($)', getValue: (ep) => ep.damage_crops_usd, threshold: () => dlCropDmgMin },
      ];
      if (dlInundationRequired) fs.push({ label: 'Inundation Maps Available', getValue: (ep) => (ep.n_inundation_layers > 0 ? 1 : 0), threshold: () => 1 });
      if (dlBasinFilterActive()) fs.push({ label: 'Basin Size', getValue: dlBasinPassValue, threshold: () => 1 });
      if (dlSviFilterActive()) fs.push({ label: 'County SVI', getValue: dlSviValue, threshold: () => dlSviMin });
      return fs;
    }
    default:
      return [];
  }
}

function episodePassesDLUpTo(ep, uptoIndex) {
  for (let i = 0; i <= uptoIndex; i++) {
    for (const f of dlStageFilters(i)) {
      if (f.getValue(ep) < f.threshold()) return false;
    }
  }
  return true;
}
function computeDLIdsUpTo(uptoIndex) {
  if (uptoIndex < 0) return Object.keys(WIZARD_DATA.episodes);
  return Object.keys(WIZARD_DATA.episodes).filter((id) => episodePassesDLUpTo(WIZARD_DATA.episodes[id], uptoIndex));
}

function initDLSliders() {
  dlSlidersInitialized = true;
  const eps = Object.values(WIZARD_DATA.episodes);
  document.getElementById('dl-sensor-total').max = Math.max(...eps.map((ep) => Math.max(ep.n_sensors.county.total, ep.n_sensors.huc8.total)));
  document.getElementById('dl-sensor-usgs').max = Math.max(...eps.map((ep) => Math.max(ep.n_sensors.county.usgs, ep.n_sensors.huc8.usgs)));
  document.getElementById('dl-sensor-river').max = Math.max(...eps.map((ep) => Math.max(ep.n_sensors.county.ifc_river, ep.n_sensors.huc8.ifc_river)));
  document.getElementById('dl-sensor-hydro').max = Math.max(...eps.map((ep) => Math.max(ep.n_sensors.county.ifc_hydrostation, ep.n_sensors.huc8.ifc_hydrostation)));
  document.getElementById('dl-hwm').max = Math.max(...eps.map((ep) => ep.n_hwms));
  document.getElementById('dl-fema').max = Math.max(...eps.map((ep) => ep.n_fema_claims));
  document.getElementById('dl-precip').max = Math.ceil(Math.max(...eps.map((ep) => ep.precip_total_mm)));
  document.getElementById('dl-injuries').max = Math.max(...eps.map((ep) => ep.n_injuries));
  document.getElementById('dl-deaths').max = Math.max(...eps.map((ep) => ep.n_deaths));
  document.getElementById('dl-propdmg').max = Math.ceil(Math.max(...eps.map((ep) => ep.damage_property_usd)));
  document.getElementById('dl-cropdmg').max = Math.ceil(Math.max(...eps.map((ep) => ep.damage_crops_usd)));
}

function renderDownloadWizard() {
  if (!dlSlidersInitialized) initDLSliders();

  const progressEl = document.getElementById('dl-progress');
  progressEl.innerHTML = DL_STAGE_TITLES.map((_, i) => {
    const cls = dlStage === 5 || dlStage > i ? 'done' : (i === dlStage ? 'current' : '');
    return `<div class="dl-progress-step ${cls}"></div>`;
  }).join('');

  if (dlStage >= 5) {
    renderDLResults();
    return;
  }

  document.getElementById('dl-step-label').textContent = `Step ${dlStage + 1} of 5`;
  document.getElementById('dl-stage-title').textContent = DL_STAGE_TITLES[dlStage];
  for (let i = 0; i < 5; i++) document.getElementById(`dl-stage-${i}`).style.display = i === dlStage ? 'block' : 'none';
  document.getElementById('dl-stage-results').style.display = 'none';

  const priorIds = computeDLIdsUpTo(dlStage - 1);
  const currentIds = computeDLIdsUpTo(dlStage);
  document.querySelector('.dl-running-count').innerHTML = `<b id="dl-running-count">${currentIds.length}</b> of ${priorIds.length} episodes match so far`;

  const stageCard = document.getElementById(`dl-stage-${dlStage}`);
  const warningEl = stageCard.querySelector('.dl-warning');
  const isCulprit = priorIds.length > 0 && currentIds.length === 0;
  stageCard.classList.toggle('dl-culprit', isCulprit);

  if (isCulprit) {
    const filters = dlStageFilters(dlStage);
    const closest = priorIds.map((id) => {
      const ep = WIZARD_DATA.episodes[id];
      let shortfall = 0;
      for (const f of filters) {
        const val = f.getValue(ep), thr = f.threshold();
        if (val < thr) shortfall += (thr - val) / Math.max(thr, 1);
      }
      return { id, ep, shortfall };
    }).sort((a, b) => a.shortfall - b.shortfall).slice(0, 5);

    const closestHtml = closest.map((c) => {
      const details = filters.map((f) => `${f.label}: ${f.getValue(c.ep)} (need ${f.threshold()})`).join(', ');
      return `<div class="dl-closest-row"><span><b>${c.id}</b> — ${c.ep.event_types.join(', ')}</span><span style="color:#888">${details}</span></div>`;
    }).join('');

    warningEl.innerHTML = `<b>Nothing matches with this setting — something here needs to change.</b> Try lowering the value(s) below. Closest episodes:<div class="dl-closest-list">${closestHtml}</div>`;
  } else {
    warningEl.innerHTML = '';
  }

  document.getElementById('dl-btn-back').disabled = dlStage === 0;
  document.getElementById('dl-btn-next').textContent = dlStage === 4 ? 'See Results →' : 'Next →';
}

let dlSelectedIds = new Set();

function updateDLDownloadCount() {
  document.getElementById('dl-download-count').textContent = dlSelectedIds.size;
  document.getElementById('dl-download-btn').disabled = dlSelectedIds.size === 0;
}

function renderDLResults() {
  for (let i = 0; i < 5; i++) document.getElementById(`dl-stage-${i}`).style.display = 'none';
  document.getElementById('dl-stage-results').style.display = 'block';
  document.getElementById('dl-step-label').textContent = 'Final Selection';
  document.getElementById('dl-stage-title').textContent = 'Matching Episodes';

  const ids = computeDLIdsUpTo(4);
  document.querySelector('.dl-running-count').innerHTML = `<b id="dl-running-count">${ids.length}</b> of 135 episodes match all filters`;

  // Every fresh arrival at the results stage (including after Back ->
  // adjust a filter -> forward again) starts fully selected -- the
  // filters already did the narrowing; checkboxes are for trimming
  // further, not for remembering an unrelated prior selection.
  dlSelectedIds = new Set(ids);
  document.getElementById('dl-select-all').checked = true;
  updateDLDownloadCount();

  const body = document.getElementById('dl-results-body');
  body.innerHTML = ids.length
    ? ids.map((id) => {
        const ep = WIZARD_DATA.episodes[id];
        const counts = ep.n_sensors[dlGranularity];
        return `<tr>
          <td><input type="checkbox" class="dl-row-select" data-id="${id}" checked></td>
          <td><b>${id}</b></td>
          <td>${ep.begin_date.slice(0, 10)}</td>
          <td>${episodeLocationLabel(ep)}</td>
          <td>${counts.total}</td>
          <td>${ep.n_hwms}</td>
          <td>${ep.n_fema_claims}</td>
          <td>${ep.precip_total_mm}</td>
        </tr>`;
      }).join('')
    : '<tr><td colspan="8" style="color:#999; text-align:center;">No episodes match — go back and loosen a filter.</td></tr>';

  body.querySelectorAll('.dl-row-select').forEach((cb) => {
    cb.addEventListener('change', () => {
      if (cb.checked) dlSelectedIds.add(cb.dataset.id);
      else dlSelectedIds.delete(cb.dataset.id);
      document.getElementById('dl-select-all').checked = dlSelectedIds.size === ids.length;
      updateDLDownloadCount();
    });
  });

  document.getElementById('dl-btn-back').disabled = false;
  document.getElementById('dl-btn-next').textContent = 'Start Over';
}

document.getElementById('dl-select-all').addEventListener('change', (e) => {
  const checked = e.target.checked;
  document.querySelectorAll('.dl-row-select').forEach((cb) => {
    cb.checked = checked;
    if (checked) dlSelectedIds.add(cb.dataset.id);
    else dlSelectedIds.delete(cb.dataset.id);
  });
  updateDLDownloadCount();
});

document.getElementById('dl-btn-county').addEventListener('click', () => {
  dlGranularity = 'county';
  document.getElementById('dl-btn-county').classList.add('active');
  document.getElementById('dl-btn-huc8').classList.remove('active');
  renderDownloadWizard();
});
document.getElementById('dl-btn-huc8').addEventListener('click', () => {
  dlGranularity = 'huc8';
  document.getElementById('dl-btn-huc8').classList.add('active');
  document.getElementById('dl-btn-county').classList.remove('active');
  renderDownloadWizard();
});

document.getElementById('dl-sensor-total').addEventListener('input', (e) => {
  dlSensorMin.total = parseInt(e.target.value, 10);
  document.getElementById('dl-sensor-total-value').textContent = dlSensorMin.total;
  renderDownloadWizard();
});
document.getElementById('dl-sensor-advanced-toggle').addEventListener('change', (e) => {
  dlSensorAdvanced = e.target.checked;
  document.getElementById('dl-sensor-advanced').classList.toggle('show', dlSensorAdvanced);
  renderDownloadWizard();
});
document.getElementById('dl-sensor-usgs').addEventListener('input', (e) => {
  dlSensorMin.usgs = parseInt(e.target.value, 10);
  document.getElementById('dl-sensor-usgs-value').textContent = dlSensorMin.usgs;
  renderDownloadWizard();
});
document.getElementById('dl-sensor-river').addEventListener('input', (e) => {
  dlSensorMin.ifc_river = parseInt(e.target.value, 10);
  document.getElementById('dl-sensor-river-value').textContent = dlSensorMin.ifc_river;
  renderDownloadWizard();
});
document.getElementById('dl-sensor-hydro').addEventListener('input', (e) => {
  dlSensorMin.ifc_hydrostation = parseInt(e.target.value, 10);
  document.getElementById('dl-sensor-hydro-value').textContent = dlSensorMin.ifc_hydrostation;
  renderDownloadWizard();
});

document.getElementById('dl-hwm').addEventListener('input', (e) => {
  dlHwmMin = parseInt(e.target.value, 10);
  document.getElementById('dl-hwm-value').textContent = dlHwmMin;
  renderDownloadWizard();
});
document.getElementById('dl-fema').addEventListener('input', (e) => {
  dlFemaMin = parseInt(e.target.value, 10);
  document.getElementById('dl-fema-value').textContent = dlFemaMin;
  renderDownloadWizard();
});

document.getElementById('dl-precip').addEventListener('input', (e) => {
  dlPrecipMin = parseFloat(e.target.value);
  document.getElementById('dl-precip-value').textContent = dlPrecipMin;
  renderDownloadWizard();
});

document.getElementById('dl-inundation').addEventListener('change', (e) => {
  dlInundationRequired = e.target.checked;
  renderDownloadWizard();
});
const dlBasinButtons = { all: 'dl-basin-all', above: 'dl-basin-above', below: 'dl-basin-below' };
Object.entries(dlBasinButtons).forEach(([value, id]) => {
  document.getElementById(id).addEventListener('click', () => {
    dlBasinSizeFilter = value;
    Object.values(dlBasinButtons).forEach((bid) => document.getElementById(bid).classList.remove('active'));
    document.getElementById(id).classList.add('active');
    renderDownloadWizard();
  });
});
document.getElementById('dl-svi').addEventListener('input', (e) => {
  dlSviMin = parseFloat(e.target.value);
  document.getElementById('dl-svi-value').textContent = dlSviMin.toFixed(2);
  renderDownloadWizard();
});
document.getElementById('dl-injuries').addEventListener('input', (e) => {
  dlInjuriesMin = parseInt(e.target.value, 10);
  document.getElementById('dl-injuries-value').textContent = dlInjuriesMin;
  renderDownloadWizard();
});
document.getElementById('dl-deaths').addEventListener('input', (e) => {
  dlDeathsMin = parseInt(e.target.value, 10);
  document.getElementById('dl-deaths-value').textContent = dlDeathsMin;
  renderDownloadWizard();
});
document.getElementById('dl-propdmg').addEventListener('input', (e) => {
  dlPropDmgMin = parseFloat(e.target.value);
  document.getElementById('dl-propdmg-value').textContent = '$' + dlPropDmgMin.toLocaleString();
  renderDownloadWizard();
});
document.getElementById('dl-cropdmg').addEventListener('input', (e) => {
  dlCropDmgMin = parseFloat(e.target.value);
  document.getElementById('dl-cropdmg-value').textContent = '$' + dlCropDmgMin.toLocaleString();
  renderDownloadWizard();
});

document.getElementById('dl-btn-back').addEventListener('click', () => {
  if (dlStage > 0) { dlStage--; renderDownloadWizard(); }
});
document.getElementById('dl-btn-next').addEventListener('click', () => {
  if (dlStage < 5) { dlStage++; renderDownloadWizard(); }
  else { dlStage = 0; renderDownloadWizard(); }
});

// ── CSV / ZIP export -- entirely client-side, no server round-trip. Every
// field written here already lives in WIZARD_DATA (embedded in this page),
// so building a ZIP is just formatting what's already loaded. ────────────
function toCsv(rows, columns) {
  const esc = (v) => {
    if (v == null) return '';
    const s = String(v);
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  };
  const header = columns.map(esc).join(',');
  const lines = rows.map((row) => columns.map((c) => esc(row[c])).join(','));
  return [header, ...lines].join('\r\n');
}

// The real per-sensor time series + MRMS data is embedded further down as
// EMBEDDED_EPISODE_DATA_B64 -- gzip-compressed then base64-encoded at build
// time (raw JSON across all 135 episodes measured ~116MB; compressed+base64
// is what actually ships in this file). Decompressed lazily via the
// browser's native DecompressionStream on first download click, not at page
// load, so opening the page itself stays fast; cached after that so a
// second download doesn't pay the decompression cost again.
let _episodeDataCache = null;
async function getEpisodeDataPayload() {
  if (_episodeDataCache) return _episodeDataCache;
  const binaryStr = atob(EMBEDDED_EPISODE_DATA_B64);
  const bytes = new Uint8Array(binaryStr.length);
  for (let i = 0; i < binaryStr.length; i++) bytes[i] = binaryStr.charCodeAt(i);
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
  const text = await new Response(stream).text();
  _episodeDataCache = JSON.parse(text);
  return _episodeDataCache;
}

function addEpisodeToZip(zip, episodeId, granularity, episodeData) {
  const ep = WIZARD_DATA.episodes[episodeId];
  const folder = zip.folder(episodeId);

  const summaryRow = {
    episode_id: episodeId,
    event_types: ep.event_types.join('; '),
    begin_date: ep.begin_date,
    end_date: ep.end_date,
    counties: ep.counties.map((c) => countyNameLookup[c] || c).join('; '),
    huc8s: ep.huc8s.map((h) => huc8NameLookup[h] || h).join('; '),
    granularity_used: granularity,
    n_ifc_river_sensors: ep.n_sensors[granularity].ifc_river,
    n_ifc_hydrostations: ep.n_sensors[granularity].ifc_hydrostation,
    n_usgs_sensors: ep.n_sensors[granularity].usgs,
    n_sensors_total: ep.n_sensors[granularity].total,
    n_hwms: ep.n_hwms,
    n_fema_claims: ep.n_fema_claims,
    n_inundation_layers: ep.n_inundation_layers,
    precip_total_mm: ep.precip_total_mm,
    n_injuries: ep.n_injuries,
    n_deaths: ep.n_deaths,
    damage_property_usd: ep.damage_property_usd,
    damage_crops_usd: ep.damage_crops_usd,
  };
  folder.file('summary.csv', toCsv([summaryRow], Object.keys(summaryRow)));

  const hwmRows = ep.hwm_points.map((h) => ({ waterbody: h.waterbody, elev_ft: h.elev_ft, lat: h.lat, lon: h.lon }));
  folder.file('hwms.csv', toCsv(hwmRows, ['waterbody', 'elev_ft', 'lat', 'lon']));

  const femaRows = ep.fema_points.map((f) => ({ amount: f.amount, date: f.date, lat: f.lat, lon: f.lon }));
  folder.file('fema_claims.csv', toCsv(femaRows, ['amount', 'date', 'lat', 'lon']));

  // Real per-sensor time series (usgs_sensors/, ifc_hydrostations/,
  // ifc_river_sensors/) and mrms_precipitation/hourly_precip.csv --
  // union of county+huc8 matched sensors, already sliced to this
  // episode's padded window.
  const files = (episodeData && episodeData[episodeId]) || {};
  for (const [relPath, content] of Object.entries(files)) {
    folder.file(relPath, content);
  }
}

async function downloadEpisodesZip(episodeIds, granularity, zipFilename, readmeText, button) {
  if (!episodeIds.length) return;
  const originalHtml = button ? button.innerHTML : null;
  if (button) { button.disabled = true; button.textContent = 'Decompressing data…'; }
  try {
    const episodeData = await getEpisodeDataPayload();
    if (button) button.textContent = 'Building ZIP…';
    const zip = new JSZip();
    zip.file('README.txt', readmeText);
    if (episodeIds.length > 1) {
      const manifestRows = episodeIds.map((id) => {
        const ep = WIZARD_DATA.episodes[id];
        return {
          episode_id: id, begin_date: ep.begin_date.slice(0, 10), event_types: ep.event_types.join('; '),
          n_sensors: ep.n_sensors[granularity].total, n_hwms: ep.n_hwms, n_fema_claims: ep.n_fema_claims, precip_total_mm: ep.precip_total_mm,
        };
      });
      zip.file('manifest.csv', toCsv(manifestRows, ['episode_id', 'begin_date', 'event_types', 'n_sensors', 'n_hwms', 'n_fema_claims', 'precip_total_mm']));
    }
    for (const id of episodeIds) addEpisodeToZip(zip, id, granularity, episodeData);
    const blob = await zip.generateAsync({ type: 'blob' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = zipFilename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  } finally {
    if (button) { button.disabled = false; button.innerHTML = originalHtml; }
  }
}

document.getElementById('dl-download-btn').addEventListener('click', (e) => {
  const ids = Array.from(dlSelectedIds);
  const readme = `Iowa Flood Compendium -- filtered episode export\r\nGenerated: ${new Date().toISOString()}\r\nGranularity: ${dlGranularity}\r\nEpisodes selected: ${ids.length}\r\n\r\nEach episode folder contains:\r\n  summary.csv              -- episode metadata and headline counts\r\n  usgs_sensors/*.csv        -- one file per USGS sensor, time series over the episode window\r\n  ifc_hydrostations/*.csv   -- one file per IFC hydrostation, time series over the episode window\r\n  ifc_river_sensors/*.csv   -- one file per IFC river sensor, time series over the episode window\r\n  mrms_precipitation/hourly_precip.csv -- hourly MRMS rainfall (mean_mm, max_mm) over the episode window\r\n  hwms.csv                  -- matched USGS High-Water Mark locations\r\n  fema_claims.csv           -- matched FEMA NFIP claim locations\r\n\r\nSensors included are the union of county- and HUC8-matched sensors, so the same episode folder is complete regardless of which region view you were filtering under.\r\n`;
  downloadEpisodesZip(ids, dlGranularity, `iowa_flood_episodes_${ids.length}.zip`, readme, e.currentTarget);
});

// ── Leaderboard row click -> summary/download popup ──────────────────────
let lbModalTarget = null;

function openEpisodeModal(episodeId, granularity) {
  const ep = WIZARD_DATA.episodes[episodeId];
  lbModalTarget = { type: 'episode', id: episodeId, granularity };
  document.getElementById('lb-modal-title').textContent = episodeId;
  document.getElementById('lb-modal-body').innerHTML = `
    <div>${ep.event_types.join(', ')} — ${ep.begin_date.slice(0, 10)} to ${ep.end_date.slice(0, 10)}</div>
    <div class="lb-loc">${episodeLocationLabel(ep)}</div>
    Sensors: ${ep.n_sensors[granularity].total} &nbsp;|&nbsp; HWMs: ${ep.n_hwms} &nbsp;|&nbsp; FEMA claims: ${ep.n_fema_claims}<br>
    Inundation layers: ${ep.n_inundation_layers} &nbsp;|&nbsp; Rainfall: ${ep.precip_total_mm} mm<br>
    Injuries: ${ep.n_injuries} &nbsp;|&nbsp; Deaths: ${ep.n_deaths}<br>
    Property damage: $${ep.damage_property_usd.toLocaleString()} &nbsp;|&nbsp; Crop damage: $${ep.damage_crops_usd.toLocaleString()}
  `;
  document.getElementById('lb-modal-overlay').style.display = 'flex';
}

function openRegionModal(regionName, episodeIds, granularity) {
  lbModalTarget = { type: 'region', name: regionName, ids: episodeIds, granularity };
  document.getElementById('lb-modal-title').textContent = regionName;
  const totalDoc = episodeIds.reduce((sum, id) => sum + docScore(WIZARD_DATA.episodes[id], granularity), 0);
  document.getElementById('lb-modal-body').innerHTML = `
    <div>${episodeIds.length} episode(s) touching this ${granularity === 'huc8' ? 'watershed' : 'county'}</div>
    <div class="lb-loc">Combined documentation score: ${totalDoc}</div>
    ${episodeIds.slice(0, 8).map((id) => `<div>&bull; <b>${id}</b> — ${WIZARD_DATA.episodes[id].event_types.join(', ')} (${WIZARD_DATA.episodes[id].begin_date.slice(0, 10)})</div>`).join('')}
    ${episodeIds.length > 8 ? `<div style="color:#888;">+${episodeIds.length - 8} more</div>` : ''}
  `;
  document.getElementById('lb-modal-overlay').style.display = 'flex';
}

document.getElementById('lb-modal-close').addEventListener('click', () => {
  document.getElementById('lb-modal-overlay').style.display = 'none';
});
document.getElementById('lb-modal-overlay').addEventListener('click', (e) => {
  if (e.target.id === 'lb-modal-overlay') document.getElementById('lb-modal-overlay').style.display = 'none';
});
document.getElementById('lb-modal-download').addEventListener('click', (e) => {
  if (!lbModalTarget) return;
  if (lbModalTarget.type === 'episode') {
    const readme = `Iowa Flood Compendium -- single-episode export\r\nEpisode: ${lbModalTarget.id}\r\nGenerated: ${new Date().toISOString()}\r\nGranularity: ${lbModalTarget.granularity}\r\n\r\nIncludes real sensor time series (usgs_sensors/, ifc_hydrostations/, ifc_river_sensors/), hourly MRMS rainfall (mrms_precipitation/), HWM and FEMA claim locations, and a summary.csv.\r\n`;
    downloadEpisodesZip([lbModalTarget.id], lbModalTarget.granularity, `${lbModalTarget.id}.zip`, readme, e.currentTarget);
  } else {
    const readme = `Iowa Flood Compendium -- region export\r\nRegion: ${lbModalTarget.name}\r\nGenerated: ${new Date().toISOString()}\r\nGranularity: ${lbModalTarget.granularity}\r\nEpisodes included: ${lbModalTarget.ids.length}\r\n\r\nEach episode folder includes real sensor time series (usgs_sensors/, ifc_hydrostations/, ifc_river_sensors/), hourly MRMS rainfall (mrms_precipitation/), HWM and FEMA claim locations, and a summary.csv.\r\n`;
    downloadEpisodesZip(lbModalTarget.ids, lbModalTarget.granularity, `${lbModalTarget.name.replace(/\s+/g, '_')}_episodes.zip`, readme, e.currentTarget);
  }
});
</script>
<!-- impacts-addon -->
<script>
/* Impacts add-on for the Iowa Flood Compendium wizard.
 * Injected by augment_site.py. Requires WIZARD_DATA episodes to carry
 * n_impacts, n_impacts_crowd, has_crowdsource, impact_points
 * (added by the same script). Additive: wraps episodePassesFilter and
 * recompute instead of editing them, so the page works with or without it.
 *
 * v2 (round 4): one-click presets, impact-type chips for the map layer,
 * live result counts, richer popups (quantity, confidence), reset link,
 * link to the full dataset. */
(function () {
  if (window.__IMPACTS_ADDON__) return;
  window.__IMPACTS_ADDON__ = true;
  var eps = (typeof WIZARD_DATA !== 'undefined' && WIZARD_DATA.episodes) || {};
  var epIds = Object.keys(eps);
  if (!epIds.length || typeof eps[epIds[0]].n_impacts === 'undefined') return;

  var maxImpacts = 0, nCrowd = 0, nRecords = 0, nCrowdRecords = 0;
  var typeTotals = {};
  epIds.forEach(function (id) {
    var e = eps[id];
    maxImpacts = Math.max(maxImpacts, e.n_impacts || 0);
    nRecords += (e.n_impacts || 0);
    nCrowdRecords += (e.n_impacts_crowd || 0);
    if (e.has_crowdsource) nCrowd += 1;
    (e.impact_points || []).forEach(function (p) {
      typeTotals[p.t] = (typeTotals[p.t] || 0) + 1;
    });
  });

  /* episode -> derived flags for presets */
  var STREET_TYPES = { road_flooded: 1, road_closed: 1, bridge_damaged: 1 };
  epIds.forEach(function (id) {
    var e = eps[id], street = false, severe = false;
    (e.impact_points || []).forEach(function (p) {
      if (STREET_TYPES[p.t]) street = true;
      if (p.sv >= 3) severe = true;
    });
    e.__has_street = street;
    e.__has_severe = severe;
  });

  var state = {
    show: true, crowdOnly: false, min: 0,
    street: false, severe: false,
    types: {}            /* impact_type -> false when hidden on the map */
  };
  var SEV_COLORS = ['#9aa0a6', '#f2c14e', '#e8710a', '#d93025'];
  var SEV_NAMES = ['overbank', 'minor', 'moderate', 'major'];
  var TYPE_ORDER = ['road_flooded', 'road_closed', 'bridge_damaged', 'rescue',
    'evacuation', 'home_flooded', 'business_flooded', 'agriculture',
    'infrastructure', 'injury', 'fatality', 'river_overbank', 'other'];

  function esc(t) {
    return String(t == null ? '' : t)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  /* ---------- panel UI ---------- */
  var section = document.createElement('div');
  section.className = 'section';
  section.id = 'impacts-section';
  var chipCss = 'display:inline-block;margin:2px 4px 2px 0;padding:2px 8px;' +
    'border:1px solid #bbb;border-radius:12px;cursor:pointer;font-size:11px;' +
    'background:#fff;user-select:none';
  var html =
    '<h3 style="margin:0 0 6px">Flood impact reports</h3>' +
    '<div class="legend" style="margin-bottom:6px">' + nRecords +
    ' geo-referenced impact records (' + (nRecords - nCrowdRecords) +
    ' from NOAA storm narratives, ' + nCrowdRecords +
    ' crowdsourced from local news and agencies): flooded and closed ' +
    'streets, rescues, evacuations, flooded homes.</div>' +
    '<div style="margin:4px 0 6px" id="impacts-presets">' +
    '<span class="imp-chip" data-preset="all" style="' + chipCss + '">All episodes</span>' +
    '<span class="imp-chip" data-preset="crowd" style="' + chipCss + '">Crowdsourced (' + nCrowd + ')</span>' +
    '<span class="imp-chip" data-preset="street" style="' + chipCss + '">Street-level</span>' +
    '<span class="imp-chip" data-preset="severe" style="' + chipCss + '">Severe (3)</span>' +
    '</div>' +
    '<div class="checkbox-row"><input type="checkbox" id="impacts-show" checked>' +
    '<label for="impacts-show" style="margin-bottom:0">Show impact reports on the map</label></div>' +
    '<div class="checkbox-row"><input type="checkbox" id="impacts-crowd-only">' +
    '<label for="impacts-crowd-only" style="margin-bottom:0">Only episodes with crowdsourced reports</label></div>' +
    '<label for="impacts-min" style="display:block;margin-top:6px">Minimum impact records: ' +
    '<span id="impacts-min-val">0</span></label>' +
    '<input type="range" id="impacts-min" min="0" max="' + maxImpacts +
    '" value="0" step="1" style="width:100%">' +
    '<details style="margin-top:6px"><summary style="cursor:pointer;font-size:12px">' +
    'Impact types on the map</summary><div id="impacts-type-chips" style="margin-top:4px">';
  TYPE_ORDER.forEach(function (t) {
    if (!typeTotals[t]) return;
    html += '<span class="imp-type" data-type="' + t + '" style="' + chipCss +
      '">' + t.replace(/_/g, ' ') + ' (' + typeTotals[t] + ')</span>';
  });
  html += '</div></details>' +
    '<div class="legend" id="impacts-counts" style="margin-top:6px;font-weight:bold"></div>' +
    '<div class="legend" id="impacts-legend" style="margin-top:4px"></div>' +
    '<div class="legend" style="margin-top:4px">' +
    '<a href="#" id="impacts-reset" style="margin-right:10px">Reset impact filters</a>' +
    '<a href="https://github.com/CorinFuchtman/IA_Flood_Compendium/tree/main/AFinal/impacts" ' +
    'target="_blank" rel="noopener">Full dataset + schema</a></div>';
  section.innerHTML = html;

  var legendHtml = 'Severity: ';
  for (var s = 0; s <= 3; s++) {
    legendHtml += '<span style="display:inline-block;width:10px;height:10px;' +
      'border-radius:50%;background:' + SEV_COLORS[s] +
      ';margin:0 3px 0 8px"></span>' + s + ' ' + SEV_NAMES[s];
  }
  legendHtml += '<br><span style="display:inline-block;width:10px;height:10px;' +
    'border-radius:50%;background:#fff;border:2px solid #1a4d8f;margin-right:4px">' +
    '</span>ring = crowdsourced (news or agency), no ring = NOAA narrative';
  var inund = document.getElementById('inundation-present');
  var host = inund ? inund.closest('.section') : null;
  if (host && host.parentNode) {
    host.parentNode.insertBefore(section, host.nextSibling);
  } else {
    var panel = document.getElementById('panel');
    if (panel) panel.appendChild(section);
  }
  document.getElementById('impacts-legend').innerHTML = legendHtml;

  /* ---------- filter wrap ---------- */
  function impactsPass(ep) {
    if ((ep.n_impacts || 0) < state.min) return false;
    if (state.crowdOnly && !ep.has_crowdsource) return false;
    if (state.street && !ep.__has_street) return false;
    if (state.severe && !ep.__has_severe) return false;
    return true;
  }
  try {
    var origFilter = episodePassesFilter;
    episodePassesFilter = function (ep) {
      return origFilter(ep) && impactsPass(ep);
    };
  } catch (e) { console.warn('impacts addon: filter wrap failed', e); }

  /* ---------- map layer ---------- */
  var impactsLayer = null;
  try { impactsLayer = L.layerGroup().addTo(map); }
  catch (e) { console.warn('impacts addon: no map', e); }

  function updateCounts(nEp, nPts) {
    var el = document.getElementById('impacts-counts');
    if (el) el.textContent = nEp + ' of ' + epIds.length +
      ' episodes pass filters; ' + nPts + ' impact points on map';
  }

  function renderImpacts() {
    if (impactsLayer) impactsLayer.clearLayers();
    var nEp = 0, nPts = 0;
    epIds.forEach(function (id) {
      var ep = eps[id];
      var ok = true;
      try { ok = episodePassesFilter(ep); } catch (e) {}
      if (!ok) return;
      nEp += 1;
      if (!state.show || !ep.impact_points || !ep.impact_points.length) return;
      ep.impact_points.forEach(function (p) {
        if (state.types[p.t] === false) return;
        nPts += 1;
        if (!impactsLayer) return;
        var crowd = p.src !== 'noaa_narrative';
        var m = L.circleMarker([p.lat, p.lon], {
          radius: crowd ? 6 : 4.5,
          fillColor: SEV_COLORS[p.sv] || SEV_COLORS[1],
          fillOpacity: 0.85,
          color: crowd ? '#1a4d8f' : '#ffffff',
          weight: crowd ? 2.5 : 1,
          pane: 'eventsPane'
        });
        m.bindTooltip(esc(p.t.replace(/_/g, ' ')) + ' | ' + esc(p.d) +
          ' | ' + esc(p.dt), { sticky: true });
        var extra = '';
        if (p.q) extra += '<br>Quantity: ' + esc(p.q);
        if (p.cf) extra += (extra ? ' | ' : '<br>') + 'Confidence ' + esc(p.cf);
        var pop = '<div style="max-width:280px"><b>' +
          esc(p.t.replace(/_/g, ' ')) + '</b> (severity ' + p.sv +
          ')<br><i>' + esc(p.d) + '</i> <br>' + esc(p.dt) +
          ' | episode ' + esc(id) + extra + '<br>' + esc(p.tx || '') +
          (p.u ? '<br><a href="' + encodeURI(p.u) +
            '" target="_blank" rel="noopener">source</a>'
            : '<br>Source: NOAA Storm Events narrative') + '</div>';
        m.bindPopup(pop);
        impactsLayer.addLayer(m);
      });
    });
    updateCounts(nEp, nPts);
  }

  try {
    var origRecompute = recompute;
    recompute = function () { origRecompute(); renderImpacts(); };
  } catch (e) { console.warn('impacts addon: recompute wrap failed', e); }

  /* ---------- impacts.csv inside per-episode download ZIPs ---------- */
  function impactsCsvFor(epId) {
    var ep = eps[epId];
    if (!ep || !ep.impact_points || !ep.impact_points.length) return null;
    var q = function (v) {
      v = String(v == null ? '' : v);
      return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
    };
    var rows = ['impact_type,severity,confidence,source_type,date,lat,lon,location,quantity,text,source_url'];
    ep.impact_points.forEach(function (p) {
      rows.push([p.t, p.sv, p.cf || '', p.src, p.dt, p.lat, p.lon, p.d,
        p.q || '', p.tx || '', p.u || 'NOAA Storm Events narrative']
        .map(q).join(','));
    });
    return rows.join('\n');
  }
  try {
    if (typeof addEpisodeToZip === 'function') {
      var origAddZip = addEpisodeToZip;
      addEpisodeToZip = function (zip, episodeId, granularity, episodeData) {
        var r = origAddZip(zip, episodeId, granularity, episodeData);
        try {
          var csv = impactsCsvFor(episodeId);
          if (csv) zip.file('episode_' + episodeId + '/impacts.csv', csv);
        } catch (e) { console.warn('impacts addon: zip append failed', e); }
        return r;
      };
    }
  } catch (e) { console.warn('impacts addon: download wrap failed', e); }

  /* ---------- listeners ---------- */
  function refresh() { try { recompute(); } catch (e) { renderImpacts(); }
    try { renderMap(); } catch (e) {} }

  function syncUi() {
    document.getElementById('impacts-show').checked = state.show;
    document.getElementById('impacts-crowd-only').checked = state.crowdOnly;
    var slider = document.getElementById('impacts-min');
    slider.value = state.min;
    document.getElementById('impacts-min-val').textContent = state.min;
    var presets = { all: !state.crowdOnly && !state.street && !state.severe && state.min === 0,
      crowd: state.crowdOnly, street: state.street, severe: state.severe };
    Array.prototype.forEach.call(
      document.querySelectorAll('#impacts-presets .imp-chip'), function (c) {
        var on = presets[c.getAttribute('data-preset')];
        c.style.background = on ? '#1a4d8f' : '#fff';
        c.style.color = on ? '#fff' : '#000';
        c.style.borderColor = on ? '#1a4d8f' : '#bbb';
      });
    Array.prototype.forEach.call(
      document.querySelectorAll('#impacts-type-chips .imp-type'), function (c) {
        var off = state.types[c.getAttribute('data-type')] === false;
        c.style.background = off ? '#eee' : '#fff';
        c.style.color = off ? '#999' : '#000';
        c.style.textDecoration = off ? 'line-through' : 'none';
      });
  }

  document.getElementById('impacts-show').addEventListener('change',
    function () { state.show = this.checked; renderImpacts(); });
  document.getElementById('impacts-crowd-only').addEventListener('change',
    function () { state.crowdOnly = this.checked; syncUi(); refresh(); });
  document.getElementById('impacts-min').addEventListener('input',
    function () {
      state.min = parseInt(this.value, 10) || 0;
      syncUi(); refresh();
    });
  Array.prototype.forEach.call(
    document.querySelectorAll('#impacts-presets .imp-chip'), function (c) {
      c.addEventListener('click', function () {
        var p = c.getAttribute('data-preset');
        if (p === 'all') { state.crowdOnly = state.street = state.severe = false; state.min = 0; }
        if (p === 'crowd') state.crowdOnly = !state.crowdOnly;
        if (p === 'street') state.street = !state.street;
        if (p === 'severe') state.severe = !state.severe;
        syncUi(); refresh();
      });
    });
  Array.prototype.forEach.call(
    document.querySelectorAll('#impacts-type-chips .imp-type'), function (c) {
      c.addEventListener('click', function () {
        var t = c.getAttribute('data-type');
        state.types[t] = state.types[t] === false ? true : false;
        syncUi(); renderImpacts();
      });
    });
  document.getElementById('impacts-reset').addEventListener('click',
    function (ev) {
      ev.preventDefault();
      state.crowdOnly = state.street = state.severe = false;
      state.min = 0; state.show = true; state.types = {};
      syncUi(); refresh();
    });

  syncUi();
  try { recompute(); } catch (e) { renderImpacts(); }
})();

</script>
</body>
</html>
"""


def build():
    data = json.loads(DATA_PATH.read_text())
    embedded_b64 = EMBEDDED_EPISODE_DATA_PATH.read_text(encoding='ascii') if EMBEDDED_EPISODE_DATA_PATH.exists() else ''
    if not embedded_b64:
        print(f'[!] {EMBEDDED_EPISODE_DATA_PATH.name} not found -- downloads will have no real time-series data until build_embedded_episode_data.py has been run.')
    html = HTML_TEMPLATE.replace('__WIZARD_DATA_JSON__', json.dumps(data))
    html = html.replace('__EMBEDDED_EPISODE_DATA_B64__', embedded_b64)
    OUT_PATH.write_text(html, encoding='utf-8')
    print(f'[OK] wrote {OUT_PATH} ({OUT_PATH.stat().st_size / 1e6:.2f} MB)')


if __name__ == '__main__':
    build()
