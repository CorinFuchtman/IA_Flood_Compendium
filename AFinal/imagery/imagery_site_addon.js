/* Satellite imagery add-on for the Iowa Flood Compendium wizard.
 * Injected by augment_site_imagery.py. Requires WIZARD_DATA episodes to carry
 * imagery_grade, n_overpasses_during, overpasses[] (added by the same script).
 * Additive: wraps episodePassesFilter, recompute, renderDetailEpisode and
 * addEpisodeToZip instead of editing them, so the page still works without it. */
(function () {
  if (window.__IMAGERY_ADDON__) return;
  window.__IMAGERY_ADDON__ = true;
  var eps = (typeof WIZARD_DATA !== 'undefined' && WIZARD_DATA.episodes) || {};
  var epIds = Object.keys(eps);
  if (!epIds.length || typeof eps[epIds[0]].imagery_grade === 'undefined') return;

  var GRADES = {
    clear: { label: 'Clear optical image of the flood', color: '#1e8e3e' },
    radar: { label: 'Radar image of the flood (sees through cloud)', color: '#1a73e8' },
    cloudy: { label: 'Passes happened but too cloudy', color: '#9aa0a6' },
    none: { label: 'No pass in the window', color: '#c5c9cd' }
  };
  var counts = { clear: 0, radar: 0, cloudy: 0, none: 0 };
  var nBaseline = 0;
  epIds.forEach(function (id) {
    var g = eps[id].imagery_grade || 'none';
    if (counts[g] === undefined) counts[g] = 0;
    counts[g] += 1;
    if (eps[id].has_flood_imagery && eps[id].has_baseline_imagery) nBaseline += 1;
  });
  var nUsable = counts.clear + counts.radar;

  /* imageryMode: any | flood | baseline */
  var imageryMode = 'any';

  /* ---------- panel UI ---------- */
  var section = document.createElement('div');
  section.className = 'section';
  section.id = 'imagery-section';
  section.innerHTML =
    '<h3 style="margin:0 0 6px">Satellite imagery</h3>' +
    '<div class="legend" style="margin-bottom:8px">Satellite passes within 48 ' +
    'hours before and after each episode, from Sentinel-2, Sentinel-1 radar ' +
    'and Landsat. Pick episodes that were actually imaged while the water was ' +
    'up, and if you need change detection, ones with a clear image from before ' +
    'the flood as well.</div>' +
    '<div class="toggle-group" id="imagery-toggle" style="margin-bottom:6px">' +
    '<button type="button" data-mode="any" class="active">Any</button>' +
    '<button type="button" data-mode="flood">Imaged in flood (' + nUsable + ')</button>' +
    '<button type="button" data-mode="baseline">Plus baseline (' + nBaseline + ')</button>' +
    '</div>' +
    '<div class="legend" id="imagery-legend"></div>';

  var host = document.getElementById('impacts-section');
  if (host && host.parentNode) {
    host.parentNode.insertBefore(section, host.nextSibling);
  } else {
    var panel = document.getElementById('panel');
    if (panel) panel.appendChild(section);
  }
  var legendHtml = '';
  ['clear', 'radar', 'cloudy', 'none'].forEach(function (g) {
    legendHtml += '<span style="display:inline-block;width:9px;height:9px;' +
      'border-radius:50%;background:' + GRADES[g].color +
      ';margin:0 4px 0 0"></span>' + GRADES[g].label + ': ' +
      (counts[g] || 0) + ' episodes<br>';
  });
  document.getElementById('imagery-legend').innerHTML = legendHtml;

  /* ---------- filter wrap ---------- */
  function imageryPass(ep) {
    if (imageryMode === 'any') return true;
    if (!ep.has_flood_imagery) return false;
    if (imageryMode === 'baseline') return !!ep.has_baseline_imagery;
    return true;
  }
  try {
    var origFilter = episodePassesFilter;
    episodePassesFilter = function (ep) {
      return origFilter(ep) && imageryPass(ep);
    };
  } catch (e) { console.warn('imagery addon: filter wrap failed', e); }

  /* ---------- episode detail readout ---------- */
  function esc(t) {
    return String(t == null ? '' : t)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function overpassHtml(ep) {
    var list = (ep.overpasses || []).slice();
    if (!list.length) {
      return '<div style="margin-top:8px"><b>Satellite imagery</b><br>' +
        '<span style="color:#666">No overpass recorded in the window.</span></div>';
    }
    list.sort(function (a, b) { return a.t < b.t ? -1 : 1; });
    var rows = list.slice(0, 12).map(function (o) {
      var cloud = (o.c === '' || o.c === null || typeof o.c === 'undefined')
        ? 'radar' : o.c + '% cloud';
      var when = o.w === 'pre' ? ' <span style="color:#8a6d00">before the flood (baseline)</span>'
        : o.w === 'post' ? ' <span style="color:#666">after the flood ended</span>'
        : ' <span style="color:#1e8e3e">during the flood</span>';
      return '<div style="margin:2px 0">' +
        '<span style="display:inline-block;width:9px;height:9px;border-radius:50%;' +
        'background:' + (o.s === 'radar' ? GRADES.radar.color
          : (typeof o.c === 'number' && o.c < 35 ? GRADES.clear.color : GRADES.cloudy.color)) +
        ';margin-right:5px"></span>' +
        '<b>' + esc(o.t.replace('T', ' ').replace('Z', ' UTC')) + '</b> ' +
        esc(o.p) + ', ' + Math.round((o.v || 0) * 100) + '% of area, ' +
        esc(cloud) + when + '</div>';
    }).join('');
    var more = list.length > 12
      ? '<div style="color:#666">and ' + (list.length - 12) + ' more</div>' : '';
    var g = GRADES[ep.imagery_grade] || GRADES.none;
    var baseNote = ep.has_baseline_imagery
      ? '<div style="color:#8a6d00">A clear pre-flood image is available for change detection.</div>' : '';
    return '<div style="margin-top:8px"><b>Satellite imagery</b> ' +
      '<span style="color:' + g.color + ';font-weight:600">' + g.label + '</span>' + baseNote +
      '<div style="font-size:11px;margin-top:4px">' + rows + more + '</div></div>';
  }

  try {
    var origDetail = renderDetailEpisode;
    renderDetailEpisode = function () {
      var r = origDetail.apply(this, arguments);
      try {
        var infoEl = document.getElementById('detail-episode-info');
        var ids = (typeof detailEpisodeIds !== 'undefined') ? detailEpisodeIds : [];
        var i = (typeof detailEpisodeIndex !== 'undefined') ? detailEpisodeIndex : 0;
        var id = ids[i];
        if (infoEl && id && eps[id]) {
          var div = document.createElement('div');
          div.innerHTML = overpassHtml(eps[id]);
          infoEl.appendChild(div);
        }
      } catch (e) { console.warn('imagery addon: detail render failed', e); }
      return r;
    };
  } catch (e) { console.warn('imagery addon: detail wrap failed', e); }

  /* ---------- overpasses.csv in per-episode download ZIPs ---------- */
  try {
    if (typeof addEpisodeToZip === 'function') {
      var origAddZip = addEpisodeToZip;
      addEpisodeToZip = function (zip, episodeId, granularity, episodeData) {
        var r = origAddZip(zip, episodeId, granularity, episodeData);
        try {
          var ep = eps[episodeId];
          if (ep && ep.overpasses && ep.overpasses.length) {
            var rows = ['overpass_utc,platform,sensor_type,aoi_coverage,' +
              'cloud_pct,hours_from_episode_begin,window_label'];
            ep.overpasses.forEach(function (o) {
              rows.push([o.t, o.p, o.s, o.v, o.c, o.h, o.w].join(','));
            });
            zip.file('episode_' + episodeId + '/satellite_overpasses.csv',
              rows.join('\n'));
          }
        } catch (e) { console.warn('imagery addon: zip append failed', e); }
        return r;
      };
    }
  } catch (e) { console.warn('imagery addon: download wrap failed', e); }

  /* ---------- listeners ---------- */
  var toggle = document.getElementById('imagery-toggle');
  toggle.addEventListener('click', function (ev) {
    var btn = ev.target.closest('button[data-mode]');
    if (!btn) return;
    imageryMode = btn.getAttribute('data-mode');
    Array.prototype.forEach.call(toggle.querySelectorAll('button'),
      function (b) { b.classList.toggle('active', b === btn); });
    try { recompute(); } catch (e) {}
    try { renderMap(); } catch (e) {}
  });
})();
