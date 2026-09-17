/* ============================================================================
   autoseg.js: step 4. Runs one of the installed back-ends on the same image
   and compares its mask with the hand-traced one.

   The method list is not hard-coded: it comes from getAppInfo().methods, which
   the backend builds from its registry (imtop/core/segmentation/__init__.py).
   Add a back-end there and it shows up here, with the reason spelled out when
   it cannot run.
   ========================================================================== */

/* Look a method up by id. */
function methodById(id) {
  for (let i = 0; i < METHODS.length; i++) if (METHODS[i].id === id) return METHODS[i];
  return null;
}

/* Fill both the panel-1 summary and the panel-4 picker. Called once, after
   getAppInfo() answers. */
function buildMethodList(methods) {
  METHODS = methods || [];

  /* panel 1: a plain list, with the unavailable ones marked */
  const notes = document.getElementById('methodNotes');
  notes.innerHTML = '';
  if (!METHODS.length) {
    notes.textContent = 'None installed';
  } else {
    METHODS.forEach(function (m) {
      const line = document.createElement('div');
      line.textContent = m.label + (m.available ? '' : '   (not available)');
      if (!m.available) line.style.opacity = '.55';
      notes.appendChild(line);
    });
  }

  /* panel 4: the picker */
  const list = document.getElementById('methodList');
  list.innerHTML = '';
  METHODS.forEach(function (m) {
    const row = document.createElement('div');
    row.className = 'mitem' + (m.available ? '' : ' off');
    row.dataset.id = m.id;
    if (!m.available && m.reason) row.title = m.reason;
    const name = document.createElement('span');
    name.className = 'mitem-name';
    name.textContent = m.label;
    const badge = document.createElement('span');
    badge.className = 'mbadge';
    badge.textContent = m.available ? 'Ready' : 'N/A';
    row.appendChild(name);
    row.appendChild(badge);
    row.onclick = function () { selMethod(m.id); };
    list.appendChild(row);
  });

  /* Default: SAM when it is usable, otherwise the first back-end that is. */
  const usable = METHODS.filter(function (m) { return m.available; });
  const sam = usable.filter(function (m) { return m.needs_model; })[0];
  curMethod = (sam || usable[0] || { id: null }).id;
  markMethod(curMethod);
}

/* Paint the selection state on the picker. */
function markMethod(id) {
  const rows = document.querySelectorAll('#methodList .mitem');
  for (let i = 0; i < rows.length; i++) {
    const on = rows[i].dataset.id === id;
    rows[i].classList.toggle('sel', on);
    const badge = rows[i].querySelector('.mbadge');
    if (rows[i].classList.contains('off')) badge.textContent = 'N/A';
    else badge.textContent = on ? 'Active' : 'Ready';
  }
}

/* Picking a method immediately re-runs it: the GT contour is already there, so
   the comparison is one click away and the numbers always match the selection. */
function selMethod(id) {
  const m = methodById(id);
  if (!m) return;
  if (!m.available) { toast('⚠ ' + (m.reason || m.label + ' is not available'), 3800); return; }
  curMethod = id;
  markMethod(id);
  startSeg(id);
}

/* Run a method, loading its model first if needed. Says what is missing
   instead of failing later: the buttons that lead here are always live. */
function startSeg(id) {
  const m = methodById(id);
  if (!m) { toast('No segmentation method available'); return; }
  if (!iUrl) { toast('Load an image first'); return; }
  if (segPts.length < 3) { toast('Trace the wound first (step 3, at least 3 points)'); return; }
  if (!segCl) { toast('Close the shape first (Enter, or click the first point)'); return; }
  if (m.needs_model && !samReady) {
    _pendingRun = true;
    showSamLoader();
    be('loadSAMAsync');
    return;
  }
  runSeg();
}

function runSeg() {
  const m = methodById(curMethod);
  showLd('Running ' + ((m && m.label) || curMethod) + '…');
  setSt('Running…', 'busy');
  be('runAutoSegmentation', curMethod);
}

/* "Confirm & run" on step 3. */
function runPipeline() { startSeg(curMethod); }

/* ── step 4 drawing: both contours as crisp vector polylines ──────────────── */
function dAuto() {
  if (!iUrl) return;
  const f = beginFrame(), ctx = f.ctx;
  strokeContour(ctx, metCnt.gt, f, COL['c-gt'], null);
  strokeContour(ctx, metCnt.auto, f, COL['c-auto'], null);
}

/* Stroke one contour (image coordinates) on the canvas. */
function strokeContour(ctx, pts, f, color, dash) {
  if (!pts || pts.length < 2) return;
  ctx.beginPath();
  ctx.moveTo(pts[0].x * f.sc + f.ox, pts[0].y * f.sc + f.oy);
  for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x * f.sc + f.ox, pts[i].y * f.sc + f.oy);
  ctx.closePath();
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  if (dash) ctx.setLineDash(dash);
  ctx.stroke();
  ctx.setLineDash([]);
}
