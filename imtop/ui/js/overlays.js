/* ============================================================================
   overlays.js: "show me what this number means".

   Clicking a metric row draws that metric on the image: not a legend, but the
   actual geometry it was computed from (the intersection for Dice, the worst
   pair for Hausdorff, the equal-area circle for compactness, …). Everything is
   drawn as vectors on the 2D canvas, so it stays crisp at any zoom and needs
   no backend round-trip.

   Each metric belongs to a group (overlap / area / distance / manual geometry
   / auto geometry) and is drawn in shades of that group's colour, the same
   colour the row uses in the side panel.
   ========================================================================== */

/* metric key -> group id. Every key the panel lists (metrics.js MGRPS) has an
   entry; a key missing here would fall through to the bbox drawing in the
   wrong colour, and test_phase2.py checks the two lists agree. */
const MGRP = {
  dice: 'ov', iou: 'ov',
  area_gt_mm2: 'ar', area_auto_mm2: 'ar', area_rel_err: 'ar',
  under_cov_mm2: 'ar', over_cov_mm2: 'ar', cri_lambda3: 'ar',
  hausdorff_mm: 'di', avg_surf_dist_mm: 'di',
  perimeter_gt_mm: 'gm', compactness_gt: 'gm', bbox_gt_major_mm: 'gm',
  bbox_gt_minor_mm: 'gm', aspect_ratio_gt: 'gm',
  perimeter_auto_mm: 'ga', compactness_auto: 'ga', bbox_auto_major_mm: 'ga',
  bbox_auto_minor_mm: 'ga', aspect_ratio_auto: 'ga',
  bbox_auto_x_mm: 'ga', bbox_auto_y_mm: 'ga',
};

/* ── drawing primitives ───────────────────────────────────────────────────── */
/* image coordinates -> client coordinates */
function _toC(arr, sc, ox, oy) {
  return arr.map(function (p) { return { x: p.x * sc + ox, y: p.y * sc + oy }; });
}

function _path(ctx, P) {
  ctx.beginPath();
  ctx.moveTo(P[0].x, P[0].y);
  for (let i = 1; i < P.length; i++) ctx.lineTo(P[i].x, P[i].y);
  ctx.closePath();
}

/* Stroke a polyline over a dark halo, so it stays readable on a light image. */
function _stroke(ctx, P, color, w, dash, closed) {
  ctx.save();
  ctx.lineJoin = 'round'; ctx.lineCap = 'round';
  if (dash) ctx.setLineDash(dash);
  ctx.beginPath();
  ctx.moveTo(P[0].x, P[0].y);
  for (let i = 1; i < P.length; i++) ctx.lineTo(P[i].x, P[i].y);
  if (closed) ctx.closePath();
  ctx.strokeStyle = 'rgba(0,0,0,.5)'; ctx.lineWidth = w + 2.4; ctx.stroke();
  ctx.strokeStyle = color; ctx.lineWidth = w; ctx.stroke();
  ctx.restore();
}

/* Halo alone, for contours whose colour is painted segment by segment. */
function _halo(ctx, P, w, closed) {
  ctx.save();
  ctx.lineJoin = 'round'; ctx.lineCap = 'round';
  ctx.beginPath();
  ctx.moveTo(P[0].x, P[0].y);
  for (let i = 1; i < P.length; i++) ctx.lineTo(P[i].x, P[i].y);
  if (closed) ctx.closePath();
  ctx.strokeStyle = 'rgba(0,0,0,.45)'; ctx.lineWidth = w; ctx.stroke();
  ctx.restore();
}

function _seg(ctx, a, b, color, w) {
  ctx.beginPath();
  ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y);
  ctx.strokeStyle = color; ctx.lineWidth = w; ctx.stroke();
}

function _fillPoly(ctx, P, color) {
  ctx.save();
  _path(ctx, P);
  ctx.fillStyle = color;
  ctx.fill();
  ctx.restore();
}

/* Boolean region fills (A∩B, A∪B, A\B) through an offscreen canvas, still
   vectorial, so still crisp. One offscreen canvas is reused. */
/* The offscreen canvas has the stage's device-pixel size and the same
   CSS-pixel transform, so a region lands on the photo pixel for pixel. */
let _oc = null, _octx = null;
function _offc(w, h, dpr) {
  if (!_oc) { _oc = document.createElement('canvas'); _octx = _oc.getContext('2d'); }
  if (_oc.width !== w || _oc.height !== h) { _oc.width = w; _oc.height = h; }
  _octx.setTransform(1, 0, 0, 1, 0, 0);
  _octx.globalCompositeOperation = 'source-over';
  _octx.clearRect(0, 0, w, h);
  _octx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return _octx;
}
function _region(ctx, op, A, B, color) {
  const c = MC(), dpr = window.devicePixelRatio || 1;
  const o = _offc(c.width, c.height, dpr);
  o.fillStyle = color;
  if (op === 'union') { _path(o, A); o.fill(); _path(o, B); o.fill(); }
  else if (op === 'inter') { _path(o, A); o.fill(); o.globalCompositeOperation = 'destination-in'; _path(o, B); o.fill(); }
  else if (op === 'aMinusB') { _path(o, A); o.fill(); o.globalCompositeOperation = 'destination-out'; _path(o, B); o.fill(); }
  else if (op === 'bMinusA') { _path(o, B); o.fill(); o.globalCompositeOperation = 'destination-out'; _path(o, A); o.fill(); }
  ctx.drawImage(_oc, 0, 0, c.width / dpr, c.height / dpr);
}

function _bbox(P) {
  let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
  for (const p of P) {
    if (p.x < x0) x0 = p.x;
    if (p.y < y0) y0 = p.y;
    if (p.x > x1) x1 = p.x;
    if (p.y > y1) y1 = p.y;
  }
  return { x0: x0, y0: y0, x1: x1, y1: y1 };
}

/* Distance from every point of P to the nearest point of Q. */
function _minD(P, Q) {
  return P.map(function (p) {
    let m = 1e18;
    for (let i = 0; i < Q.length; i++) {
      const dx = p.x - Q[i].x, dy = p.y - Q[i].y, d = dx * dx + dy * dy;
      if (d < m) m = d;
    }
    return Math.sqrt(m);
  });
}

function _marker(ctx, x, y, r, color) {
  ctx.save();
  ctx.beginPath(); ctx.arc(x, y, r, 0, 7);
  ctx.fillStyle = color; ctx.strokeStyle = 'rgba(0,0,0,.55)'; ctx.lineWidth = 2;
  ctx.fill(); ctx.stroke();
  ctx.restore();
}

/* A measured axis: the segment plus its two end caps. */
function _axis(ctx, a, b, color, w) {
  _stroke(ctx, [a, b], color, w, null, false);
  const dx = b.x - a.x, dy = b.y - a.y, L = Math.hypot(dx, dy) || 1;
  const nx = -dy / L, ny = dx / L, cap = 7;
  _stroke(ctx, [{ x: a.x - nx * cap, y: a.y - ny * cap }, { x: a.x + nx * cap, y: a.y + ny * cap }], color, w, null, false);
  _stroke(ctx, [{ x: b.x - nx * cap, y: b.y - ny * cap }, { x: b.x + nx * cap, y: b.y + ny * cap }], color, w, null, false);
}

/* ── the overlay itself ───────────────────────────────────────────────────── */
/* G = manual contour, A = automatic contour, both in client coordinates. */
function drawMetricOverlay(ctx, key, G, A) {
  const C = MPAL[MGRP[key] || 'gm'];
  const faint = function (P, dash) { if (P) _stroke(ctx, P, _rgba(C.m, .55), 1.4, dash || [5, 4], true); };

  /* OVERLAP, Dice and IoU are the same numbers on different regions, so they
     are drawn differently: IoU shows union vs intersection, Dice shows the two
     summed areas with the overlap counted twice. */
  if (key === 'dice' || key === 'iou') {
    if (!G || !A) { if (G) _stroke(ctx, G, C.m, 2, null, true); if (A) _stroke(ctx, A, C.m, 2, null, true); return; }
    if (key === 'iou') {
      _region(ctx, 'union', G, A, _rgba(C.l, .32));
      _region(ctx, 'inter', G, A, _rgba(C.d, .55));
    } else {
      _region(ctx, 'aMinusB', G, A, _rgba(C.m, .30));
      _region(ctx, 'bMinusA', G, A, _rgba(C.l, .34));
      _region(ctx, 'inter', G, A, _rgba(C.d, .60));
    }
    _stroke(ctx, G, C.m, 1.8, null, true);
    _stroke(ctx, A, C.m, 1.8, [5, 4], true);
    return;
  }

  /* AREA */
  if (key === 'area_gt_mm2') { faint(A); if (G) { _fillPoly(ctx, G, _rgba(C.m, .34)); _stroke(ctx, G, C.d, 2.4, null, true); } return; }
  if (key === 'area_auto_mm2') { faint(G); if (A) { _fillPoly(ctx, A, _rgba(C.m, .34)); _stroke(ctx, A, C.d, 2.4, null, true); } return; }
  if (key === 'area_rel_err') {                 // where the error actually comes from
    if (G && A) {
      _region(ctx, 'aMinusB', G, A, _rgba(C.d, .5));
      _region(ctx, 'bMinusA', G, A, _rgba(C.l, .55));
      _stroke(ctx, G, C.m, 1.8, null, true);
      _stroke(ctx, A, C.m, 1.8, [5, 4], true);
    } else { faint(G); faint(A); }
    return;
  }
  /* COVERAGE, W = the wound (manual), P = the patch (auto). Uncovered wound
     is W minus P, excess on skin is P minus W; the cost index weighs the first
     three times the second, so it shows both, with the weights as opacity. */
  if (key === 'under_cov_mm2' || key === 'over_cov_mm2' || key === 'cri_lambda3') {
    if (!G || !A) { faint(G); faint(A); return; }
    if (key !== 'over_cov_mm2') _region(ctx, 'aMinusB', G, A, _rgba(C.d, key === 'cri_lambda3' ? .62 : .55));
    if (key !== 'under_cov_mm2') _region(ctx, 'bMinusA', G, A, _rgba(C.l, key === 'cri_lambda3' ? .28 : .55));
    _stroke(ctx, G, C.m, 1.8, null, true);
    _stroke(ctx, A, C.m, 1.8, [5, 4], true);
    return;
  }

  /* DISTANCE, the contour is coloured by its local distance to the other one. */
  if (key === 'hausdorff_mm' || key === 'avg_surf_dist_mm') {
    if (!G || !A) { faint(G); faint(A); return; }
    const dg = _minD(G, A);
    let mx = 0;
    for (const d of dg) if (d > mx) mx = d;
    mx += 1e-9;
    _halo(ctx, G, 5, true);
    for (let i = 0; i < G.length; i++) _seg(ctx, G[i], G[(i + 1) % G.length], _mix(C.l, C.d, dg[i] / mx), 3.4);
    if (key === 'avg_surf_dist_mm') {           // symmetric: colour both contours
      const da = _minD(A, G);
      let mx2 = 0;
      for (const d of da) if (d > mx2) mx2 = d;
      mx2 += 1e-9;
      _halo(ctx, A, 3.6, true);
      for (let i = 0; i < A.length; i++) _seg(ctx, A[i], A[(i + 1) % A.length], _mix(C.l, C.d, da[i] / mx2), 2.2);
    } else {                                    // Hausdorff: mark the single worst pair
      _stroke(ctx, A, _rgba(C.m, .8), 1.5, [5, 4], true);
      let wi = 0;
      for (let i = 1; i < dg.length; i++) if (dg[i] > dg[wi]) wi = i;
      let cj = 0, best = 1e18;
      for (let i = 0; i < A.length; i++) {
        const dx = G[wi].x - A[i].x, dy = G[wi].y - A[i].y, d = dx * dx + dy * dy;
        if (d < best) { best = d; cj = i; }
      }
      _stroke(ctx, [G[wi], A[cj]], C.d, 2.4, null, false);
      _marker(ctx, G[wi].x, G[wi].y, 4.5, C.d);
    }
    return;
  }

  /* GEOMETRY, the other contour stays faint, the measured one is highlighted. */
  const isAuto = key.indexOf('auto') >= 0;
  const T = isAuto ? A : G, O = isAuto ? G : A;
  faint(O);
  if (!T) return;

  if (key.indexOf('perimeter') === 0) { _stroke(ctx, T, C.m, 3.2, null, true); return; }

  if (key.indexOf('compactness') === 0) {       // the shape against an equal-area circle
    _stroke(ctx, T, C.m, 2.2, null, true);
    let cx = 0, cy = 0;
    for (const p of T) { cx += p.x; cy += p.y; }
    cx /= T.length; cy /= T.length;
    let ar = 0;
    for (let i = 0; i < T.length; i++) { const j = (i + 1) % T.length; ar += T[i].x * T[j].y - T[j].x * T[i].y; }
    ar = Math.abs(ar) / 2;
    const rr = Math.sqrt(ar / Math.PI);
    ctx.save();
    ctx.setLineDash([7, 5]);
    ctx.beginPath(); ctx.arc(cx, cy, rr, 0, 7);
    ctx.strokeStyle = 'rgba(0,0,0,.5)'; ctx.lineWidth = 4; ctx.stroke();
    ctx.strokeStyle = C.d; ctx.lineWidth = 2; ctx.stroke();
    ctx.restore();
    return;
  }

  /* bounding box + the axis the metric refers to. X and Y are the box's own
     sides; major and minor are the same two sides sorted by length. */
  const b = _bbox(T);
  const W = b.x1 - b.x0, H = b.y1 - b.y0;
  const mx = (b.x0 + b.x1) / 2, my = (b.y0 + b.y1) / 2, hor = W >= H;
  _stroke(ctx, [{ x: b.x0, y: b.y0 }, { x: b.x1, y: b.y0 }, { x: b.x1, y: b.y1 }, { x: b.x0, y: b.y1 }], _rgba(C.m, .6), 1.4, [5, 4], true);
  _stroke(ctx, T, _rgba(C.m, .8), 1.5, null, true);
  const XA = { x: b.x0, y: my }, XB = { x: b.x1, y: my };     // horizontal side
  const YA = { x: mx, y: b.y0 }, YB = { x: mx, y: b.y1 };     // vertical side
  const MA = hor ? XA : YA, MB = hor ? XB : YB;
  const NA = hor ? YA : XA, NB = hor ? YB : XB;
  if (key.indexOf('_x_') >= 0) _axis(ctx, XA, XB, C.m, 3.2);
  else if (key.indexOf('_y_') >= 0) _axis(ctx, YA, YB, C.m, 3.2);
  else if (key.indexOf('major') >= 0) _axis(ctx, MA, MB, C.m, 3.2);
  else if (key.indexOf('minor') >= 0) _axis(ctx, NA, NB, C.m, 3.2);
  else { _axis(ctx, MA, MB, C.d, 3); _axis(ctx, NA, NB, C.l, 3); }   // aspect ratio: both
}
