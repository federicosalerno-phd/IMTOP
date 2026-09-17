/* ============================================================================
   nav.js: the window chrome: which step is on screen, the status pill, the
   toast, and the two blocking overlays (generic spinner + SAM progress bar).
   ========================================================================== */

/* ── steps ────────────────────────────────────────────────────────────────── */
/* Enter a step. Any step, any time: the whole app is navigable before an image
   is loaded (tabs, arrow keys, Back/Next). A step without its data shows what
   it is waiting for instead of its content. */
function go(idx) {
  if (idx < 0 || idx > 5) return false;
  const prev = curP;
  curP = idx;
  paintTabs();

  /* side panel */
  for (let i = 0; i < 6; i++) document.getElementById('rp' + i).style.display = 'none';
  document.getElementById(SCFG[idx].rp).style.display = 'flex';

  /* work area: drop zone · image stage · 3D viewers */
  document.getElementById('p0wrap').style.display = 'none';
  document.getElementById('canvaswrap').style.display = 'none';
  document.getElementById('p5wrap').style.display = 'none';
  const stage = SCFG[idx].canvas;
  if (stage === 'p0') {
    document.getElementById('p0wrap').style.display = 'flex';
  } else if (stage === 'p5') {
    document.getElementById('p5wrap').style.display = 'flex';
  } else {
    document.getElementById('canvaswrap').style.display = 'flex';
    document.getElementById('noimg').style.display = iUrl ? 'none' : 'flex';
    // Paint in THIS frame. Reading the stage size flushes the layout, so the
    // canvas is resized and drawn before the browser composites the step
    // change; going through a timer left the stage empty for a frame or two,
    // which reads as a black flash on every step change. The zoom and the pan
    // survive a move between two steps that both show the photo.
    const keepView = SCFG[prev].canvas === 'canvas';
    initCanvas(idx, keepView);
    if (!cssW()) setTimeout(function () { if (curP === idx) initCanvas(idx, keepView); }, 20);
  }

  /* canvas toolbar: the row is always laid out, so the stage below keeps one
     rectangle on every step. Controls a step has no use for are hidden in
     place (visibility), never removed, so nothing next to them slides. */
  const tb = document.getElementById('ctoolbar');
  tb.classList.toggle('no-view', !SCFG[idx].toolbar);
  tb.classList.toggle('no-edit', idx !== 2);
  document.getElementById('cthint').textContent = SCFG[idx].hint;

  setSt(SCFG[idx].st, 'ok');

  if (idx === 5) patch3DEnter();
  else v3dStop();          // stop the 3D render loop as soon as we leave step 6
  return true;
}

/* A tick means the step's task was carried through, never that the step was
   visited (his rule). Image: loaded. Calibration: a scale set, or pixels
   chosen on purpose. Manual seg: the trace closed. Auto seg: a segmentation
   done. Metrics: the numbers saved (JSON or report). 3D patch: the STL
   exported. Jumping ahead with nothing loaded ticks nothing. */
function stepDone(i) {
  if (i === 0) return !!iUrl;
  if (i === 1) return !!iUrl && calDone;
  if (i === 2) return !!iUrl && segCl && segPts.length >= 3;
  if (i === 3) return !!(metCnt && metCnt.auto);
  if (i === 4) return !!(metCnt && metCnt.auto) && resSaved;
  return !!(metCnt && metCnt.auto) && stlSaved;
}

/* tabs: done (check) · active · plain. Called by go() and whenever the state
   behind stepDone() changes (image, scale, trace, segmentation). */
function paintTabs() {
  for (let i = 0; i < 6; i++) {
    const t = document.getElementById('tab' + i);
    const n = document.getElementById('tn' + i);
    t.className = 'tab';
    n.textContent = i + 1;
    if (i === curP) t.classList.add('active');
    if (stepDone(i)) { t.classList.add('done'); n.innerHTML = '<span style="font-size:10px">&#10003;</span>'; }
  }
}

/* ── status pill ──────────────────────────────────────────────────────────── */
function setSt(msg, kind) {
  document.getElementById('st').textContent = msg;
  document.getElementById('sd').className = 'sd' + (kind ? ' ' + kind : '');
}

/* ── toast ────────────────────────────────────────────────────────────────── */
let _toastT = null;
function toast(msg, ms) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(_toastT);
  _toastT = setTimeout(function () { el.classList.remove('show'); }, ms || 2800);
}

/* ── generic blocking spinner ─────────────────────────────────────────────── */
function showLd(msg) {
  document.getElementById('ldT').textContent = msg || 'Loading…';
  document.getElementById('loading').classList.add('show');
}
function hideLd() { document.getElementById('loading').classList.remove('show'); }

/* ── SAM loading bar ──────────────────────────────────────────────────────── */
/* The backend reports real progress for the download and the weight load, but
   "encoding" is one opaque slow step, the target creeps up there so the bar
   never looks frozen. The shown value always eases towards the target. */
function showSamLoader() {
  _samTarget = 0;
  _samShown = 0;
  if (_samCreepT) { clearInterval(_samCreepT); _samCreepT = null; }
  document.getElementById('samldStage').textContent = 'Preparing…';
  document.getElementById('samldPct').textContent = '0';
  document.getElementById('samldFill').style.transform = 'scaleX(0)';
  document.getElementById('samld').classList.add('show');
  if (!_samRAF) _samRAF = requestAnimationFrame(_samTick);
}

function hideSamLoader() {
  document.getElementById('samld').classList.remove('show');
  if (_samRAF) { cancelAnimationFrame(_samRAF); _samRAF = 0; }
  if (_samCreepT) { clearInterval(_samCreepT); _samCreepT = null; }
}

function samProgress(pct, stage) {
  if (pct > _samTarget) _samTarget = pct;
  if (stage) document.getElementById('samldStage').textContent = stage;
  if (stage && stage.toLowerCase().indexOf('encod') >= 0 && !_samCreepT) {
    _samCreepT = setInterval(function () { if (_samTarget < 94) _samTarget += 1; }, 230);
  }
  if (pct >= 100) {
    if (_samCreepT) { clearInterval(_samCreepT); _samCreepT = null; }
    _samTarget = 100;
  }
}

function _samTick() {
  _samShown += (_samTarget - _samShown) * 0.14;
  if (_samTarget - _samShown < 0.05) _samShown = _samTarget;
  document.getElementById('samldPct').textContent = Math.round(_samShown);
  document.getElementById('samldFill').style.transform = 'scaleX(' + (_samShown / 100).toFixed(4) + ')';
  _samRAF = requestAnimationFrame(_samTick);
}
