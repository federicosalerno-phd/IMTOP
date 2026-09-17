/* ============================================================================
   widgets.js: the two small controls the platform does not give us in a usable
   shape.

   1. `.combo`, a dropdown. A native <select> hands its popup to the platform,
      which draws it outside the page with its own colours; inside the embedded
      browser that popup comes up unreadable (you cannot see which row you are
      about to pick). This one is a plain list in the page, in the app's own
      palette. It answers to `.value` exactly like a <select> does, and it
      fires a `change` event, so the code around it does not know the
      difference.

   2. `fitOneLine`, text that must never spill out of its box. A wound photo
      routinely carries a 50 character file name; the label shrinks until it
      fits, and only if it is still too long does it drop the middle, keeping
      the start and the extension. The full name is always in the tooltip.
   ========================================================================== */

/* ── dropdown ─────────────────────────────────────────────────────────────── */
let _cbPop = null;        // the open popup, or null
let _cbOwner = null;      // the .combo it belongs to

function _cbClose() {
  if (_cbPop && _cbPop.parentNode) _cbPop.parentNode.removeChild(_cbPop);
  if (_cbOwner) _cbOwner.classList.remove('open');
  _cbPop = null;
  _cbOwner = null;
}

function _cbOptions(el) {
  return (el.getAttribute('data-options') || '').split('|').filter(function (s) { return s.length; });
}

function _cbSet(el, value, fire) {
  el.setAttribute('data-value', value);
  const lbl = el.querySelector('.combo-v');
  if (lbl) lbl.textContent = value;
  if (fire) el.dispatchEvent(new Event('change', { bubbles: true }));
}

/* Open under the trigger, or above it when there is no room below. Fixed
   positioning, so no scroll container can clip it. */
function _cbOpen(el) {
  if (_cbOwner === el) { _cbClose(); return; }
  _cbClose();
  const opts = _cbOptions(el);
  if (!opts.length) return;
  const r = el.getBoundingClientRect();
  const pop = document.createElement('div');
  pop.className = 'combopop';
  opts.forEach(function (o) {
    const row = document.createElement('div');
    row.className = 'combo-opt' + (o === el.getAttribute('data-value') ? ' combo-on' : '');
    row.textContent = o;
    row.onmousedown = function (e) { e.preventDefault(); };
    row.onclick = function () { _cbSet(el, o, true); _cbClose(); };
    pop.appendChild(row);
  });
  pop.style.left = Math.round(r.left) + 'px';
  pop.style.width = Math.round(r.width) + 'px';
  pop.style.visibility = 'hidden';
  document.body.appendChild(pop);
  const h = pop.offsetHeight;
  const below = window.innerHeight - r.bottom - 6;
  pop.style.top = (below >= h || r.top < h + 6)
    ? Math.round(r.bottom + 4) + 'px'
    : Math.round(r.top - h - 4) + 'px';
  pop.style.visibility = '';
  el.classList.add('open');
  _cbPop = pop;
  _cbOwner = el;
}

/* Give every `.combo` on the page a `.value` property and its handlers. Called
   once at boot, before anything reads a value. */
function initSelects() {
  document.querySelectorAll('.combo').forEach(function (el) {
    if (el._wired) return;
    el._wired = true;
    Object.defineProperty(el, 'value', {
      get: function () { return el.getAttribute('data-value') || ''; },
      set: function (v) { _cbSet(el, v, false); },
      configurable: true,
    });
    _cbSet(el, el.getAttribute('data-value') || _cbOptions(el)[0] || '', false);
    el.addEventListener('mousedown', function (e) { e.preventDefault(); });
    el.addEventListener('click', function (e) { e.stopPropagation(); _cbOpen(el); });
    el.addEventListener('keydown', function (e) {
      const opts = _cbOptions(el);
      const i = opts.indexOf(el.value);
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); _cbOpen(el); }
      else if (e.key === 'ArrowDown' && i < opts.length - 1) { e.preventDefault(); _cbSet(el, opts[i + 1], true); }
      else if (e.key === 'ArrowUp' && i > 0) { e.preventDefault(); _cbSet(el, opts[i - 1], true); }
      else if (e.key === 'Escape') _cbClose();
    });
  });

  // Capture phase, so a click anywhere else closes the popup before that click
  // does anything; a press on the popup itself, or on its trigger, is its own
  // business (removing the popup here would eat the option's click).
  window.addEventListener('mousedown', function (e) {
    if (!_cbPop) return;
    if (_cbPop.contains(e.target)) return;
    if (_cbOwner && _cbOwner.contains(e.target)) return;
    _cbClose();
  }, true);
  window.addEventListener('resize', function () { _cbClose(); });
  window.addEventListener('keydown', function (e) { if (e.key === 'Escape') _cbClose(); }, true);
}

/* ── number stepper ───────────────────────────────────────────────────────── */
/* What the engine's own up/down arrows do, done by hand: stepUp()/stepDown()
   refuse to work on an empty field, and the result has to keep the number of
   decimals the step implies or "50" becomes "50.00000000000001". */
function numStep(id, dir) {
  const el = document.getElementById(id);
  if (!el) return;
  const step = parseFloat(el.step) || 1;
  const cur = parseFloat(el.value);
  let v = isNaN(cur) ? (dir > 0 ? step : 0) : cur + dir * step;
  const lo = parseFloat(el.min), hi = parseFloat(el.max);
  if (!isNaN(lo) && v < lo) v = lo;
  if (!isNaN(hi) && v > hi) v = hi;
  if (v < 0) v = 0;
  const dec = (String(step).split('.')[1] || '').length;
  el.value = dec ? v.toFixed(dec) : String(Math.round(v));
  el.dispatchEvent(new Event('input', { bubbles: true }));
}

/* ── text that has to fit ─────────────────────────────────────────────────── */
/* Shrink from `maxPx` down to `minPx` in half-pixel steps; if that is still
   not enough, keep the head and the last 8 characters (the extension is what
   tells two exports of the same case apart at a glance). The element needs
   `white-space:nowrap; overflow:hidden` for the measurement to mean anything,
   which is what the `.fit` class is for. */
function fitOneLine(el, text, maxPx, minPx) {
  if (!el) return;
  const s = (text == null) ? '' : String(text);
  el.title = s;
  el.style.fontSize = '';
  el.textContent = s;
  if (!s) return;
  const box = el.clientWidth;
  if (box < 24) return;                       // not laid out yet: leave it alone
  let size = maxPx;
  el.style.fontSize = size + 'px';
  while (el.scrollWidth > box && size > minPx) {
    size -= 0.5;
    el.style.fontSize = size + 'px';
  }
  if (el.scrollWidth <= box) return;
  const tail = s.slice(-8);
  for (let head = s.length - 8; head > 3; head -= 2) {
    el.textContent = s.slice(0, head) + '…' + tail;
    if (el.scrollWidth <= box) return;
  }
}
