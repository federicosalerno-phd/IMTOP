/* ============================================================================
   titlebar.js: the window's own title bar.

   The Qt window is frameless, so moving, resizing, minimising, maximising and
   closing it are the page's job. None of that is reimplemented here: the drag
   and the resize are handed straight back to the window manager through
   startSystemMove / startSystemResize (imtop/bridge.py), which is what keeps
   Windows' snapping, edge magnetism, shadow and double-click behaviour.

   The band's shape is clipped here instead of in CSS. Its lower profile,
   measured down from the top edge of the window, is:

     0            x1        x2                                    W
     ┌────────────────────────────────────────────────────────────┐  0
     │                        ·.                                  │
     │                          `·.____________________________   │  --tbar-thin
     │                                                            │
     └──────────────────────────┘                                 │  --tbar-h
       thick, under the name      the taper      thin, under the buttons

   so the band never stops: it only gets thinner. x1 is wherever the name ends,
   which is why this is computed from the real layout instead of guessed.
   ========================================================================== */

const MAX_ICON = '<svg viewBox="0 0 12 12"><rect x="2.5" y="2.5" width="7" height="7" rx="1"/></svg>';
const RESTORE_ICON = '<svg viewBox="0 0 12 12"><rect x="1.8" y="4.2" width="6" height="6" rx="1"/>' +
                     '<path d="M4.6 4.2V2.8a1 1 0 0 1 1-1h3.6a1 1 0 0 1 1 1v3.6a1 1 0 0 1-1 1H8.8"/></svg>';

/* An SVG path through the points, with each corner rounded by its own `r`
   (a quadratic through the vertex, trimmed to half the shorter side). */
function roundedPolyPath(pts) {
  const n = pts.length;
  let d = '';
  for (let i = 0; i < n; i++) {
    const prev = pts[(i - 1 + n) % n], cur = pts[i], next = pts[(i + 1) % n];
    const r = cur.r || 0;
    if (r <= 0) {
      d += (i === 0 ? 'M' : 'L') + cur.x.toFixed(2) + ',' + cur.y.toFixed(2) + ' ';
      continue;
    }
    const v1x = prev.x - cur.x, v1y = prev.y - cur.y;
    const v2x = next.x - cur.x, v2y = next.y - cur.y;
    const l1 = Math.hypot(v1x, v1y) || 1, l2 = Math.hypot(v2x, v2y) || 1;
    const a = Math.min(r, l1 / 2), b = Math.min(r, l2 / 2);
    const p1x = cur.x + v1x / l1 * a, p1y = cur.y + v1y / l1 * a;
    const p2x = cur.x + v2x / l2 * b, p2y = cur.y + v2y / l2 * b;
    d += (i === 0 ? 'M' : 'L') + p1x.toFixed(2) + ',' + p1y.toFixed(2) + ' ';
    d += 'Q' + cur.x.toFixed(2) + ',' + cur.y.toFixed(2) + ' ' +
         p2x.toFixed(2) + ',' + p2y.toFixed(2) + ' ';
  }
  return d + 'Z';
}

/* Cut the band to that profile. Called at start-up and on every resize, since
   both the window width and the width of the name decide the shape. */
function shapeTitleBar() {
  const bar = document.getElementById('titlebar');
  const band = document.getElementById('tbarBand');
  const brand = document.querySelector('.tbar-brand');
  if (!bar || !band || !brand) return;

  const cs = getComputedStyle(document.documentElement);
  const num = function (name, fallback) {
    const v = parseFloat(cs.getPropertyValue(name));
    return isNaN(v) ? fallback : v;
  };
  const W = bar.clientWidth;
  const h1 = bar.clientHeight;
  const h2 = num('--tbar-thin', 28);
  const slant = num('--tbar-slant', 38);
  const join = num('--tbar-join', 8);

  const x1 = Math.round(brand.getBoundingClientRect().width);
  const x2 = Math.min(W, x1 + slant);

  const pts = [
    { x: 0, y: 0 },                 // window corner
    { x: W, y: 0 },                 // window corner
    { x: W, y: h2 },                // the thin end, flush with the right edge
    { x: x2, y: h2, r: join },      // top of the taper
    { x: x1, y: h1, r: join },      // bottom of the taper
    { x: 0, y: h1 },                // window corner
  ];
  const d = roundedPolyPath(pts);

  // clip-path: path() needs a recent Chromium (Qt 6). On the Qt 5 fallback the
  // same profile is used without the rounded joints.
  if (window.CSS && CSS.supports && CSS.supports('clip-path', 'path("M0 0")')) {
    band.style.clipPath = 'path("' + d + '")';
  } else {
    band.style.clipPath = 'polygon(' + pts.map(function (p) {
      return p.x + 'px ' + p.y + 'px';
    }).join(',') + ')';
  }
}

function initTitlebar() {
  const bar = document.getElementById('titlebar');

  /* Anywhere on the bar except the buttons starts a system move. */
  bar.addEventListener('mousedown', function (e) {
    if (e.button !== 0 || e.target.closest('.wbtn')) return;
    e.preventDefault();
    be('winDrag');
  });
  bar.addEventListener('dblclick', function (e) {
    if (e.target.closest('.wbtn')) return;
    be('winMaximizeToggle');
  });

  document.getElementById('winMin').onclick = function () { be('winMinimize'); };
  document.getElementById('winMax').onclick = function () { be('winMaximizeToggle'); };
  document.getElementById('winClose').onclick = function () { be('winClose'); };

  const edges = document.querySelectorAll('.rz');
  for (let i = 0; i < edges.length; i++) {
    edges[i].addEventListener('mousedown', function (e) {
      if (e.button !== 0) return;
      e.preventDefault();
      be('winResize', this.dataset.edge);
    });
  }

  shapeTitleBar();
  // Once more after the first frame: the name's width is only final after layout.
  requestAnimationFrame(shapeTitleBar);
  window.addEventListener('resize', shapeTitleBar);

  be('winIsMaximized', undefined, onWindowMaximized);
}

/* Qt tells us when the window was maximised or restored, including when it was
   Windows that did it (snap, Win+Up, double-click). */
function onWindowMaximized(max) {
  document.body.classList.toggle('maximized', !!max);
  const btn = document.getElementById('winMax');
  btn.title = max ? 'Restore' : 'Maximise';
  btn.innerHTML = max ? RESTORE_ICON : MAX_ICON;
  shapeTitleBar();
}
