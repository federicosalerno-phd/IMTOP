/* ============================================================================
   theme.js: IMTOP's own tokens, read back for the things it draws.

   The <canvas> cannot use CSS variables, so the colours it needs are read off
   :root once at start-up. The reading itself is SlantUI's: Theme.read(names)
   is the same call its thirty roles come through, given IMTOP's names from
   css/tokens.css instead. Everything else this file used to carry, the colour
   maths included, is in slantui.js now.

   Edit css/tokens.css and the overlays follow; nothing else in the JS
   hard-codes a colour.
   ========================================================================== */

/* Resolved colours, filled by initTheme(). */
const COL = {};

/* Light / mid / dark shade of each metric group, keyed by the group id used in
   overlays.js (ov = overlap, ar = area, di = distance, gm/ga = geometry). */
const MPAL = {};

const _CANVAS_KEYS = [
  'c-gt', 'c-auto', 'c-point', 'c-first', 'c-cal', 'c-badge-bg',
  'c-3d-gt', 'c-3d-gt-edge', 'c-3d-auto', 'c-3d-auto-edge',
];
const _GROUPS = ['ov', 'ar', 'di', 'gm', 'ga'];

/* Corner rounding of the photo, in screen pixels (see --img-radius). */
let IMG_RADIUS = 0;

function initTheme() {
  const own = _CANVAS_KEYS.concat(['img-radius']);
  _GROUPS.forEach(function (g) { own.push(g + '-l', g + '-m', g + '-d'); });
  Theme.read();          // SlantUI's roles, for the code that asks Theme.get
  Theme.read(own);       // IMTOP's own, through the same call
  _CANVAS_KEYS.forEach(function (k) { COL[k] = Theme.get(k) || '#888888'; });
  _GROUPS.forEach(function (g) {
    MPAL[g] = { l: Theme.get(g + '-l'), m: Theme.get(g + '-m'), d: Theme.get(g + '-d') };
  });
  IMG_RADIUS = parseFloat(Theme.get('img-radius')) || 0;
}

/* The canvas code calls these by their old names; the maths is SlantUI's. */
function _mix(a, b, t) { return Theme.mix(a, b, t); }
function _rgba(h, a) { return Theme.rgba(h, a); }
function _hex3d(h) { return Theme.hex3d(h); }
