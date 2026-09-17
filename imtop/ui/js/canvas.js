/* ============================================================================
   canvas.js: the image stage shared by steps 2..5.

   The photo and the vector overlays are painted on ONE canvas, the size of the
   stage, every frame. The photo is drawn from a pyramid of pre-scaled copies
   (built once per image), picking the level closest to the zoom, so a frame
   costs one drawImage of at most stage-sized pixels at any zoom, on any
   machine, with nothing left to a tile rasteriser: no tiles, no
   checkerboarding, no black squares while zooming or panning.

   The canvas backing store follows devicePixelRatio, so the photo and the
   lines are crisp on a scaled display; every coordinate in the drawing code
   stays in CSS pixels through one setTransform per frame.

   Every module that draws implements one d*() function and registers it here
   through the step index; redraw() dispatches to it.
   ========================================================================== */

const MC = () => document.getElementById('mc');
const MX = () => MC().getContext('2d');

function _dpr() { return window.devicePixelRatio || 1; }
/* The stage size in CSS pixels: what every mapping below is expressed in. */
function cssW() { return document.getElementById('cwrap').clientWidth; }
function cssH() { return document.getElementById('cwrap').clientHeight; }

/* Resize the backing store only when the stage (or the pixel ratio) really
   changed: resizing a canvas reallocates and clears it. */
function rzC() {
  const c = MC(), dpr = _dpr();
  const W = Math.round(cssW() * dpr), H = Math.round(cssH() * dpr);
  if (c.width !== W || c.height !== H) {
    c.width = W;
    c.height = H;
  }
}

/* Scale that maps image pixels to CSS pixels at the current zoom. */
function gsc() { return Math.min(cssW() / iW, cssH() / iH) * view.z; }

/* Clamp the translation: an axis smaller than the stage stays centred (equal
   borders), a larger one keeps covering the stage (no gap at the edge). */
function clampT() {
  const W = cssW(), H = cssH(), sc = gsc(), iw = iW * sc, ih = iH * sc;
  view.tx = iw <= W ? (W - iw) / 2 : Math.min(0, Math.max(W - iw, view.tx));
  view.ty = ih <= H ? (H - ih) / 2 : Math.min(0, Math.max(H - ih, view.ty));
}
function gof() { clampT(); return { ox: view.tx, oy: view.ty }; }

/* image <-> client coordinates */
function i2c(x, y) { const sc = gsc(), o = gof(); return { x: x * sc + o.ox, y: y * sc + o.oy }; }
function c2i(cx, cy) { const sc = gsc(), o = gof(); return { x: (cx - o.ox) / sc, y: (cy - o.oy) / sc }; }

/* ── the photo ────────────────────────────────────────────────────────────── */
/* The pyramid: level 0 is the photo at its own size, each next level is half
   the previous one, down to about 256 px. Kept until the next image. */
let _base = null;        // { levels: [{ src, w, h, s }] }, s = level px per image px
let _baseGen = 0;        // bumps per setBaseImage(): a late decode of an old image is dropped
const PYRAMID_MIN_SIDE = 256;
const COPY_LEVEL0_MAX_PX = 16e6;   // above this the decoded <img> itself is level 0

function buildPyramid(img, w, h) {
  const levels = [];
  let src = img, sw = w, sh = h, s = 1;
  if (w * h <= COPY_LEVEL0_MAX_PX) {
    // A canvas copy is drawn from a stable bitmap; an <img> can be evicted
    // from the decode cache and re-decoded mid-drag.
    const cv = document.createElement('canvas');
    cv.width = w; cv.height = h;
    cv.getContext('2d').drawImage(img, 0, 0);
    src = cv;
  }
  levels.push({ src: src, w: w, h: h, s: 1 });
  while (Math.max(sw, sh) > PYRAMID_MIN_SIDE) {
    const nw = Math.max(1, Math.round(sw / 2)), nh = Math.max(1, Math.round(sh / 2));
    const cv = document.createElement('canvas');
    cv.width = nw; cv.height = nh;
    const g = cv.getContext('2d');
    g.imageSmoothingEnabled = true;
    g.imageSmoothingQuality = 'high';
    g.drawImage(src, 0, 0, sw, sh, 0, 0, nw, nh);
    s /= 2;
    levels.push({ src: cv, w: nw, h: nh, s: s });
    src = cv; sw = nw; sh = nh;
  }
  return { levels: levels };
}

/* Decode a new image and build its pyramid. The loading overlay (if any) is
   dropped once the photo can actually be drawn, never before. */
function setBaseImage(url, w, h) {
  _base = null;
  const gen = ++_baseGen;
  const img = new Image();
  img.onload = function () {
    if (gen !== _baseGen) return;           // a newer image replaced it meanwhile
    _base = buildPyramid(img, w, h);
    hideLd();
    redraw();
  };
  img.onerror = function () {
    if (gen !== _baseGen) return;
    hideLd();
    toast('Could not decode the image');
  };
  img.src = url;
}

/* The smallest level that is still at least `need` device pixels per image
   pixel: the photo is only ever scaled down (or, past its own size, up from
   the original), never down by more than half. */
function pickLevel(need) {
  const L = _base.levels;
  let best = L[0];
  for (let i = 0; i < L.length; i++) {
    if (L[i].s >= need) best = L[i];
    else break;
  }
  return best;
}

function _rrect(ctx, x, y, w, h, r) {
  r = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y); ctx.arcTo(x + w, y, x + w, y + r, r);
  ctx.lineTo(x + w, y + h - r); ctx.arcTo(x + w, y + h, x + w - r, y + h, r);
  ctx.lineTo(x + r, y + h); ctx.arcTo(x, y + h, x, y + h - r, r);
  ctx.lineTo(x, y + r); ctx.arcTo(x, y, x + r, y, r);
  ctx.closePath();
}

/* Draw the visible part of the photo so that image pixel (x,y) lands on CSS
   pixel (x*sc+ox, y*sc+oy), the exact mapping i2c() uses. Only the part
   inside the stage is drawn: at a high zoom the source rectangle is small
   and the destination is at most the stage. */
function drawBase(ctx, sc, ox, oy) {
  if (!_base) return;
  const W = cssW(), H = cssH();
  const lv = pickLevel(sc * _dpr());
  const x0 = Math.max(0, Math.floor(-ox / sc)), y0 = Math.max(0, Math.floor(-oy / sc));
  const x1 = Math.min(iW, Math.ceil((W - ox) / sc)), y1 = Math.min(iH, Math.ceil((H - oy) / sc));
  if (x1 <= x0 || y1 <= y0) return;
  ctx.save();
  if (IMG_RADIUS) { _rrect(ctx, ox, oy, iW * sc, iH * sc, IMG_RADIUS); ctx.clip(); }
  ctx.drawImage(lv.src,
                x0 * lv.s, y0 * lv.s, (x1 - x0) * lv.s, (y1 - y0) * lv.s,
                ox + x0 * sc, oy + y0 * sc, (x1 - x0) * sc, (y1 - y0) * sc);
  ctx.restore();
}

/* ── zoom & pan ───────────────────────────────────────────────────────────── */
/* Cursor-anchored, applied at once; painting is throttled to one frame so a
   fast wheel burst stays fluid but stops the instant the wheel does. Never
   goes below fit (z = 1). */
function zoomAt(ax, ay, f) {
  const nz = Math.max(1, Math.min(30, view.z * f));
  if (nz === view.z) return;
  const sc = gsc(), o = gof();
  const ix = (ax - o.ox) / sc, iy = (ay - o.oy) / sc;   // image point under (ax,ay)
  view.z = nz;
  const sc2 = gsc();
  view.tx = ax - ix * sc2;
  view.ty = ay - iy * sc2;                              // keep it under the cursor
  clampT();
  scheduleRedraw();
}
function zoomStep(f) { zoomAt(cssW() / 2, cssH() / 2, f); }
function resetView() { view = { z: 1, tx: 0, ty: 0 }; redraw(); }

/* Ctrl+wheel over the canvas: ~10% per notch, proportional to the delta so a
   trackpad gets small smooth steps. Plain wheel does nothing. */
function zmC(e) {
  if (!e.ctrlKey) return;
  e.preventDefault();
  if (!iUrl) return;
  _markFast(); _frPing('zoom');
  const r = MC().getBoundingClientRect();
  zoomAt(e.clientX - r.left, e.clientY - r.top, Math.pow(1.10, -e.deltaY / 120));
}

/* Middle-button pan. Started only over the canvas, but tracked at window level
   so the drag survives the cursor leaving the canvas (wired in main.js). */
function panStart(e) {
  if (e.button !== 1 || curP < 1 || curP > 4 || !iUrl) return;
  e.preventDefault();
  _pan = { sx: e.clientX, sy: e.clientY, tx: view.tx, ty: view.ty };
  MC().style.cursor = 'grabbing';
}
function panDrag(e) {
  if (!_pan) return;
  _markFast(); _frPing('pan');
  view.tx = _pan.tx + (e.clientX - _pan.sx);
  view.ty = _pan.ty + (e.clientY - _pan.sy);
  clampT();
  scheduleRedraw();
}
function panEnd() {
  if (!_pan) return;
  _pan = null;
  MC().style.cursor = '';
  redraw();
}

/* ── painting ─────────────────────────────────────────────────────────────── */
/* Coalesce rapid zoom/pan requests into one paint per animation frame. */
let _raf = 0;
function scheduleRedraw() {
  if (_raf) return;
  _raf = requestAnimationFrame(function () { _raf = 0; redraw(); });
}

/* Mark an interaction in progress: cheap scaling now, one crisp repaint when
   the wheel or the drag goes quiet. */
function _markFast() {
  _fast = true;
  clearTimeout(_fastT);
  _fastT = setTimeout(function () { _fast = false; redraw(); }, 140);
}

/* Wire the canvas for one step. `keepView` keeps the zoom and the pan the
   operator set: walking from the trace to the metrics is the same photo, and
   snapping it back to fit every time reads as the app refreshing under you. */
function initCanvas(step, keepView) {
  const c = MC();
  rzC();
  if (!keepView) view = { z: 1, tx: 0, ty: 0 };
  c.onmousedown = null; c.onmousemove = null; c.onmouseup = null;
  c.ondblclick = null; c.onwheel = null; c.oncontextmenu = null;
  c.style.cursor = '';
  c.onwheel = zmC;
  c.ondblclick = resetView;
  c.oncontextmenu = (e) => e.preventDefault();
  _cm = step;
  if (step === 1) {
    c.onmousedown = calClick;
  } else if (step === 2) {
    c.onmousedown = sdD; c.onmousemove = sdM; c.onmouseup = sdU; c.oncontextmenu = sdR;
  }
  redraw();
}

/* Paint the current step. Each step owns its d*() in its own module. */
function redraw() {
  if (!iUrl || !cssW() || !cssH()) return;
  const t0 = performance.now();
  const ctx = MX();
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = _fast ? 'low' : 'high';
  if (_cm === 1) dCal();
  else if (_cm === 2) dSeg();
  else if (_cm === 3) dAuto();
  else if (_cm === 4) { if (metKey) dMetVec(); else dMet(); }
  _rec(performance.now() - t0);      // always recorded (cheap), so F10 can report
  if (_prof) hudPaint();
}

/* Clear the canvas and paint the photo, the first two lines of every d*(). */
function beginFrame() {
  const ctx = MX(), dpr = _dpr();
  rzC();
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW(), cssH());
  const sc = gsc(), o = gof();
  drawBase(ctx, sc, o.ox, o.oy);
  return { ctx: ctx, sc: sc, ox: o.ox, oy: o.oy };
}

/* A crosshair marker, the app's one on-canvas point glyph. */
function cross(ctx, x, y, col, size, lw) {
  ctx.beginPath();
  ctx.moveTo(x - size / 2, y); ctx.lineTo(x + size / 2, y);
  ctx.moveTo(x, y - size / 2); ctx.lineTo(x, y + size / 2);
  ctx.strokeStyle = col;
  ctx.lineWidth = lw;
  ctx.stroke();
}
