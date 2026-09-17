/* ============================================================================
   metrics.js: step 5. The numbers the app exists to produce.

   The key strings are a published schema (imtop/core/metrics.py, results_all.csv
   and the Monte Carlo scripts read them): never rename one here. Grouping and
   wording are ours; the colour of a group is the colour its overlay uses.

   Each item says what it measures (`u`: 'len', 'area', or nothing for a ratio)
   and how many decimals it deserves (`p`). The unit itself is not written
   here: the "_mm" suffix in the keys is historical, and on an uncalibrated
   image the same numbers are pixels. getMetricsInfo() says which, once per
   run, and the labels are built from that answer.
   ========================================================================== */

const MGRPS = [
  { t: 'Overlap', g: 'ov', items: [
    { k: 'dice', l: 'Dice', p: 4 },
    { k: 'iou', l: 'IoU / Jaccard', p: 4 },
  ] },
  { t: 'Area', g: 'ar', items: [
    { k: 'area_gt_mm2', l: 'Manual area', u: 'area', p: 1 },
    { k: 'area_auto_mm2', l: 'Auto area', u: 'area', p: 1 },
    { k: 'area_rel_err', l: 'Rel. area error', p: 3 },
    { k: 'under_cov_mm2', l: 'Uncovered', u: 'area', p: 1, tip: 'Wound area the patch leaves uncovered (manual minus auto)' },
    { k: 'over_cov_mm2', l: 'Excess', u: 'area', p: 1, tip: 'Patch area on healthy skin (auto minus manual)' },
    { k: 'cri_lambda3', l: 'Cost index λ=3', p: 3, tip: 'Coverage cost: uncovered wound weighs 3 times the excess, relative to the wound area' },
  ] },
  { t: 'Distance', g: 'di', items: [
    { k: 'hausdorff_mm', l: 'Hausdorff', u: 'len', p: 2 },
    { k: 'avg_surf_dist_mm', l: 'Avg surface dist', u: 'len', p: 2 },
  ] },
  { t: 'Manual geometry', g: 'gm', items: [
    { k: 'perimeter_gt_mm', l: 'Perimeter', u: 'len', p: 2 },
    { k: 'compactness_gt', l: 'Compactness', p: 3 },
    { k: 'bbox_gt_major_mm', l: 'BBox major', u: 'len', p: 2 },
    { k: 'bbox_gt_minor_mm', l: 'BBox minor', u: 'len', p: 2 },
    { k: 'aspect_ratio_gt', l: 'Aspect ratio', p: 3 },
  ] },
  { t: 'Auto geometry', g: 'ga', items: [
    { k: 'perimeter_auto_mm', l: 'Perimeter', u: 'len', p: 2 },
    { k: 'compactness_auto', l: 'Compactness', p: 3 },
    { k: 'bbox_auto_major_mm', l: 'BBox major', u: 'len', p: 2 },
    { k: 'bbox_auto_minor_mm', l: 'BBox minor', u: 'len', p: 2 },
    { k: 'aspect_ratio_auto', l: 'Aspect ratio', p: 3 },
    { k: 'bbox_auto_x_mm', l: 'A (X)', u: 'len', p: 2 },
    { k: 'bbox_auto_y_mm', l: 'B (Y)', u: 'len', p: 2 },
  ] },
];

/* The unit an item is in right now. metInfo is the backend's word; until it
   has answered, the scale the contours came with is the next best thing. */
function metUnit(it) {
  if (!it.u) return '';
  if (metInfo) return it.u === 'area' ? metInfo.area_unit : metInfo.length_unit;
  const cal = !!pxPerMm;
  return it.u === 'area' ? (cal ? 'mm²' : 'px²') : (cal ? 'mm' : 'px');
}

function metLabel(it) {
  const u = metUnit(it);
  return u ? it.l + ' (' + u + ')' : it.l;
}

/* Pixels are whole numbers: a pixel area with a decimal is noise, a pixel
   length gets one. Everything else keeps the item's own precision. */
function fmtMetric(it, v) {
  if (v === undefined || v === null) return '-';
  if (typeof v !== 'number') return String(v);
  let p = it.p;
  if (it.u && metUnit(it).indexOf('px') === 0) p = it.u === 'area' ? 0 : 1;
  return v.toFixed(p);
}

function buildMetPanel() {
  const panel = document.getElementById('metPanel');
  panel.innerHTML = '';
  MGRPS.forEach(function (grp) {
    const color = MPAL[grp.g].m;
    const head = document.createElement('div');
    head.className = 'mg';
    head.style.color = color;
    head.textContent = grp.t;
    panel.appendChild(head);

    grp.items.forEach(function (it) {
      const row = document.createElement('div');
      row.className = 'mrow';
      row.style.setProperty('--gc', color);
      if (it.tip) row.title = it.tip;
      const label = document.createElement('span');
      label.className = 'ml';
      label.textContent = metLabel(it);
      const value = document.createElement('span');
      value.className = 'mv';
      value.style.color = color;
      value.textContent = fmtMetric(it, metData[it.k]);
      row.appendChild(label);
      row.appendChild(value);
      row.onclick = function () { clickMet(it.k, row); };
      panel.appendChild(row);
    });
  });
}

/* Clicking a row draws its overlay; clicking it again goes back to the plain
   two-contour view. */
function clickMet(key, row) {
  if (!metCnt.auto) { toast('Run the segmentation first'); return; }
  const rows = document.querySelectorAll('.mrow');
  for (let i = 0; i < rows.length; i++) rows[i].classList.remove('active');
  if (metKey === key) { metKey = null; dMet(); return; }
  metKey = key;
  row.classList.add('active');
  redraw();
}

/* Plain view: both contours, filled faintly. */
function dMet() {
  if (!iUrl) return;
  const f = beginFrame(), ctx = f.ctx;
  if (metCnt.gt) {
    const P = _toC(metCnt.gt, f.sc, f.ox, f.oy);
    _fillPoly(ctx, P, _rgba(COL['c-gt'], .09));
    strokeContour(ctx, metCnt.gt, f, COL['c-gt'], null);
  }
  if (metCnt.auto) {
    const P = _toC(metCnt.auto, f.sc, f.ox, f.oy);
    _fillPoly(ctx, P, _rgba(COL['c-auto'], .09));
    strokeContour(ctx, metCnt.auto, f, COL['c-auto'], [5, 3]);
  }
}

/* Overlay view: the geometry behind the selected number. */
function dMetVec() {
  if (!iUrl) return;
  const f = beginFrame(), ctx = f.ctx;
  const G = metCnt.gt ? _toC(metCnt.gt, f.sc, f.ox, f.oy) : null;
  const A = metCnt.auto ? _toC(metCnt.auto, f.sc, f.ox, f.oy) : null;
  drawMetricOverlay(ctx, metKey, G, A);
}

/* ── exports ──────────────────────────────────────────────────────────────── */
function saveRes() {
  be('saveResults', undefined, function (msg) {
    toast(msg || 'Saved ✓');
    if (msg && msg.indexOf('Saved') === 0) { resSaved = true; paintTabs(); }
  });
}

/* The report: a PDF, printed by the same engine that renders this page, so
   it needs nothing installed and no connection. Printing is asynchronous, the
   outcome arrives later on reportDone (an .html name typed in the dialog is
   written and answered at once instead). The step-6 sliders travel with the
   request because only this page knows them; with no patch built the report
   simply has no 3D section. */
function saveReport() {
  if (!metCnt.auto) { toast('Run the segmentation first'); return; }
  const payload = { patch3d: patch3dParams() };
  beJson('saveReport', JSON.stringify(payload), function (r) {
    if (!r) { toast('⚠ Report export failed', 3800); return; }
    if (r.pending) { showLd('Building the PDF…'); setSt('Building the PDF…', 'busy'); return; }
    if (!r.ok && r.msg === 'Cancelled') return;        // he changed his mind, not an error
    toast((r.ok ? '✓ ' : '⚠ ') + r.msg, r.ok ? 3400 : 3800);
    if (r.ok) { resSaved = true; paintTabs(); }
  });
}

/* The PDF went the long way round, through Chromium, and lands here. */
function onReportDone(ok, msg) {
  hideLd();
  setSt(ok ? 'Report saved ✓' : 'Error', ok ? 'ok' : 'err');
  toast((ok ? '✓ ' : '⚠ ') + msg, ok ? 3400 : 4200);
  if (ok) { resSaved = true; paintTabs(); }
}

function saveGT() {
  beJson('saveGTMask', undefined, function (r) {
    if (!r) { toast('⚠ Could not save the mask', 3800); return; }
    toast((r.ok ? '✓ ' : '⚠ ') + r.msg, r.ok ? 3200 : 3800);
  });
}
