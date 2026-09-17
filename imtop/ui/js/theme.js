/* ============================================================================
   theme.js: the bridge between css/tokens.css and everything drawn on the
   <canvas>. The canvas cannot use CSS variables, so the few colours it needs
   are read back from :root once at start-up. Edit tokens.css and the overlays
   follow; nothing else in the JS hard-codes a colour.
   ========================================================================== */

/* Resolved colours, filled by initTheme(). */
const COL = {};

/* Light / mid / dark shade of each metric group, keyed by the group id used in
   overlays.js (ov = overlap, ar = area, di = distance, gm/ga = geometry). */
const MPAL = {};

const _THEME_KEYS = [
  'c-gt', 'c-auto', 'c-point', 'c-first', 'c-cal', 'c-badge-bg',
  'c-3d-gt', 'c-3d-gt-edge', 'c-3d-auto', 'c-3d-auto-edge',
  'text', 'text-dim', 'accent', 'ink', 'bg', 'line',
];

/* Corner rounding of the photo, in screen pixels (see --img-radius). */
let IMG_RADIUS = 0;

function initTheme() {
  const cs = getComputedStyle(document.documentElement);
  const v = (name) => cs.getPropertyValue('--' + name).trim();
  _THEME_KEYS.forEach((k) => { COL[k] = v(k) || '#888888'; });
  ['ov', 'ar', 'di', 'gm', 'ga'].forEach((g) => {
    MPAL[g] = { l: v(g + '-l'), m: v(g + '-m'), d: v(g + '-d') };
  });
  IMG_RADIUS = parseFloat(v('img-radius')) || 0;
}

/* ── colour maths (hex in, canvas-ready strings out) ─────────────────────── */
function _hx(h) {
  h = h.replace('#', '');
  if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}
/* Linear blend between two hex colours, t in [0,1]. */
function _mix(a, b, t) {
  const x = _hx(a), y = _hx(b);
  return 'rgb(' + (x[0] + (y[0] - x[0]) * t | 0) + ',' +
                  (x[1] + (y[1] - x[1]) * t | 0) + ',' +
                  (x[2] + (y[2] - x[2]) * t | 0) + ')';
}
/* Same colour at a given alpha. */
function _rgba(h, a) {
  const x = _hx(h);
  return 'rgba(' + x[0] + ',' + x[1] + ',' + x[2] + ',' + a + ')';
}
/* 0xRRGGBB for Three.js materials. */
function _hex3d(h) {
  const x = _hx(h);
  return (x[0] << 16) | (x[1] << 8) | x[2];
}
