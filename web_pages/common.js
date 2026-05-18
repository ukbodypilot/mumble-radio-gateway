/* Common utilities for all gateway pages — loaded from /pages/common.js */

// ── Theme ──────────────────────────────────────────────────────────────────

var _T = {};

function loadTheme() {
  return fetch('/theme')
    .then(function(r) { return r.json(); })
    .then(function(t) {
      _T = t;
      // Alias for shell.html compat (uses camelCase)
      _T.btnBorder = t.btn_border;
      var root = document.documentElement;
      root.style.setProperty('--t-bg', t.bg);
      root.style.setProperty('--t-panel', t.panel);
      root.style.setProperty('--t-border', t.border);
      root.style.setProperty('--t-accent', t.accent);
      root.style.setProperty('--t-btn', t.btn);
      root.style.setProperty('--t-btn-border', t.btn_border);
      root.style.setProperty('--t-btn-hover', t.btn_hover);
      root.style.setProperty('--t-btn-active', t.btn_active_bg);
      root.style.setProperty('--t-checkbox', t.checkbox);
      // Semantic tokens — each key optional; common.css defaults apply if absent.
      if (t.panel_hi)  root.style.setProperty('--t-panel-hi',  t.panel_hi);
      if (t.border_hi) root.style.setProperty('--t-border-hi', t.border_hi);
      if (t.text)      root.style.setProperty('--t-text',      t.text);
      if (t.text_dim)  root.style.setProperty('--t-text-dim',  t.text_dim);
      if (t.text_mute) root.style.setProperty('--t-text-mute', t.text_mute);
      if (t.ok)        root.style.setProperty('--t-ok',        t.ok);
      if (t.warn)      root.style.setProperty('--t-warn',      t.warn);
      if (t.err)       root.style.setProperty('--t-err',       t.err);
      if (t.gateway_name) {
        document.title = t.gateway_name + ' - ' + document.title;
      }
    })
    .catch(function(e) {
      console.warn('Failed to load theme:', e);
    });
}

loadTheme();


// ── Fetch helpers ──────────────────────────────────────────────────────────

function postJson(url, data) {
  return fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(data)
  }).then(function(r) { return r.json(); });
}

function getJson(url) {
  return fetch(url).then(function(r) { return r.json(); });
}


// ── Polling ────────────────────────────────────────────────────────────────

function createPoller(url, intervalMs, callback, opts) {
  opts = opts || {};
  var timeoutMs = opts.timeout || 10000;
  var busy = false;
  var timer = null;

  function poll() {
    if (busy) return;
    busy = true;
    var ac = new AbortController();
    var to = setTimeout(function() { ac.abort(); }, timeoutMs);
    fetch(url, {signal: ac.signal})
      .then(function(r) { return r.json(); })
      .then(function(data) { callback(data); })
      .catch(function() {})
      .finally(function() { clearTimeout(to); busy = false; });
  }

  timer = setInterval(poll, intervalMs);
  poll();

  return {
    stop: function() { if (timer) { clearInterval(timer); timer = null; } },
    poll: poll
  };
}


// ── Common actions ─────────────────────────────────────────────────────────

function sendKey(k) {
  postJson('/key', {key: k});
  if (document.activeElement) document.activeElement.blur();
}

function openTmux() {
  postJson('/open_tmux', {});
}


// ── Formatting ─────────────────────────────────────────────────────────────

function fmtSecs(s) {
  if (s === null || s === undefined) return '--';
  if (s < 60) return s + 's';
  if (s < 3600) return Math.floor(s / 60) + 'm ' + Math.floor(s % 60) + 's';
  return Math.floor(s / 3600) + 'h ' + Math.floor((s % 3600) / 60) + 'm';
}

function fmtTimestamp(ts) {
  if (!ts) return '--';
  try { return new Date(ts).toLocaleTimeString(); }
  catch(e) { return typeof ts === 'string' ? ts.slice(11, 19) : '--'; }
}

function fmtDuration(s) {
  if (!s || isNaN(s)) return '0:00';
  var m = Math.floor(s / 60), sec = Math.floor(s % 60);
  return m + ':' + (sec < 10 ? '0' : '') + sec;
}

function fmtBytes(b) {
  if (b >= 1048576) return (b / 1048576).toFixed(1) + ' MB/s';
  if (b >= 1024) return (b / 1024).toFixed(1) + ' KB/s';
  return b + ' B/s';
}


// ── Motion ─────────────────────────────────────────────────────────────────

// Restart the CSS cell-flash animation on an element — use sparingly for
// "this value just changed" moments. Pair with setFlash() for set-and-flash.
function flashValue(el) {
  if (!el) return;
  el.classList.remove('flash');
  void el.offsetWidth;   // force reflow so animation restarts
  el.classList.add('flash');
}

// Set text content and flash iff the value actually changed.
// Drop-in replacement for `el.textContent = x` on live-updating cells.
function setFlash(el, text) {
  if (!el) return;
  var v = (text === null || text === undefined) ? '' : String(text);
  if (el.textContent === v) return;
  el.textContent = v;
  flashValue(el);
}

// Change-detection DOM helpers — skip the write if value is already current.
function setText(el, val) { if (el && el.textContent !== String(val)) el.textContent = String(val); }
function setClass(el, cls) { if (el && el.className !== cls) el.className = cls; }
function setHTML(el, html) { if (el && el.innerHTML !== html) el.innerHTML = html; }

// ── Audio meter physics (RG.vu) ────────────────────────────────────────
// rAF-driven interpolator that gives real VU-meter behavior to any bar.
//
// Why this exists: poll-driven `el.style.width = X%` looks stuttery
// because CSS transitions are direction-blind (same speed up and down)
// and poll rates (250-500ms) are much slower than perceived motion.
//
// What it does: target value is decoupled from displayed value. Each
// frame, current → target using asymmetric envelope (~50ms attack,
// ~500ms decay). Optional peak-hold sliver follows the high-water mark
// and falls gently after ~1s — the detail every hardware meter has.
//
// Opt-in: add class "vu" to the wrap, or attach explicitly via
// RG.vu.set(elementOrId, percent, optionalClass). Existing pages that
// keep using style.width still work (CSS transition gives some smoothing)
// — only opt-ins get the full physics.

window.RG = window.RG || {};
(function() {
  var meters = [];
  var attached = new WeakSet();

  function _findWrap(el) {
    if (!el) return null;
    if (el.classList && (el.classList.contains('bar-wrap') ||
                         el.classList.contains('vu') ||
                         el.classList.contains('lvl-bar') ||
                         el.classList.contains('vad-bar'))) return el;
    return el.querySelector('.bar-wrap, .vu, .lvl-bar, .vad-bar');
  }

  function attach(el) {
    if (!el || attached.has(el)) return null;
    var wrap = _findWrap(el) || el;
    var fill = wrap.querySelector('.bar-fill, .lvl-fill, .vad-fill');
    if (!fill) return null;
    // GPU-accelerated transform path. The fill spans the full wrap; we
    // reveal it with scaleX so width-of-bar animations don't trigger layout.
    fill.style.transformOrigin = 'left center';
    fill.style.width = '100%';
    fill.style.transition = 'none';
    var initialW = (parseFloat(fill.dataset.initialWidth) || 0);
    fill.style.transform = 'scaleX(' + (initialW / 100) + ')';
    // Peak marker — auto-created if missing.
    var peak = wrap.querySelector('.vu-peak');
    if (!peak) {
      peak = document.createElement('span');
      peak.className = 'vu-peak';
      wrap.appendChild(peak);
    }
    var m = {
      el: el, wrap: wrap, fill: fill, peak: peak,
      target: initialW, current: initialW, peakLvl: initialW,
      peakHoldUntil: 0,
    };
    meters.push(m);
    attached.add(el);
    return m;
  }

  function set(elOrId, percent, opts) {
    var el = (typeof elOrId === 'string') ? document.getElementById(elOrId) : elOrId;
    if (!el) return;
    var m = meters.find(function(x) { return x.el === el || x.wrap === el || x.fill === el; });
    if (!m) m = attach(el);
    if (!m) return;
    var v = Math.max(0, Math.min(100, +percent || 0));
    m.target = v;
    if (opts && typeof opts === 'string') {
      // backward-compat: pass class name as 3rd arg
      var base = 'bar-fill';
      if (m.fill.className !== base + ' ' + opts) m.fill.className = base + ' ' + opts;
    } else if (opts && opts.class) {
      var c = 'bar-fill ' + opts.class;
      if (m.fill.className !== c) m.fill.className = c;
    }
  }

  var ATTACK = 0.55;     // close 55% of the gap per frame on rise — punchy
  var DECAY  = 0.06;     // close 6% per frame on fall — gentle gravity
  var PEAK_HOLD_MS = 1100;
  var PEAK_FALL_PER_FRAME = 0.7;

  function tick() {
    var now = performance.now();
    for (var i = 0; i < meters.length; i++) {
      var m = meters[i];
      var diff = m.target - m.current;
      if (Math.abs(diff) < 0.05) {
        m.current = m.target;
      } else if (diff > 0) {
        m.current += diff * ATTACK;
      } else {
        m.current += diff * DECAY;
      }
      // Peak hold + fall
      if (m.current > m.peakLvl) {
        m.peakLvl = m.current;
        m.peakHoldUntil = now + PEAK_HOLD_MS;
      } else if (now > m.peakHoldUntil) {
        m.peakLvl -= PEAK_FALL_PER_FRAME;
        if (m.peakLvl < m.current) m.peakLvl = m.current;
      }
      m.fill.style.transform = 'scaleX(' + (m.current / 100) + ')';
      // peak visible only when above the current rendered level by >2pt,
      // and above 4% — avoids a stray pixel at idle.
      if (m.peakLvl > 4 && m.peakLvl - m.current > 2) {
        m.peak.style.left = m.peakLvl + '%';
        m.peak.style.opacity = '1';
      } else {
        m.peak.style.opacity = '0';
      }
    }
    requestAnimationFrame(tick);
  }

  function autoAttach() {
    document.querySelectorAll('.vu').forEach(attach);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', autoAttach);
  } else {
    autoAttach();
  }
  requestAnimationFrame(tick);

  window.RG.vu = { set: set, attach: attach };
})();
