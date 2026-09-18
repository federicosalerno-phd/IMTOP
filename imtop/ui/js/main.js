/* ============================================================================
   main.js: boot and wiring. The only file that runs code at load time.

   It connects the backend signals to the modules that care, handles opening an
   image (browse, drag & drop, command line) and owns the window-level input
   handlers. Everything it calls lives in another module.
   ========================================================================== */

let APPINFO = null;    // getAppInfo(): version, SAM state, installed back-ends

/* ── backend signals ──────────────────────────────────────────────────────── */
/* A new image resets the whole pipeline: the scale, the trace and the metrics
   all belong to the previous photo. */
function onImageReady(url) {
  iUrl = url;
  resetSessionState();

  const calok = document.getElementById('calok');
  if (calok) calok.style.display = 'none';
  document.getElementById('calLen').value = '';
  document.getElementById('noimg').style.display = 'none';
  buildMetPanel();                           // rows back to '-'
  updSS();

  beJson('getImageSize', undefined, function (s) {
    if (!s) { hideLd(); return; }
    iW = s.w; iH = s.h;
    document.getElementById('iw').textContent = iW;
    document.getElementById('ih').textContent = iH;
    document.getElementById('imgstats').style.display = 'block';
    setFileName(s.name);                    // authoritative: whatever really loaded
    setBaseImage(iUrl, iW, iH);             // drops the loading overlay once the photo is drawable
    if (curP === 0) go(1);                  // straight to calibration; SAM can wait
  });

  updSamCard();
  setSt('Image loaded ✓', 'ok');
}

/* modelReady arrives when a lazy SAM load finishes, successfully or not. */
function onModelReady(ok) {
  hideSamLoader();
  if (!ok) {
    _pendingRun = false;
    setSt('SAM unavailable', '');
    toast('⚠ SAM is not available', 3800);
    updSamCard();
    return;
  }
  samReady = true;
  // Which device it ended up on can differ from what getAppInfo said at boot:
  // SAM moves to the processor when the card runs out of memory, and the card on
  // step 1 should say where the model really is. The method list is not rebuilt,
  // that would drop the operator's choice.
  beJson('getAppInfo', undefined, function (info) {
    if (info && APPINFO) { APPINFO.sam_device = info.sam_device; }
    updSamCard();
  });
  if (_pendingRun) { _pendingRun = false; runSeg(); }
}

function onStatus(msg) { setSt(msg, msg.indexOf('✓') >= 0 ? 'ok' : 'busy'); }

function onError(msg) {
  hideLd();
  hideSamLoader();
  _pendingRun = false;
  setSt('Error', 'err');
  toast('⚠ ' + msg, 4000);
}

function onScaleSet(v) {
  document.getElementById('calok').style.display = 'inline-flex';
  document.getElementById('calval').textContent = v.toFixed(2) + ' px/mm';
  calDone = true;
  paintTabs();
  toast('Scale set: ' + v.toFixed(2) + ' px/mm ✓');
}

/* The overlay image the backend renders is not used: the UI draws its own
   vector overlays, which stay crisp at any zoom. */
function onAutoSegDone() {
  hideLd();
  hideSamLoader();
  setSt('Auto seg done ✓', 'ok');
  _pc = {};                                  // new masks invalidate the 3D contour cache
  metKey = null;
  resSaved = false; stlSaved = false;        // whatever was exported belongs to the old run

  // The unit first, then the numbers: the panel labels are built from the
  // unit, so it must be known before the rows are.
  beJson('getMetricsInfo', undefined, function (info) {
    metInfo = info || null;
    beJson('getMetrics', undefined, function (m) { metData = m || {}; buildMetPanel(); paintTabs(); });
  });
  // The trace as the backend has it. After a manual run this is what the page
  // already holds; after an auto-prompt it is the detected object, which the
  // page has never seen and step 3 would otherwise show as empty.
  beJson('getControlPoints', undefined, function (r) {
    if (!r) return;
    segPts = r.pts || []; segCl = !!r.closed; segDr = -1;
    updSS();
  });
  // Fetch the contours FIRST, then navigate: this guarantees metCnt is filled
  // before dAuto runs, so the automatic contour is never missing on arrival.
  beJson('getContours', undefined, function (c) {
    metCnt = c || {};
    pxPerMm = metCnt.s || null;
    if (curP === 3) { redraw(); setTimeout(redraw, 40); }
    else go(3);
  });
}

/* ── application info ─────────────────────────────────────────────────────── */
function applyAppInfo(info) {
  APPINFO = info || {};
  // The About note carries the full credit. The title bar's line is the
  // licence one SlantUI writes there, and it is about the layout, not the app.
  const about = document.getElementById('appVer');
  about.textContent = 'IMTOP ' + (APPINFO.version ? 'v' + APPINFO.version : '');
  if (APPINFO.credit) {
    about.appendChild(document.createElement('br'));
    about.appendChild(document.createTextNode(APPINFO.credit));
  }
  buildMethodList(APPINFO.methods);
  updSamCard();
}

/* The SAM card on step 1 says exactly where the model stands. */
function updSamCard() {
  const el = document.getElementById('samst');
  const sub = document.getElementById('samsub');
  if (!APPINFO) { el.textContent = 'Checking…'; return; }
  if (!APPINFO.sam_available) {
    el.textContent = 'Not installed';
    sub.textContent = 'torch + segment-anything missing';
  } else if (samReady) {
    el.textContent = '✓ Ready';
    sub.textContent = 'ViT-B on ' + (APPINFO.sam_device || 'cpu');
  } else if (!APPINFO.sam_checkpoint_present) {
    el.textContent = 'Downloads on first use';
    sub.textContent = 'ViT-B checkpoint, ~375 MB';
  } else {
    el.textContent = 'Loads on first run';
    sub.textContent = 'ViT-B on ' + (APPINFO.sam_device || 'cpu');
  }
}

/* ── opening an image ─────────────────────────────────────────────────────── */
/* The native dialog is modal on its own: no overlay while it is open. */
function doBrowse() {
  setSt('Opening…', 'busy');
  be('browseFile', undefined, function (path) {
    if (!path || !path.length) { setSt('Ready', 'ok'); return; }
    setFileName(path.split(/[\\/]/).pop());
    showLd('Loading image…');
    setSt('Loading…', 'busy');
    be('loadImage', path);
  });
}

/* Show which photo is open: in the canvas toolbar on every step that has the
   image on screen, and as a card on the first one. */
function setFileName(name) {
  const n = name || '';
  // A wound photo routinely carries a 50 character name: both labels shrink
  // until it fits and drop the middle only if that was not enough, so nothing
  // ever spills out of the toolbar or out of the card (js/widgets.js).
  fitOneLine(document.getElementById('fname'), n, 11.5, 9.5);
  document.getElementById('fileIcon').style.display = n ? 'block' : 'none';
  fitOneLine(document.getElementById('fnameCard'), n || '-', 13.5, 10);
}

/* Dropped files have no path, so they travel to Python as a data URL. */
function onDrop(e) {
  e.preventDefault();
  const dz = document.getElementById('dzd');
  if (dz) dz.classList.remove('dzover');
  const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
  if (!f) return;
  if (!/^image\//.test(f.type) && !/\.(jpe?g|png|bmp|tiff?)$/i.test(f.name)) {
    toast('Not an image file');
    return;
  }
  setFileName(f.name);
  showLd('Loading image…');
  setSt('Loading…', 'busy');
  const rd = new FileReader();
  rd.onload = function () { be('loadImageData', JSON.stringify({ name: f.name, data: rd.result })); };
  rd.onerror = function () { hideLd(); setSt('Error', 'err'); toast('Could not read the file'); };
  rd.readAsDataURL(f);
}

/* ── window-level input ───────────────────────────────────────────────────── */
function installInputHandlers() {
  /* Keep the web view from navigating to a file dropped outside the drop zone. */
  ['dragover', 'drop'].forEach(function (ev) {
    window.addEventListener(ev, function (e) { e.preventDefault(); }, false);
  });

  /* Ctrl+wheel must zoom the image, never the app. This capture-phase listener
     cancels the browser's page zoom everywhere without stopping propagation,
     so the canvas handler still sees the event. */
  window.addEventListener('wheel', function (e) {
    if (e.ctrlKey) e.preventDefault();
  }, { passive: false, capture: true });

  /* Same for keyboard page zoom. */
  window.addEventListener('keydown', function (e) {
    if (e.ctrlKey && (e.key === '+' || e.key === '-' || e.key === '=' || e.key === '0')) e.preventDefault();
  }, { passive: false, capture: true });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'F9') { e.preventDefault(); hudToggle(); return; }
    if (e.key === 'F10') { e.preventDefault(); dumpPerf(); return; }
    if (e.key === 'F8') { e.preventDefault(); baselineTest(); return; }
    if (e.ctrlKey && (e.key === 'z' || e.key === 'Z')) { e.preventDefault(); if (curP === 2) sUndo(); return; }
    if (e.ctrlKey && (e.key === 'y' || e.key === 'Y')) { e.preventDefault(); if (curP === 2) sRedo(); return; }
    if (e.key === 'Enter' && curP === 2) {
      e.preventDefault();
      if (!segCl) closeContour(); else runPipeline();
      return;
    }
    if (e.key === 'Escape' && curP === 2) { sReset(); return; }
    if (e.key === 'ArrowRight' && !e.ctrlKey) go(curP + 1);
    if (e.key === 'ArrowLeft' && !e.ctrlKey) go(curP - 1);
  });

  /* On resize/maximize, repaint the stage at the new size. The zoom and the
     pan are kept (clampT() in the draw keeps them legal): snapping the photo
     back to fit while the operator drags the window edge is a refresh he did
     not ask for. */
  window.addEventListener('resize', function () {
    if (curP >= 1 && curP <= 4 && _cm !== null) redraw();
    if (curP === 5) patch3DResize();
  });

  /* Middle-button pan: it starts over the canvas but is tracked at window level
     so the drag survives the cursor leaving it. Capture phase, and the
     preventDefault kills Chromium's middle-click autoscroll. */
  window.addEventListener('mousedown', function (e) { if (e.target === MC()) panStart(e); }, true);
  window.addEventListener('mousemove', panDrag, true);
  window.addEventListener('mouseup', panEnd, true);
  window.addEventListener('auxclick', function (e) { if (e.button === 1) e.preventDefault(); });
}

/* ── boot ─────────────────────────────────────────────────────────────────── */
(function boot() {
  initTheme();
  initSelects();
  initTitlebar();
  installInputHandlers();

  Bridge.on('imageReady', onImageReady);
  Bridge.on('modelReady', onModelReady);
  Bridge.on('statusUpdate', onStatus);
  Bridge.on('error', onError);
  Bridge.on('scaleSet', onScaleSet);
  Bridge.on('autoSegDone', onAutoSegDone);
  Bridge.on('samProgress', samProgress);
  Bridge.on('reportDone', onReportDone);

  setSt('Waiting…', '');
  buildMetPanel();                           // the rows exist before any number does
  go(0);
  updSS();

  Bridge.init();
  beJson('getAppInfo', undefined, applyAppInfo);

  /* An image passed on the command line (or by "Open with"). */
  be('getStartupImage', undefined, function (path) {
    if (!path || !path.length) { setSt('Ready', 'ok'); return; }
    setFileName(path.split(/[\\/]/).pop());
    showLd('Loading image…');
    setSt('Loading…', 'busy');
    be('loadImage', path);
  });
})();
