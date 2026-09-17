/* ============================================================================
   manualseg.js: step 3. The operator traces the wound boundary; those control
   points are the ground truth every metric is measured against.

   The on-screen curve is a closed Catmull-Rom spline computed here, so
   dragging a point is fluid (no backend round-trip per frame). The backend
   keeps the authoritative control points and recomputes the real contour on
   Confirm.
   ========================================================================== */

/* Nearest control point to a client position. */
function nPt(cx, cy) {
  let idx = -1, best = Infinity;
  segPts.forEach(function (p, i) {
    const c = i2c(p.x, p.y), d = Math.hypot(cx - c.x, cy - c.y);
    if (d < best) { best = d; idx = i; }
  });
  return { idx: idx, dist: best };
}

/* Is the cursor over the first point (the "close the shape" target)? */
function nFirst(cx, cy) {
  if (segPts.length < 3 || segCl) return false;
  const c = i2c(segPts[0].x, segPts[0].y);
  return Math.hypot(cx - c.x, cy - c.y) <= SNAP;
}

/* ── mouse ────────────────────────────────────────────────────────────────── */
function sdD(e) {                                  // left button down
  if (e.button !== 0) return;
  if (!iUrl) { toast('Load an image first'); return; }
  if (segCl) return;
  const r = MC().getBoundingClientRect(), cx = e.clientX - r.left, cy = e.clientY - r.top;
  const near = nPt(cx, cy);
  if (near.idx >= 0 && near.dist <= SNAP) {        // grab an existing point
    segDr = near.idx;
    justPlaced = false;
    MC().style.cursor = GRAB_CUR;
    return;
  }
  if (nFirst(cx, cy)) {                            // click the first point = close
    pushUndo();
    segCl = true;
    justPlaced = false;
    updSS(); recomp();
    return;
  }
  pushUndo();
  segPts.push(c2i(cx, cy));
  justPlaced = true;
  updSS(); recomp();
}

function sdM(e) {                                  // move
  if (_pan || !iUrl) return;                       // don't fight the pan gesture
  const r = MC().getBoundingClientRect(), cx = e.clientX - r.left, cy = e.clientY - r.top;
  if (segDr >= 0) {                                // dragging: local update only
    segPts[segDr] = c2i(cx, cy);
    _frPing('drag');
    scheduleRedraw();
    return;
  }
  if (segCl) return;
  const near = nPt(cx, cy), over = near.idx >= 0 && near.dist <= SNAP;
  // Right after placing a point the cursor still sits on it: keep the crosshair
  // until it actually leaves, so it does not snap to a hand instantly.
  if (justPlaced) {
    if (over) { MC().style.cursor = 'crosshair'; return; }
    justPlaced = false;
  }
  MC().style.cursor = over ? GRAB_CUR : (nFirst(cx, cy) ? 'pointer' : 'crosshair');
}

function sdU() {                                   // release: sync once
  if (segDr >= 0) { segDr = -1; recomp(); }
}

function sdR(e) {                                  // right button: remove a point
  e.preventDefault();
  if (segCl || !iUrl) return;
  const r = MC().getBoundingClientRect();
  const near = nPt(e.clientX - r.left, e.clientY - r.top);
  if (near.idx >= 0 && near.dist <= SNAP * 2) {
    pushUndo();
    segPts.splice(near.idx, 1);
    justPlaced = false;
    updSS(); recomp();
  }
}

/* ── history ──────────────────────────────────────────────────────────────── */
/* Snapshot the state *before* a change. The backend keeps the stack. */
function pushUndo() {
  be('pushUndo', JSON.stringify({ pts: segPts, closed: segCl }));
}

/* A snapshot is the points AND whether the shape was closed: undoing the
   close reopens it, redoing it closes it again. */
function applySnapshot(r) {
  segPts = r.pts || [];
  segCl = !!r.closed;
  segDr = -1; justPlaced = false;
  recomp(); updSS();
}

function sUndo() {
  beJson('undo', undefined, function (r) {
    if (!r || !r.ok) { toast('Nothing to undo'); return; }
    applySnapshot(r);
  });
}

function sRedo() {
  beJson('redo', undefined, function (r) {
    if (!r || !r.ok) { toast('Nothing to redo'); return; }
    applySnapshot(r);
  });
}

function sReset() {
  be('resetSegmentation');
  segPts = []; segCl = false; segDr = -1;
  updSS();
  dSeg();
}

/* ── panel state ──────────────────────────────────────────────────────────── */
/* Keep the backend's trace in sync (the points AND whether it is closed: the
   undo snapshots are taken from what the backend holds) and repaint. */
function recomp() {
  be('setControlPoints', JSON.stringify(segPts));
  be('setContourClosed', segCl);
  dSeg();
}

/* The status chip, from the point count alone. The buttons stay live: what
   they need and do not have, they say when pressed. */
function updSS() {
  const n = segPts.length;
  const chip = document.getElementById('segst');
  paintTabs();
  if (!chip) return;
  if (n === 0) {
    chip.className = 'chip';
    chip.textContent = iUrl ? 'Click to add points' : 'No image loaded';
  } else if (n < 3) {
    chip.className = 'chip chip-info';
    chip.textContent = n + ' pts, need 3+';
  } else if (!segCl) {
    chip.className = 'chip chip-info';
    chip.textContent = n + ' pts, close the shape';
  } else {
    chip.className = 'chip chip-ok';
    chip.textContent = '✓ Closed (' + n + ' pts)';
  }
}

/* Close the outline: panel button, Enter, or clicking the first point. */
function closeContour() {
  if (!iUrl) { toast('Load an image first'); return; }
  if (segCl) { toast('The shape is already closed'); return; }
  if (segPts.length < 3) { toast('Add at least 3 points first'); return; }
  pushUndo();
  segCl = true;
  justPlaced = false;
  updSS(); recomp();
  toast('Contour closed, press Confirm & run');
}

/* ── the curve ────────────────────────────────────────────────────────────── */
/* Closed Catmull-Rom through the control points: smooth and interpolating.
   Preview only, the final GT contour is recomputed by the backend. */
function csSpline(pts) {
  const n = pts.length;
  if (n < 3) return [];
  const SEG = 18, out = [];
  for (let i = 0; i < n; i++) {
    const p0 = pts[(i - 1 + n) % n], p1 = pts[i], p2 = pts[(i + 1) % n], p3 = pts[(i + 2) % n];
    for (let j = 0; j < SEG; j++) {
      const t = j / SEG, t2 = t * t, t3 = t2 * t;
      out.push({
        x: 0.5 * ((2 * p1.x) + (-p0.x + p2.x) * t + (2 * p0.x - 5 * p1.x + 4 * p2.x - p3.x) * t2 + (-p0.x + 3 * p1.x - 3 * p2.x + p3.x) * t3),
        y: 0.5 * ((2 * p1.y) + (-p0.y + p2.y) * t + (2 * p0.y - 5 * p1.y + 4 * p2.y - p3.y) * t2 + (-p0.y + 3 * p1.y - 3 * p2.y + p3.y) * t3),
      });
    }
  }
  return out;
}

function dSeg() {
  if (!iUrl) return;
  const f = beginFrame(), ctx = f.ctx;
  const gt = COL['c-gt'];

  const crv = csSpline(segPts);
  if (crv.length > 2) {
    ctx.beginPath();
    ctx.moveTo(crv[0].x * f.sc + f.ox, crv[0].y * f.sc + f.oy);
    for (let i = 1; i < crv.length; i++) ctx.lineTo(crv[i].x * f.sc + f.ox, crv[i].y * f.sc + f.oy);
    ctx.closePath();
    if (segCl) {
      ctx.fillStyle = _rgba(gt, 0.16);
      ctx.fill();
      ctx.strokeStyle = gt; ctx.lineWidth = 2; ctx.stroke();
    } else {
      ctx.strokeStyle = gt; ctx.lineWidth = 2;
      ctx.setLineDash([7, 4]); ctx.stroke(); ctx.setLineDash([]);
    }
  }

  segPts.forEach(function (p, i) {
    const c = i2c(p.x, p.y);
    const first = i === 0 && !segCl && segPts.length >= 3;
    cross(ctx, c.x, c.y, first ? COL['c-first'] : COL['c-point'], first ? 30 : 24, first ? 3 : 2.5);
    if (first) {                                  // the snap ring you can click to close
      ctx.beginPath();
      ctx.arc(c.x, c.y, SNAP, 0, Math.PI * 2);
      ctx.strokeStyle = _rgba(COL['c-first'], 0.35);
      ctx.lineWidth = 1;
      ctx.stroke();
    }
  });
}

/* ── SAM auto-prompt ──────────────────────────────────────────────────────── */
/* No manual trace: the backend finds the object (Otsu) and feeds its contour
   into the ordinary SAM pipeline, so the result arrives through autoSegDone.
   One loader only, the percentage bar, because the backend worker loads,
   encodes and segments without ever touching the GUI thread. */
function autoPrompt() {
  if (!iUrl) { toast('Load an image first'); return; }
  showSamLoader();
  setSt('Running…', 'busy');
  be('runAutoPrompt');
}
