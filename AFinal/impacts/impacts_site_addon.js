/* Impacts add-on for the Iowa Flood Compendium wizard.
 * Injected by augment_site.py. Requires WIZARD_DATA episodes to carry
 * n_impacts, n_impacts_crowd, has_crowdsource, impact_points
 * (added by the same script). Additive: wraps episodePassesFilter and
 * recompute instead of editing them, so the page works with or without it. */
(function () {
  if (window.__IMPACTS_ADDON__) return;
  window.__IMPACTS_ADDON__ = true;
  var eps = (typeof WIZARD_DATA !== 'undefined' && WIZARD_DATA.episodes) || {};
  var epIds = Object.keys(eps);
  if (!epIds.length || typeof eps[epIds[0]].n_impacts === 'undefined') return;

  var maxImpacts = 0, nCrowd = 0, nRecords = 0;
  epIds.forEach(function (id) {
    var e = eps[id];
    maxImpacts = Math.max(maxImpacts, e.n_impacts || 0);
    nRecords += (e.n_impacts || 0);
    if (e.has_crowdsource) nCrowd += 1;
  });

  var impactsMin = 0, crowdOnly = false, showImpacts = true;
  var SEV_COLORS = ['#9aa0a6', '#f2c14e', '#e8710a', '#d93025'];
  var SEV_NAMES = ['overbank only', 'minor', 'moderate', 'major'];

  /* ---------- panel UI ---------- */
  var section = document.createElement('div');
  section.className = 'section';
  section.id = 'impacts-section';
  section.innerHTML =
    '<h3 style="margin:0 0 6px">Flood impact reports</h3>' +
    '<div class="legend" style="margin-bottom:6px">' + nRecords +
    ' geo-referenced impact records mined from NOAA storm narratives and ' +
    'local news (flooded and closed streets, rescues, evacuations, flooded ' +
    'homes).</div>' +
    '<div class="checkbox-row"><input type="checkbox" id="impacts-show" checked>' +
    '<label for="impacts-show" style="margin-bottom:0">Show impact reports on the map</label></div>' +
    '<div class="checkbox-row"><input type="checkbox" id="impacts-crowd-only">' +
    '<label for="impacts-crowd-only" style="margin-bottom:0">Only episodes with crowdsourced reports (' +
    nCrowd + ' episodes)</label></div>' +
    '<label for="impacts-min" style="display:block;margin-top:6px">Minimum impact records: ' +
    '<span id="impacts-min-val">0</span></label>' +
    '<input type="range" id="impacts-min" min="0" max="' + maxImpacts +
    '" value="0" step="1" style="width:100%">' +
    '<div class="legend" id="impacts-legend" style="margin-top:6px"></div>';
  var legendHtml = 'Severity: ';
  for (var s = 0; s <= 3; s++) {
    legendHtml += '<span style="display:inline-block;width:10px;height:10px;' +
      'border-radius:50%;background:' + SEV_COLORS[s] +
      ';margin:0 3px 0 8px"></span>' + s + ' ' + SEV_NAMES[s];
  }
  legendHtml += '<br><span style="display:inline-block;width:10px;height:10px;' +
    'border-radius:50%;background:#fff;border:2px solid #1a4d8f;margin-right:4px">' +
    '</span>ring = crowdsourced (news or agency report), no ring = NOAA narrative';
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
    if ((ep.n_impacts || 0) < impactsMin) return false;
    if (crowdOnly && !ep.has_crowdsource) return false;
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

  function esc(t) {
    return String(t == null ? '' : t)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function renderImpacts() {
    if (!impactsLayer) return;
    impactsLayer.clearLayers();
    if (!showImpacts) return;
    epIds.forEach(function (id) {
      var ep = eps[id];
      if (!ep.impact_points || !ep.impact_points.length) return;
      var ok = true;
      try { ok = episodePassesFilter(ep); } catch (e) {}
      if (!ok) return;
      ep.impact_points.forEach(function (p) {
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
        var pop = '<div style="max-width:280px"><b>' +
          esc(p.t.replace(/_/g, ' ')) + '</b> (severity ' + p.sv +
          ')<br><i>' + esc(p.d) + '</i> <br>' + esc(p.dt) +
          ' | episode ' + esc(id) + '<br>' + esc(p.tx || '') +
          (p.u ? '<br><a href="' + encodeURI(p.u) +
            '" target="_blank" rel="noopener">source</a>'
            : '<br>Source: NOAA Storm Events narrative') + '</div>';
        m.bindPopup(pop);
        impactsLayer.addLayer(m);
      });
    });
  }

  try {
    var origRecompute = recompute;
    recompute = function () { origRecompute(); renderImpacts(); };
  } catch (e) { console.warn('impacts addon: recompute wrap failed', e); }

  /* ---------- listeners ---------- */
  document.getElementById('impacts-show').addEventListener('change',
    function () { showImpacts = this.checked; renderImpacts(); });
  document.getElementById('impacts-crowd-only').addEventListener('change',
    function () { crowdOnly = this.checked; recompute(); try { renderMap(); } catch (e) {} });
  document.getElementById('impacts-min').addEventListener('input',
    function () {
      impactsMin = parseInt(this.value, 10) || 0;
      document.getElementById('impacts-min-val').textContent = this.value;
      recompute(); try { renderMap(); } catch (e) {}
    });

  try { recompute(); } catch (e) { renderImpacts(); }
})();
