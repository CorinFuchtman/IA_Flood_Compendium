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
    recompute = function () { var r = origRecompute(); renderImpacts(); return r; };
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
