/* ============================================================================
   perf.js: developer instrumentation. Not part of the workflow.

     F9   toggle the on-screen HUD
     F10  write a full perf report next to the app (backend savePerf)
     F8   bare compositor baseline: is the ceiling Chromium's, or ours?

   Sampling is always on because it costs a couple of array writes per frame,
   which means F10 can report on a session that already felt wrong instead of
   asking the user to reproduce it.
   ========================================================================== */

let _prof = false;                                 // HUD visible?
const _RING = 4000;                                // samples kept per buffer

/* Redraw timing, JS canvas work only, excludes the compositor. */
let _pst = { n: 0, sum: 0, max: 0, last: 0 };
let _ring = [], _ri = 0;
let _fpsN = 0, _fps = 0, _fpsT0 = 0;

/* Frame cadence, rAF deltas during an interaction, INCLUDING the compositor.
   This is the number that actually corresponds to "it feels smooth". */
let _fr = { last: 0, on: false, end: 0 };
let _fring = [], _fri = 0, _frKind = '';

/* Record one redraw. */
function _rec(dt) {
  _pst.last = dt; _pst.sum += dt; _pst.n++;
  if (dt > _pst.max) _pst.max = dt;
  const now = performance.now();
  _fpsN++;
  if (now - _fpsT0 >= 500) { _fps = _fpsN * 1000 / (now - _fpsT0); _fpsN = 0; _fpsT0 = now; }
  _ring[_ri % _RING] = { ms: dt, p: _cm, z: +view.z.toFixed(2) };
  _ri++;
}

/* Start (or extend) a frame-cadence measurement window. */
function _frPing(kind) {
  if (kind) _frKind = kind;
  _fr.end = performance.now() + 800;
  if (!_fr.on) { _fr.on = true; _fr.last = 0; requestAnimationFrame(_frTick); }
}
function _frTick(t) {
  if (_fr.last) { _fring[_fri % _RING] = { dt: t - _fr.last, p: _cm, k: _frKind }; _fri++; }
  _fr.last = t;
  if (performance.now() < _fr.end) requestAnimationFrame(_frTick);
  else { _fr.on = false; _fr.last = 0; }
}

/* ── HUD ──────────────────────────────────────────────────────────────────── */
function hudPaint() {
  const h = document.getElementById('perfhud');
  if (!h) return;
  const avg = _pst.n ? (_pst.sum / _pst.n) : 0;
  h.textContent =
    'PERF   F9 close · F10 save report\n' +
    'page ' + _cm + '   zoom ' + view.z.toFixed(2) + 'x\n' +
    'redraw last  ' + _pst.last.toFixed(1) + ' ms\n' +
    'redraw avg   ' + avg.toFixed(1) + ' ms  (n=' + _pst.n + ')\n' +
    'redraw max   ' + _pst.max.toFixed(1) + ' ms\n' +
    'fps          ' + _fps.toFixed(0);
}
function hudToggle() {
  _prof = !_prof;
  const h = document.getElementById('perfhud');
  if (h) h.style.display = _prof ? 'block' : 'none';
  if (_prof) hudPaint();
}

/* Does QtWebEngine render through the real GPU or a software fallback
   (SwiftShader/llvmpipe)? That is the usual answer to "smooth on PC A, janky
   on PC B". */
function gpuInfo() {
  try {
    const cv = document.createElement('canvas');
    const gl = cv.getContext('webgl') || cv.getContext('experimental-webgl');
    if (!gl) return 'no WebGL context';
    const dbg = gl.getExtension('WEBGL_debug_renderer_info');
    const ven = dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR);
    const ren = dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
    return ven + ' | ' + ren;
  } catch (e) { return 'err: ' + e; }
}

/* ── report ───────────────────────────────────────────────────────────────── */
function _ordered(buf, i) {
  return i > _RING ? buf.slice(i % _RING).concat(buf.slice(0, i % _RING)) : buf.slice(0, i);
}
function _stat(a) {
  if (!a.length) return '(no samples)';
  const s = a.slice().sort(function (x, y) { return x - y; });
  const avg = a.reduce(function (p, c) { return p + c; }, 0) / a.length;
  return 'n=' + a.length + '  avg=' + avg.toFixed(1) +
         '  p50=' + s[Math.floor(s.length * 0.5)].toFixed(1) +
         '  p95=' + s[Math.min(s.length - 1, Math.floor(s.length * 0.95))].toFixed(1) +
         '  max=' + s[s.length - 1].toFixed(1) + ' ms';
}

function dumpPerf() {
  const ord = _ordered(_ring, _ri);
  const byPage = { 1: [], 2: [], 3: [], 4: [] };
  const label = { 1: 'calibration', 2: 'manual-seg', 3: 'auto-seg', 4: 'metrics' };
  ord.forEach(function (s) { if (byPage[s.p]) byPage[s.p].push(s.ms); });

  let r = 'IMTOP: PERF REPORT\n\n';
  r += 'userAgent       : ' + navigator.userAgent + '\n';
  r += 'devicePixelRatio: ' + window.devicePixelRatio + '\n';
  r += 'GPU (WebGL)     : ' + gpuInfo() + '\n';
  r += 'image / canvas  : ' + iW + 'x' + iH + '  /  ' + MC().width + 'x' + MC().height +
       '   zoom now ' + view.z.toFixed(2) + 'x\n';
  r += 'total redraws   : ' + _ri + '\n\n';
  r += 'REDRAW TIME: JS canvas work only, EXCLUDES the compositor (lower=better)\n';
  r += '  overall : ' + _stat(ord.map(function (s) { return s.ms; })) + '\n';
  [1, 2, 3, 4].forEach(function (p) {
    if (byPage[p].length) r += '  page ' + p + ' (' + label[p] + ') : ' + _stat(byPage[p]) + '\n';
  });

  const ford = _ordered(_fring, _fri);
  const byKind = {};
  ford.forEach(function (s) { const k = s.k || '?'; (byKind[k] = byKind[k] || []).push(s.dt); });
  const line = function (a) { return _stat(a) + '   >33ms: ' + a.filter(function (d) { return d > 33; }).length + '/' + a.length; };
  r += '\nFRAME CADENCE during interaction: rAF deltas, INCLUDES the GPU compositor\n';
  r += '  16.7ms = perfect 60fps; >20ms = stutter; >33ms = sub-30fps jank\n';
  r += '  overall : ' + line(ford.map(function (s) { return s.dt; })) + '\n';
  Object.keys(byKind).forEach(function (k) { r += '  ' + k + ' : ' + line(byKind[k]) + '\n'; });

  r += '\nLAST ' + Math.min(ord.length, 60) + ' REDRAW SAMPLES (ms @ page,zoom):\n';
  ord.slice(-60).forEach(function (s) { r += '  ' + s.ms.toFixed(1).padStart(6) + '  p' + s.p + '  ' + s.z + 'x\n'; });

  be('savePerf', r, function (path) { toast('Perf report saved → ' + (path || 'perf_report.txt'), 4000); });
}

/* BASELINE probe: animate a real element every frame for 3s, which forces the
   compositor to produce a frame each time (Chromium throttles rAF when nothing
   is damaged). If even this trivial single-layer animation is janky, the
   QtWebEngine compositor is the ceiling; if it is smooth, the jank is ours. */
function baselineTest() {
  toast('Baseline: 3s animation test, do not touch anything…', 3400);
  setSt('Baseline test…', 'busy');
  const d = document.createElement('div');
  d.style.cssText = 'position:fixed;top:64px;left:0;width:80px;height:80px;background:' +
                    COL.accent + ';z-index:99999;will-change:transform;pointer-events:none';
  document.body.appendChild(d);

  let last = 0, k = 0;
  const arr = [], end = performance.now() + 3000;
  function tick(t) {
    if (last) arr.push(t - last);
    last = t;
    k = (k + 3) % 240;
    d.style.transform = 'translateX(' + k + 'px)';
    if (performance.now() < end) { requestAnimationFrame(tick); return; }
    d.remove();
    const s = arr.slice().sort(function (a, b) { return a - b; });
    const avg = arr.reduce(function (p, c) { return p + c; }, 0) / (arr.length || 1);
    const bad = arr.filter(function (x) { return x > 33; }).length;
    const jit = arr.filter(function (x) { return x > 20; }).length;
    let r = 'IMTOP BASELINE FRAME TEST (animated div, forces a frame each time)\n';
    r += 'GPU (WebGL): ' + gpuInfo() + '\n';
    r += 'If THIS is janky the QtWebEngine compositor is the ceiling; if smooth, the jank is our canvas rendering.\n\n';
    r += 'n=' + arr.length + '  avg=' + avg.toFixed(1) +
         '  p50=' + s[s.length >> 1].toFixed(1) +
         '  p95=' + s[Math.min(s.length - 1, Math.floor(s.length * 0.95))].toFixed(1) +
         '  max=' + s[s.length - 1].toFixed(1) + ' ms\n';
    r += 'frames >20ms: ' + jit + ' / ' + arr.length + '    frames >33ms: ' + bad + ' / ' + arr.length + '\n';
    be('savePerf', r, function () { toast('Baseline saved → perf_report.txt', 4000); setSt('Baseline done', 'ok'); });
  }
  requestAnimationFrame(tick);
}
