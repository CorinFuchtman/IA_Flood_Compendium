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
EMBEDDED_MRMS_GRIDS_PATH = BASE_DIR / 'choropleth' / 'embedded_mrms_grids.b64.txt'
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
  .detail-legend .circ { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:6px; vertical-align:middle; border:2px dashed #0868ac; background:rgba(67,162,202,0.25); }
  .detail-hint { font-size:11px; color:#888; margin-top:6px; font-style:italic; }
  .mrms-scale-bar { display:inline-block; width:120px; height:10px; vertical-align:middle; border:1px solid #999; border-radius:2px; }
  /* Show the MRMS overlay's actual per-cell grid instead of the browser's
     default smooth (bilinear) upscaling when the map is zoomed past the
     grid's native ~1km resolution. */
  .leaflet-mrms-pane img { image-rendering: -moz-crisp-edges; image-rendering: -webkit-crisp-edges; image-rendering: pixelated; }
  .events-year-legend { background:rgba(255,255,255,0.92); border-radius:6px; padding:8px 10px; box-shadow:0 1px 4px rgba(0,0,0,0.3); font-size:11px; color:#333; line-height:1.5; }
  .events-year-legend b { font-family:var(--font-condensed); font-size:11px; display:block; margin-bottom:3px; }
  .events-year-legend .dot { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:6px; vertical-align:middle; border:1px solid rgba(0,0,0,0.4); }
  .sensor-chart-popup { font-size:12px; min-width:220px; }
  .sensor-chart-popup b { font-size:12px; }
  .sensor-chart-popup .sc-sub { color:#888; font-size:10px; display:block; margin-bottom:2px; }
  .sensor-chart-popup .sc-loading, .sensor-chart-popup .sc-empty, .sensor-chart-popup .sc-error { color:#888; font-style:italic; }
  .sensor-chart-popup .sc-error { color:#c0392b; }
  .sensor-chart-popup .sc-stats { font-size:11px; color:#333; margin:2px 0 6px 0; }
  .sensor-chart-popup .sc-stats b { color:#111; }
  .sensor-chart-popup .sc-legend { margin-top:2px; }
  .sensor-chart-popup .sc-legend-item { display:inline-flex; align-items:center; margin-right:8px; font-size:10px; color:#555; }
  .sensor-chart-popup .sc-dot { width:8px; height:8px; border-radius:50%; display:inline-block; margin-right:3px; }
  h1 { font-family:var(--font-display); font-size:22px; margin:0 0 6px 0; font-weight:700; letter-spacing:0.02em; text-transform:uppercase; color:var(--uiowa-black); border-bottom:3px solid var(--uiowa-gold); display:inline-block; padding-bottom:4px; }
  .subtitle { font-family:var(--font-accent); font-size:12px; color:#666; margin-bottom:16px; margin-top:8px; font-style:italic; }
  .section { margin-bottom:20px; padding-bottom:16px; border-bottom:1px solid #e0e0e0; }
  .section:last-child { border-bottom:none; }
  .filter-group { margin-bottom:20px; padding-bottom:4px; border-bottom:1px solid #e0e0e0; }
  .filter-group summary { font-family:var(--font-condensed); font-size:13px; font-weight:700; color:var(--uiowa-black); cursor:pointer; padding:4px 0 10px 0; letter-spacing:0.01em; list-style:none; text-transform:uppercase; }
  .filter-group summary::-webkit-details-marker { display:none; }
  .filter-group summary::before { content:'▸ '; color:var(--uiowa-gold-dark); }
  .filter-group[open] summary::before { content:'▾ '; }
  .filter-group .section { padding-left:2px; }
  .filter-group .section:last-child { border-bottom:none; margin-bottom:4px; padding-bottom:0; }
  .search-input { width:100%; box-sizing:border-box; padding:7px 10px; border:1px solid #ccc; border-radius:6px; font-size:13px; font-family:var(--font-body); }
  .search-input:focus { outline:none; border-color:var(--uiowa-gold-dark); box-shadow:0 0 0 2px rgba(255,205,0,0.25); }
  .search-result-card { margin-top:8px; background:#fffbe6; border:1px solid #f0dfa0; border-radius:6px; padding:8px 10px; font-size:12px; }
  .search-result-card b { font-size:13px; }
  .search-result-card .search-download-btn { margin-top:6px; padding:6px 14px; font-size:12px; }
  .search-not-found { color:#c0392b; font-size:12px; margin-top:6px; }
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

  /* Info icon + tooltip -- replaces always-visible caption paragraphs under
     filter controls. Gold-on-black badge matches the toggle-group active
     state and detail-nav buttons elsewhere on the page, so it reads as part
     of the same Hawkeye-branded control language rather than a generic
     help icon. */
  .info-wrap { position:relative; display:inline-flex; vertical-align:middle; margin-left:5px; }
  .info-icon {
    display:inline-flex; align-items:center; justify-content:center;
    width:15px; height:15px; border-radius:50%; box-sizing:border-box;
    background:var(--uiowa-gold); color:var(--uiowa-black); border:none;
    font-family:var(--font-display); font-size:11px; font-weight:700; line-height:1;
    cursor:help; padding:0;
  }
  .info-icon:hover, .info-icon:focus { background:var(--uiowa-gold-dark); outline:none; }
  /* position:fixed (not absolute) + JS-computed left/top (see positionInfoTip
     below) -- the sidebar panel's overflow-y:auto implicitly makes its
     overflow-x 'auto' too (CSS spec: one non-visible overflow axis forces
     the other off 'visible'), so an absolutely-positioned tooltip wide
     enough to spill past the 320px panel forced a horizontal scrollbar.
     Fixed positioning escapes that containing block entirely. */
  .info-tip {
    display:none; position:fixed; z-index:1200; width:230px;
    background:var(--uiowa-black-soft); color:#f2f2f2; font-family:var(--font-body);
    font-size:11px; font-weight:400; line-height:1.5; letter-spacing:normal; text-transform:none;
    border-radius:6px; border-top:2px solid var(--uiowa-gold); padding:9px 11px;
    box-shadow:0 4px 16px rgba(0,0,0,0.35);
  }
  .info-wrap:hover .info-tip, .info-wrap:focus-within .info-tip { display:block; }
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
  #page-leaderboard { scroll-behavior:smooth; }
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
  .lb-quicknav { position:sticky; top:0; z-index:10; background:#fafaf7; padding:4px 0 14px 0; margin-bottom:12px; border-bottom:1px solid #e0e0e0; }
  .lb-quicknav-row { display:flex; flex-wrap:wrap; align-items:center; gap:8px; }
  .lb-quicknav-row + .lb-quicknav-row { margin-top:10px; }
  .lb-quicknav-row .lb-quicknav-label { font-size:12px; font-weight:600; color:#333; margin-right:2px; }
  .lb-quicknav-row a { font-family:var(--font-condensed); font-size:12px; font-weight:600; color:var(--uiowa-black); background:#fff; border:1px solid #ddd; border-radius:20px; padding:5px 13px; text-decoration:none; transition:background 0.15s, border-color 0.15s; }
  .lb-quicknav-row a:hover { background:var(--uiowa-gold); border-color:var(--uiowa-gold-dark); }
  .lb-section { margin-bottom:36px; scroll-margin-top:96px; }
  .lb-section-title { font-family:var(--font-display); font-size:20px; color:var(--uiowa-black); text-transform:uppercase; letter-spacing:0.02em; margin:0 0 4px 0; padding-bottom:6px; border-bottom:3px solid var(--uiowa-gold); display:inline-block; }
  .lb-section-desc { font-size:12px; color:#888; margin:4px 0 16px 0; font-style:italic; }

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
  .lb-modal-secondary-btn { font-family:var(--font-condensed); font-weight:700; background:#fff; color:var(--uiowa-black); border:2px solid var(--uiowa-gold); border-radius:6px; padding:8px 18px; font-size:13px; cursor:pointer; margin-bottom:12px; margin-left:8px; }
  .lb-modal-secondary-btn:hover { background:#fffbe6; }

  /* ── Leaderboard row click -> summary/download popup ── */
  .leaderboard-card tbody tr { cursor:pointer; }
  .leaderboard-card tbody tr:hover { background:#fffbe6; }
  .lb-modal-overlay { display:none; position:fixed; inset:0; background:rgba(0,0,0,0.5); z-index:2000; align-items:center; justify-content:center; }
  .lb-modal { background:#fff; border-radius:10px; border-top:5px solid var(--uiowa-gold); width:380px; max-width:90vw; max-height:80vh; overflow-y:auto; padding:22px; position:relative; box-shadow:0 10px 40px rgba(0,0,0,0.3); }
  .lb-modal h3 { font-family:var(--font-display); font-size:20px; margin:0 22px 10px 0; color:var(--uiowa-black); text-transform:uppercase; }
  .lb-modal-body { font-size:13px; line-height:1.7; color:#333; }
  .lb-modal-body .lb-loc { color:#888; margin:4px 0 10px 0; }
  .flash-toggle-btn { background:none; border:1px solid #ccc; border-radius:5px; padding:2px 8px; font-size:12px; color:#333; cursor:pointer; margin-left:4px; }
  .flash-toggle-btn:hover { background:#f2f2f2; }
  .flash-table-wrap { margin-top:8px; max-height:220px; overflow:auto; border:1px solid #e2e2e2; border-radius:6px; }
  table.flash-table { border-collapse:collapse; font-size:11.5px; width:100%; }
  table.flash-table th { position:sticky; top:0; background:#f7f5ef; text-align:right; padding:4px 7px; border-bottom:1px solid #ddd; white-space:nowrap; }
  table.flash-table th:first-child, table.flash-table td:first-child { text-align:left; }
  table.flash-table td { text-align:right; padding:3px 7px; border-bottom:1px solid #f0f0f0; white-space:nowrap; }
  table.flash-table td.flash-severe { background:var(--uiowa-gold); font-weight:700; color:var(--uiowa-black); }
  table.flash-table td.flash-empty { color:#bbb; }
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
      <label>Search</label>
      <input type="text" id="episode-search" class="search-input" placeholder="Episode ID (e.g. 191899_0)" list="episode-search-list" autocomplete="off">
      <datalist id="episode-search-list"></datalist>
      <div id="episode-search-status" class="legend"></div>

      <input type="text" id="region-search" class="search-input" placeholder="County or HUC-8 watershed name" list="region-search-list" autocomplete="off" style="margin-top:10px;">
      <datalist id="region-search-list"></datalist>
      <div id="region-search-status" class="legend"></div>
    </div>

    <div class="section">
      <label>Region view</label>
      <div class="toggle-group">
        <button id="btn-county" class="active">County</button>
        <button id="btn-huc8">HUC-8 watershed</button>
      </div>
    </div>

    <div class="section">
      <label>Basin size (HUC-8 view only)<span class="info-wrap" tabindex="0"><span class="info-icon">i</span><span class="info-tip">23 of 56 Iowa-touching HUC-8 basins fall below the 3,000 km&sup2; threshold; 33 are above it. Has no effect in County view. Source: USGS National Map, Watershed Boundary Dataset (HUC-8).</span></span></label>
      <div class="toggle-group">
        <button id="btn-basin-all" class="active">All</button>
        <button id="btn-basin-above">Above 3,000 km&sup2;</button>
        <button id="btn-basin-below">Below 3,000 km&sup2;</button>
      </div>
    </div>

    <!-- ── Toggles ── -->
    <div class="section">
      <div class="checkbox-row">
        <input type="checkbox" id="show-events">
        <label for="show-events" style="margin-bottom:0">Show raw NOAA event locations</label>
        <span class="info-wrap" tabindex="0"><span class="info-icon">i</span><span class="info-tip">Plots each reported event as a line from its start to end location, colored by year. Reflects episodes currently matching your filters. Source: NOAA Storm Events Database.</span></span>
        <input type="checkbox" id="show-event-footprints" style="margin-left:14px;">
        <label for="show-event-footprints" style="margin-bottom:0">Show episode footprint</label>
        <span class="info-wrap" tabindex="0"><span class="info-icon">i</span><span class="info-tip">Draws one buffer circle per episode, sized to enclose all of its reported event points and colored by year. Source: NOAA Storm Events Database.</span></span>
      </div>
      <div class="legend" id="events-status" style="font-weight:600; color:#333;"></div>
      <div class="toggle-group" id="events-year-toggle" style="margin-top:8px;"></div>
    </div>

    <div class="section">
      <div class="checkbox-row">
        <input type="checkbox" id="inundation-present">
        <label for="inundation-present" style="margin-bottom:0">Only episodes with IFC inundation maps available</label>
        <span class="info-wrap" tabindex="0"><span class="info-icon">i</span><span class="info-tip">51 of 135 episodes match a mapped IFIS community with stage and flood-frequency layers available. Source: Iowa Flood Center's IFIS inundation maps (ifis.iowafloodcenter.org).</span></span>
      </div>
    </div>

    <!-- ── Sliders: instrumentation → ground-truth evidence → impact → context ── -->
    <details class="filter-group">
      <summary>Sensors &amp; Rainfall</summary>
      <div class="section">
        <label>Minimum IFC river sensors: <span class="slider-value" id="ifc_river-min-value">0</span><span class="info-wrap" tabindex="0"><span class="info-icon">i</span><span class="info-tip">IFC river (bridge) stage sensors, matched to an episode by HUC-8/county and its time window. Source: Iowa Flood Center's HydroIowa API (hydroiowa.org/api/riversensor).</span></span></label>
        <input type="range" id="ifc_river-min" min="0" max="0" value="0" step="1" data-sensor-type="ifc_river">
      </div>

      <div class="section">
        <label>Minimum IFC hydrostations: <span class="slider-value" id="ifc_hydrostation-min-value">0</span><span class="info-wrap" tabindex="0"><span class="info-icon">i</span><span class="info-tip">IFC hydrostations (rain, wind, soil moisture, well level, stage), matched to an episode by HUC-8/county and its time window. Source: Iowa Flood Center's HydroIowa API (hydroiowa.org/api/hydrostation).</span></span></label>
        <input type="range" id="ifc_hydrostation-min" min="0" max="0" value="0" step="1" data-sensor-type="ifc_hydrostation">
      </div>

      <div class="section">
        <label>Minimum USGS sensors: <span class="slider-value" id="usgs-min-value">0</span><span class="info-wrap" tabindex="0"><span class="info-icon">i</span><span class="info-tip">USGS stream gauges (falling back to an NWS gauge where no USGS site exists), matched to an episode by HUC-8/county and its time window. Source: USGS Water Services (waterservices.usgs.gov/nwis/iv) and NOAA/NWS gauge data (api.water.noaa.gov).</span></span></label>
        <input type="range" id="usgs-min" min="0" max="0" value="0" step="1" data-sensor-type="usgs">
      </div>

      <div class="section">
        <label>Minimum USGS High-Water Marks: <span class="slider-value" id="hwm-min-value">0</span><span class="info-wrap" tabindex="0"><span class="info-icon">i</span><span class="info-tip">Only 7 of 135 episodes include USGS High-Water Mark records. Coverage is limited, so most values above zero will return no results. Source: USGS Short-Term Network (STN) high-water marks (stn.wim.usgs.gov).</span></span></label>
        <input type="range" id="hwm-min" min="0" max="0" value="0" step="1">
      </div>

      <div class="section">
        <label>Minimum accumulated rainfall (mm): <span class="slider-value" id="precip-min-value">0</span><span class="info-wrap" tabindex="0"><span class="info-icon">i</span><span class="info-tip">126 of 135 episodes have MRMS precipitation data, computed as the mean grid accumulation over the episode's padded window. Maximum observed: 225 mm. Source: NOAA Multi-Radar/Multi-Sensor (MRMS) QPE, via the public noaa-mrms-pds S3 archive.</span></span></label>
        <input type="range" id="precip-min" min="0" max="0" value="0" step="1">
      </div>

      <div class="section">
        <label>Minimum recurrence interval (yr): <span class="slider-value" id="flash-min-value">0</span><span class="info-wrap" tabindex="0"><span class="info-icon">i</span><span class="info-tip">FLASH QPE ARI (Average Recurrence Interval): the most severe rainfall cell found anywhere in the episode's window, across the 30-min/1h/3h/6h/24h durations -- e.g. a 100-yr value means some cell somewhere in the episode saw a 100-year rainfall for one of those durations. All 135 episodes have FLASH data. Maximum observed: 200-yr (the product's own cap). Source: NOAA/NSSL FLASH QPE ARI product, same noaa-mrms-pds archive.</span></span></label>
        <input type="range" id="flash-min" min="0" max="0" value="0" step="1">
      </div>
    </details>

    <details class="filter-group">
      <summary>Impacts</summary>
      <div class="section">
        <label>Minimum NOAA estimated property damage ($): <span class="slider-value" id="propdmg-min-value">0</span><span class="info-wrap" tabindex="0"><span class="info-icon">i</span><span class="info-tip">23 of 135 episodes report NOAA-estimated property damage. Maximum observed: approximately $60.8M. Source: NOAA Storm Events Database (DAMAGE_PROPERTY).</span></span></label>
        <input type="range" id="propdmg-min" min="0" max="0" value="0" step="1000">
      </div>

      <div class="section">
        <label>Minimum FEMA NFIP payout ($): <span class="slider-value" id="fema-payout-min-value">0</span><span class="info-wrap" tabindex="0"><span class="info-icon">i</span><span class="info-tip">36 of 135 episodes have a recorded FEMA NFIP payout, covering paid building, contents, and compliance claims (maximum approximately $11.8M). Reflects insured, paid claims only — not total property damage. Source: FEMA's OpenFEMA API (FimaNfipClaims).</span></span></label>
        <input type="range" id="fema-payout-min" min="0" max="0" value="0" step="1000">
      </div>

      <div class="section">
        <label>Minimum crop insurance indemnity, flood-related ($): <span class="slider-value" id="cropdmg-min-value">0</span><span class="info-wrap" tabindex="0"><span class="info-icon">i</span><span class="info-tip">81 of 135 episodes touch a county with at least one flood-related crop insurance claim that month (maximum approximately $66.2M). Source: USDA RMA Summary of Business, Cause of Loss data (rma.usda.gov), aggregated per county per month rather than per storm — episodes sharing a county and month show the same total.</span></span></label>
        <input type="range" id="cropdmg-min" min="0" max="0" value="0" step="1000">
      </div>

      <div class="section">
        <label>Minimum direct + indirect injuries: <span class="slider-value" id="injuries-min-value">0</span><span class="info-wrap" tabindex="0"><span class="info-icon">i</span><span class="info-tip">No episodes in this dataset have recorded injuries. Source: NOAA Storm Events Database (INJURIES_DIRECT + INJURIES_INDIRECT).</span></span></label>
        <input type="range" id="injuries-min" min="0" max="0" value="0" step="1">
      </div>

      <div class="section">
        <label>Minimum direct + indirect deaths: <span class="slider-value" id="deaths-min-value">0</span><span class="info-wrap" tabindex="0"><span class="info-icon">i</span><span class="info-tip">Source: NOAA Storm Events Database (DEATHS_DIRECT + DEATHS_INDIRECT).</span></span></label>
        <input type="range" id="deaths-min" min="0" max="0" value="0" step="1">
      </div>
    </details>

    <div class="section">
      <label>Minimum county social vulnerability (SVI, county view only): <span class="slider-value" id="svi-min-value">0.0</span><span class="info-wrap" tabindex="0"><span class="info-icon">i</span><span class="info-tip">CDC/ATSDR Social Vulnerability Index overall percentile rank, from 0 (least vulnerable) to 1 (most vulnerable), ranked among Iowa's 99 counties using 2022 data. Has no effect in HUC-8 view. Source: CDC/ATSDR SVI 2022, Iowa county file (svi2.cdc.gov).</span></span></label>
      <input type="range" id="svi-min" min="0" max="1" value="0" step="0.05">
    </div>

    <div class="section legend">
      Only episodes meeting ALL filters above (for the selected region view) are counted on the map.
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
      <div class="checkbox-row" style="margin-bottom:10px;">
        <input type="checkbox" id="detail-mrms-toggle">
        <label for="detail-mrms-toggle" style="margin-bottom:0">Show MRMS accumulated precipitation</label>
      </div>
      <div class="detail-legend" id="detail-mrms-legend" style="display:none; border-top:none; padding-top:0; margin-bottom:10px;"></div>
      <div class="detail-episode-info" id="detail-episode-info"></div>
      <button class="dl-download-btn" id="detail-download-btn" style="width:100%; box-sizing:border-box;">Download this episode's ZIP</button>
      <div class="detail-legend">
        <div><span class="dot" style="background:#1f78b4"></span>IFC River Sensor</div>
        <div><span class="dot" style="background:#33a02c"></span>USGS Sensor</div>
        <div><span class="sq" style="background:#e31a1c"></span>IFC Hydrostation</div>
        <div><span class="tri"></span>USGS High-Water Mark</div>
        <div><span class="excl">!</span>FEMA NFIP Claim</div>
        <div><span class="circ"></span>IFC Inundation Map (approximate)</div>
      </div>
      <div class="detail-hint">Inundation circles mark the matched IFIS community's town center, not the mapped flood extent — the actual KMZ flood-extent polygons aren't downloaded anywhere in this pipeline.</div>
    </div>
  </div>
</div>
</div>

<div id="page-leaderboard">
  <div class="lb-quicknav">
    <div class="lb-quicknav-row">
      <span class="lb-quicknav-label">Sensor counts use:</span>
      <div class="toggle-group" style="display:inline-flex; width:200px;">
        <button id="lb-btn-county" class="active">County</button>
        <button id="lb-btn-huc8">HUC-8 watershed</button>
      </div>
    </div>
    <div class="lb-quicknav-row" id="lb-quicknav-links"></div>
  </div>
  <div id="lb-sections"></div>
</div>

<div id="page-download">
  <div class="dl-wrap">
    <div class="dl-stage-card" style="margin-bottom:20px;">
      <label style="font-family:var(--font-condensed); font-weight:600; font-size:13px; display:block; margin-bottom:6px;">Looking for one episode or region? Search instead of stepping through filters below.</label>
      <input type="text" id="dl-episode-search" class="search-input" placeholder="Episode ID (e.g. 191899_0)" list="dl-episode-search-list" autocomplete="off">
      <datalist id="dl-episode-search-list"></datalist>
      <div id="dl-episode-search-result"></div>

      <input type="text" id="dl-region-search" class="search-input" placeholder="County or HUC-8 watershed name" list="dl-region-search-list" autocomplete="off" style="margin-top:10px;">
      <datalist id="dl-region-search-list"></datalist>
      <div id="dl-region-search-result"></div>
    </div>

    <div class="dl-progress" id="dl-progress"></div>
    <div class="dl-step-label" id="dl-step-label">Step 1 of 5</div>
    <h2 class="dl-stage-title" id="dl-stage-title">Region Type</h2>

    <div class="dl-running-count">
      <b id="dl-running-count">135</b> of 135 episodes match so far
    </div>

    <!-- Stage 0: Region Type -->
    <div class="dl-stage-card" id="dl-stage-0">
      <label style="font-family:var(--font-condensed); font-weight:600; font-size:13px; display:block; margin-bottom:8px;">Filter by watershed (HUC-8) or by county?<span class="info-wrap" tabindex="0"><span class="info-icon">i</span><span class="info-tip">Determines which boundary sensor coverage — and the basin size and SVI filters — are matched against. County and HUC-8 views cannot be combined, consistent with the main map. County boundaries: Census TIGERweb. HUC-8 boundaries: USGS National Map (WBD).</span></span></label>
      <div class="toggle-group">
        <button id="dl-btn-county" class="active">County</button>
        <button id="dl-btn-huc8">HUC-8 Watershed</button>
      </div>
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
      <label style="margin-top:12px;">Minimum FEMA NFIP payout ($): <span class="slider-value" id="dl-fema-value">0</span></label>
      <input type="range" id="dl-fema" min="0" max="0" value="0" step="1000">
      <div class="dl-warning"></div>
    </div>

    <!-- Stage 3: Rainfall -->
    <div class="dl-stage-card" id="dl-stage-3" style="display:none;">
      <label>Minimum accumulated rainfall (mm): <span class="slider-value" id="dl-precip-value">0</span></label>
      <input type="range" id="dl-precip" min="0" max="0" value="0" step="1">
      <label style="margin-top:12px;">Minimum recurrence interval (yr): <span class="slider-value" id="dl-flash-value">0</span></label>
      <input type="range" id="dl-flash" min="0" max="0" value="0" step="1">
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

      <label style="margin-top:14px;">Minimum NOAA estimated property damage ($): <span class="slider-value" id="dl-propdmg-value">0</span></label>
      <input type="range" id="dl-propdmg" min="0" max="0" value="0" step="1000">

      <label style="margin-top:14px;">Minimum crop insurance indemnity, flood-related ($): <span class="slider-value" id="dl-cropdmg-value">0</span></label>
      <input type="range" id="dl-cropdmg" min="0" max="0" value="0" step="1000">
      <div class="dl-warning"></div>
    </div>

    <!-- Results -->
    <div class="dl-stage-card" id="dl-stage-results" style="display:none;">
      <p style="font-size:13px; color:#555;">These are the episodes matching every filter you set. Check/uncheck to choose exactly which ones go in the ZIP — each gets its own folder with real sensor time series (USGS, IFC hydrostations, IFC river), hourly MRMS rainfall, HWM and FEMA claim locations, and a summary. Everything is embedded in this page and built entirely in your browser — no server, no internet needed once the page is loaded.</p>
      <button class="dl-download-btn" id="dl-download-btn">Download ZIP (<span id="dl-download-count">0</span> selected)</button>
      <div style="max-height:420px; overflow-y:auto; border:1px solid #eee; border-radius:6px;">
        <table class="dl-results-table">
          <thead><tr><th><input type="checkbox" id="dl-select-all" checked></th><th>Episode</th><th>Date</th><th>Region(s)</th><th>Sensors</th><th>HWM</th><th>FEMA $</th><th>Rain (mm)</th></tr></thead>
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
    <button class="lb-modal-secondary-btn" id="lb-modal-view-map" style="margin-top:14px;">View Event in Map Viewer</button>
  </div>
</div>

<script>
const WIZARD_DATA = __WIZARD_DATA_JSON__;
// Real per-episode sensor time series + MRMS rainfall, gzip-compressed then
// base64-encoded at build time (see build_embedded_episode_data.py). Decoded
// lazily by getEpisodeDataPayload() on first download, not at page load.
const EMBEDDED_EPISODE_DATA_B64 = "__EMBEDDED_EPISODE_DATA_B64__";
// Per-episode accumulated MRMS precipitation grids, gzip-compressed then
// base64-encoded at build time (see build_embedded_mrms_grids.py). Decoded
// lazily by getMrmsGridPayload() on first "Show MRMS" toggle, not at page load.
const EMBEDDED_MRMS_GRIDS_B64 = "__EMBEDDED_MRMS_GRIDS_B64__";

const map = L.map('map').setView([42.0, -93.5], 7);
// Split into two CARTO raster layers instead of one combined 'light_all':
// 'light_nolabels' underneath everything (base cartography -- rivers,
// roads, land) and 'light_only_labels' (transparent, town/place names
// only) in a dedicated top pane above the choropleth/events/MRMS layers,
// so labels never get buried under the region-count fill. The labels pane
// gets pointer-events:none (see CSS) so it never blocks clicking a county/
// watershed polygon underneath it.
L.tileLayer('https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}{r}.png', {
  attribution: '&copy; OpenStreetMap contributors &copy; CARTO', maxZoom: 12
}).addTo(map);

// CARTO's tile set has no separate "water only" layer to stack above the
// choropleth the way light_only_labels does for text -- so this pulls just
// the water-colored pixels back out of the SAME light_nolabels tiles
// (CARTO's CDN sends Access-Control-Allow-Origin: *, so this is allowed)
// and re-renders them, recolored to a bold blue, in their own pane above
// the choropleth fill. CARTO Positron renders rivers/lakes as a pale
// blue-gray (~rgb(212,218,221), calibrated by sampling real tiles) that's
// reliably distinguishable from the neutral/warm grays used for roads and
// boundaries by blue channel exceeding red -- every other pixel is left
// fully transparent.
const RIVER_HIGHLIGHT_COLOR = [33, 113, 181];
const RiverHighlightLayer = L.GridLayer.extend({
  createTile: function (coords, done) {
    const size = this.getTileSize();
    const tile = document.createElement('canvas');
    tile.width = size.x; tile.height = size.y;
    const ctx = tile.getContext('2d');
    const sub = ['a', 'b', 'c', 'd'][Math.abs(coords.x + coords.y) % 4];
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => {
      ctx.drawImage(img, 0, 0, size.x, size.y);
      try {
        const imgData = ctx.getImageData(0, 0, size.x, size.y);
        const d = imgData.data;
        for (let i = 0; i < d.length; i += 4) {
          const r = d[i], b = d[i + 2];
          const isWater = (b - r) >= 3 && b >= 205 && r >= 190 && r <= 242;
          if (isWater) {
            d[i] = RIVER_HIGHLIGHT_COLOR[0]; d[i + 1] = RIVER_HIGHLIGHT_COLOR[1]; d[i + 2] = RIVER_HIGHLIGHT_COLOR[2]; d[i + 3] = 255;
          } else {
            d[i + 3] = 0;
          }
        }
        ctx.putImageData(imgData, 0, 0);
      } catch (e) { /* tile failed to decode -- leave this tile blank rather than erroring the whole layer */ }
      done(null, tile);
    };
    img.onerror = () => done(null, tile);
    img.src = `https://${sub}.basemaps.cartocdn.com/light_nolabels/${coords.z}/${coords.x}/${coords.y}.png`;
    return tile;
  },
});

map.createPane('riversPane');
map.getPane('riversPane').style.zIndex = 410;
map.getPane('riversPane').style.pointerEvents = 'none';
// Clipped to the actual study area (every county this dataset covers,
// padded 10%) rather than the whole world -- otherwise panning away from
// Iowa keeps highlighting rivers anywhere on the globe for no reason.
const studyAreaBounds = L.geoJSON(WIZARD_DATA.county_geometry).getBounds().pad(0.1);
new RiverHighlightLayer({ pane: 'riversPane', maxZoom: 12, tileSize: 256, bounds: studyAreaBounds }).addTo(map);

map.createPane('labelsPane');
map.getPane('labelsPane').style.zIndex = 460;
map.getPane('labelsPane').style.pointerEvents = 'none';
L.tileLayer('https://{s}.basemaps.cartocdn.com/light_only_labels/{z}/{x}/{y}{r}.png', {
  pane: 'labelsPane', maxZoom: 12,
}).addTo(map);

// #region-detail-panel is an HTML overlay positioned INSIDE the map
// container (not a Leaflet control), so without this, scrolling or
// clicking inside it bubbles straight through to Leaflet's own container
// listeners -- e.g. scrolling the panel to read episode info fires the
// map's scrollWheelZoom handler underneath, zooming the map out from under
// you. This is the standard Leaflet fix for any raw HTML overlay like it.
L.DomEvent.disableClickPropagation(document.getElementById('region-detail-panel'));
L.DomEvent.disableScrollPropagation(document.getElementById('region-detail-panel'));

let granularity = 'county';
const sensorMin = { ifc_river: 0, ifc_hydrostation: 0, usgs: 0 };
let hwmMin = 0;
let femaPayoutMin = 0;
let inundationRequired = false;
let basinSizeFilter = 'all';  // 'all' | 'above' | 'below'
const BASIN_SIZE_THRESHOLD_SQKM = 3000;
let precipMin = 0;
let flashRecurrenceMin = 0;
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
      && ep.fema_payout_usd >= femaPayoutMin
      && (!inundationRequired || ep.n_inundation_layers > 0)
      && episodePassesBasinSizeFilter(ep)
      && ep.precip_total_mm >= precipMin
      && (flashRecurrenceMin <= 0 || (ep.flash_most_severe && ep.flash_most_severe.ari_years >= flashRecurrenceMin))
      && episodePassesSviFilter(ep)
      && ep.n_injuries >= injuriesMin
      && ep.n_deaths >= deathsMin
      && ep.damage_property_usd >= propDmgMin
      && ep.crop_indemnity_usd >= cropDmgMin;
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
             + `&nbsp;&nbsp;river: ${c.ifc_river}, hydrostation: ${c.ifc_hydrostation}, USGS: ${c.usgs}, HWMs: ${ep.n_hwms}, FEMA NFIP payout: $${ep.fema_payout_usd.toLocaleString()}, inundation maps: ${ep.n_inundation_layers > 0 ? 'yes ('+ep.n_inundation_layers+' layers)' : 'no'}, rainfall: ${ep.precip_total_mm} mm<br>`
             + `&nbsp;&nbsp;injuries: ${ep.n_injuries}, deaths: ${ep.n_deaths}, property damage: $${ep.damage_property_usd.toLocaleString()}, flood-related crop indemnity (USDA RMA): $${ep.crop_indemnity_usd.toLocaleString()}</div>`;
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

// Between the choropleth (default overlayPane, z-index 400) and eventsPane
// (450) -- so the MRMS overlay paints over the region-count choropleth but
// underneath sensor/HWM/FEMA markers, which stay fully visible and clickable.
map.createPane('mrmsPane');
map.getPane('mrmsPane').style.zIndex = 420;

let eventsLayer = L.layerGroup().addTo(map);
let showEventLines = false;
let showEventFootprints = false;

// ── MRMS accumulated precipitation overlay ───────────────────────────────
// Per-episode grids are embedded the same lazy-decompress-on-first-use way
// as the sensor time series (EMBEDDED_EPISODE_DATA_B64/getEpisodeDataPayload
// above) -- see build_embedded_mrms_grids.py for how the grid itself is
// built (summed across every cached MRMS hour in the episode's padded UTC
// window, cropped to a padded bbox around the episode's own event points).
let showMrms = false;
let mrmsLayer = null;
let _mrmsGridCache = null;
let mrmsRenderToken = 0;  // bumped on every render call; a stale call whose token has been superseded bails instead of racing to add/remove layers

// Standard green->yellow->orange->red rainfall/intensity ramp (ColorBrewer
// RdYlGn, reversed so green=low and red=high).
const MRMS_COLOR_STOPS = [
  [26, 152, 80], [145, 207, 96], [217, 239, 139],
  [254, 224, 139], [252, 141, 89], [215, 48, 39],
];
function mrmsColor(t) {
  t = Math.max(0, Math.min(1, t));
  const n = MRMS_COLOR_STOPS.length - 1;
  const scaled = t * n;
  const i = Math.min(Math.floor(scaled), n - 1);
  const frac = scaled - i;
  const [r1, g1, b1] = MRMS_COLOR_STOPS[i];
  const [r2, g2, b2] = MRMS_COLOR_STOPS[i + 1];
  return [Math.round(r1 + (r2 - r1) * frac), Math.round(g1 + (g2 - g1) * frac), Math.round(b1 + (b2 - b1) * frac)];
}
// Square-root color scale: cell color uses sqrt(v / globalMax) rather than
// v / globalMax directly. Rainfall totals across episodes are heavily
// right-skewed (most cells sit well under 30mm, a handful of episodes push
// past 150mm) -- a straight linear scale against a fixed global max would
// leave the bulk of episodes looking uniformly pale green with almost no
// visible cell-to-cell contrast. Taking the square root stretches that
// crowded low end across more of the ramp while staying a fixed, monotonic
// function of the true mm value, so colors are still honestly comparable
// episode to episode -- unlike the old per-episode max scale, where the
// same color could mean a very different mm value on two different
// episodes' overlays.
const MRMS_COLOR_GAMMA = 0.5;

async function getMrmsGridPayload() {
  if (_mrmsGridCache) return _mrmsGridCache;
  if (!EMBEDDED_MRMS_GRIDS_B64) { _mrmsGridCache = {}; return _mrmsGridCache; }
  const binaryStr = atob(EMBEDDED_MRMS_GRIDS_B64);
  const bytes = new Uint8Array(binaryStr.length);
  for (let i = 0; i < binaryStr.length; i++) bytes[i] = binaryStr.charCodeAt(i);
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
  const text = await new Response(stream).text();
  _mrmsGridCache = JSON.parse(text);
  return _mrmsGridCache;
}

// Highest single cell value across every episode's grid -- computed once
// (from the already-decompressed payload) and cached, so every episode's
// overlay and legend share one fixed 0..globalMax scale instead of each
// being independently normalized to its own max. That's what makes overlay
// color comparable across episodes: e.g. a cell in a 40mm episode and a
// cell in a 190mm episode now only look the same color if they actually
// received similar rainfall.
let _mrmsGlobalMaxCache = null;
async function getMrmsGlobalMax() {
  if (_mrmsGlobalMaxCache != null) return _mrmsGlobalMaxCache;
  const payload = await getMrmsGridPayload();
  let max = 0;
  for (const grid of Object.values(payload)) {
    for (const row of grid.values) for (const v of row) if (v != null && v > max) max = v;
  }
  _mrmsGlobalMaxCache = max;
  return max;
}

function clearMrmsLayer() {
  if (mrmsLayer) { map.removeLayer(mrmsLayer); mrmsLayer = null; }
  const legendEl = document.getElementById('detail-mrms-legend');
  if (legendEl) legendEl.style.display = 'none';
}

// Mean of a grid's non-null cells, optionally restricted to cells whose
// center falls inside `geometry` (same pointInGeometry test renderMrmsOverlay
// uses to clip the painted overlay to "This Region") -- so the displayed
// rainfall figure always matches the area the overlay is actually shading,
// unlike the old single bbox-mean scalar (episode_precip_extract.py's
// precip_total_mm), which used only the NOAA storm report's own point bbox
// and could land far from where the heaviest rain in the episode's wider
// matched area actually fell.
// mean/median/max across the accumulated grid's non-null cells, optionally
// restricted to cells whose center falls inside `geometry` -- same
// "This Region" clip renderMrmsOverlay uses, so every figure in the detail
// panel always matches the area the heatmap is actually shading. median and
// max are per-PIXEL accumulated totals ("the typical/worst single cell in
// the basin got this much rain"), distinct from mean (spread evenly across
// the basin) -- see episode_precip_extract.py's own median_mm/max_mm for
// the same distinction applied to the narrower per-episode scalar.
function mrmsGridStats(grid, geometry) {
  const { lat_min, lat_max, lon_min, lon_max, nlat, nlon, values } = grid;
  const latStep = (lat_max - lat_min) / (Math.max(nlat, 2) - 1);
  const lonStep = (lon_max - lon_min) / (Math.max(nlon, 2) - 1);
  const cells = [];
  for (let row = 0; row < nlat; row++) {
    const lat = lat_max - row * latStep;
    const gridRow = values[row];
    for (let col = 0; col < nlon; col++) {
      const v = gridRow[col];
      if (v == null) continue;
      if (geometry && !pointInGeometry(lat, lon_min + col * lonStep, geometry)) continue;
      cells.push(v);
    }
  }
  if (!cells.length) return null;
  cells.sort((a, b) => a - b);
  const mid = Math.floor(cells.length / 2);
  const median = cells.length % 2 ? cells[mid] : (cells[mid - 1] + cells[mid]) / 2;
  return { mean: cells.reduce((s, v) => s + v, 0) / cells.length, median, max: cells[cells.length - 1] };
}

// Fills in the detail panel's "Rainfall" figure asynchronously -- it needs
// the full MRMS grid (getMrmsGridPayload(), gzip-decompressed on first use)
// rather than the per-episode scalar already in WIZARD_DATA, so it can't be
// computed synchronously inline with the rest of renderDetailEpisode()'s
// info block. detailRainfallToken guards against the same rapid-click race
// renderMrmsOverlay's mrmsRenderToken guards against (prev/next or scope
// toggles fired again before this resolves).
let detailRainfallToken = 0;
async function updateDetailRainfall() {
  const myToken = ++detailRainfallToken;
  const el = document.getElementById('detail-rainfall');
  if (!el || detailEpisodeIds.length === 0) return;
  const epId = detailEpisodeIds[detailEpisodeIndex];
  const scoped = detailScope === 'region';
  const payload = await getMrmsGridPayload();
  if (myToken !== detailRainfallToken) return;

  const grid = payload[epId];
  if (!grid) { el.textContent = 'no MRMS grid data for this episode'; return; }
  const stats = mrmsGridStats(grid, scoped ? detailRegionGeometry : null);
  if (!stats) { el.textContent = 'no MRMS grid cells fall within this region'; return; }
  // Peak single-HOUR intensity, scoped the same way as avg/median/max: when
  // "This Region" is active, look up the precise polygon-clipped value for
  // JUST that county/watershed (ep.region_intensity_mm, precomputed
  // server-side -- see build_embedded_mrms_grids.py's
  // compute_region_intensities()); when "entire episode" is active, show
  // the whole-footprint figure instead.
  const ep = WIZARD_DATA.episodes[epId];
  const regionIntensityMap = (ep.region_intensity_mm && ep.region_intensity_mm[granularity]) || {};
  const intensityValue = scoped ? (regionIntensityMap[detailRegionId] ?? null) : ep.precip_max_intensity_mm;
  const intensityText = intensityValue != null ? ` &nbsp;|&nbsp; peak hourly intensity: ${intensityValue.toFixed(1)} mm/hr` : '';
  el.innerHTML = `${stats.mean.toFixed(1)} mm avg &middot; ${stats.median.toFixed(1)} mm median (pixel) &middot; ${stats.max.toFixed(1)} mm max (pixel) (${scoped ? 'this region' : 'entire episode'})${intensityText}`;
}

// Renders the current detail-panel episode's accumulated-rain grid as a
// canvas -> PNG image overlay, colored on a FIXED 0..globalMax scale (see
// getMrmsGlobalMax) with a square-root color curve (MRMS_COLOR_GAMMA) so
// colors mean the same mm value on every episode's overlay -- switching
// between episodes no longer silently rescales what "red" means. When scope
// is "This Region", cells outside the drilled-into region's polygon are
// left fully transparent (same pointInGeometry test already used to filter
// sensor/HWM/FEMA markers by scope).
async function renderMrmsOverlay() {
  const myToken = ++mrmsRenderToken;
  if (!showMrms || !detailMode || detailEpisodeIds.length === 0) {
    clearMrmsLayer();
    return;
  }
  const epId = detailEpisodeIds[detailEpisodeIndex];
  const scope = detailScope;
  const payload = await getMrmsGridPayload();
  const globalMax = await getMrmsGlobalMax();
  // Rapid clicks (prev/next, scope toggle) can fire several of these before
  // the first one's await resolves -- since getMrmsGridPayload() only truly
  // awaits anything on the very first call (cached after that), several
  // calls can end up racing to touch `mrmsLayer` in whatever order their
  // microtasks happen to resume. Only the call that's still the LATEST one
  // by the time its await resolves is allowed to touch the map/DOM; every
  // superseded call bails here instead of clearing/re-adding out of order.
  if (myToken !== mrmsRenderToken) return;

  const legendEl = document.getElementById('detail-mrms-legend');
  const grid = payload[epId];
  clearMrmsLayer();
  if (!grid) {
    legendEl.style.display = 'block';
    legendEl.innerHTML = '<span style="color:#888">No cached MRMS data for this episode.</span>';
    return;
  }

  const { lat_min, lat_max, lon_min, lon_max, nlat, nlon, values } = grid;

  const canvas = document.createElement('canvas');
  canvas.width = nlon; canvas.height = nlat;
  const ctx = canvas.getContext('2d');
  const imgData = ctx.createImageData(nlon, nlat);
  const scoped = scope === 'region';
  const latStep = (lat_max - lat_min) / (Math.max(nlat, 2) - 1);
  const lonStep = (lon_max - lon_min) / (Math.max(nlon, 2) - 1);

  for (let row = 0; row < nlat; row++) {
    const lat = lat_max - row * latStep;
    const gridRow = values[row];
    for (let col = 0; col < nlon; col++) {
      const v = gridRow[col];
      if (v == null) continue;  // leave transparent -- ImageData defaults every channel (incl. alpha) to 0
      if (scoped && !pointInGeometry(lat, lon_min + col * lonStep, detailRegionGeometry)) continue;
      const t = globalMax > 0 ? Math.pow(v / globalMax, MRMS_COLOR_GAMMA) : 0;
      const [r, g, b] = mrmsColor(t);
      const idx = (row * nlon + col) * 4;
      imgData.data[idx] = r; imgData.data[idx + 1] = g; imgData.data[idx + 2] = b; imgData.data[idx + 3] = 178;
    }
  }
  ctx.putImageData(imgData, 0, 0);

  const bounds = L.latLngBounds([[lat_min, lon_min], [lat_max, lon_max]]);
  mrmsLayer = L.imageOverlay(canvas.toDataURL('image/png'), bounds, { pane: 'mrmsPane', interactive: false }).addTo(map);

  // Gradient bar stops are placed at each color's gamma-corrected position
  // (t^(1/gamma) of the bar's width) rather than evenly spaced, so the
  // printed bar actually matches the non-linear scale used to color the
  // cells -- an evenly-spaced bar would visually imply a linear scale and
  // mislabel where each color falls on the true mm range.
  const gradientStops = MRMS_COLOR_STOPS.map((c, i) => {
    const t = i / (MRMS_COLOR_STOPS.length - 1);
    const pct = Math.pow(t, 1 / MRMS_COLOR_GAMMA) * 100;
    return `rgb(${c.join(',')}) ${pct.toFixed(1)}%`;
  }).join(', ');

  legendEl.style.display = 'block';
  legendEl.innerHTML = `
    <div style="display:flex; align-items:center; gap:6px;">
      <span>0 mm</span>
      <span class="mrms-scale-bar" style="background:linear-gradient(to right, ${gradientStops});"></span>
      <span>${globalMax.toFixed(0)} mm</span>
    </div>
    <div style="color:#888; margin-top:2px;">Accumulated over the episode's padded window${scoped ? ', clipped to this region' : ' (entire episode bbox)'}. Scale is fixed across all episodes for comparison.</div>
  `;
}

// ── Event buffer color-by-year + legend ──────────────────────────────────
// Qualitative, colorblind-friendlier palette (Tol/ColorBrewer-derived).
// Assigned in order across whatever years actually appear in the data, so
// this keeps working if the dataset's year range ever changes.
const EVENT_YEAR_PALETTE = ['#1f78b4', '#e31a1c', '#33a02c', '#ff7f00', '#6a3d9a', '#b15928', '#a6cee3', '#fdbf6f'];
const ALL_EVENT_YEARS = [...new Set(Object.values(WIZARD_DATA.episodes).map((ep) => ep.begin_date.slice(0, 4)))].sort();
const EVENT_YEAR_COLORS = {};
ALL_EVENT_YEARS.forEach((y, i) => { EVENT_YEAR_COLORS[y] = EVENT_YEAR_PALETTE[i % EVENT_YEAR_PALETTE.length]; });
function colorForEventYear(year) { return EVENT_YEAR_COLORS[year] || '#999999'; }

const eventsYearLegend = L.control({ position: 'bottomright' });
eventsYearLegend.onAdd = function () {
  const div = L.DomUtil.create('div', 'events-year-legend');
  div.innerHTML = '<b>Episode year</b>' + ALL_EVENT_YEARS.map((y) =>
    `<div><span class="dot" style="background:${colorForEventYear(y)}"></span>${y}</div>`).join('');
  return div;
};
let eventsYearLegendShown = false;
function setEventsYearLegendVisible(visible) {
  if (visible && !eventsYearLegendShown) { eventsYearLegend.addTo(map); eventsYearLegendShown = true; }
  else if (!visible && eventsYearLegendShown) { eventsYearLegend.remove(); eventsYearLegendShown = false; }
}

// Set of selected year strings ('2021', ...) -- lets you isolate any
// combination of years' episode buffers instead of all 5 years stacked on
// top of each other. Starts with every year selected (same as no filter).
// Only affects the "browse all matching episodes" view, not a single
// episode you've drilled into (that one's buffer always shows regardless
// of this toggle -- you deliberately picked it).
let eventsYearFilter = new Set(ALL_EVENT_YEARS);
function refreshEventsYearToggleUI() {
  const container = document.getElementById('events-year-toggle');
  const allSelected = eventsYearFilter.size === ALL_EVENT_YEARS.length;
  container.querySelector('[data-year="all"]').classList.toggle('active', allSelected);
  ALL_EVENT_YEARS.forEach((y) => {
    container.querySelector(`[data-year="${y}"]`).classList.toggle('active', eventsYearFilter.has(y));
  });
}
function initEventsYearToggle() {
  const container = document.getElementById('events-year-toggle');
  const options = ['all', ...ALL_EVENT_YEARS];
  container.innerHTML = options.map((y) => `<button data-year="${y}">${y === 'all' ? 'All' : y}</button>`).join('');
  container.querySelectorAll('button').forEach((btn) => {
    btn.addEventListener('click', () => {
      const y = btn.dataset.year;
      if (y === 'all') {
        eventsYearFilter = new Set(ALL_EVENT_YEARS);
      } else if (eventsYearFilter.has(y)) {
        if (eventsYearFilter.size > 1) eventsYearFilter.delete(y);  // keep at least one year selected
      } else {
        eventsYearFilter.add(y);
      }
      refreshEventsYearToggleUI();
      renderMap();
    });
  });
  refreshEventsYearToggleUI();
}

function renderEventsLayer(matchingIds) {
  eventsLayer.clearLayers();
  const statusEl = document.getElementById('events-status');

  if (!showEventLines && !showEventFootprints) {
    statusEl.textContent = '';
    setEventsYearLegendVisible(false);
    return;
  }
  const yearFilteredIds = matchingIds.filter((id) => WIZARD_DATA.episodes[id] && eventsYearFilter.has(WIZARD_DATA.episodes[id].begin_date.slice(0, 4)));
  const allYearsSelected = eventsYearFilter.size === ALL_EVENT_YEARS.length;
  statusEl.textContent = `Showing ${yearFilteredIds.length} episode(s)${allYearsSelected ? '' : ` in ${[...eventsYearFilter].sort().join(', ')}`}.`;
  // Both footprints and event lines are year-colored now, so the legend
  // applies to either -- show it whenever at least one is on screen.
  setEventsYearLegendVisible(showEventFootprints || showEventLines);

  const matchingSet = new Set(yearFilteredIds);
  const byEpisode = {};
  for (const ev of WIZARD_DATA.events) {
    if (!matchingSet.has(ev.episode_id)) continue;
    (byEpisode[ev.episode_id] = byEpisode[ev.episode_id] || []).push(ev);
  }

  // Pass 1: one buffer circle per episode (not per event) -- centered on
  // the centroid of all that episode's event begin/end points, radius
  // sized to enclose all of them (+15% padding, 2km floor so a
  // single-point episode still shows a visible buffer instead of a dot).
  // Drawn BEFORE the event lines (pass 2) so the lines render on top when
  // both are on, since layers added later within the same pane stack above
  // earlier ones. Independently toggleable from the lines via
  // showEventFootprints -- "episode footprint" vs. "raw event locations".
  if (showEventFootprints) {
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
      const year = (WIZARD_DATA.episodes[epId] ? WIZARD_DATA.episodes[epId].begin_date : evs[0].begin_date).slice(0, 4);
      const fillColor = colorForEventYear(year);

      L.circle(center, {
        pane: 'eventsPane', radius, color: fillColor, weight: 1.5, fillColor, fillOpacity: 0.15,
      }).bindTooltip(`Episode ${epId} (${year}) — ${evs.length} event(s)`, { sticky: true }).addTo(eventsLayer);
    }
  }

  // Pass 2: individual event lines, drawn on top of the buffer circles --
  // colored by the same year palette as the footprint circles, so the two
  // layers read as one consistent year-coding whether shown together or
  // separately.
  if (showEventLines) {
    for (const ev of WIZARD_DATA.events) {
      if (!matchingSet.has(ev.episode_id)) continue;
      const samePoint = Math.abs(ev.begin_lat - ev.end_lat) < 1e-6 && Math.abs(ev.begin_lon - ev.end_lon) < 1e-6;
      if (samePoint) continue;
      const lineColor = colorForEventYear(ev.begin_date.slice(0, 4));
      L.polyline([[ev.begin_lat, ev.begin_lon], [ev.end_lat, ev.end_lon]], {
        pane: 'eventsPane', color: lineColor, weight: 2.5, opacity: 0.9,
      }).bindTooltip(`${ev.event_type} — ${ev.begin_date.slice(0, 10)} (${ev.episode_id})`, { sticky: true })
        .addTo(eventsLayer);
    }
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
      return { fillColor: colorForCount(count), fillOpacity: 0.55, color: '#666', weight: 0.6 };
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

// Town-center coordinates for every community in ifis_community_ids.csv --
// IFC's inundation KMZs aren't downloaded/parsed anywhere in this pipeline
// (episode_inundation_extract.py only matches community NAMES by text, via
// ifc_community_layers.py's cached layer-availability list), so there's no
// real flood-extent polygon to draw. This is deliberately just "roughly
// where this community's inundation map is," not the actual mapped extent:
// a fixed town-center point per community, hand-filled from public
// geographic reference data since IFC's own lookup has no lat/lon column.
const IFIS_COMMUNITY_COORDS = {
  'ADEL': [41.6142, -94.0189], 'DECORAH': [43.3031, -91.7857], 'ELKADER': [42.8494, -91.4051],
  'MANCHESTER': [42.4844, -91.4515], 'MAQUOKETA': [42.0692, -90.6631], 'INDEPENDENCE': [42.4694, -91.8901],
  'IOWA CITY': [41.6611, -91.5302], 'HILLS': [41.5561, -91.5926], 'CHARLES CITY': [43.0672, -92.6741],
  'WAVERLY': [42.7278, -92.4780], 'MASON CITY': [43.1536, -93.2010], 'CLARKSVILLE': [42.7847, -92.6690],
  'NEW HARTFORD': [42.5717, -92.3396], 'CEDAR FALLS': [42.5278, -92.4455], 'WATERLOO': [42.4928, -92.3426],
  'VINTON': [42.1642, -92.0146], 'PALO': [42.0728, -91.7799], 'CEDAR RAPIDS': [41.9779, -91.6656],
  'OAKVILLE': [41.1178, -91.1071], 'AMES': [42.0308, -93.6319], 'ESTHERVILLE': [43.4022, -94.8330],
  'HUMBOLDT': [42.7217, -94.2136], 'FORT DODGE': [42.4975, -94.1680], 'DES MOINES': [41.5868, -93.6250],
  'OTTUMWA': [41.0206, -92.4110], 'ROCK RAPIDS': [43.4283, -96.1755], 'ROCK VALLEY': [43.2050, -96.2966],
  'RED OAK': [41.0089, -95.2247], 'BELLEVUE': [42.2536, -90.4243], 'COLUMBUS JUNCTION': [41.2822, -91.3654],
  'CAMANCHE': [41.7883, -90.2529], 'DUBUQUE': [42.5006, -90.6646], 'KEOKUK': [40.3978, -91.3874],
  'FULTON': [41.8631, -90.1720], 'GLADSTONE': [40.8992, -90.9782], 'ILLINOIS CITY': [41.3067, -90.9968],
  'LECLAIRE': [41.5967, -90.3376],
};
function titleCaseCommunity(name) {
  return name.split(' ').map((w) => w[0] + w.slice(1).toLowerCase()).join(' ');
}

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
// ── Sensor click -> small time-series chart ──────────────────────────────
// Reuses the same real per-sensor CSVs the "Download Data" ZIP export pulls
// from (EMBEDDED_EPISODE_DATA_B64, decompressed lazily by
// getEpisodeDataPayload -- defined further down, but hoisted since it's a
// function declaration), keyed the same way build_embedded_episode_data.py
// wrote them: '<folder>/<safe_code>.csv'.
const SENSOR_ARCHIVE_FOLDER = { usgs: 'usgs_sensors', ifc_hydrostation: 'ifc_hydrostations', ifc_river: 'ifc_river_sensors' };
// Single-series field to chart for the two "one number over time" sensor
// types. ifc_hydrostation is handled separately (see buildHydrostationHtml)
// since it reports rain/wind/soil rather than one continuous stage-like
// value -- those get summary stats plus a depth-resolved soil chart instead.
const SENSOR_CHART_FIELD = {
  usgs: { field: 'gage_height_ft', label: 'Gage height (ft)' },
  ifc_river: { field: 'elevation', label: 'Elevation' },
};
// Distinct from EVENT_YEAR_PALETTE so a shallow-to-deep soil legend never
// looks like it's reusing the year-color meaning from the main map legend.
const SOIL_DEPTH_PALETTE = ['#8c510a', '#1f78b4', '#33a02c', '#e31a1c', '#6a3d9a'];

// Minimal RFC4180-ish CSV parser -- needed because ifc_hydrostation columns
// (soil_Temp etc.) are quoted arrays like "[60.6, 60.8]" with commas inside
// the quotes, which a naive text.split(',') would split incorrectly.
function parseCsv(text) {
  const rows = [];
  let field = '', row = [], inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') { if (text[i + 1] === '"') { field += '"'; i++; } else { inQuotes = false; } }
      else field += c;
    } else if (c === '"') inQuotes = true;
    else if (c === ',') { row.push(field); field = ''; }
    else if (c === '\r') { /* skip */ }
    else if (c === '\n') { row.push(field); rows.push(row); row = []; field = ''; }
    else field += c;
  }
  if (field.length || row.length) { row.push(field); rows.push(row); }
  if (!rows.length) return [];
  const columns = rows[0];
  return rows.slice(1)
    .filter((r) => r.length === columns.length)
    .map((r) => Object.fromEntries(columns.map((c, idx) => [c, r[idx]])));
}

// General multi-series line chart: gridlines + axis lines + one polyline per
// series (each series pre-sorted by time), all sharing one x/y scale so
// different depths/sensors line up. Single-series callers just pass a
// one-element array.
function buildLineChartSvg(seriesArr) {
  const allPoints = seriesArr.flatMap((s) => s.points);
  if (!allPoints.length) return '<div class="sc-empty">No numeric readings in this window.</div>';
  const w = 250, h = 90, padL = 38, padR = 8, padT = 10, padB = 16;
  const xs = allPoints.map((p) => p.t), ys = allPoints.map((p) => p.v);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const spanX = Math.max(maxX - minX, 1);
  const spanY = Math.max(maxY - minY, 1e-9);
  const sx = (x) => padL + ((x - minX) / spanX) * (w - padL - padR);
  const sy = (y) => h - padB - ((y - minY) / spanY) * (h - padT - padB);
  const fmtT = (ms) => new Date(ms).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit' });

  const gridYs = [minY, (minY + maxY) / 2, maxY];
  const gridLines = gridYs.map((v) => `<line x1="${padL}" y1="${sy(v).toFixed(1)}" x2="${w - padR}" y2="${sy(v).toFixed(1)}" stroke="#eee" stroke-width="1"/>`).join('');
  const yLabels = gridYs.map((v) => `<text x="2" y="${(sy(v) + 3).toFixed(1)}" font-size="9" fill="#888">${v.toFixed(2)}</text>`).join('');
  const axisLines = `<line x1="${padL}" y1="${padT}" x2="${padL}" y2="${h - padB}" stroke="#bbb" stroke-width="1"/>`
    + `<line x1="${padL}" y1="${h - padB}" x2="${w - padR}" y2="${h - padB}" stroke="#bbb" stroke-width="1"/>`;
  const paths = seriesArr.map((s) => {
    const sorted = s.points.slice().sort((a, b) => a.t - b.t);
    const d = sorted.map((p, i) => `${i === 0 ? 'M' : 'L'} ${sx(p.t).toFixed(1)} ${sy(p.v).toFixed(1)}`).join(' ');
    return `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="1.5"/>`;
  }).join('');

  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
    ${gridLines}
    ${axisLines}
    ${yLabels}
    ${paths}
    <text x="${padL}" y="${h - 3}" font-size="9" fill="#888">${fmtT(minX)}</text>
    <text x="${w - padR}" y="${h - 3}" font-size="9" fill="#888" text-anchor="end">${fmtT(maxX)}</text>
  </svg>`;
}

function legendItemHtml(label, color, square) {
  const shape = square ? 'border-radius:2px;' : '';
  return `<span class="sc-legend-item"><span class="sc-dot" style="background:${color};${shape}"></span>${label}</span>`;
}
function seriesLegendHtml(seriesArr) {
  if (seriesArr.length <= 1) return '';
  return `<div class="sc-legend">${seriesArr.map((s) => legendItemHtml(s.label, s.color)).join('')}</div>`;
}

// Combo chart for the hydrostation popup: soil-moisture depth lines plotted
// the normal way (full chart height, low near the bottom) PLUS an hourly
// precipitation hyetograph hanging DOWN from the top edge on its own,
// independently-scaled right-hand axis -- the same layout IFIS's own
// station charts use so a wet spell lines up visually with the soil
// response underneath it, without the much-smaller rain bars being
// squashed flat by the soil axis's own range.
const PRECIP_BAR_COLOR = '#2b6cb0';
const PRECIP_BAND_FRAC = 0.42; // fraction of the plot height precip's 0..max maps into, from the top

function buildSoilPrecipComboChart(soilSeries, precipBins) {
  const soilPoints = soilSeries.flatMap((s) => s.points);
  if (!soilPoints.length && !precipBins.length) return '<div class="sc-empty">No numeric readings in this window.</div>';

  const w = 250, h = 112, padL = 34, padR = 34, padT = 10, padB = 16;
  const plotTop = padT, plotBottom = h - padB;
  const allT = soilPoints.map((p) => p.t).concat(precipBins.map((p) => p.t));
  const minX = Math.min(...allT), maxX = Math.max(...allT);
  const spanX = Math.max(maxX - minX, 1);
  const sx = (x) => padL + ((x - minX) / spanX) * (w - padL - padR);
  const fmtT = (ms) => new Date(ms).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit' });

  // -- Precipitation: independent axis, 0 at the top edge, max mapped only
  // into the top PRECIP_BAND_FRAC of the plot so bars read as a hyetograph
  // hugging the top rather than competing with the soil lines for the same
  // vertical space.
  const precipMax = precipBins.length ? Math.max(...precipBins.map((p) => p.v), 0) : 0;
  const precipBandBottom = plotTop + PRECIP_BAND_FRAC * (plotBottom - plotTop);
  const syPrecip = (v) => precipMax > 0 ? plotTop + (v / precipMax) * (precipBandBottom - plotTop) : plotTop;
  const barW = precipBins.length ? Math.max(1, Math.min(6, (w - padL - padR) / precipBins.length - 1)) : 0;
  const bars = precipBins.map((p) => {
    if (p.v <= 0) return '';
    const yBot = syPrecip(p.v);
    return `<rect x="${(sx(p.t) - barW / 2).toFixed(1)}" y="${plotTop.toFixed(1)}" width="${barW.toFixed(1)}" height="${(yBot - plotTop).toFixed(1)}" fill="${PRECIP_BAR_COLOR}" fill-opacity="0.55"/>`;
  }).join('');
  const precipAxisLabels = precipMax > 0
    ? `<text x="${w - 2}" y="${(plotTop + 8).toFixed(1)}" font-size="9" fill="${PRECIP_BAR_COLOR}" text-anchor="end">0</text>`
      + `<text x="${w - 2}" y="${(precipBandBottom + 3).toFixed(1)}" font-size="9" fill="${PRECIP_BAR_COLOR}" text-anchor="end">${precipMax.toFixed(precipMax < 1 ? 2 : 1)} mm</text>`
    : '';

  // -- Soil moisture: same full-height scaling buildLineChartSvg used.
  let soilBlock = '';
  if (soilPoints.length) {
    const ys = soilPoints.map((p) => p.v);
    const minY = Math.min(...ys), maxY = Math.max(...ys);
    const spanY = Math.max(maxY - minY, 1e-9);
    const syS = (y) => plotBottom - ((y - minY) / spanY) * (plotBottom - plotTop);
    const gridYs = [minY, (minY + maxY) / 2, maxY];
    const gridLines = gridYs.map((v) => `<line x1="${padL}" y1="${syS(v).toFixed(1)}" x2="${w - padR}" y2="${syS(v).toFixed(1)}" stroke="#eee" stroke-width="1"/>`).join('');
    const yLabels = gridYs.map((v) => `<text x="2" y="${(syS(v) + 3).toFixed(1)}" font-size="9" fill="#888">${v.toFixed(2)}</text>`).join('');
    const paths = soilSeries.map((s) => {
      const sorted = s.points.slice().sort((a, b) => a.t - b.t);
      const d = sorted.map((p, i) => `${i === 0 ? 'M' : 'L'} ${sx(p.t).toFixed(1)} ${syS(p.v).toFixed(1)}`).join(' ');
      return `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="1.5"/>`;
    }).join('');
    soilBlock = gridLines + yLabels + paths;
  }

  const axisLines = `<line x1="${padL}" y1="${plotTop}" x2="${padL}" y2="${plotBottom}" stroke="#bbb" stroke-width="1"/>`
    + `<line x1="${padL}" y1="${plotBottom}" x2="${w - padR}" y2="${plotBottom}" stroke="#bbb" stroke-width="1"/>`;

  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
    ${bars}
    ${soilBlock}
    ${axisLines}
    ${precipAxisLabels}
    <text x="${padL}" y="${h - 3}" font-size="9" fill="#888">${fmtT(minX)}</text>
    <text x="${w - padR}" y="${h - 3}" font-size="9" fill="#888" text-anchor="end">${fmtT(maxX)}</text>
  </svg>`;
}

// soil_VWC (volumetric water content -- the standard soil-saturation
// measure) and soil_depth are both stored per-row as bracketed arrays, e.g.
// "[0.226, 0.216, 0.235, 0.268]" / "[5, 10, 20, 50]" -- same length, same
// order, one entry per depth probe on the station. Depth labels are read
// from the first row that has them (they don't change over a station's
// life) and used to build one time series per depth.
function parseNumArray(str) {
  if (!str) return [];
  return String(str).replace(/[[\]]/g, '').split(',').map((s) => parseFloat(s.trim())).filter((v) => Number.isFinite(v));
}
function buildSoilDepthSeries(records) {
  let depths = null;
  for (const r of records) {
    const d = parseNumArray(r.soil_depth);
    if (d.length) { depths = d; break; }
  }
  if (!depths) return [];
  const series = depths.map((d, i) => ({ label: `${d} cm`, color: SOIL_DEPTH_PALETTE[i % SOIL_DEPTH_PALETTE.length], points: [] }));
  for (const r of records) {
    const t = new Date(r.datetime).getTime();
    if (!Number.isFinite(t)) continue;
    parseNumArray(r.soil_VWC).forEach((v, i) => {
      if (series[i]) series[i].points.push({ t, v });
    });
  }
  return series.filter((s) => s.points.length > 1);
}

// rain_accum is IFC's own per-reading (15-min) precipitation increment, in
// mm -- summed here into hourly bins so the hyetograph bars stay readable
// (a multi-day episode window at native 15-min resolution would produce
// far too many bars to render distinctly at this popup's chart width).
function buildHourlyPrecipBins(records) {
  const bins = new Map();
  for (const r of records) {
    const t = new Date(r.datetime).getTime();
    const v = parseFloat(r.rain_accum);
    if (!Number.isFinite(t) || !Number.isFinite(v)) continue;
    const hourMs = Math.floor(t / 3600000) * 3600000;
    bins.set(hourMs, (bins.get(hourMs) || 0) + v);
  }
  return [...bins.entries()].map(([t, v]) => ({ t, v })).sort((a, b) => a.t - b.t);
}

function buildHydrostationPopupHtml(title, records) {
  const maxWind = records.reduce((m, r) => Math.max(m, parseFloat(r.wind_maxSpeed) || 0), 0);
  const totalRain = records.reduce((sum, r) => sum + (parseFloat(r.rain_accum) || 0), 0);
  const soilSeries = buildSoilDepthSeries(records);
  const precipBins = buildHourlyPrecipBins(records);
  const chart = buildSoilPrecipComboChart(soilSeries, precipBins);
  const legendItems = soilSeries.map((s) => legendItemHtml(s.label, s.color))
    .concat(precipBins.some((p) => p.v > 0) ? [legendItemHtml('Precipitation (hourly, mm)', PRECIP_BAR_COLOR, true)] : []);
  const legend = legendItems.length ? `<div class="sc-legend">${legendItems.join('')}</div>` : '';
  return `<div class="sensor-chart-popup">${title}
    <div class="sc-stats">Max wind speed: <b>${maxWind.toFixed(1)}</b> &nbsp;|&nbsp; Total precipitation: <b>${totalRain.toFixed(3)}</b> mm</div>
    <span class="sc-sub">Soil moisture (VWC) by depth, with hourly precipitation from the top, over the episode window</span>
    ${chart}${legend}</div>`;
}

function buildSingleSeriesPopupHtml(title, records, cfg) {
  const points = records
    .map((r) => ({ t: new Date(r.datetime).getTime(), v: parseFloat(r[cfg.field]) }))
    .filter((pt) => Number.isFinite(pt.t) && Number.isFinite(pt.v));
  const chart = buildLineChartSvg([{ points, color: '#1f78b4' }]);
  return `<div class="sensor-chart-popup">${title}<span class="sc-sub">${cfg.label} over the episode window</span>${chart}</div>`;
}

async function showSensorChartPopup(marker, epId, p) {
  const title = `<b>${p.description || p.code}</b>`;
  marker.setPopupContent(`<div class="sensor-chart-popup">${title}<div class="sc-loading">Loading chart…</div></div>`).openPopup();
  try {
    const payload = await getEpisodeDataPayload();
    const files = (payload && payload[epId]) || {};
    const folder = SENSOR_ARCHIVE_FOLDER[p.type];
    const safeCode = String(p.code).replace(/[\\/]/g, '_');
    const csvText = files[`${folder}/${safeCode}.csv`];
    if (!csvText) {
      marker.setPopupContent(`<div class="sensor-chart-popup">${title}<div class="sc-empty">No time-series data available for this sensor over this episode's window.</div></div>`);
      return;
    }
    const records = parseCsv(csvText);
    const html = p.type === 'ifc_hydrostation'
      ? buildHydrostationPopupHtml(title, records)
      : buildSingleSeriesPopupHtml(title, records, SENSOR_CHART_FIELD[p.type]);
    marker.setPopupContent(html);
  } catch (err) {
    marker.setPopupContent(`<div class="sensor-chart-popup">${title}<div class="sc-error">Failed to load chart.</div></div>`);
  }
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

// Opens the region-detail panel for a specific episode, regardless of
// current filter settings -- a filter that would otherwise exclude this
// episode from a region's normal paginated list doesn't hide it here, since
// you searched for it by exact ID and clearly want to see it.
function jumpToEpisode(episodeId) {
  const ep = WIZARD_DATA.episodes[episodeId];
  if (!ep) return false;
  const regionIds = ep[episodeRegionKey()];
  if (!regionIds || !regionIds.length) return false;
  const regionId = regionIds[0];
  const geo = granularity === 'county' ? WIZARD_DATA.county_geometry : WIZARD_DATA.huc8_geometry;
  const feature = geo.features.find((f) => f.properties[regionIdField()] === regionId);
  if (!feature) return false;
  const bounds = L.geoJSON(feature).getBounds();
  openRegionDetail(regionId, feature.properties[regionNameField()], bounds, feature.geometry);
  if (!detailEpisodeIds.includes(episodeId)) detailEpisodeIds.unshift(episodeId);
  detailEpisodeIndex = detailEpisodeIds.indexOf(episodeId);
  renderDetailEpisode();
  return true;
}

// Like jumpToEpisode, but pins to a SPECIFIC region (the one a "Most Intense
// Rainfall" region-leaderboard row was ranked under) instead of just
// whichever region the episode happens to touch first, and forces "This
// Region" scope so the panel shows that county/watershed's own numbers --
// matching "brings you to the episode, but only the basin/county."
function jumpToEpisodeRegion(episodeId, regionId) {
  const ep = WIZARD_DATA.episodes[episodeId];
  if (!ep) return false;
  const geo = granularity === 'county' ? WIZARD_DATA.county_geometry : WIZARD_DATA.huc8_geometry;
  const feature = geo.features.find((f) => f.properties[regionIdField()] === regionId);
  if (!feature) return false;
  const bounds = L.geoJSON(feature).getBounds();
  openRegionDetail(regionId, feature.properties[regionNameField()], bounds, feature.geometry);
  if (!detailEpisodeIds.includes(episodeId)) detailEpisodeIds.unshift(episodeId);
  detailEpisodeIndex = detailEpisodeIds.indexOf(episodeId);
  detailScope = 'region';
  document.getElementById('detail-scope-region').classList.add('active');
  document.getElementById('detail-scope-episode').classList.remove('active');
  renderDetailEpisode();
  return true;
}

function findRegionFeatureByName(name) {
  const geo = granularity === 'county' ? WIZARD_DATA.county_geometry : WIZARD_DATA.huc8_geometry;
  const nameField = regionNameField();
  const needle = name.trim().toLowerCase();
  return geo.features.find((f) => f.properties[nameField].toLowerCase() === needle)
      || geo.features.find((f) => f.properties[nameField].toLowerCase().includes(needle));
}

function populateEpisodeSearchList() {
  document.getElementById('episode-search-list').innerHTML =
    Object.keys(WIZARD_DATA.episodes).sort().map((id) => `<option value="${id}">`).join('');
}
function populateRegionSearchList() {
  const geo = granularity === 'county' ? WIZARD_DATA.county_geometry : WIZARD_DATA.huc8_geometry;
  const nameField = regionNameField();
  const names = [...new Set(geo.features.map((f) => f.properties[nameField]))].sort();
  document.getElementById('region-search-list').innerHTML = names.map((n) => `<option value="${n}">`).join('');
}

function runEpisodeSearch() {
  const input = document.getElementById('episode-search');
  const statusEl = document.getElementById('episode-search-status');
  const val = input.value.trim();
  if (!val) { statusEl.textContent = ''; return; }
  if (!WIZARD_DATA.episodes[val]) { statusEl.textContent = `No episode "${val}" found.`; return; }
  const ok = jumpToEpisode(val);
  statusEl.textContent = ok ? '' : `Episode ${val} has no ${granularity === 'county' ? 'county' : 'HUC-8'} match to show -- try switching region view.`;
}
function runRegionSearch() {
  const input = document.getElementById('region-search');
  const statusEl = document.getElementById('region-search-status');
  const val = input.value.trim();
  if (!val) { statusEl.textContent = ''; return; }
  const feature = findRegionFeatureByName(val);
  if (!feature) { statusEl.textContent = `No ${granularity === 'county' ? 'county' : 'watershed'} matching "${val}".`; return; }
  statusEl.textContent = '';
  const bounds = L.geoJSON(feature).getBounds();
  openRegionDetail(feature.properties[regionIdField()], feature.properties[regionNameField()], bounds, feature.geometry);
}
document.getElementById('episode-search').addEventListener('change', runEpisodeSearch);
document.getElementById('episode-search').addEventListener('keydown', (e) => { if (e.key === 'Enter') runEpisodeSearch(); });
document.getElementById('region-search').addEventListener('change', runRegionSearch);
document.getElementById('region-search').addEventListener('keydown', (e) => { if (e.key === 'Enter') runRegionSearch(); });

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
  clearMrmsLayer();
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
    document.getElementById('detail-download-btn').disabled = true;
    clearMrmsLayer();
    return;
  }

  const epId = detailEpisodeIds[detailEpisodeIndex];
  const ep = WIZARD_DATA.episodes[epId];
  counterEl.textContent = `${detailEpisodeIndex + 1} of ${detailEpisodeIds.length}`;
  document.getElementById('detail-download-btn').disabled = false;

  const scoped = detailScope === 'region';
  const allSensors = ep.sensor_points[granularity] || [];
  const sensors = scoped ? allSensors.filter((p) => pointInGeometry(p.lat, p.lng, detailRegionGeometry)) : allSensors;
  const hwms = scoped ? ep.hwm_points.filter((h) => pointInGeometry(h.lat, h.lon, detailRegionGeometry)) : ep.hwm_points;
  const femas = scoped ? ep.fema_points.filter((f) => pointInGeometry(f.lat, f.lon, detailRegionGeometry)) : ep.fema_points;

  // Communities matched to an unknown name (missing from IFIS_COMMUNITY_COORDS)
  // are dropped rather than shown with no location -- shouldn't happen since
  // the lookup covers every name in ifis_community_ids.csv, but a text-only
  // community match with nothing to plot isn't worth a broken marker.
  const allCommunities = (ep.inundation_communities || [])
    .filter((name) => IFIS_COMMUNITY_COORDS[name])
    .map((name) => ({ name, coord: IFIS_COMMUNITY_COORDS[name] }));
  const communities = scoped ? allCommunities.filter((c) => pointInGeometry(c.coord[0], c.coord[1], detailRegionGeometry)) : allCommunities;

  const scopeNote = scoped
    ? `<span style="color:#888">(within this region — ${allSensors.length - sensors.length} sensor(s)/${ep.hwm_points.length - hwms.length} HWM(s)/${ep.fema_points.length - femas.length} claim(s)/${allCommunities.length - communities.length} inundation map(s) outside it, hidden)</span>`
    : `<span style="color:#888">(entire episode, all regions it touches)</span>`;

  const inundationText = communities.length
    ? `${ep.n_inundation_layers} (${communities.map((c) => titleCaseCommunity(c.name)).join(', ')})`
    : String(ep.n_inundation_layers);

  infoEl.innerHTML = `
    <b>${epId}</b> — ${ep.event_types.join(', ')}<br>
    ${ep.begin_date.slice(0, 10)} to ${ep.end_date.slice(0, 10)}<br>
    Rainfall: <span id="detail-rainfall">computing…</span> &nbsp;|&nbsp; Inundation layers: ${inundationText}<br>
    ${flashSevereLine(ep, epId, '-detail')}
    <div id="flash-table-container-detail" class="flash-table-wrap" style="display:none"></div>
    Injuries: ${ep.n_injuries} &nbsp;|&nbsp; Deaths: ${ep.n_deaths} &nbsp;|&nbsp; Property: $${ep.damage_property_usd.toLocaleString()} &nbsp;|&nbsp; Flood-related crop indemnity (USDA RMA): $${ep.crop_indemnity_usd.toLocaleString()}<br>
    Shown here: ${sensors.length} sensor(s), ${hwms.length} HWM(s), ${femas.length} FEMA claim(s), ${communities.length} inundation map(s)<br>
    ${scopeNote}
  `;
  updateDetailRainfall();

  for (const c of communities) {
    L.circle(c.coord, {
      pane: 'eventsPane', radius: 3500, color: '#0868ac', weight: 2, dashArray: '4,4',
      fillColor: '#43a2ca', fillOpacity: 0.18,
    })
      .bindTooltip(`IFC inundation map: ${titleCaseCommunity(c.name)} (approximate location)`, { sticky: true })
      .addTo(detailLayer);
  }

  for (const p of sensors) {
    const style = SENSOR_STYLE[p.type];
    const marker = style.shape === 'circle'
      ? L.circleMarker([p.lat, p.lng], { pane: 'eventsPane', radius: 5, color: '#000', weight: 1, fillColor: style.color, fillOpacity: 1 })
      : L.marker([p.lat, p.lng], { pane: 'eventsPane', icon: squareIcon(style.color) });
    marker.bindTooltip(`${p.description}`, { sticky: true });
    marker.bindPopup('', { maxWidth: 280 });
    marker.on('click', () => showSensorChartPopup(marker, epId, p));
    marker.addTo(detailLayer);
  }
  for (const h of hwms) {
    L.marker([h.lat, h.lon], { pane: 'eventsPane', icon: triangleIcon('#000') })
      .bindTooltip(`HWM: ${h.waterbody}${h.elev_ft != null ? ` (${h.elev_ft.toFixed(1)} ft)` : ''}`, { sticky: true }).addTo(detailLayer);
  }
  for (const f of femas) {
    L.marker([f.lat, f.lon], { pane: 'eventsPane', icon: exclamationIcon('#FFCD00') })
      .bindTooltip(`FEMA claim: ${f.amount != null ? '$' + f.amount.toLocaleString() : '?'} (${f.date})`, { sticky: true }).addTo(detailLayer);
  }
  renderMrmsOverlay();
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
document.getElementById('detail-mrms-toggle').addEventListener('change', (e) => {
  showMrms = e.target.checked;
  renderMrmsOverlay();
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
document.getElementById('detail-download-btn').addEventListener('click', (e) => {
  if (detailEpisodeIds.length === 0) return;
  const epId = detailEpisodeIds[detailEpisodeIndex];
  const readme = buildEpisodesReadme('single-episode export', granularity, `Episode: ${epId}`);
  downloadEpisodesZip([epId], granularity, `${epId}.zip`, readme, e.currentTarget);
});

function resetRegionSearch() {
  document.getElementById('region-search').value = '';
  document.getElementById('region-search-status').textContent = '';
  populateRegionSearchList();
}
// County FIPS and HUC8 codes are different ID spaces -- if a specific
// episode's detail panel is open when the granularity toggle flips,
// detailRegionId (still the OLD county/HUC8 id) means nothing under the
// NEW one, so renderMap()'s own refreshDetailForCurrentFilters() silently
// resolves to the wrong (usually empty) episode list. Capture which
// episode was actually being viewed first and re-jump to that SAME
// episode under the new granularity once renderMap() has settled, so
// switching region view keeps looking at the same event instead of
// whatever region id happened to collide.
function switchGranularity(newGranularity) {
  const currentEpisodeId = (detailMode && detailEpisodeIds.length) ? detailEpisodeIds[detailEpisodeIndex] : null;
  granularity = newGranularity;
  document.getElementById('btn-county').classList.toggle('active', granularity === 'county');
  document.getElementById('btn-huc8').classList.toggle('active', granularity === 'huc8');
  resetRegionSearch();
  renderMap();
  if (currentEpisodeId && !jumpToEpisode(currentEpisodeId)) {
    // Episode has no match at all under the new granularity (e.g. it
    // touches no real HUC8) -- show "no episodes" rather than leave the
    // stale region-based list from refreshDetailForCurrentFilters() up.
    detailEpisodeIds = [];
    detailEpisodeIndex = 0;
    renderDetailEpisode();
  }
}
document.getElementById('btn-county').addEventListener('click', () => switchGranularity('county'));
document.getElementById('btn-huc8').addEventListener('click', () => switchGranularity('huc8'));

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

const femaPayoutSlider = document.getElementById('fema-payout-min');
const femaPayoutSliderValue = document.getElementById('fema-payout-min-value');
femaPayoutSlider.max = Math.ceil(Math.max(...Object.values(WIZARD_DATA.episodes).map(ep => ep.fema_payout_usd)));
femaPayoutSlider.addEventListener('input', () => {
  femaPayoutMin = parseFloat(femaPayoutSlider.value);
  femaPayoutSliderValue.textContent = '$' + femaPayoutMin.toLocaleString();
  renderMap();
});

const inundationCheckbox = document.getElementById('inundation-present');
inundationCheckbox.addEventListener('change', () => {
  inundationRequired = inundationCheckbox.checked;
  renderMap();
});

const showEventsCheckbox = document.getElementById('show-events');
showEventsCheckbox.addEventListener('change', () => {
  showEventLines = showEventsCheckbox.checked;
  renderMap();
});
const showEventFootprintsCheckbox = document.getElementById('show-event-footprints');
showEventFootprintsCheckbox.addEventListener('change', () => {
  showEventFootprints = showEventFootprintsCheckbox.checked;
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

function episodeFlashAriYears(ep) { return ep.flash_most_severe ? ep.flash_most_severe.ari_years : 0; }

// Standard NOAA Atlas-14-style ARI categories -- the FLASH product's own
// docs describe it as finding the "closest ARI" against a static frequency
// table, so the underlying grid is meant to be read as one of these
// categories, not a continuous number. Every recurrence value shown
// anywhere in the UI (headline text, leaderboard, hourly table, both
// sliders) is floored to the highest category it has reached rather than
// showing the raw decimal (e.g. "10.92-yr" reads as "10-yr").
const ARI_BUCKETS = [1, 2, 5, 10, 25, 50, 100, 200, 500, 1000];
function ariBlockLabel(years) {
  if (years == null || years < ARI_BUCKETS[0]) return '&lt;1-yr';
  let block = ARI_BUCKETS[0];
  for (const b of ARI_BUCKETS) { if (years >= b) block = b; else break; }
  return `${block}-yr`;
}

// The slider's own value is the bucket INDEX (0 = no minimum, 1..10 = one
// of ARI_BUCKETS), not years directly -- so it can only ever land on a real
// category, matching the "blocks, not continuous" ask.
const flashSlider = document.getElementById('flash-min');
const flashSliderValue = document.getElementById('flash-min-value');
flashSlider.min = 0;
flashSlider.max = ARI_BUCKETS.length;
flashSlider.step = 1;
flashSlider.addEventListener('input', () => {
  const idx = parseInt(flashSlider.value, 10);
  flashRecurrenceMin = idx === 0 ? 0 : ARI_BUCKETS[idx - 1];
  flashSliderValue.textContent = idx === 0 ? 'Any' : `${flashRecurrenceMin}-yr`;
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
cropDmgSlider.max = Math.ceil(Math.max(...Object.values(WIZARD_DATA.episodes).map(ep => ep.crop_indemnity_usd)));
cropDmgSlider.addEventListener('input', () => {
  cropDmgMin = parseFloat(cropDmgSlider.value);
  cropDmgSliderValue.textContent = '$' + cropDmgMin.toLocaleString();
  renderMap();
});

// Pushes the shared filter state onto the Map page's own controls -- needed
// because the Download page can change that same state (granularity,
// sensorMin, hwmMin, etc. -- see the state comment above dlSensorAdvanced)
// while the Map page's sliders/toggles, which are separate DOM elements,
// aren't visible to update live. Called whenever the user navigates back to
// the Map page.
function syncMapControlsFromState() {
  document.getElementById('btn-county').classList.toggle('active', granularity === 'county');
  document.getElementById('btn-huc8').classList.toggle('active', granularity === 'huc8');
  Object.values(basinButtons).forEach((bid) => document.getElementById(bid).classList.toggle('active', bid === basinButtons[basinSizeFilter]));
  ['ifc_river', 'ifc_hydrostation', 'usgs'].forEach((t) => {
    document.getElementById(`${t}-min`).value = sensorMin[t];
    document.getElementById(`${t}-min-value`).textContent = sensorMin[t];
  });
  hwmSlider.value = hwmMin;
  hwmSliderValue.textContent = hwmMin;
  femaPayoutSlider.value = femaPayoutMin;
  femaPayoutSliderValue.textContent = '$' + femaPayoutMin.toLocaleString();
  inundationCheckbox.checked = inundationRequired;
  precipSlider.value = precipMin;
  precipSliderValue.textContent = precipMin;
  const flashIdx = flashRecurrenceMin > 0 ? ARI_BUCKETS.indexOf(flashRecurrenceMin) + 1 : 0;
  flashSlider.value = flashIdx;
  flashSliderValue.textContent = flashIdx === 0 ? 'Any' : `${flashRecurrenceMin}-yr`;
  sviSlider.value = sviMin;
  sviSliderValue.textContent = sviMin.toFixed(2);
  injuriesSlider.value = injuriesMin;
  injuriesSliderValue.textContent = injuriesMin;
  deathsSlider.value = deathsMin;
  deathsSliderValue.textContent = deathsMin;
  propDmgSlider.value = propDmgMin;
  propDmgSliderValue.textContent = '$' + propDmgMin.toLocaleString();
  cropDmgSlider.value = cropDmgMin;
  cropDmgSliderValue.textContent = '$' + cropDmgMin.toLocaleString();
}

initEventsYearToggle();
populateEpisodeSearchList();
populateRegionSearchList();
renderMap();

// ── Info-icon tooltips: fixed-position, computed on show ─────────────────
// .info-tip is `position:fixed` (see CSS comment above .info-tip) so it
// escapes the sidebar panel's scroll box instead of forcing a horizontal
// scrollbar; that means it has no CSS-only anchor to its icon, so we place
// it explicitly here, clamped to the viewport so it's never cut off on
// either edge regardless of where the triggering icon sits in the panel.
function positionInfoTip(wrap) {
  const tip = wrap.querySelector('.info-tip');
  const iconRect = wrap.getBoundingClientRect();
  const tipWidth = tip.offsetWidth || 230;
  let left = iconRect.left;
  left = Math.min(left, window.innerWidth - tipWidth - 10);
  left = Math.max(left, 10);
  let top = iconRect.bottom + 6;
  tip.style.left = `${left}px`;
  tip.style.top = `${top}px`;
}
document.querySelectorAll('.info-wrap').forEach((wrap) => {
  wrap.addEventListener('mouseenter', () => positionInfoTip(wrap));
  wrap.addEventListener('focus', () => positionInfoTip(wrap));
});

// ── Top 5 Leaderboards page ──────────────────────────────────────────────
let lbGranularity = 'county';

// "Documentation" = how much data exists for an episode across every
// source tracked (sensors + HWMs + FEMA claims + inundation layers).
// Rainfall isn't included -- it's a intensity measure, not a count of
// documentation artifacts, so it'd skew the composite toward wet episodes
// regardless of how much else was actually recorded about them.
function docScore(ep, gran) {
  return ep.n_sensors[gran].total + ep.n_hwms + ep.fema_points.length + ep.n_inundation_layers;
}

// group ties each category to one of LEADERBOARD_GROUPS below -- purely a
// display grouping for the page, doesn't affect how any value is computed.
const LEADERBOARD_GROUPS = [
  { id: 'instrumentation', label: 'Instrumentation', desc: 'Sensor and rain-gauge coverage per episode.' },
  { id: 'impacts', label: 'Impacts', desc: 'Real-world cost and harm per episode.' },
  { id: 'documentation', label: 'Documentation & Context', desc: 'How thoroughly an episode is documented, and who it affected.' },
  { id: 'region', label: 'By Region', desc: 'Rollups across every episode touching a watershed or county.' },
];

const LEADERBOARD_CATEGORIES = [
  { group: 'instrumentation', label: 'IFC River Sensors', getValue: (ep) => ep.n_sensors[lbGranularity].ifc_river },
  { group: 'instrumentation', label: 'IFC Hydrostations', getValue: (ep) => ep.n_sensors[lbGranularity].ifc_hydrostation },
  { group: 'instrumentation', label: 'USGS Sensors', getValue: (ep) => ep.n_sensors[lbGranularity].usgs },
  { group: 'instrumentation', label: 'Total Sensors (all types)', getValue: (ep) => ep.n_sensors[lbGranularity].total },
  { group: 'instrumentation', label: 'USGS High-Water Marks', getValue: (ep) => ep.n_hwms },
  { group: 'instrumentation', label: 'Accumulated Rainfall', unit: ' mm', getValue: (ep) => ep.precip_total_mm },
  {
    group: 'instrumentation',
    label: 'Most Intense Rainfall',
    subLabel: 'peak single-hour rain rate observed anywhere in the episode’s matched area (MRMS QPE)',
    getValue: (ep) => (ep.precip_max_intensity_mm != null ? ep.precip_max_intensity_mm : -1),
    format: (v) => (v < 0 ? 'no data' : `${v.toFixed(1)} mm/hr`),
  },
  {
    group: 'instrumentation',
    label: 'Most Severe Rainfall Recurrence',
    subLabel: 'FLASH QPE ARI -- the single most severe (highest-recurrence) cell found anywhere in the episode’s window, across the 30-min/1h/3h/6h/24h durations',
    getValue: (ep) => (ep.flash_most_severe ? ep.flash_most_severe.ari_years : -1),
    format: (v, ep) => {
      if (v < 0 || !ep.flash_most_severe) return 'no data';
      const s = ep.flash_most_severe;
      return `${ariBlockLabel(v)} (${FLASH_DURATION_PROSE_LABELS[s.duration]}, ${formatUtc(s.datetime_utc)})`;
    },
  },
  { group: 'impacts', label: 'FEMA NFIP Payout', getValue: (ep) => ep.fema_payout_usd, format: (v) => '$' + v.toLocaleString() },
  { group: 'impacts', label: 'Most NOAA Estimated Property Damage', getValue: (ep) => ep.damage_property_usd, format: (v) => '$' + v.toLocaleString() },
  { group: 'impacts', label: 'Most Flood-Related Crop Indemnity (USDA RMA)', getValue: (ep) => ep.crop_indemnity_usd, format: (v) => '$' + v.toLocaleString() },
  { group: 'impacts', label: 'Most Injuries (direct + indirect)', getValue: (ep) => ep.n_injuries },
  { group: 'impacts', label: 'Most Deaths (direct + indirect)', getValue: (ep) => ep.n_deaths },
  { group: 'documentation', label: 'IFC Inundation Map Layers', getValue: (ep) => ep.n_inundation_layers },
  {
    group: 'documentation',
    label: 'Most Vulnerable Counties Touched (SVI)',
    getValue: (ep) => ep.counties.reduce((max, c) => Math.max(max, countySviLookup[c] ?? 0), 0),
    format: (v) => v.toFixed(2),
  },
  {
    group: 'documentation',
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

// Region-level "most intense rainfall" -- unlike computeRegionLeaderboard
// (which SUMS a score across every episode touching a region), this takes
// the MAX: for each region, which single episode produced the highest peak
// single-hour rain rate found within THAT region's own boundary alone (via
// ep.region_intensity_mm, precisely polygon-clipped server-side -- see
// build_embedded_mrms_grids.py's compute_region_intensities()), not the
// episode's whole matched-area footprint. Remembers which episode achieved
// it so a leaderboard row can jump straight to that one.
function computeRegionIntensityLeaderboard(regionKey, geometryFeatures, idField, nameField, granularity) {
  const best = {};
  for (const [epId, ep] of Object.entries(WIZARD_DATA.episodes)) {
    const values = (ep.region_intensity_mm && ep.region_intensity_mm[granularity]) || {};
    for (const regionId of ep[regionKey]) {
      const v = values[regionId];
      if (v == null) continue;
      if (!best[regionId] || v > best[regionId].value) best[regionId] = { episodeId: epId, value: v };
    }
  }
  const nameLookup = {};
  for (const f of geometryFeatures) nameLookup[f.properties[idField]] = f.properties[nameField];

  return Object.entries(best)
    .map(([id, b]) => ({ id, name: nameLookup[id] || id, value: b.value, episodeId: b.episodeId }))
    .sort((a, b) => b.value - a.value)
    .slice(0, 5);
}

function buildLeaderboardCard(title, subLabel, rows) {
  const card = document.createElement('div');
  card.className = 'leaderboard-card';
  const subLabelHtml = subLabel ? `<div class="lb-sublabel">Counts: ${subLabel}</div>` : '';
  card.innerHTML = `<h3>${title}</h3>${subLabelHtml}<table><tbody>${rows}</tbody></table>`;
  return card;
}

function renderLeaderboard() {
  // Quick-nav pills: one per section, jumps via native #anchor + the
  // smooth-scroll CSS on #page-leaderboard, with scroll-margin-top on each
  // section so the sticky bar above doesn't cover the heading it jumps to.
  document.getElementById('lb-quicknav-links').innerHTML = LEADERBOARD_GROUPS
    .map((g) => `<a href="#lb-section-${g.id}">${g.label}</a>`).join('');

  const sectionsEl = document.getElementById('lb-sections');
  sectionsEl.innerHTML = LEADERBOARD_GROUPS.map((g) => `
    <section class="lb-section" id="lb-section-${g.id}">
      <h2 class="lb-section-title">${g.label}</h2>
      <div class="lb-section-desc">${g.desc}</div>
      <div class="leaderboard-grid" id="lb-grid-${g.id}"></div>
    </section>`).join('');

  for (const cat of LEADERBOARD_CATEGORIES) {
    const ranked = Object.entries(WIZARD_DATA.episodes)
      .map(([id, ep]) => ({ id, ep, value: cat.getValue(ep) }))
      .sort((a, b) => b.value - a.value)
      .slice(0, 5);

    const rows = ranked.map((r, i) => `
      <tr>
        <td class="rank">${i + 1}</td>
        <td><b>${r.id}</b><br><span style="color:#888">${r.ep.event_types.join(', ')} — ${r.ep.begin_date.slice(0, 10)}</span><br><span style="color:#888">${episodeLocationLabel(r.ep)}</span></td>
        <td class="value">${cat.format ? cat.format(r.value, r.ep) : r.value}${cat.unit || ''}</td>
      </tr>`).join('');

    const card = buildLeaderboardCard(cat.label, cat.subLabel, rows);
    card.querySelectorAll('tbody tr').forEach((tr, i) => {
      tr.addEventListener('click', () => openEpisodeModal(ranked[i].id, lbGranularity));
    });
    document.getElementById(`lb-grid-${cat.group}`).appendChild(card);
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
    const card = buildLeaderboardCard(rb.label, rb.subLabel, rows);
    card.querySelectorAll('tbody tr').forEach((tr, i) => {
      tr.addEventListener('click', () => openRegionModal(ranked[i].name, ranked[i].episodeIds, rb.gran));
    });
    document.getElementById('lb-grid-region').appendChild(card);
  }

  const regionIntensityBoards = [
    {
      label: 'Most Intense Rainfall (Watersheds)',
      subLabel: 'peak single-hour rain rate recorded within that watershed alone, from whichever episode hit it hardest',
      key: 'huc8s', geo: WIZARD_DATA.huc8_geometry.features, idField: 'huc8', nameField: 'huc8_name', gran: 'huc8',
    },
    {
      label: 'Most Intense Rainfall (Counties)',
      subLabel: 'peak single-hour rain rate recorded within that county alone, from whichever episode hit it hardest',
      key: 'counties', geo: WIZARD_DATA.county_geometry.features, idField: 'county_fips', nameField: 'county_name', gran: 'county',
    },
  ];
  for (const rb of regionIntensityBoards) {
    const ranked = computeRegionIntensityLeaderboard(rb.key, rb.geo, rb.idField, rb.nameField, rb.gran);
    const rows = ranked.map((r, i) => `
      <tr>
        <td class="rank">${i + 1}</td>
        <td><b>${r.name}</b><br><span style="color:#888">${r.episodeId}</span></td>
        <td class="value">${r.value.toFixed(1)} mm/hr</td>
      </tr>`).join('');
    const card = buildLeaderboardCard(rb.label, rb.subLabel, rows);
    card.querySelectorAll('tbody tr').forEach((tr, i) => {
      tr.addEventListener('click', () => openRegionIntensityModal(ranked[i].id, ranked[i].name, ranked[i].episodeId, rb.gran, ranked[i].value));
    });
    document.getElementById('lb-grid-region').appendChild(card);
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
document.getElementById('nav-map').addEventListener('click', () => {
  switchPage('map');
  syncMapControlsFromState();
  resetRegionSearch();
  renderMap();
});
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
// Filter thresholds (granularity, sensorMin, hwmMin, femaPayoutMin,
// precipMin, inundationRequired, basinSizeFilter, sviMin, injuriesMin,
// deathsMin, propDmgMin, cropDmgMin) are the SAME module-level state the map
// page's sliders write to (declared above) -- the download wizard reads and
// writes those directly so a filter set on either page is reflected on the
// other. dlSensorTotalMin is the one DL-only exception: the map page has no
// "total sensors" slider (only the three per-type ones), so this has no map
// counterpart to share.
let dlSensorAdvanced = false;
let dlSensorTotalMin = 0;
let dlStage = 0;  // 0-4 filter stages, 5 = results
let dlSlidersInitialized = false;

const DL_STAGE_TITLES = ['Region Type', 'Sensor Coverage', 'High-Water Marks & FEMA Payout', 'Rainfall', 'Everything Else'];

function dlBasinFilterActive() { return granularity === 'huc8' && basinSizeFilter !== 'all'; }
function dlBasinPassValue(ep) {
  return ep.huc8s.some((h) => {
    const area = huc8AreaLookup[h];
    if (area == null) return false;
    return basinSizeFilter === 'above' ? area > BASIN_SIZE_THRESHOLD_SQKM : area <= BASIN_SIZE_THRESHOLD_SQKM;
  }) ? 1 : 0;
}
function dlSviFilterActive() { return granularity === 'county' && sviMin > 0; }
function dlSviValue(ep) { return ep.counties.reduce((max, c) => Math.max(max, countySviLookup[c] ?? 0), 0); }

function dlStageFilters(stageIndex) {
  switch (stageIndex) {
    case 0:
      return [];
    case 1:
      return dlSensorAdvanced
        ? [
            { label: 'USGS Sensors', getValue: (ep) => ep.n_sensors[granularity].usgs, threshold: () => sensorMin.usgs },
            { label: 'IFC River (Bridge) Sensors', getValue: (ep) => ep.n_sensors[granularity].ifc_river, threshold: () => sensorMin.ifc_river },
            { label: 'IFC Hydrostations', getValue: (ep) => ep.n_sensors[granularity].ifc_hydrostation, threshold: () => sensorMin.ifc_hydrostation },
          ]
        : [{ label: 'Total Sensors', getValue: (ep) => ep.n_sensors[granularity].total, threshold: () => dlSensorTotalMin }];
    case 2:
      return [
        { label: 'USGS High-Water Marks', getValue: (ep) => ep.n_hwms, threshold: () => hwmMin },
        { label: 'FEMA NFIP Payout ($)', getValue: (ep) => ep.fema_payout_usd, threshold: () => femaPayoutMin },
      ];
    case 3:
      return [
        { label: 'Accumulated Rainfall (mm)', getValue: (ep) => ep.precip_total_mm, threshold: () => precipMin },
        { label: 'Recurrence Interval (yr)', getValue: episodeFlashAriYears, threshold: () => flashRecurrenceMin },
      ];
    case 4: {
      const fs = [
        { label: 'Injuries', getValue: (ep) => ep.n_injuries, threshold: () => injuriesMin },
        { label: 'Deaths', getValue: (ep) => ep.n_deaths, threshold: () => deathsMin },
        { label: 'NOAA Estimated Property Damage ($)', getValue: (ep) => ep.damage_property_usd, threshold: () => propDmgMin },
        { label: 'Flood-Related Crop Indemnity, USDA RMA ($)', getValue: (ep) => ep.crop_indemnity_usd, threshold: () => cropDmgMin },
      ];
      if (inundationRequired) fs.push({ label: 'Inundation Maps Available', getValue: (ep) => (ep.n_inundation_layers > 0 ? 1 : 0), threshold: () => 1 });
      if (dlBasinFilterActive()) fs.push({ label: 'Basin Size', getValue: dlBasinPassValue, threshold: () => 1 });
      if (dlSviFilterActive()) fs.push({ label: 'County SVI', getValue: dlSviValue, threshold: () => sviMin });
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
  document.getElementById('dl-fema').max = Math.ceil(Math.max(...eps.map((ep) => ep.fema_payout_usd)));
  document.getElementById('dl-precip').max = Math.ceil(Math.max(...eps.map((ep) => ep.precip_total_mm)));
  document.getElementById('dl-flash').min = 0;
  document.getElementById('dl-flash').max = ARI_BUCKETS.length;
  document.getElementById('dl-flash').step = 1;
  document.getElementById('dl-injuries').max = Math.max(...eps.map((ep) => ep.n_injuries));
  document.getElementById('dl-deaths').max = Math.max(...eps.map((ep) => ep.n_deaths));
  document.getElementById('dl-propdmg').max = Math.ceil(Math.max(...eps.map((ep) => ep.damage_property_usd)));
  document.getElementById('dl-cropdmg').max = Math.ceil(Math.max(...eps.map((ep) => ep.crop_indemnity_usd)));
}

// Mirror image of syncMapControlsFromState() -- pushes the shared filter
// state onto the Download page's own controls. Called at the top of every
// renderDownloadWizard() so a filter set on the Map page (or via Back/Next
// navigation within this wizard itself) always shows correctly here too.
function syncDLControlsFromState() {
  document.getElementById('dl-btn-county').classList.toggle('active', granularity === 'county');
  document.getElementById('dl-btn-huc8').classList.toggle('active', granularity === 'huc8');
  populateDLRegionSearchList();

  document.getElementById('dl-sensor-usgs').value = sensorMin.usgs;
  document.getElementById('dl-sensor-usgs-value').textContent = sensorMin.usgs;
  document.getElementById('dl-sensor-river').value = sensorMin.ifc_river;
  document.getElementById('dl-sensor-river-value').textContent = sensorMin.ifc_river;
  document.getElementById('dl-sensor-hydro').value = sensorMin.ifc_hydrostation;
  document.getElementById('dl-sensor-hydro-value').textContent = sensorMin.ifc_hydrostation;

  document.getElementById('dl-hwm').value = hwmMin;
  document.getElementById('dl-hwm-value').textContent = hwmMin;
  document.getElementById('dl-fema').value = femaPayoutMin;
  document.getElementById('dl-fema-value').textContent = '$' + femaPayoutMin.toLocaleString();
  document.getElementById('dl-precip').value = precipMin;
  document.getElementById('dl-precip-value').textContent = precipMin;
  const dlFlashIdx = flashRecurrenceMin > 0 ? ARI_BUCKETS.indexOf(flashRecurrenceMin) + 1 : 0;
  document.getElementById('dl-flash').value = dlFlashIdx;
  document.getElementById('dl-flash-value').textContent = dlFlashIdx === 0 ? 'Any' : `${flashRecurrenceMin}-yr`;
  document.getElementById('dl-inundation').checked = inundationRequired;

  Object.entries(dlBasinButtons).forEach(([value, id]) => {
    document.getElementById(id).classList.toggle('active', value === basinSizeFilter);
  });

  document.getElementById('dl-svi').value = sviMin;
  document.getElementById('dl-svi-value').textContent = sviMin.toFixed(2);
  document.getElementById('dl-injuries').value = injuriesMin;
  document.getElementById('dl-injuries-value').textContent = injuriesMin;
  document.getElementById('dl-deaths').value = deathsMin;
  document.getElementById('dl-deaths-value').textContent = deathsMin;
  document.getElementById('dl-propdmg').value = propDmgMin;
  document.getElementById('dl-propdmg-value').textContent = '$' + propDmgMin.toLocaleString();
  document.getElementById('dl-cropdmg').value = cropDmgMin;
  document.getElementById('dl-cropdmg-value').textContent = '$' + cropDmgMin.toLocaleString();
}

function renderDownloadWizard() {
  if (!dlSlidersInitialized) initDLSliders();
  syncDLControlsFromState();

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
        const counts = ep.n_sensors[granularity];
        return `<tr>
          <td><input type="checkbox" class="dl-row-select" data-id="${id}" checked></td>
          <td><b>${id}</b></td>
          <td>${ep.begin_date.slice(0, 10)}</td>
          <td>${episodeLocationLabel(ep)}</td>
          <td>${counts.total}</td>
          <td>${ep.n_hwms}</td>
          <td>$${ep.fema_payout_usd.toLocaleString()}</td>
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

function populateDLRegionSearchList() {
  const geo = granularity === 'county' ? WIZARD_DATA.county_geometry : WIZARD_DATA.huc8_geometry;
  const nameField = granularity === 'county' ? 'county_name' : 'huc8_name';
  const names = [...new Set(geo.features.map((f) => f.properties[nameField]))].sort();
  document.getElementById('dl-region-search-list').innerHTML = names.map((n) => `<option value="${n}">`).join('');
}
document.getElementById('dl-btn-county').addEventListener('click', () => {
  granularity = 'county';
  document.getElementById('dl-region-search').value = '';
  document.getElementById('dl-region-search-result').innerHTML = '';
  renderDownloadWizard();
});
document.getElementById('dl-btn-huc8').addEventListener('click', () => {
  granularity = 'huc8';
  document.getElementById('dl-region-search').value = '';
  document.getElementById('dl-region-search-result').innerHTML = '';
  renderDownloadWizard();
});

// ── Download page: search-and-download shortcut, bypasses the 5-stage
// filter entirely -- for when you already know the exact episode or
// region you want rather than wanting to narrow down by criteria. ──
document.getElementById('dl-episode-search-list').innerHTML =
  Object.keys(WIZARD_DATA.episodes).sort().map((id) => `<option value="${id}">`).join('');
populateDLRegionSearchList();

function dlSearchEpisode() {
  const val = document.getElementById('dl-episode-search').value.trim();
  const resultEl = document.getElementById('dl-episode-search-result');
  if (!val) { resultEl.innerHTML = ''; return; }
  const ep = WIZARD_DATA.episodes[val];
  if (!ep) { resultEl.innerHTML = `<div class="search-not-found">No episode "${val}" found.</div>`; return; }
  resultEl.innerHTML = `
    <div class="search-result-card">
      <b>${val}</b> — ${ep.event_types.join(', ')} (${ep.begin_date.slice(0, 10)})<br>
      <span style="color:#888">${episodeLocationLabel(ep)}</span><br>
      <button class="dl-download-btn search-download-btn" id="dl-episode-search-download">Download this episode's ZIP</button>
    </div>`;
  document.getElementById('dl-episode-search-download').addEventListener('click', (e) => {
    const readme = buildEpisodesReadme('single-episode export', granularity, `Episode: ${val}`);
    downloadEpisodesZip([val], granularity, `${val}.zip`, readme, e.currentTarget);
  });
}
function dlSearchRegion() {
  const val = document.getElementById('dl-region-search').value.trim();
  const resultEl = document.getElementById('dl-region-search-result');
  if (!val) { resultEl.innerHTML = ''; return; }
  const geo = granularity === 'county' ? WIZARD_DATA.county_geometry : WIZARD_DATA.huc8_geometry;
  const idField = granularity === 'county' ? 'county_fips' : 'huc8';
  const nameField = granularity === 'county' ? 'county_name' : 'huc8_name';
  const needle = val.toLowerCase();
  const feature = geo.features.find((f) => f.properties[nameField].toLowerCase() === needle)
      || geo.features.find((f) => f.properties[nameField].toLowerCase().includes(needle));
  if (!feature) { resultEl.innerHTML = `<div class="search-not-found">No ${granularity === 'county' ? 'county' : 'watershed'} matching "${val}".</div>`; return; }
  const regionKey = granularity === 'county' ? 'counties' : 'huc8s';
  const regionId = feature.properties[idField];
  const regionName = feature.properties[nameField];
  const ids = Object.keys(WIZARD_DATA.episodes).filter((id) => WIZARD_DATA.episodes[id][regionKey].includes(regionId));
  resultEl.innerHTML = `
    <div class="search-result-card">
      <b>${regionName}</b> — ${ids.length} episode(s) touching it<br>
      <button class="dl-download-btn search-download-btn" id="dl-region-search-download" ${ids.length ? '' : 'disabled'}>Download all ${ids.length} episode(s) as ZIP</button>
    </div>`;
  if (ids.length) {
    document.getElementById('dl-region-search-download').addEventListener('click', (e) => {
      const readme = buildEpisodesReadme('region export', granularity, `Region: ${regionName}\r\nEpisodes included: ${ids.length}`);
      downloadEpisodesZip(ids, granularity, `${regionName.replace(/\s+/g, '_')}_episodes.zip`, readme, e.currentTarget);
    });
  }
}
document.getElementById('dl-episode-search').addEventListener('input', dlSearchEpisode);
document.getElementById('dl-region-search').addEventListener('input', dlSearchRegion);

document.getElementById('dl-sensor-total').addEventListener('input', (e) => {
  dlSensorTotalMin = parseInt(e.target.value, 10);
  document.getElementById('dl-sensor-total-value').textContent = dlSensorTotalMin;
  renderDownloadWizard();
});
document.getElementById('dl-sensor-advanced-toggle').addEventListener('change', (e) => {
  dlSensorAdvanced = e.target.checked;
  document.getElementById('dl-sensor-advanced').classList.toggle('show', dlSensorAdvanced);
  renderDownloadWizard();
});
document.getElementById('dl-sensor-usgs').addEventListener('input', (e) => {
  sensorMin.usgs = parseInt(e.target.value, 10);
  document.getElementById('dl-sensor-usgs-value').textContent = sensorMin.usgs;
  renderDownloadWizard();
});
document.getElementById('dl-sensor-river').addEventListener('input', (e) => {
  sensorMin.ifc_river = parseInt(e.target.value, 10);
  document.getElementById('dl-sensor-river-value').textContent = sensorMin.ifc_river;
  renderDownloadWizard();
});
document.getElementById('dl-sensor-hydro').addEventListener('input', (e) => {
  sensorMin.ifc_hydrostation = parseInt(e.target.value, 10);
  document.getElementById('dl-sensor-hydro-value').textContent = sensorMin.ifc_hydrostation;
  renderDownloadWizard();
});

document.getElementById('dl-hwm').addEventListener('input', (e) => {
  hwmMin = parseInt(e.target.value, 10);
  document.getElementById('dl-hwm-value').textContent = hwmMin;
  renderDownloadWizard();
});
document.getElementById('dl-fema').addEventListener('input', (e) => {
  femaPayoutMin = parseFloat(e.target.value);
  document.getElementById('dl-fema-value').textContent = '$' + femaPayoutMin.toLocaleString();
  renderDownloadWizard();
});

document.getElementById('dl-precip').addEventListener('input', (e) => {
  precipMin = parseFloat(e.target.value);
  document.getElementById('dl-precip-value').textContent = precipMin;
  renderDownloadWizard();
});
document.getElementById('dl-flash').addEventListener('input', (e) => {
  const idx = parseInt(e.target.value, 10);
  flashRecurrenceMin = idx === 0 ? 0 : ARI_BUCKETS[idx - 1];
  document.getElementById('dl-flash-value').textContent = idx === 0 ? 'Any' : `${flashRecurrenceMin}-yr`;
  renderDownloadWizard();
});

document.getElementById('dl-inundation').addEventListener('change', (e) => {
  inundationRequired = e.target.checked;
  renderDownloadWizard();
});
const dlBasinButtons = { all: 'dl-basin-all', above: 'dl-basin-above', below: 'dl-basin-below' };
Object.entries(dlBasinButtons).forEach(([value, id]) => {
  document.getElementById(id).addEventListener('click', () => {
    basinSizeFilter = value;
    renderDownloadWizard();
  });
});
document.getElementById('dl-svi').addEventListener('input', (e) => {
  sviMin = parseFloat(e.target.value);
  document.getElementById('dl-svi-value').textContent = sviMin.toFixed(2);
  renderDownloadWizard();
});
document.getElementById('dl-injuries').addEventListener('input', (e) => {
  injuriesMin = parseInt(e.target.value, 10);
  document.getElementById('dl-injuries-value').textContent = injuriesMin;
  renderDownloadWizard();
});
document.getElementById('dl-deaths').addEventListener('input', (e) => {
  deathsMin = parseInt(e.target.value, 10);
  document.getElementById('dl-deaths-value').textContent = deathsMin;
  renderDownloadWizard();
});
document.getElementById('dl-propdmg').addEventListener('input', (e) => {
  propDmgMin = parseFloat(e.target.value);
  document.getElementById('dl-propdmg-value').textContent = '$' + propDmgMin.toLocaleString();
  renderDownloadWizard();
});
document.getElementById('dl-cropdmg').addEventListener('input', (e) => {
  cropDmgMin = parseFloat(e.target.value);
  document.getElementById('dl-cropdmg-value').textContent = '$' + cropDmgMin.toLocaleString();
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

// Shared by every download entry point (map button, DL wizard results,
// DL episode/region search shortcuts, leaderboard modal) so the folder
// contents list only needs to be kept accurate in one place.
function buildEpisodesReadme(scopeLabel, gran, extraLine) {
  return `Iowa Flood Compendium -- ${scopeLabel}\r\nGenerated: ${new Date().toISOString()}\r\nGranularity: ${gran}\r\n${extraLine}\r\n\r\nEach episode folder contains:\r\n  summary.csv              -- episode metadata and headline counts\r\n  usgs_sensors/*.csv        -- one file per USGS sensor, time series over the episode window\r\n  ifc_hydrostations/*.csv   -- one file per IFC hydrostation, time series over the episode window\r\n  ifc_river_sensors/*.csv   -- one file per IFC river sensor, time series over the episode window\r\n  mrms_precipitation/hourly_precip.csv -- hourly MRMS rainfall (mean_mm, max_mm) over the episode window\r\n  hwms.csv                  -- matched USGS High-Water Mark locations\r\n  fema_claims.csv           -- matched FEMA NFIP claim locations\r\n\r\nSensors included are the union of county- and HUC8-matched sensors, so the same episode folder is complete regardless of which region view you were filtering under.\r\n`;
}

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
    fema_nfip_payout_usd: ep.fema_payout_usd,
    n_inundation_layers: ep.n_inundation_layers,
    precip_total_mm: ep.precip_total_mm,
    n_injuries: ep.n_injuries,
    n_deaths: ep.n_deaths,
    damage_property_usd: ep.damage_property_usd,
    crop_flood_indemnity_usd_rma: ep.crop_indemnity_usd,
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
          n_sensors: ep.n_sensors[granularity].total, n_hwms: ep.n_hwms, fema_nfip_payout_usd: ep.fema_payout_usd, precip_total_mm: ep.precip_total_mm,
        };
      });
      zip.file('manifest.csv', toCsv(manifestRows, ['episode_id', 'begin_date', 'event_types', 'n_sensors', 'n_hwms', 'fema_nfip_payout_usd', 'precip_total_mm']));
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
  const readme = buildEpisodesReadme('filtered episode export', granularity, `Episodes selected: ${ids.length}`);
  downloadEpisodesZip(ids, granularity, `iowa_flood_episodes_${ids.length}.zip`, readme, e.currentTarget);
});

// ── Leaderboard row click -> summary/download popup ──────────────────────
let lbModalTarget = null;

// duration key ('30m', '3h', ...) -> label used in prose vs. table headers.
const FLASH_DURATION_PROSE_LABELS = { '30m': '30-minute', '1h': '1-hour', '3h': '3-hour', '6h': '6-hour', '24h': '24-hour' };
const FLASH_DURATION_COL_LABELS = { ari_30m: '30-min', ari_1h: '1-hr', ari_3h: '3-hr', ari_6h: '6-hr', ari_24h: '24-hr' };
const FLASH_DURATION_COLS = Object.keys(FLASH_DURATION_COL_LABELS);

function formatUtc(isoStr) {
  return isoStr.replace('T', ' ') + ' UTC';
}

// idSuffix lets the same line + toggle/table render in more than one panel
// at once (the leaderboard modal and the map's region-detail panel both use
// this) without colliding on the same DOM ids.
function flashSevereLine(ep, episodeId, idSuffix) {
  idSuffix = idSuffix || '';
  if (!ep.flash_most_severe) {
    return `Most severe rainfall recurrence: <span style="color:#999">not available</span>`;
  }
  const s = ep.flash_most_severe;
  return `Most severe rainfall recurrence: <b>${ariBlockLabel(s.ari_years)}</b> (${FLASH_DURATION_PROSE_LABELS[s.duration]}) at ${formatUtc(s.datetime_utc)}`
    + ` <button class="flash-toggle-btn" id="flash-toggle-btn${idSuffix}" onclick="toggleFlashTable('${episodeId}','${idSuffix}')">Show hourly table &#9662;</button>`;
}

function openEpisodeModal(episodeId, granularity) {
  const ep = WIZARD_DATA.episodes[episodeId];
  lbModalTarget = { type: 'episode', id: episodeId, granularity };
  document.getElementById('lb-modal-title').textContent = episodeId;
  document.getElementById('lb-modal-body').innerHTML = `
    <div>${ep.event_types.join(', ')} — ${ep.begin_date.slice(0, 10)} to ${ep.end_date.slice(0, 10)}</div>
    <div class="lb-loc">${episodeLocationLabel(ep)}</div>
    Sensors: ${ep.n_sensors[granularity].total} &nbsp;|&nbsp; HWMs: ${ep.n_hwms} &nbsp;|&nbsp; FEMA NFIP payout: $${ep.fema_payout_usd.toLocaleString()}<br>
    Inundation layers: ${ep.n_inundation_layers} &nbsp;|&nbsp; Rainfall: ${ep.precip_total_mm} mm avg &middot; ${ep.precip_median_mm} mm median (pixel) &middot; ${ep.precip_max_mm} mm max (pixel)<br>
    ${flashSevereLine(ep, episodeId)}
    <div id="flash-table-container" class="flash-table-wrap" style="display:none"></div>
    Injuries: ${ep.n_injuries} &nbsp;|&nbsp; Deaths: ${ep.n_deaths}<br>
    Property damage: $${ep.damage_property_usd.toLocaleString()} &nbsp;|&nbsp; Flood-related crop indemnity (USDA RMA): $${ep.crop_indemnity_usd.toLocaleString()}
  `;
  document.getElementById('lb-modal-view-map').style.display = 'inline-block';
  document.getElementById('lb-modal-overlay').style.display = 'flex';
}

// FLASH hourly table is lazily decompressed/parsed on first expand (same
// getEpisodeDataPayload + parseCsv already used for the sensor charts), then
// cached per episode so re-toggling doesn't re-parse. idSuffix picks which
// panel's container/button to operate on -- see flashSevereLine's comment.
let _flashTableCache = {};
async function toggleFlashTable(episodeId, idSuffix) {
  idSuffix = idSuffix || '';
  const container = document.getElementById('flash-table-container' + idSuffix);
  const btn = document.getElementById('flash-toggle-btn' + idSuffix);
  if (!container || !btn) return;

  if (container.style.display !== 'none') {
    container.style.display = 'none';
    btn.innerHTML = 'Show hourly table &#9662;';
    return;
  }

  if (!_flashTableCache[episodeId]) {
    const originalHtml = btn.innerHTML;
    btn.innerHTML = 'Loading…';
    btn.disabled = true;
    const episodeData = await getEpisodeDataPayload();
    const csv = (episodeData[episodeId] || {})['flash_recurrence/hourly_ari.csv'];
    btn.disabled = false;
    if (!csv) {
      btn.innerHTML = originalHtml;
      container.innerHTML = '<div style="padding:8px;color:#999;">No hourly FLASH data cached for this episode.</div>';
      container.style.display = 'block';
      return;
    }
    _flashTableCache[episodeId] = parseCsv(csv);
  }

  const rows = _flashTableCache[episodeId];
  const severe = WIZARD_DATA.episodes[episodeId].flash_most_severe;
  const severeCol = severe ? `ari_${severe.duration}` : null;
  // severe.datetime_utc comes from flash_summary.json as Python's isoformat()
  // ("...T06:00:00"); the table CSV's own datetime_utc column is written by
  // pandas.to_csv as space-separated ("...  06:00:00") -- normalize both to
  // the same separator before comparing, or the severe cell never matches.
  const severeDt = severe ? severe.datetime_utc.replace('T', ' ') : null;
  const head = `<tr><th>UTC hour</th>${FLASH_DURATION_COLS.map((c) => `<th>${FLASH_DURATION_COL_LABELS[c]}</th>`).join('')}</tr>`;
  const body = rows.map((r) => {
    const rowDt = r.datetime_utc.replace('T', ' ');
    const cells = FLASH_DURATION_COLS.map((c) => {
      const raw = r[c];
      if (raw === '' || raw === undefined) return '<td class="flash-empty">—</td>';
      const isSevere = severeCol === c && rowDt === severeDt;
      return `<td class="${isSevere ? 'flash-severe' : ''}">${ariBlockLabel(parseFloat(raw))}</td>`;
    }).join('');
    return `<tr><td>${rowDt}</td>${cells}</tr>`;
  }).join('');
  container.innerHTML = `<table class="flash-table">${head}${body}</table>`;
  container.style.display = 'block';
  btn.innerHTML = 'Hide hourly table &#9652;';
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
  document.getElementById('lb-modal-view-map').style.display = 'none';
  document.getElementById('lb-modal-overlay').style.display = 'flex';
}

// Opened from a "Most Intense Rainfall (Watersheds/Counties)" leaderboard
// row -- unlike openEpisodeModal (which summarizes the whole episode),
// this headlines the ONE region-clipped figure the row was ranked by,
// alongside the episode's own whole-footprint peak for comparison.
// lbModalTarget.forceRegionId tells "View Event in Map Viewer" to jump to
// this specific region instead of just the episode's first touched region.
function openRegionIntensityModal(regionId, regionName, episodeId, granularity, value) {
  const ep = WIZARD_DATA.episodes[episodeId];
  lbModalTarget = { type: 'episode', id: episodeId, granularity, forceRegionId: regionId };
  document.getElementById('lb-modal-title').textContent = episodeId;
  document.getElementById('lb-modal-body').innerHTML = `
    <div>${ep.event_types.join(', ')} — ${ep.begin_date.slice(0, 10)} to ${ep.end_date.slice(0, 10)}</div>
    <div class="lb-loc">${regionName} (${granularity === 'huc8' ? 'watershed' : 'county'})</div>
    Peak hourly intensity in ${regionName}: <b>${value.toFixed(1)} mm/hr</b><br>
    Episode-wide peak (entire footprint): ${ep.precip_max_intensity_mm != null ? ep.precip_max_intensity_mm.toFixed(1) + ' mm/hr' : 'n/a'}
  `;
  document.getElementById('lb-modal-view-map').style.display = 'inline-block';
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
    const readme = buildEpisodesReadme('single-episode export', lbModalTarget.granularity, `Episode: ${lbModalTarget.id}`);
    downloadEpisodesZip([lbModalTarget.id], lbModalTarget.granularity, `${lbModalTarget.id}.zip`, readme, e.currentTarget);
  } else {
    const readme = buildEpisodesReadme('region export', lbModalTarget.granularity, `Region: ${lbModalTarget.name}\r\nEpisodes included: ${lbModalTarget.ids.length}`);
    downloadEpisodesZip(lbModalTarget.ids, lbModalTarget.granularity, `${lbModalTarget.name.replace(/\s+/g, '_')}_episodes.zip`, readme, e.currentTarget);
  }
});
document.getElementById('lb-modal-view-map').addEventListener('click', () => {
  if (!lbModalTarget || lbModalTarget.type !== 'episode') return;
  document.getElementById('lb-modal-overlay').style.display = 'none';
  switchPage('map');
  // Match the region view the leaderboard row was ranked under first; if
  // this episode has no match there (e.g. it touches no HUC8 even though
  // it has county matches), fall back to the other granularity rather than
  // silently failing to jump anywhere.
  granularity = lbModalTarget.granularity;
  document.getElementById('btn-county').classList.toggle('active', granularity === 'county');
  document.getElementById('btn-huc8').classList.toggle('active', granularity === 'huc8');
  // From a "Most Intense Rainfall (Watersheds/Counties)" row, pin to that
  // SPECIFIC region (This Region scope) instead of just the episode's
  // first touched region -- see jumpToEpisodeRegion's comment.
  const jump = () => lbModalTarget.forceRegionId
    ? jumpToEpisodeRegion(lbModalTarget.id, lbModalTarget.forceRegionId)
    : jumpToEpisode(lbModalTarget.id);
  if (!jump()) {
    granularity = granularity === 'county' ? 'huc8' : 'county';
    document.getElementById('btn-county').classList.toggle('active', granularity === 'county');
    document.getElementById('btn-huc8').classList.toggle('active', granularity === 'huc8');
    jumpToEpisode(lbModalTarget.id);
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
    mrms_b64 = EMBEDDED_MRMS_GRIDS_PATH.read_text(encoding='ascii') if EMBEDDED_MRMS_GRIDS_PATH.exists() else ''
    if not mrms_b64:
        print(f'[!] {EMBEDDED_MRMS_GRIDS_PATH.name} not found -- the "Show MRMS accumulated precipitation" map toggle will have no data until build_embedded_mrms_grids.py has been run.')
    html = HTML_TEMPLATE.replace('__WIZARD_DATA_JSON__', json.dumps(data))
    html = html.replace('__EMBEDDED_EPISODE_DATA_B64__', embedded_b64)
    html = html.replace('__EMBEDDED_MRMS_GRIDS_B64__', mrms_b64)
    OUT_PATH.write_text(html, encoding='utf-8')
    print(f'[OK] wrote {OUT_PATH} ({OUT_PATH.stat().st_size / 1e6:.2f} MB)')


if __name__ == '__main__':
    build()
