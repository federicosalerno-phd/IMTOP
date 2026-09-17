/* ============================================================================
   calibration.js: step 2. Two clicks on a reference of known length give the
   px/mm scale. Skipping it is legitimate: every metric is then reported in
   pixels, and getMetricsInfo() tells the rest of the UI which it is.
   ========================================================================== */

function calClick(e) {
  if (e.button !== 0) return;                 // middle = pan, right = nothing
  if (!iUrl) { toast('Load an image first'); return; }
  const r = MC().getBoundingClientRect();
  const pt = c2i(e.clientX - r.left, e.clientY - r.top);
  if (calPts.length >= 2) calPts = [];        // a third click starts over
  calPts.push(pt);
  be('setCalPoints', JSON.stringify(calPts));
  dCal();
}

function dCal() {
  if (!iUrl) return;
  const f = beginFrame(), ctx = f.ctx;
  const col = COL['c-cal'];

  calPts.forEach(function (p) {
    const c = i2c(p.x, p.y);
    cross(ctx, c.x, c.y, col, 24, 2.5);
  });
  if (calPts.length !== 2) return;

  const a = i2c(calPts[0].x, calPts[0].y), b = i2c(calPts[1].x, calPts[1].y);
  ctx.beginPath();
  ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y);
  ctx.strokeStyle = col; ctx.lineWidth = 1.5;
  ctx.setLineDash([5, 3]); ctx.stroke(); ctx.setLineDash([]);

  /* Badge in the middle: the typed length, or the raw pixel distance. */
  const px = Math.hypot(calPts[1].x - calPts[0].x, calPts[1].y - calPts[0].y);
  const typed = document.getElementById('calLen').value;
  const label = typed ? typed + ' mm (' + px.toFixed(0) + ' px)' : px.toFixed(0) + ' px';

  ctx.save();
  ctx.font = '600 11px ' + getComputedStyle(document.body).fontFamily;
  const w = ctx.measureText(label).width + 14, h = 22, rr = 4;
  const cx = (a.x + b.x) / 2, cy = (a.y + b.y) / 2, x = cx - w / 2, y = cy - h / 2;
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.lineTo(x + w - rr, y); ctx.arcTo(x + w, y, x + w, y + rr, rr);
  ctx.lineTo(x + w, y + h - rr); ctx.arcTo(x + w, y + h, x + w - rr, y + h, rr);
  ctx.lineTo(x + rr, y + h); ctx.arcTo(x, y + h, x, y + h - rr, rr);
  ctx.lineTo(x, y + rr); ctx.arcTo(x, y, x + rr, y, rr);
  ctx.closePath();
  ctx.fillStyle = COL['c-badge-bg']; ctx.fill();
  ctx.strokeStyle = col; ctx.lineWidth = 1.4; ctx.stroke();
  ctx.fillStyle = COL.text;
  ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.fillText(label, cx, cy);
  ctx.restore();
}

function setScale() {
  if (!iUrl) { toast('Load an image first'); return; }
  if (calPts.length < 2) { toast('Click the two ends of the reference first'); return; }
  const mm = parseFloat(document.getElementById('calLen').value);
  if (isNaN(mm) || mm <= 0) { toast('Enter a valid length in mm'); return; }
  beJson('setScale', mm, function (r) {
    if (!r || !r.ok) toast('⚠ ' + ((r && r.msg) || 'Could not set the scale'));
    else dCal();
  });
}

function skipScale() {
  if (!iUrl) { toast('Load an image first'); return; }
  be('skipScale', undefined, function () {
    calDone = true;
    document.getElementById('calok').style.display = 'none';
    toast('Using pixels as the unit');
    go(2);
    preEncode();
  });
}

/* Calibration is the last step that changes the image, so start the SAM
   encode now in the background: the first run then feels instant. The backend
   ignores the request if the image is already encoded. */
function preEncode() { be('prewarmSAM'); }

/* "Confirm" used to only advance. A user who placed 2 points and typed the
   length but never pressed "Set scale" then worked with the scale UNSET, and
   every metric came out in pixels while labelled mm. Apply it here instead
   (setScale is idempotent); no valid length means pixels, exactly like Skip. */
function confirmCal() {
  if (!iUrl) { toast('Load an image first'); return; }
  const mm = parseFloat(document.getElementById('calLen').value);
  if (calPts.length >= 2 && !isNaN(mm) && mm > 0) {
    beJson('setScale', mm, function (r) {
      if (!r || !r.ok) toast('⚠ ' + ((r && r.msg) || 'Could not set the scale'));
      else dCal();
      calDone = true;
      go(2);
      preEncode();
    });
  } else {
    calDone = true;
    go(2);
    preEncode();
  }
}
