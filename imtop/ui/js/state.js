/* ============================================================================
   state.js: every piece of state shared between modules, declared once.
   Classic scripts share one global scope, so these top-level bindings are
   visible everywhere; nothing else in the UI declares module-wide state.
   ========================================================================== */

/* ── workflow ─────────────────────────────────────────────────────────────── */
let curP = 0;            // current step, 0..5 (index into SCFG)
let _cm = null;          // which drawing mode the canvas is wired for (1..4)
/* Every step is always reachable, image or not (his rule): a tab is never
   locked and a button is never disabled. What a step needs and does not have
   is said in a toast when the button is pressed. The tab bar shows which steps
   have produced their output (paintTabs() in nav.js); this is the one bit of
   that state nothing else records. */
let calDone = false;     // the operator set a scale, or chose to work in pixels
let resSaved = false;    // the metrics of the current run were saved (JSON or report)
let stlSaved = false;    // the patch of the current run was exported

/* ── image ────────────────────────────────────────────────────────────────── */
let iUrl = null;         // data URL of the loaded image
let iW = 0, iH = 0;      // image size in pixels
let pxPerMm = null;      // set once the scale is known; null = work in pixels

/* ── view (image stage) ───────────────────────────────────────────────────── */
let view = { z: 1, tx: 0, ty: 0 };
let _pan = null;               // {sx,sy,tx,ty} while middle-dragging
let _fast = false, _fastT = null;   // low-quality scaling during interaction

/* ── step 2: calibration ──────────────────────────────────────────────────── */
let calPts = [];

/* ── step 3: manual segmentation ──────────────────────────────────────────── */
let segPts = [];         // control points, image coordinates
let segCl = false;       // contour closed?
let segDr = -1;          // index of the point being dragged, -1 = none
let justPlaced = false;  // keep the crosshair on the point just added

/* ── step 4: automatic segmentation ───────────────────────────────────────── */
let METHODS = [];        // from getAppInfo().methods, the installed back-ends
let curMethod = null;    // id of the selected one
let samReady = false;    // SAM loaded AND the current image encoded
let _pendingRun = false; // run as soon as modelReady arrives

/* ── step 5: metrics ──────────────────────────────────────────────────────── */
let metData = {};        // key -> value
let metInfo = null;      // getMetricsInfo(): {calibrated, px_per_mm, length_unit, area_unit}
let metCnt = {};         // {gt:[{x,y}…], auto:[…], s:px_per_mm}
let metKey = null;       // metric whose overlay is on screen, null = plain view

/* ── step 6: 3D patch ─────────────────────────────────────────────────────── */
let patchVol = {};       // side -> volume of the built solid, null until it exists.
                         // Area and perimeter come from metData; the volume only
                         // exists once the mesh has been built, and the report
                         // asks for it (patch3dParams() in patch3d.js).

/* ── SAM loading overlay ──────────────────────────────────────────────────── */
let _samTarget = 0, _samShown = 0, _samRAF = 0, _samCreepT = null;

/* ── constants ────────────────────────────────────────────────────────────── */
const SNAP = 12;         // px: grab radius for a control point

/* Line-style "hand" cursor for grabbing points. A PNG, not an SVG, because the
   embedded Chromium renders SVG cursors unreliably; falls back to grab. */
const GRAB_CUR = 'url("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAB4AAAAeCAYAAAA7MK6iAAAACXBIWXMAABYlAAAWJQFJUiTwAAAFM0lEQVRIidWWXWwUVRTH//fOnZntzkzLdm23LR/SpCktAUuBgEYUDCQiNE0ggqhETDQxISHRZxMT8UVD4pOB+KCJ0WIkwIMPfGhEhVZbVEQLSFvQtrS0u7Td3dmZ2d35uj6U1qVfu9SY4HmYh5lz/79zzzlz7gX+x0YAyPNZNF9jiqJsoRJbTZWg5iVSva7rns5kMv0FLZ4nlGoLtJeKdm3cLa2t3UREQfZGkn3WZ9/WSb3Rw7qu9+QTEO4TSILBYGUwGNwW2LZul7xx5XYiUAYANBhYIC5fvMxuu/awSIUyURTDjuPcBuDOKFQoUdO0MKd8l9RQ00hkqUHatKKeVYaLna6Bjuz5q78qL295lYgCc4fGdBKQMv6tO3+Yn35zCrb7USqVGp2qV2iqVSKxg+r+pp2suqICADjnAADv5pDtdP7pctv1iSiAVZYWAygWQmq59saOiH7omA7gQwA8V5AWAGVasfa28tozu8ehHLBdn5DxZHGOCgCLiCwyAPBHdTP7fWcbOPeFqnCd1FDTqChKZKpoXrCiKFuKmtZvZTULHxp/QwCJ0Yn4aXlJEEAYwnjZvIFR3TrR2omMnQIAtqS8HMB9gwmV2GrpyZW1Ey/c3lh8gg8AVGIUgPBPu/C7T5Kb2mm9NCc4FAoVC1XhMiKxyV7wegbH7nHivJByTbM5F3HOCei8dP8d+L+0Bw4860TzEobt9Awaftr24bg82941RAhJ8oSVAQD7Wv8oIcTKB84dIExV1Sc4IY2ssrQKo3rcs91YbgT2hSt9zsWuc9kLV6IANhNCDAAnGWM/Jw+2vEnVQKmfNL/jnKcLBmua1hTYuuYFefOq7UQSg9zzXa8/djVz7rcfJ8Ht1zuoxw/phtEH4J0pWi9GAgE5ahimoiivFwQOhUIlvCq0UX567Q5CCQM4JwJlrLqiQX2lomHCmTPhNoDkLFpuNBqdOBAIIWS8jOOz1Z/qTAHAdd1KqbFmKaGE+boV099qeS/b0fU1PM/JF/lMRgSicJkpAODFDYMxlpoRTAhxueu6AEACksY1WbZazrUl3z/5gXd7rGtyx2bGlmV5zmBUVS0TaxdFCCECAHiDI3EAial+DABkWR62O7q65U0NaSKxouL9zfv0w19+wgdGz+vvftETWF+3xvc5vFjiUtQwzDwbXimtq6sHAD+VvuPeHOoxDCM+1UkAAMuybOb4BJmsKNYtXk5kUZVX1Syzu24lvIRxJtsfPU1iyfZUKtWKKcdbrpWUlISEkLan6NnHnyOCINqXb7Q6nb0nbdvunREMAI7j9LKo7vtm2suFOz0DvmDZfbqu/54P6nG+Vz3QvI+G1CqezsatI6eOphLJ45jhFnLPoLBtu3savLGmnutWEYklVVEURxzHSecGoKpqmSRJjwohbY96oHmfsDBcz33uWsfOf+70Drc4jjM4U6AzXX2IpmnN0oblO4M7NzwPgYoA5+61/h/SZy/95P41HOecp4Dx7hVrF0WkdXX14iNLHyOSGOQ+d7Nnfjlpnb74sWmaZ2fL0Gx3LqJpWhNbUb1N2fvUHlIkLZj4wB0vS1w3A0IJlwSFUDqZNZ7Oxq0TbSeyHdePm6b5FeYozawz2bbtbjqWijkd3aO0OOjSkFpKRBYgAmUQWQBMkCeGhJ9K37Ev32i1jpw66vQOt5im2Tabbr4dT1okElEMw9gAihVS3ZJqoSocolowCM65FzcMb3Ak7t4c6gFwxTCMdgCZfJoFgXN9VVUto5SWeJ6nAPDvTqREMpmc9p8+sPY3XOo66rhD1CgAAAAASUVORK5CYII=") 12 10, grab';

/* Per-step configuration: which side panel, whether the canvas toolbar shows,
   the hint line in it, which stage fills the work area, the status message. */
const SCFG = [
  { rp: 'rp0', toolbar: false, canvas: 'p0',     hint: 'Drop a photo on the stage, or press Open image',                              st: 'Load an image' },
  { rp: 'rp1', toolbar: true,  canvas: 'canvas', hint: 'Click 2 points on a known-length reference · Ctrl+scroll zoom',            st: 'Scale calibration' },
  { rp: 'rp2', toolbar: true,  canvas: 'canvas', hint: 'Left-click add · Drag move · Right-click remove · Enter to close', st: 'Drawing GT…' },
  { rp: 'rp3', toolbar: true,  canvas: 'canvas', hint: 'Manual contour in green, automatic in red',                                      st: 'Segmentation done ✓' },
  { rp: 'rp4', toolbar: true,  canvas: 'canvas', hint: 'Click any metric row to overlay it on the image',                               st: 'Metrics ready' },
  { rp: 'rp5', toolbar: false, canvas: 'p5',     hint: 'Drag to rotate · Ctrl+scroll to zoom the patch',                            st: 'Ready to export' },
];

/* Reset everything that belongs to one image. Called when a new image loads. */
function resetSessionState() {
  view = { z: 1, tx: 0, ty: 0 };
  calPts = [];
  segPts = []; segCl = false; segDr = -1; justPlaced = false;
  metData = {}; metInfo = null; metCnt = {}; metKey = null;
  patchVol = {};
  samReady = false; _pendingRun = false; pxPerMm = null;
  calDone = false; resSaved = false; stlSaved = false;
}
