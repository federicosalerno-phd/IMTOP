/* ============================================================================
   patch3d.js: step 6. The wound contour becomes a solid patch: extruded to a
   thickness, with a chamfered or filleted edge and an optional perilesional
   offset, ready to export as STL.

   The mesh is built in JS straight from the contour, so moving a slider updates
   at 60fps with no backend round-trip, and the camera is never touched on an
   update, the view you rotated to stays put.

   Nothing is allocated while a slider moves. The shape of the solid (how many
   vertices, which triangle uses which of them, which vertex belongs to which
   ring) depends only on the number of contour points and the edge mode, never
   on the slider values: that plan, the index buffer and the vertex buffers are
   built once per contour and then only written into. Normals come out of the
   plan too, in closed form, instead of being re-derived from 13000 triangles
   every frame. Dragging a slider used to cost 7 ms per frame with peaks of
   12 ms, which is most of a frame gone before anything is drawn.

   The viewers render on demand, never in a loop: a frame is drawn when the
   camera moved, when the mesh changed or when the viewer was resized, and
   nothing at all is drawn while nobody touches them. The rotation has no
   inertia, the patch follows the mouse one to one.
   ========================================================================== */

const V3D = {};            // side -> {canvas,renderer,scene,camera,controls,group,framed,dirty,w,h,dpr}
let _v3dRAF = 0;
let _pc = {};              // side -> cached centred contour
const N_ARC3D = 20;        // segments in a fillet quarter-arc

function v3dColors(side) {
  return side === 'gt'
    ? { s: _hex3d(COL['c-3d-gt']), e: _hex3d(COL['c-3d-gt-edge']) }
    : { s: _hex3d(COL['c-3d-auto']), e: _hex3d(COL['c-3d-auto-edge']) };
}

/* ── the contour, prepared once ───────────────────────────────────────────── */
/* Centred contour (mm) as FLAT typed arrays, plus, per point:
     dir  the radial direction from the centroid, which is what an offset and
          an edge push the point along (the app has always worked this way);
     nrm  the contour's own outward normal, taken from the local tangent. It is
          what the wall of the solid actually faces, and it carries the winding
          of the contour with it: on a contour traced the other way round it
          points the other way, exactly as a normal derived from the triangles
          would, and the material is double-sided for that case anyway.
   sgn is the sign of the contour's signed area, the same winding expressed for
   the vertical part of a normal (a cap faces up on one winding, down on the
   other). */
function prepContour(cpx, s) {
  const N = cpx.length;
  const c = new Float32Array(2 * N), dir = new Float32Array(2 * N), nrm = new Float32Array(2 * N);
  let mx = 0, my = 0;
  for (const p of cpx) { mx += p.x; my += p.y; }
  mx /= N; my /= N;
  for (let i = 0; i < N; i++) { c[2 * i] = (cpx[i].x - mx) / s; c[2 * i + 1] = (cpx[i].y - my) / s; }
  let area2 = 0;
  for (let i = 0; i < N; i++) {
    const x = c[2 * i], y = c[2 * i + 1], L = Math.hypot(x, y) + 1e-9;
    dir[2 * i] = x / L; dir[2 * i + 1] = y / L;
    const j = (i + 1) % N, k = (i - 1 + N) % N;
    const tx = c[2 * j] - c[2 * k], ty = c[2 * j + 1] - c[2 * k + 1];
    const M = Math.hypot(tx, ty) + 1e-9;
    nrm[2 * i] = ty / M; nrm[2 * i + 1] = -tx / M;
    area2 += c[2 * i] * c[2 * j + 1] - c[2 * j] * c[2 * i + 1];
  }
  return { c: c, dir: dir, nrm: nrm, N: N, sgn: area2 >= 0 ? 1 : -1 };
}

/* ── the plan ─────────────────────────────────────────────────────────────── */
/* A part is one ring of N vertices, or one cap centre. Every part sits at a
   "level": a radial inset and a height, both functions of the thickness and
   the edge size, recomputed per frame into plan.lv. A part also carries the
   normal of the surface it belongs to, as (nr, nz) in (outward, up) terms,
   which is why the same ring appears more than once: the boundary of the top
   cap and the top of the wall are the same circle with two different normals,
   and that hard edge is what makes the solid read as a machined object.

   Levels, in order:
     flat      0 = (inset 0, height t)   1 = (0, 0)
     chamfer   0 = (0, t)  1 = (0, r)    2 = (r, 0)
     fillet    0 = (0, t)  1 = (0, r)    2..N_ARC3D+1 = the quarter arc
   ========================================================================== */
const _plans = {};

function patchPlan(N, mode, hasEdge) {
  const kind = !hasEdge ? 'flat' : (mode.indexOf('Chamfer') >= 0 ? 'chamfer' : 'fillet');
  const key = N + '|' + kind;
  if (_plans[key]) return _plans[key];
  if (Object.keys(_plans).length > 8) for (const k in _plans) delete _plans[k];

  const parts = [], caps = [], bands = [], loops = [];
  let nLv = 0, arc = null;
  const P = function (lv, ring, nr, nz) {
    parts.push({ lv: lv, ring: ring, nr: nr, nz: nz, base: 0 });
    return parts.length - 1;
  };

  if (kind === 'flat') {
    nLv = 2;
    caps.push({ r: P(0, true, 0, 1), c: P(0, false, 0, 1), up: true });
    caps.push({ r: P(1, true, 0, -1), c: P(1, false, 0, -1), up: false });
    bands.push([P(0, true, 1, 0), P(1, true, 1, 0)]);
    loops.push(0, 1);
  } else if (kind === 'chamfer') {
    nLv = 3;
    const S = Math.SQRT1_2;                       // the face slopes at 45 degrees
    caps.push({ r: P(0, true, 0, 1), c: P(0, false, 0, 1), up: true });
    caps.push({ r: P(2, true, 0, -1), c: P(2, false, 0, -1), up: false });
    bands.push([P(0, true, 1, 0), P(1, true, 1, 0)]);        // vertical wall
    bands.push([P(1, true, S, -S), P(2, true, S, -S)]);      // chamfer face
    loops.push(0, 1, 2);
  } else {
    nLv = N_ARC3D + 2;
    arc = new Float32Array(2 * N_ARC3D);          // (1 - cos, 1 - sin) per step
    const ring = [P(0, true, 1, 0), P(1, true, 1, 0)];
    for (let k = 1; k <= N_ARC3D; k++) {
      const th = k / N_ARC3D * (Math.PI / 2);
      arc[2 * (k - 1)] = 1 - Math.cos(th);
      arc[2 * (k - 1) + 1] = 1 - Math.sin(th);
      ring.push(P(k + 1, true, Math.cos(th), -Math.sin(th)));
    }
    for (let g = 0; g < ring.length - 1; g++) bands.push([ring[g], ring[g + 1]]);
    caps.push({ r: P(0, true, 0, 1), c: P(0, false, 0, 1), up: true });
    caps.push({ r: P(N_ARC3D + 1, true, 0, -1), c: P(N_ARC3D + 1, false, 0, -1), up: false });
    loops.push(0, 1, N_ARC3D + 1);
  }

  let base = 0;
  for (const p of parts) { p.base = base; base += p.ring ? N : 1; }

  // Vertices and triangles come out in exactly the order the hand-written
  // builder produced them, so an STL exported today is byte for byte the one
  // the previous version wrote: caps first, except on the fillet, where the
  // smooth strip was emitted before them.
  const tri = [];
  const emitCaps = function () {
    for (const cp of caps) {
      const rb = parts[cp.r].base, cb = parts[cp.c].base;
      for (let i = 0; i < N; i++) {
        const j = (i + 1) % N;
        if (cp.up) tri.push(cb, rb + i, rb + j); else tri.push(cb, rb + j, rb + i);
      }
    }
  };
  const emitBands = function () {
    for (const bd of bands) {
      const a = parts[bd[0]].base, b = parts[bd[1]].base;
      for (let i = 0; i < N; i++) {
        const j = (i + 1) % N;
        tri.push(a + i, b + i, a + j, b + i, b + j, a + j);
      }
    }
  };
  if (kind === 'fillet') { emitBands(); emitCaps(); } else { emitCaps(); emitBands(); }

  const plan = {
    kind: kind, N: N, parts: parts, loops: loops, arc: arc,
    lv: new Float32Array(2 * nLv),
    idx: new Uint32Array(tri),
    pos: new Float32Array(base * 3),
    nor: new Float32Array(base * 3),
    loopBuf: loops.map(function () { return new Float32Array(3 * N); }),
  };
  _plans[key] = plan;
  return plan;
}

/* Where every level sits for this thickness and this edge size. */
function patchLevels(plan, t, r) {
  const L = plan.lv;
  L[0] = 0; L[1] = t;
  if (plan.kind === 'flat') { L[2] = 0; L[3] = 0; return; }
  L[2] = 0; L[3] = r;
  if (plan.kind === 'chamfer') { L[4] = r; L[5] = 0; return; }
  for (let k = 1; k <= N_ARC3D; k++) {
    L[2 * (k + 1)] = r * plan.arc[2 * (k - 1)];
    L[2 * (k + 1) + 1] = r * plan.arc[2 * (k - 1) + 1];
  }
}

/* Write this frame's coordinates into the plan's buffers. Nothing is allocated
   here: for N = 300 contour points and a fillet this is 21600 float writes and
   no garbage at all. Plane coordinates (x, y) at height z become world
   (x, z, -y), which is why every y is negated on the way out. */
function buildPatchGeo(pc, t, edgeVal, mode, off) {
  const r = Math.min(edgeVal, t * 0.99);
  const plan = patchPlan(pc.N, mode, r > 1e-6);
  const N = pc.N, c = pc.c, dir = pc.dir, nrm = pc.nrm, sgn = pc.sgn;
  const pos = plan.pos, nor = plan.nor;
  patchLevels(plan, t, r);
  const L = plan.lv;

  for (let p = 0; p < plan.parts.length; p++) {
    const part = plan.parts[p], ri = L[2 * part.lv], z = L[2 * part.lv + 1];
    let o = part.base * 3;
    if (!part.ring) {                           // a cap centre, on the axis
      pos[o] = 0; pos[o + 1] = z; pos[o + 2] = 0;
      nor[o] = 0; nor[o + 1] = part.nz * sgn; nor[o + 2] = 0;
      continue;
    }
    const d = ri - off;                         // the offset pushes outward, the edge inward
    const nr = part.nr, nz = part.nz * sgn;
    for (let i = 0; i < N; i++) {
      pos[o] = c[2 * i] - dir[2 * i] * d;
      pos[o + 1] = z;
      pos[o + 2] = -(c[2 * i + 1] - dir[2 * i + 1] * d);
      nor[o] = nrm[2 * i] * nr;
      nor[o + 1] = nz;
      nor[o + 2] = -nrm[2 * i + 1] * nr;
      o += 3;
    }
  }

  for (let k = 0; k < plan.loops.length; k++) {
    const lv = plan.loops[k], d = L[2 * lv] - off, z = L[2 * lv + 1], buf = plan.loopBuf[k];
    for (let i = 0; i < N; i++) {
      buf[3 * i] = c[2 * i] - dir[2 * i] * d;
      buf[3 * i + 1] = z;
      buf[3 * i + 2] = -(c[2 * i + 1] - dir[2 * i + 1] * d);
    }
  }
  return plan;
}

/* Signed volume of a closed triangle soup (divergence theorem). */
function meshVolume(pos, idx) {
  let v = 0;
  for (let f = 0; f < idx.length; f += 3) {
    const a = idx[f] * 3, b = idx[f + 1] * 3, c = idx[f + 2] * 3;
    v += pos[a] * (pos[b + 1] * pos[c + 2] - pos[b + 2] * pos[c + 1])
       - pos[a + 1] * (pos[b] * pos[c + 2] - pos[b + 2] * pos[c])
       + pos[a + 2] * (pos[b] * pos[c + 1] - pos[b + 1] * pos[c]);
  }
  return Math.abs(v) / 6;
}

/* ── viewers ──────────────────────────────────────────────────────────────── */
/* Soft gradient environment -> a studio/CAD sheen on the surface. */
function v3dEnv(renderer) {
  const cnv = document.createElement('canvas');
  cnv.width = 16; cnv.height = 128;
  const g = cnv.getContext('2d'), grd = g.createLinearGradient(0, 0, 0, 128);
  grd.addColorStop(0, '#eef3f6'); grd.addColorStop(0.5, '#8b969e'); grd.addColorStop(1, '#23272b');
  g.fillStyle = grd; g.fillRect(0, 0, 16, 128);
  const tex = new THREE.CanvasTexture(cnv);
  tex.mapping = THREE.EquirectangularReflectionMapping;
  const pm = new THREE.PMREMGenerator(renderer);
  const env = pm.fromEquirectangular(tex).texture;
  tex.dispose(); pm.dispose();
  return env;
}

function v3dInit(side) {
  if (V3D[side]) return V3D[side];
  if (typeof THREE === 'undefined') return null;
  const canvas = document.getElementById(side === 'gt' ? 'cv3g' : 'cv3a');
  const renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true, alpha: true });
  // sRGB output for correct colours, but NO ACES tone-mapping: it washes them
  // out to pastel, and the patch should read as a solid object.
  try { renderer.outputEncoding = THREE.sRGBEncoding; renderer.toneMapping = THREE.NoToneMapping; } catch (e) {}

  const scene = new THREE.Scene();
  try { scene.environment = v3dEnv(renderer); } catch (e) {}
  const camera = new THREE.PerspectiveCamera(35, 1, 0.05, 100000);
  const controls = new THREE.OrbitControls(camera, renderer.domElement);
  controls.enableDamping = false;              // one to one: the patch follows the mouse, no inertia
  controls.rotateSpeed = 0.9; controls.panSpeed = 0.7;
  controls.enableZoom = false;                 // zoom is Ctrl+wheel only, as on the image pages
  try { controls.mouseButtons = { LEFT: THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.ROTATE, RIGHT: THREE.MOUSE.PAN }; } catch (e) {}

  const v = { canvas: canvas, renderer: renderer, scene: scene, camera: camera, controls: controls,
              group: new THREE.Group(), framed: false, dirty: true, w: 0, h: 0, dpr: 0, plan: null };
  scene.add(v.group);
  V3D[side] = v;

  // The camera moved (drag, pan or wheel): one frame, when the browser is ready for it.
  controls.addEventListener('change', function () { v.dirty = true; v3dRequest(); });

  // Same mouse scheme as the rest of the app:
  //   drag rotate · middle-drag rotate · Ctrl+middle-drag pan · Ctrl+wheel zoom
  renderer.domElement.addEventListener('pointerdown', function (e) {
    if (e.button === 1) { controls.mouseButtons.MIDDLE = e.ctrlKey ? THREE.MOUSE.PAN : THREE.MOUSE.ROTATE; e.preventDefault(); }
  }, true);
  renderer.domElement.addEventListener('wheel', function (e) {
    if (!e.ctrlKey) return;
    e.preventDefault();
    const off = camera.position.clone().sub(controls.target);
    off.multiplyScalar(e.deltaY > 0 ? 1.12 : 0.892);
    if (off.length() < 0.05) off.setLength(0.05);
    camera.position.copy(controls.target).add(off);
    controls.update();                         // dispatches 'change' -> a frame
  }, { passive: false });

  scene.add(new THREE.HemisphereLight(0xffffff, 0x35353c, 0.55));
  const key = new THREE.DirectionalLight(0xffffff, 0.85); key.position.set(1.4, 2.2, 1.8); scene.add(key);
  const fill = new THREE.DirectionalLight(0xffffff, 0.28); fill.position.set(-1.6, 0.4, -1.3); scene.add(fill);
  const rim = new THREE.DirectionalLight(0xffffff, 0.32); rim.position.set(-0.5, 1.2, -2); scene.add(rim);
  return v;
}

/* Size the drawing buffer to the box, only when the box (or the pixel ratio)
   really changed: resizing a WebGL buffer is the one thing that is not free.
   A matte solid does not need four megapixels of anti-aliased surface: past
   that the ratio is eased down, so two large viewers on a maximised window
   still fit in one frame. Returns false when the viewer has no size yet. */
const V3D_MAX_PX = 2.2e6;
function v3dResize(side) {
  const v = V3D[side];
  if (!v) return false;
  const box = v.canvas.parentElement, w = box.clientWidth, h = box.clientHeight;
  if (w < 2 || h < 2) return false;
  const raw = Math.min(window.devicePixelRatio || 1, 2);
  const px = w * h * raw * raw;
  let dpr = px > V3D_MAX_PX ? Math.max(1, raw * Math.sqrt(V3D_MAX_PX / px)) : raw;
  dpr = Math.round(dpr * 100) / 100;
  if (v.w === w && v.h === h && v.dpr === dpr) return true;
  v.w = w; v.h = h; v.dpr = dpr;
  v.renderer.setPixelRatio(dpr);
  v.renderer.setSize(w, h, false);
  v.camera.aspect = w / h;
  v.camera.updateProjectionMatrix();
  v.dirty = true;
  return true;
}

/* Frame the object with equal margins, aspect-aware. */
function v3dFit(side) {
  const v = V3D[side];
  if (!v || !v.group.children.length) return;
  if (v.mesh) { v.mesh.geometry.computeBoundingBox(); v.mesh.geometry.computeBoundingSphere(); }
  const box = new THREE.Box3().setFromObject(v.group), sph = new THREE.Sphere();
  box.getBoundingSphere(sph);
  const fov = v.camera.fov * Math.PI / 180, asp = v.camera.aspect || 1;
  const halfV = fov / 2, halfH = Math.atan(Math.tan(halfV) * asp);
  const dist = sph.radius / Math.sin(Math.min(halfV, halfH)) * 1.16;
  const dir = new THREE.Vector3(0.45, 0.6, 1).normalize();
  v.controls.target.copy(sph.center);
  v.camera.position.copy(sph.center).add(dir.multiplyScalar(dist));
  v.camera.near = Math.max(dist / 1000, 0.01);
  v.camera.far = dist * 1000;
  v.camera.updateProjectionMatrix();
  v.controls.update();
  v.dirty = true;
}

function v3dClear(v) {
  while (v.group.children.length) {
    const c = v.group.children.pop();
    if (c.geometry) c.geometry.dispose();
    if (c.material) c.material.dispose();
  }
  v.mesh = null;
  v.loops = null;
  v.plan = null;
  v.dirty = true;
}

/* Apply a plan whose buffers hold this frame's coordinates. As long as the
   plan is the same object as last time (same contour, same edge mode) the
   buffers are updated IN PLACE: no reallocation, no edge rebuild, no normals
   to re-derive, so dragging a slider stays perfectly fluid. The camera is left
   alone unless frame is true.

   On a real rebuild the arrays are COPIED: a plan's buffers are scratch space
   shared by both viewers, and a mesh that referenced them would be overwritten
   by the other side's build. */
function v3dApply(side, plan, frame) {
  const v = v3dInit(side);
  if (!v) return;
  if (!plan) { v3dClear(v); v3dRequest(); return; }

  if (v.plan === plan && v.mesh) {
    const g = v.mesh.geometry;
    g.attributes.position.array.set(plan.pos);
    g.attributes.position.needsUpdate = true;
    g.attributes.normal.array.set(plan.nor);
    g.attributes.normal.needsUpdate = true;
    for (let i = 0; i < plan.loopBuf.length; i++) {
      v.loops[i].geometry.attributes.position.array.set(plan.loopBuf[i]);
      v.loops[i].geometry.attributes.position.needsUpdate = true;
    }
  } else {
    v3dClear(v);
    const C = v3dColors(side);
    const bg = new THREE.BufferGeometry();
    bg.setAttribute('position', new THREE.BufferAttribute(plan.pos.slice(), 3));
    bg.setAttribute('normal', new THREE.BufferAttribute(plan.nor.slice(), 3));
    bg.setIndex(new THREE.BufferAttribute(plan.idx.slice(), 1));
    // Matte and opaque. DoubleSide so a contour with inverted winding (the auto
    // mask sometimes is) still renders solid instead of looking see-through.
    v.mesh = new THREE.Mesh(bg, new THREE.MeshStandardMaterial({
      color: C.s, metalness: 0.0, roughness: 0.5, envMapIntensity: 0.35,
      flatShading: false, side: THREE.DoubleSide,
    }));
    v.mesh.frustumCulled = false;        // one object, always on screen: no bounding sphere per frame
    v.group.add(v.mesh);
    v.loops = [];
    for (const lp of plan.loopBuf) {
      const lg = new THREE.BufferGeometry();
      lg.setAttribute('position', new THREE.BufferAttribute(lp.slice(), 3));
      const line = new THREE.LineLoop(lg, new THREE.LineBasicMaterial({ color: C.e }));
      line.frustumCulled = false;
      v.group.add(line);
      v.loops.push(line);
    }
    v.plan = plan;
  }
  if (frame || !v.framed) { v3dFit(side); v.framed = true; }
  v.dirty = true;
  v3dRequest();
}

/* One frame for every viewer that has something new to show, then nothing
   until the next request. Requests coalesce into one animation frame. */
function v3dRequest() { if (!_v3dRAF) _v3dRAF = requestAnimationFrame(v3dFrame); }
function v3dFrame() {
  if (_v3dRAF) { cancelAnimationFrame(_v3dRAF); _v3dRAF = 0; }
  for (const side in V3D) {
    const v = V3D[side];
    if (!v || !v.dirty || v.canvas.style.display === 'none') continue;
    v.dirty = false;
    v.renderer.render(v.scene, v.camera);
  }
}
/* Leaving step 6: drop a pending frame; there is no loop to stop. */
function v3dStop() { if (_v3dRAF) { cancelAnimationFrame(_v3dRAF); _v3dRAF = 0; } }

/* ── the panel ────────────────────────────────────────────────────────────── */
/* The three parameters as the sliders hold them (mm). The edge can never be
   deeper than the patch is thick: the value the solid is built with is
   capped here, the sliders themselves are never touched. */
function patchParams() {
  const t = parseInt(document.getElementById('slT').value, 10) / 10;
  const e = parseInt(document.getElementById('slE').value, 10) / 10;
  const off = parseInt(document.getElementById('slO').value, 10) / 10;
  return { t: t, e: Math.min(e, t), off: off, mode: document.getElementById('selM').value };
}

/* Build both patches. frame = true re-centres the cameras. Returns false when
   a viewer had no size yet (its box was not laid out). */
function patch3DBuild(frame) {
  const P = patchParams();
  const s = pxPerMm || 1;
  let sized = true;

  ['gt', 'auto'].forEach(function (side) {
    const cv = document.getElementById(side === 'gt' ? 'cv3g' : 'cv3a');
    const wait = document.getElementById(side === 'gt' ? 'w3g' : 'w3a');
    const hint = document.getElementById(side === 'gt' ? 'h3g' : 'h3a');
    const cpx = metCnt[side];

    if (typeof THREE === 'undefined') {
      wait.style.display = 'flex';
      wait.textContent = '3D unavailable: three.js missing from imtop/vendor/';
      cv.style.display = 'none'; hint.style.display = 'none';
      patchVol[side] = null;
      return;
    }
    if (!cpx || cpx.length < 3) {
      cv.style.display = 'none'; hint.style.display = 'none';
      wait.style.display = 'flex';
      wait.textContent = side === 'gt' ? 'Draw a manual contour first' : 'Run auto segmentation first';
      patchVol[side] = null;
      return;
    }
    wait.style.display = 'none';
    cv.style.display = 'block';
    hint.style.display = 'block';
    v3dInit(side);
    if (!v3dResize(side)) sized = false;

    let pc = _pc[side];
    if (!pc || pc.ref !== cpx || pc.s !== s) { pc = prepContour(cpx, s); pc.ref = cpx; pc.s = s; _pc[side] = pc; }

    const plan = buildPatchGeo(pc, P.t, P.e, P.mode, P.off);
    const V = meshVolume(plan.pos, plan.idx);
    v3dApply(side, plan, frame);

    // Area and perimeter come from the metrics, volume from the mesh. Units are
    // mm only when the image was calibrated; otherwise pixels, honestly.
    const u = pxPerMm ? 'mm' : 'px';
    const A = metData[side === 'gt' ? 'area_gt_mm2' : 'area_auto_mm2'];
    const Pm = metData[side === 'gt' ? 'perimeter_gt_mm' : 'perimeter_auto_mm'];
    patchVol[side] = V;                        // the report asks for it later
    const pre = side === 'gt' ? 'g' : 'a';
    document.getElementById(pre + 'A').textContent = (A != null) ? A.toFixed(1) + ' ' + u + '²' : '-';
    document.getElementById(pre + 'P').textContent = (Pm != null) ? Pm.toFixed(1) + ' ' + u : '-';
    document.getElementById(pre + 'V').textContent = (V != null) ? V.toFixed(1) + ' ' + u + '³' : '-';
  });

  // Draw now, in this very frame: a slider input reaches the screen one
  // frame later than it would through a second requestAnimationFrame.
  v3dFrame();
  return sized;
}

/* What step 6 is showing, for the report: the four parameters and, per side,
   the numbers that patch produced. null when no solid was ever built, and the
   report then simply has no 3D section. */
function patch3dParams() {
  if (patchVol.gt == null && patchVol.auto == null) return null;
  const P = patchParams();
  const p = { thickness: P.t, edge: P.e, offset: P.off, mode: P.mode };
  ['gt', 'auto'].forEach(function (side) {
    if (patchVol[side] == null) return;
    p[side] = {
      area: metData[side === 'gt' ? 'area_gt_mm2' : 'area_auto_mm2'],
      perimeter: metData[side === 'gt' ? 'perimeter_gt_mm' : 'perimeter_auto_mm'],
      volume: patchVol[side],
    };
  });
  return p;
}

/* Throttle live slider updates to one rebuild per animation frame. */
let _p3dRAF = 0;
function patch3DUpdate() {
  if (_p3dRAF) return;
  _p3dRAF = requestAnimationFrame(function () { _p3dRAF = 0; patch3DBuild(false); });
}

/* Entering step 6: the stage is already laid out (go() switched it on before
   calling this), so one build frames both viewers. If a box still had no
   size, one more build once the layout has settled. */
function patch3DEnter() {
  for (const k in V3D) V3D[k].framed = false;
  if (!patch3DBuild(true)) {
    setTimeout(function () { if (curP === 5) patch3DBuild(true); }, 60);
  }
}

/* The window was resized while on step 6: new drawing buffers, one frame. */
function patch3DResize() {
  v3dResize('gt'); v3dResize('auto');
  v3dRequest();
}

/* Live slider handler: the labels, then a rebuild (throttled). Each slider is
   its own: moving one never moves or clamps another. */
function onSlInput() {
  const P = patchParams();
  document.getElementById('svT').textContent = P.t.toFixed(1) + ' mm';
  document.getElementById('svE').textContent = P.e.toFixed(1) + ' mm';
  document.getElementById('svO').textContent = P.off.toFixed(1) + ' mm';
  patch3DUpdate();
}

/* ── STL export ───────────────────────────────────────────────────────────── */
/* Exports exactly the solid on screen (mm units), reusing the Three.js
   geometry, no rebuild, so what you exported is what you saw. Manual first. */
function exportSTL() {
  if (typeof THREE === 'undefined') { toast('⚠ 3D not available'); return; }
  const v = (V3D.gt && V3D.gt.mesh) ? V3D.gt : ((V3D.auto && V3D.auto.mesh) ? V3D.auto : null);
  if (!v) { toast('⚠ No patch built yet'); return; }
  const buf = _geoToSTL(v.mesh.geometry);
  beJson('saveSTL', JSON.stringify({ data: _ab2b64(buf) }), function (r) {
    if (!r) { toast('⚠ Export failed', 3800); return; }
    toast((r.ok ? '✓ ' : '⚠ ') + r.msg, r.ok ? 3200 : 3800);
    if (r.ok) { stlSaved = true; paintTabs(); }
  });
}

function _geoToSTL(geo) {
  const pos = geo.attributes.position.array, idx = geo.index ? geo.index.array : null;
  const nTri = idx ? idx.length / 3 : pos.length / 9;
  const buf = new ArrayBuffer(84 + nTri * 50), dv = new DataView(buf);
  dv.setUint32(80, nTri, true);
  let off = 84;
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
  const ab = new THREE.Vector3(), ac = new THREE.Vector3(), n = new THREE.Vector3();
  for (let t = 0; t < nTri; t++) {
    const i0 = (idx ? idx[t * 3] : t * 3) * 3;
    const i1 = (idx ? idx[t * 3 + 1] : t * 3 + 1) * 3;
    const i2 = (idx ? idx[t * 3 + 2] : t * 3 + 2) * 3;
    a.set(pos[i0], pos[i0 + 1], pos[i0 + 2]);
    b.set(pos[i1], pos[i1 + 1], pos[i1 + 2]);
    c.set(pos[i2], pos[i2 + 1], pos[i2 + 2]);
    ab.subVectors(b, a); ac.subVectors(c, a);
    n.crossVectors(ab, ac).normalize();
    dv.setFloat32(off, n.x, true); dv.setFloat32(off + 4, n.y, true); dv.setFloat32(off + 8, n.z, true);
    dv.setFloat32(off + 12, a.x, true); dv.setFloat32(off + 16, a.y, true); dv.setFloat32(off + 20, a.z, true);
    dv.setFloat32(off + 24, b.x, true); dv.setFloat32(off + 28, b.y, true); dv.setFloat32(off + 32, b.z, true);
    dv.setFloat32(off + 36, c.x, true); dv.setFloat32(off + 40, c.y, true); dv.setFloat32(off + 44, c.z, true);
    dv.setUint16(off + 48, 0, true);
    off += 50;
  }
  return buf;
}

function _ab2b64(buf) {
  const bytes = new Uint8Array(buf);
  let bin = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  return btoa(bin);
}
