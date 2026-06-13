"""IMTOP — wound annotator.

A desktop tool to calibrate, segment and measure wounds from a single
photograph. A Qt window hosts an embedded web UI (``ui.html``) that talks to
this Python backend over QWebChannel and walks the user through:

* **Calibration** — set the image scale (px/mm) from a two-point reference.
* **Segmentation** — trace the wound by hand and/or run an automatic back-end
  (Segment Anything ViT-B, GrabCut or Watershed).
* **Metrics** — area, perimeter, Dice/IoU, Hausdorff and coverage metrics,
  reported in mm / mm² when a scale is set and in pixels otherwise.
* **3D patch** — preview and export a printable patch mesh built in-browser.

Ctrl+wheel zooms only the loaded image inside the canvas (centred on the
cursor, never below fit); whole-page zoom is disabled so the layout never
disproportions, and the window stays freely resizable/maximizable.

Run with ``python wound_app.py`` (or ``launch.bat``).
"""
import os, sys, json, base64, io, subprocess

# ── Self-heal interpreter ─────────────────────────────────────────────────────
# If launched with a Python that lacks the SAM deps (e.g. VS Code's Run button
# using the wrong interpreter), relaunch automatically with the one that has them
# so SAM always works, however the app is started.
# Interpreter to relaunch with when the SAM deps are missing; override via WOUND_PYTHON.
_PY_WITH_SAM = os.environ.get("WOUND_PYTHON",
                              os.path.join(os.environ.get("LOCALAPPDATA", ""),
                                           "Programs", "Python", "Python311", "python.exe"))
if os.environ.get("WOUND_REEXEC") != "1":
    try:
        import torch as _t            # noqa: F401  (deps present in this interpreter?)
        import segment_anything as _s  # noqa: F401
    except Exception:
        if os.path.exists(_PY_WITH_SAM) and os.path.normcase(_PY_WITH_SAM) != os.path.normcase(sys.executable):
            print("[Wound] SAM deps missing in", sys.executable, "- relaunching with", _PY_WITH_SAM)
            sys.exit(subprocess.call([_PY_WITH_SAM, os.path.abspath(__file__)] + sys.argv[1:],
                                     env=dict(os.environ, WOUND_REEXEC="1")))

# Rendering backend for the embedded web view (affects zoom smoothness).
#   True  -> GPU acceleration: much smoother zoom. Recommended.
#   False -> software rendering: use ONLY if the window comes up black/empty.
USE_GPU = True
if not USE_GPU:
    os.environ.setdefault("QT_OPENGL", "software")
os.environ.setdefault("PYVISTA_OFF_SCREEN", "false")
os.environ.setdefault("VTK_SILENCE_GET_VOID_POINTER_WARNINGS", "1")

from pathlib import Path
import numpy as np
import cv2
from PIL import Image
from scipy.interpolate import splprep, splev
from scipy.spatial.distance import cdist

# Qt binding: prefer PyQt6 (modern Chromium ~118+, vastly better compositor — the
# real fix for the QtWebEngine 5.15 frame-pacing jank), fall back to PyQt5 so the
# app still runs if PyQt6 isn't installed yet.
#   To get the fix:  pip install PyQt6 PyQt6-WebEngine
try:
    from PyQt6.QtWidgets import (QApplication, QMainWindow, QVBoxLayout,
                                 QWidget, QFileDialog)
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEngineSettings            # moved module in Qt6
    from PyQt6.QtWebChannel import QWebChannel
    from PyQt6.QtCore import QObject, pyqtSlot, pyqtSignal, QUrl, Qt
    from PyQt6.QtGui import QColor
    USE_QT6 = True
except Exception:
    from PyQt5.QtWidgets import (QApplication, QMainWindow, QVBoxLayout,
                                 QWidget, QFileDialog)
    from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEngineSettings
    from PyQt5.QtWebChannel import QWebChannel
    from PyQt5.QtCore import QObject, pyqtSlot, pyqtSignal, QUrl, Qt
    from PyQt5.QtGui import QColor
    USE_QT6 = False
print(f"[Wound] Qt binding: {'PyQt6 (Qt6)' if USE_QT6 else 'PyQt5 (Qt5)'}")

try:
    import torch
    from segment_anything import sam_model_registry, SamPredictor
    HAS_SAM = True
except Exception:
    HAS_SAM = False

# Use the GPU for SAM if a CUDA torch is installed (10-30x faster set_image/predict);
# falls back to CPU automatically. With the current torch+cpu build this is "cpu".
SAM_DEVICE = "cuda" if (HAS_SAM and torch.cuda.is_available()) else "cpu"
if HAS_SAM: print(f"[Wound] SAM device: {SAM_DEVICE}")

SAM_CHECKPOINT = os.environ.get("SAM_CHECKPOINT", "sam_vit_b.pth")   # override via the SAM_CHECKPOINT env var
CONTOUR_N = 300
SPLINE_K  = 3
N_ARC     = 20

# ── geometry ────────────────────────────────────────────────────────────────
def interpolate_spline(pts: np.ndarray, n: int = CONTOUR_N):
    """Fit a smooth closed periodic B-spline through control points.

    Parameters
    ----------
    pts : np.ndarray
        ``(k, 2)`` control points in image **pixels** (x, y); at least 3.
    n : int, optional
        Number of points sampled along the spline (default ``CONTOUR_N``).

    Returns
    -------
    np.ndarray or None
        ``(n, 2)`` sampled contour in **pixels**, or ``None`` if fewer than 3
        points were given or the spline fit failed.
    """
    if len(pts) < 3: return None
    arr = np.vstack([pts, pts[0]])
    try:
        tck, _ = splprep([arr[:,0], arr[:,1]], s=0, per=True,
                          k=min(SPLINE_K, len(arr)-1))
        u = np.linspace(0, 1, n, endpoint=False)
        x, y = splev(u, tck)
        return np.column_stack([x, y])
    except: return None

def contour_to_mask(c: np.ndarray, h: int, w: int) -> np.ndarray:
    """Rasterise a polygon contour into a filled boolean mask.

    Parameters
    ----------
    c : np.ndarray
        ``(m, 2)`` contour points in **pixels** (x, y).
    h, w : int
        Output mask height and width in **pixels**.

    Returns
    -------
    np.ndarray
        ``(h, w)`` boolean mask, ``True`` inside the polygon.
    """
    mask = np.zeros((h, w), np.uint8)
    cv2.fillPoly(mask, [c.astype(np.int32).reshape(-1,1,2)], 1)
    return mask.astype(bool)

def np_to_b64(arr: np.ndarray, fmt: str = 'PNG') -> str:
    """Encode an image array as a base64 string (no data-URL prefix).

    Parameters
    ----------
    arr : np.ndarray
        Image array (``uint8``-compatible), e.g. ``(h, w, 3)`` RGB.
    fmt : str, optional
        Pillow image format, e.g. ``'PNG'`` or ``'JPEG'`` (default ``'PNG'``).

    Returns
    -------
    str
        Base64-encoded image bytes.
    """
    img = Image.fromarray(arr.astype(np.uint8))
    buf = io.BytesIO(); img.save(buf, format=fmt, quality=90)
    return base64.b64encode(buf.getvalue()).decode()

def axis_aligned_bbox(c: np.ndarray):
    """Axis-aligned bounding box ``(x_min, y_min, x_max, y_max)`` of a contour, in **pixels**."""
    return c[:,0].min(), c[:,1].min(), c[:,0].max(), c[:,1].max()

# ── backend ──────────────────────────────────────────────────────────────────
class Backend(QObject):
    imageReady      = pyqtSignal(str)
    modelReady      = pyqtSignal(bool)
    statusUpdate    = pyqtSignal(str)
    error           = pyqtSignal(str)
    scaleSet        = pyqtSignal(float)
    autoSegDone     = pyqtSignal(str)
    samProgress     = pyqtSignal(int, str)      # (percent 0-100, stage label)

    def __init__(self):
        super().__init__()
        self.img_path=None; self.img_name=None
        self.img_np=None; self.img_h=self.img_w=0
        self.cal_pts=[]; self.cal_mm=None; self.px_per_mm=None
        self.ctrl_pts=[]; self.contour_closed=False
        self.undo_stack=[]; self.redo_stack=[]
        self.gt_contour=None; self.gt_mask=None
        self.auto_contour=None; self.auto_mask=None
        self.predictor=None; self.metrics={}
        self._sam_encoded=False; self._sam_loading=False   # lazy SAM load state
        self._suppress_gt=False    # auto-prompt: draw only the SAM result, no green GT overlay

    def _reset_pipeline(self):
        """Clear calibration / segmentation / metrics state for a new image."""
        self.cal_pts=[]; self.cal_mm=None; self.px_per_mm=None
        self.ctrl_pts=[]; self.contour_closed=False
        self.undo_stack=[]; self.redo_stack=[]
        self.gt_contour=None; self.gt_mask=None
        self.auto_contour=None; self.auto_mask=None; self.metrics={}
        self._sam_encoded=False   # new image must be re-encoded by SAM on next run

    @pyqtSlot(str)
    def loadImage(self, path: str):
        """Load an image from a local path (or ``file:///`` URL) and reset state.

        Decodes the image to an RGB array, clears the calibration/segmentation
        pipeline, and emits ``imageReady`` with a JPEG data URL. Failures are
        reported through the ``error`` signal.
        """
        try:
            p=path.replace("file:///","").replace("%20"," ")
            self.img_path=p; self.img_name=Path(p).stem
            pil=Image.open(p).convert("RGB"); self.img_np=np.array(pil)
            self.img_h,self.img_w=self.img_np.shape[:2]
            self._reset_pipeline()
            buf=io.BytesIO(); pil.save(buf,format="JPEG",quality=90)
            self.imageReady.emit("data:image/jpeg;base64,"+base64.b64encode(buf.getvalue()).decode())
        except Exception as e: self.error.emit(f"Image error: {e}")

    @pyqtSlot(result=str)
    def browseFile(self) -> str:
        """Open a file dialog and return the chosen image path (``""`` if cancelled)."""
        path,_=QFileDialog.getOpenFileName(None,"Open wound image","",
            "Images (*.jpg *.jpeg *.png *.bmp *.tiff *.tif)")
        return path or ""

    @pyqtSlot(str)
    def loadImageData(self, payload_json):
        """Load an image dropped onto the drop-zone (sent as a data URL)."""
        try:
            p=json.loads(payload_json)
            name=p.get("name","dropped_image"); data_url=p.get("data","")
            b64=data_url.split(",",1)[1] if "," in data_url else data_url
            raw=base64.b64decode(b64)
            pil=Image.open(io.BytesIO(raw)).convert("RGB")
            self.img_path=name              # filename only -> results saved in cwd
            self.img_name=Path(name).stem
            self.img_np=np.array(pil); self.img_h,self.img_w=self.img_np.shape[:2]
            self._reset_pipeline()
            buf=io.BytesIO(); pil.save(buf,format="JPEG",quality=90)
            self.imageReady.emit("data:image/jpeg;base64,"+base64.b64encode(buf.getvalue()).decode())
        except Exception as e: self.error.emit(f"Image error: {e}")

    @pyqtSlot(result=str)
    def getImageSize(self) -> str:
        """Return the current image size in **pixels** as JSON ``{"w", "h"}``."""
        return json.dumps({"w":self.img_w,"h":self.img_h})

    def _sam_encode(self):
        """Run the SAM image encoder (set_image) on the current image.
        On CUDA, do it in FP16 (autocast): the ViT-B encoder's peak VRAM drops
        from ~2.9 GB to ~1.8 GB, so it FITS the 4 GB Quadro T2000 alongside the
        Qt WebEngine GPU compositor. In FP32 it overflowed by ~0.2 GB -> Windows
        spilled GPU memory to system RAM (WDDM) -> the whole GPU (compositor incl.)
        stalled -> the window froze at "Encoding image 72%" ("Non risponde").
        Masks are unchanged (measured IoU vs FP32 = 0.9998). Features are cast back
        to fp32 so predict() runs full-precision, unchanged. CPU path is untouched."""
        if SAM_DEVICE == "cuda":
            with torch.autocast("cuda", dtype=torch.float16):
                self.predictor.set_image(self.img_np)
            try: self.predictor.features = self.predictor.features.float()
            except Exception: pass
            # NB: do NOT empty_cache() here — releasing the cache forces the next
            # image's encode to re-allocate ~1.8 GB from the driver (slow on Windows
            # WDDM), which made back-to-back auto-segmentations slower. The FP16 peak
            # (1.8 GB) fits the 4 GB card kept-resident, so we keep the cache warm.
        else:
            self.predictor.set_image(self.img_np)

    @pyqtSlot()
    def loadSAM(self):
        """Load the SAM checkpoint and encode the current image (blocking).

        Emits ``modelReady(True)`` on success, otherwise reports through
        ``error`` and emits ``modelReady(False)``. See :meth:`loadSAMAsync` for
        the non-blocking variant used by the UI.
        """
        if not HAS_SAM:
            self.error.emit("SAM not available"); self.modelReady.emit(False); return
        try:
            self.statusUpdate.emit("Loading SAM\u2026")
            m=sam_model_registry["vit_b"](checkpoint=SAM_CHECKPOINT); m.to(SAM_DEVICE).eval()
            self.predictor=SamPredictor(m); self._sam_encode()
            self.modelReady.emit(True)
        except Exception as e:
            self.error.emit(f"SAM: {e}"); self.modelReady.emit(False)

    def _build_sam_with_progress(self):
        """Build the SAM model, reading the checkpoint file in chunks so we can
        report a REAL byte-level percentage (8->50%) instead of one opaque stall."""
        size=os.path.getsize(SAM_CHECKPOINT)
        buf=io.BytesIO(); read=0; chunk=8*1024*1024
        with open(SAM_CHECKPOINT,"rb") as f:
            while True:
                b=f.read(chunk)
                if not b: break
                buf.write(b); read+=len(b)
                self.samProgress.emit(8+int(42*read/max(size,1)), "Loading SAM weights\u2026")
        buf.seek(0)
        self.samProgress.emit(52, "Preparing weights\u2026")
        state=torch.load(buf, map_location="cpu")
        m=sam_model_registry["vit_b"]()      # build architecture, no checkpoint
        m.load_state_dict(state); m.to(SAM_DEVICE).eval()
        return m

    def _load_sam_worker(self):
        try:
            if self.predictor is None:
                m=self._build_sam_with_progress()
                self.samProgress.emit(60, "Building predictor\u2026")
                self.predictor=SamPredictor(m)
            self.samProgress.emit(66, "Encoding image\u2026")   # slow encoder pass (creeps client-side)
            self._sam_encode()
            self._sam_encoded=True
            self.samProgress.emit(100, "Ready")
            self.modelReady.emit(True)
        except Exception as e:
            self.error.emit(f"SAM: {e}"); self.modelReady.emit(False)
        finally:
            self._sam_loading=False

    @pyqtSlot()
    def loadSAMAsync(self):
        """Lazy + non-blocking SAM load. Loads weights once, re-encodes per image.
        Reports progress via samProgress; finishes with modelReady."""
        if not HAS_SAM:
            self.error.emit("SAM not loaded \u2014 launch with launch.bat (Python 3.11)."); self.modelReady.emit(False); return
        if self.predictor is not None and self._sam_encoded:
            self.modelReady.emit(True); return          # already ready for this image
        if self._sam_loading: return                    # a load (incl. prewarm) is already running
        self._sam_loading=True
        import threading
        threading.Thread(target=self._load_sam_worker, daemon=True).start()

    @pyqtSlot(str)
    def setCalPoints(self, pts_json: str):
        """Store the two calibration points (image **pixels**) from a JSON list."""
        pts=json.loads(pts_json); self.cal_pts=[[p["x"],p["y"]] for p in pts]

    @pyqtSlot(float, result=str)
    def setScale(self, mm: float) -> str:
        """Set the image scale from the two calibration points.

        Parameters
        ----------
        mm : float
            Real-world distance between the two calibration points, in **mm**.

        Returns
        -------
        str
            JSON ``{"ok": True, "px_per_mm": ...}`` on success (scale in
            **pixels per mm**), or ``{"ok": False, "msg": ...}`` if fewer than
            two points are set or they are too close.
        """
        if len(self.cal_pts)<2: return json.dumps({"ok":False,"msg":"Need 2 points"})
        p0,p1=self.cal_pts; d=float(np.hypot(p1[0]-p0[0],p1[1]-p0[1]))
        if d<1: return json.dumps({"ok":False,"msg":"Points too close"})
        self.cal_mm=mm; self.px_per_mm=d/mm
        self.scaleSet.emit(self.px_per_mm)
        return json.dumps({"ok":True,"px_per_mm":round(self.px_per_mm,3)})

    @pyqtSlot(result=str)
    def skipScale(self) -> str:
        """Continue without a scale; metrics stay in **pixels**. Returns ``{"ok": True}``."""
        self.px_per_mm=None; return json.dumps({"ok":True})

    @pyqtSlot(str, result=str)
    def pushUndo(self, pts_json: str) -> str:
        """Push the given control-point list (JSON) onto the undo stack."""
        self.undo_stack.append(json.loads(pts_json)); self.redo_stack.clear()
        return json.dumps({"ok":True})

    @pyqtSlot(result=str)
    def undo(self) -> str:
        """Restore the previous control points from the undo stack (JSON result)."""
        if not self.undo_stack: return json.dumps({"ok":False})
        self.redo_stack.append(list(self.ctrl_pts)); self.ctrl_pts=self.undo_stack.pop()
        return json.dumps({"ok":True,"pts":self.ctrl_pts})

    @pyqtSlot(result=str)
    def redo(self) -> str:
        """Re-apply the most recently undone control points (JSON result)."""
        if not self.redo_stack: return json.dumps({"ok":False})
        self.undo_stack.append(list(self.ctrl_pts)); self.ctrl_pts=self.redo_stack.pop()
        return json.dumps({"ok":True,"pts":self.ctrl_pts})

    @pyqtSlot()
    def resetSegmentation(self):
        """Clear the traced contour, undo/redo history and ground-truth mask."""
        self.ctrl_pts=[]; self.contour_closed=False
        self.undo_stack=[]; self.redo_stack=[]
        self.gt_contour=None; self.gt_mask=None

    @pyqtSlot(str)
    def setControlPoints(self, pts_json: str):
        """Replace the wound-boundary control points (image **pixels**) from JSON."""
        self.ctrl_pts=json.loads(pts_json)

    @pyqtSlot(result=str)
    def computeSpline(self) -> str:
        """Return the smooth spline through the control points as JSON ``[{x, y}]``
        in **pixels** (``[]`` if fewer than 3 control points or the fit fails)."""
        if len(self.ctrl_pts)<3: return json.dumps([])
        pts=np.array([[p["x"],p["y"]] if isinstance(p,dict) else p for p in self.ctrl_pts])
        c=interpolate_spline(pts)
        if c is None: return json.dumps([])
        return json.dumps([{"x":float(x),"y":float(y)} for x,y in c])

    # ── automatic segmentation methods ───────────────────────────────────────
    def _mask_to_contour(self, mask):
        """Largest external contour of a boolean mask -> smooth closed spline."""
        if mask is None: return None
        ctrs,_=cv2.findContours(mask.astype(np.uint8),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_NONE)
        if not ctrs: return None
        raw=max(ctrs,key=cv2.contourArea).squeeze()
        if raw.ndim<2 or len(raw)<5: return None
        try:
            tck,_=splprep([raw[:,0].astype(float),raw[:,1].astype(float)],
                          s=max(len(raw)*3.,200.),per=True,k=5)
            u=np.linspace(0,1,CONTOUR_N,endpoint=False); x,y=splev(u,tck)
            return np.column_stack([x,y])
        except: return None

    def _clean_mask(self, mask):
        """Open+close to drop speckle / fill holes, then keep the largest blob."""
        if mask is None: return None
        m=mask.astype(np.uint8)
        k=max(3,int(0.012*max(self.img_h,self.img_w))); k+= (k+1)%2   # force odd
        ker=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(k,k))
        m=cv2.morphologyEx(m,cv2.MORPH_OPEN,ker)
        m=cv2.morphologyEx(m,cv2.MORPH_CLOSE,ker)
        n,lbl=cv2.connectedComponents(m)
        if n>2:
            best=max(range(1,n),key=lambda i:(lbl==i).sum())
            m=(lbl==best).astype(np.uint8)
        return m.astype(bool)

    def _seg_sam(self):
        """SAM ViT-B prompted with the manual contour's bounding box + centroid
        point — far more reliable/accurate than a bare point prompt."""
        if self.predictor is None:        # lazy-load if the model wasn't ready yet
            if not HAS_SAM: return None
            try:
                mm=sam_model_registry["vit_b"](checkpoint=SAM_CHECKPOINT); mm.to(SAM_DEVICE).eval()
                self.predictor=SamPredictor(mm); self._sam_encode()
                self.modelReady.emit(True)
            except Exception:
                return None
        gc=self.gt_contour
        box=np.array([gc[:,0].min(),gc[:,1].min(),gc[:,0].max(),gc[:,1].max()],dtype=np.float32)
        # Positive point prompt: the point DEEPEST inside the filled mask (distance-transform
        # maximum), NOT the contour centroid. For a CONCAVE shape (e.g. the half-moon) the
        # centroid falls in the concave bay = on the BACKGROUND, so labelling it foreground
        # made SAM bleed across the bay (measured: centroid is OUTSIDE the mask on all 3 MEZ
        # shots; the DT-max point is always strictly inside). For convex shapes the DT-max is
        # ~the centroid so results are unchanged (measured IoU old-vs-new: CER 1.000,
        # ELL 0.9996, FAG 0.9987, TRI 0.9976). Falls back to the centroid if no mask is set.
        gm=self.gt_mask
        if gm is not None and gm.any():
            dt=cv2.distanceTransform(gm.astype(np.uint8),cv2.DIST_L2,5)
            cy,cx=np.unravel_index(int(dt.argmax()),dt.shape)
        else:
            cx,cy=int(gc[:,0].mean()),int(gc[:,1].mean())
        masks,scores,_=self.predictor.predict(
            point_coords=np.array([[int(cx),int(cy)]]),point_labels=np.array([1]),
            box=box,multimask_output=False)
        return masks[0].astype(bool)

    def _seg_grabcut(self):
        """GrabCut seeded by the manual region (probable FG inside, BG far outside)."""
        img=cv2.cvtColor(self.img_np,cv2.COLOR_RGB2BGR)
        gt=self.gt_mask.astype(np.uint8)
        mask=np.full(gt.shape,cv2.GC_PR_BGD,np.uint8)
        mask[gt>0]=cv2.GC_PR_FGD
        k=max(7,int(0.02*max(self.img_h,self.img_w)))
        sure_fg=cv2.erode(gt,np.ones((k,k),np.uint8),iterations=2)
        sure_bg=cv2.dilate(gt,np.ones((k,k),np.uint8),iterations=2)   # narrower band
        mask[sure_fg>0]=cv2.GC_FGD
        mask[sure_bg==0]=cv2.GC_BGD
        bgd=np.zeros((1,65),np.float64); fgd=np.zeros((1,65),np.float64)
        try: cv2.grabCut(img,mask,None,bgd,fgd,5,cv2.GC_INIT_WITH_MASK)
        except Exception: return None
        out=((mask==cv2.GC_FGD)|(mask==cv2.GC_PR_FGD))
        return out & (sure_bg>0)   # don't let it leak past the allowed band

    def _seg_watershed(self):
        """Marker-based watershed: eroded manual region = FG, far outside = BG."""
        img=cv2.cvtColor(self.img_np,cv2.COLOR_RGB2BGR)
        gt=self.gt_mask.astype(np.uint8)
        k=max(7,int(0.02*max(self.img_h,self.img_w)))
        sure_fg=cv2.erode(gt,np.ones((k,k),np.uint8),iterations=2)
        sure_bg=cv2.dilate(gt,np.ones((k,k),np.uint8),iterations=3)
        markers=np.zeros(gt.shape,np.int32)
        markers[sure_bg==0]=1          # background
        markers[sure_fg>0]=2           # foreground (rest = 0 = unknown)
        cv2.watershed(img,markers)
        return (markers==2)

    def _segment(self, method):
        if method=="GrabCut":   return self._seg_grabcut()
        if method=="Watershed": return self._seg_watershed()
        return self._seg_sam()         # default: SAM (ViT-B)

    @pyqtSlot(str)
    def runAutoSegmentation(self, method: str = "SAM (ViT-B)"):
        """Run an automatic segmentation back-end seeded by the manual contour.

        Parameters
        ----------
        method : str, optional
            One of ``"SAM (ViT-B)"`` (default), ``"GrabCut"`` or ``"Watershed"``.

        Notes
        -----
        Builds the ground-truth mask from the current control points, runs the
        chosen back-end, cleans the result, computes and saves metrics, then
        emits ``autoSegDone`` with an overlay JPEG (manual contour in green,
        automatic in red). Metric distances/areas are in **mm/mm²** when a
        scale is set, otherwise in **pixels**.
        """
        try:
            pts=np.array([[p["x"],p["y"]] if isinstance(p,dict) else p for p in self.ctrl_pts])
            crv=interpolate_spline(pts)
            if crv is None: self.error.emit("Invalid contour"); return
            self.gt_contour=crv; self.gt_mask=contour_to_mask(crv,self.img_h,self.img_w)
            mask=self._segment(method or "SAM (ViT-B)")
            mask=self._clean_mask(mask)
            if mask is not None and mask.sum()>0:
                self.auto_mask=mask
                self.auto_contour=self._mask_to_contour(self.auto_mask)
            else:
                self.auto_mask=None; self.auto_contour=None
                if (method or "").startswith("SAM") and (not HAS_SAM or self.predictor is None):
                    self.error.emit("SAM not loaded — launch with launch.bat (Python 3.11). Try GrabCut/Watershed meanwhile.")
            self.metrics=self._compute_metrics(); self._save_results()
            ov=self.img_np.copy()
            if self.gt_contour is not None and not self._suppress_gt:   # auto-prompt hides the green GT
                cv2.polylines(ov,[np.vstack([self.gt_contour,self.gt_contour[0]]).astype(np.int32).reshape(-1,1,2)],True,(82,214,138),2)
            if self.auto_contour is not None:
                cv2.polylines(ov,[np.vstack([self.auto_contour,self.auto_contour[0]]).astype(np.int32).reshape(-1,1,2)],True,(240,112,112),2)
            self.autoSegDone.emit("data:image/jpeg;base64,"+np_to_b64(ov,'JPEG'))
        except Exception as e: self.error.emit(f"Auto seg: {e}")

    @pyqtSlot()
    def runAutoPrompt(self):
        """OPTIONAL auto-prompt for SAM on high-contrast images (bright object on a
        dark background): build the box+point prompt automatically (Otsu on the
        largest central object) and reuse the EXISTING SAM pipeline — no manual GT.
        Self-contained: ensures the model is loaded + the image encoded (driving the
        SINGLE progress bar), then segments — all OFF the GUI thread so the window
        never freezes and only ONE loader (the % bar) is ever shown. Manual untouched."""
        if self.img_np is None:
            self.error.emit("Load an image first"); return
        import threading
        threading.Thread(target=self._auto_prompt_worker, daemon=True).start()

    @pyqtSlot()
    def prewarmSAM(self):
        """Eager, SILENT pre-encode. Called by JS when the user leaves calibration
        (Confirm/Skip) — i.e. AFTER they've finished zooming — so the ~3 s SAM encode
        runs in the background while they move to the segmentation step, and the
        actual 'Auto-segment'/run is then near-instant. NOT triggered during the
        calibration zoom (that's what caused the GPU jank). No loader/modelReady is
        emitted: the run path just finds _sam_encoded=True (or waits for it)."""
        self._prewarm_sam()

    def _prewarm_sam(self):
        if not HAS_SAM or self._sam_encoded or self._sam_loading: return
        self._sam_loading=True
        import threading
        threading.Thread(target=self._prewarm_worker, daemon=True).start()

    def _prewarm_worker(self):
        # SILENT: emit no samProgress/modelReady (no loader is shown during prewarm, and
        # we don't want to leave a creep timer running). The run path shows the bar itself.
        try:
            if self.predictor is None:
                m=self._build_sam_silent()
                self.predictor=SamPredictor(m)
            if not self._sam_encoded:
                self._sam_encode()
                self._sam_encoded=True
        except Exception:
            pass            # best-effort; any real error surfaces on the actual run
        finally:
            self._sam_loading=False

    def _build_sam_silent(self):
        """Build SAM from the checkpoint without emitting progress (for prewarm)."""
        m=sam_model_registry["vit_b"](checkpoint=SAM_CHECKPOINT); m.to(SAM_DEVICE).eval()
        return m

    def _auto_prompt_worker(self):
        import time as _t
        if self._sam_loading:
            self.samProgress.emit(50, "Encoding image…")   # prewarm in flight -> let the bar creep
        waited=0
        while self._sam_loading and waited<60:   # let any in-flight prewarm encode finish first
            _t.sleep(0.05); waited+=0.05
        if self._sam_loading: return             # still busy after 60s -> bail
        self._sam_loading=True
        try:
            if not HAS_SAM:
                self.error.emit("SAM not available"); return
            # 1) ensure model + image encoding, reporting on the single % bar
            if self.predictor is None:
                m=self._build_sam_with_progress()                   # emits 8 -> 52 %
                self.samProgress.emit(56, "Building predictor…")
                self.predictor=SamPredictor(m)
            if not self._sam_encoded:
                self.samProgress.emit(62, "Encoding image…")
                self._sam_encode()
                self._sam_encoded=True
            # 2) Otsu auto-prompt -> bbox + centroid
            self.samProgress.emit(90, "Segmenting…")
            gray=cv2.cvtColor(self.img_np,cv2.COLOR_RGB2GRAY)
            _,th=cv2.threshold(gray,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
            fg=(th>0).astype(np.uint8)
            # polarity: object is central; background touches the borders -> if the
            # current foreground dominates the border it IS the background: invert.
            border=np.concatenate([fg[0,:],fg[-1,:],fg[:,0],fg[:,-1]])
            if border.mean()>0.5: fg=1-fg
            n,lbl=cv2.connectedComponents(fg)
            if n<2: self.error.emit("Auto-prompt: no object found"); return
            # Pick the object component. Two robustness points, both measured on the expB
            # phantom photos (validation diagnostics):
            #  (a) bincount counts ALL component sizes in ONE pass (O(pixels)). The old
            #      max(range(1,n), key=lambda i:(lbl==i).sum()) rescanned the whole image
            #      once PER component -> O(n_comp*pixels); a noisy JPEG background yields
            #      thousands of speckles (measured 6307 on CER_p1) -> ~90 s, stuck at the
            #      "Segmenting… 92%" bar. bincount picks the same component in ~50 ms.
            #  (b) EXCLUDE components that touch the image border: the phantom is always a
            #      central isolated object, while an over-exposed fabric corner (measured on
            #      CER_p2/p3) forms a large bright blob touching the border that otherwise
            #      wins on size and makes SAM segment the fabric instead of the disc. Verified
            #      to fix CER_p2/p3 and leave the other 16 photos' selection unchanged.
            #      Fall back to the overall largest if EVERY component touches the border.
            counts=np.bincount(lbl.ravel()); counts[0]=0            # 0 = background label
            border_labels=np.unique(np.concatenate([lbl[0,:],lbl[-1,:],lbl[:,0],lbl[:,-1]]))
            interior=counts.copy(); interior[border_labels]=0
            best=int(interior.argmax()) if interior.any() else int(counts.argmax())
            comp=(lbl==best)
            if int(comp.sum())<max(64,int(5e-4*self.img_h*self.img_w)):
                self.error.emit("Auto-prompt: no valid object found"); return
            crv=self._mask_to_contour(comp)                         # bbox+centroid come from this
            if crv is None: self.error.emit("Auto-prompt: object too small"); return
            # 3) feed the detected object as the contour so runAutoSegmentation builds
            # the SAME prompt and runs the SAME SAM pipeline (no duplication).
            self.ctrl_pts=[[float(x),float(y)] for x,y in crv]
            self._suppress_gt=True                                  # result = SAM only, no green GT contour
            try: self.runAutoSegmentation("SAM (ViT-B)")            # reuse the existing pipeline -> autoSegDone
            finally: self._suppress_gt=False
            self.gt_contour=None; self.gt_mask=None                 # no manual GT for downstream overlays
        except Exception as e:
            self.error.emit(f"Auto-prompt: {e}")
        finally:
            self._sam_loading=False

    def _compute_metrics(self):
        gm=self.gt_mask; sm=self.auto_mask; gc=self.gt_contour; sc=self.auto_contour
        if gm is None or sm is None: return {}
        m={}; s=self.px_per_mm or 1.
        inter=np.logical_and(gm,sm).sum(); union=np.logical_or(gm,sm).sum()
        m["dice"]=round(2*inter/(gm.sum()+sm.sum()+1e-9),4)
        m["iou"]=round(inter/(union+1e-9),4)
        m["area_gt_mm2"]=round(gm.sum()/s**2,2)
        m["area_auto_mm2"]=round(sm.sum()/s**2,2)
        m["area_rel_err"]=round(abs(m["area_gt_mm2"]-m["area_auto_mm2"])/(m["area_gt_mm2"]+1e-9),4)
        # asymmetric coverage metrics (additive). W = gm (GT), P = sm (auto).
        under_px=int(np.logical_and(gm,~sm).sum())      # wound left uncovered: W AND NOT P
        over_px =int(np.logical_and(sm,~gm).sum())      # excess on healthy skin: P AND NOT W
        area_gt_px=int(gm.sum())
        m["cri_lambda3"]=round((3*under_px+over_px)/(area_gt_px+1e-9),4)   # dimensionless, lambda=3
        if self.px_per_mm:                              # mm^2 only when a scale is available
            m["under_cov_mm2"]=round(under_px/self.px_per_mm**2,2)
            m["over_cov_mm2"] =round(over_px /self.px_per_mm**2,2)
        else:
            m["under_cov_mm2"]=None; m["over_cov_mm2"]=None
        if gc is not None and sc is not None:
            D=cdist(gc,sc)
            m["hausdorff_mm"]=round(max(D.min(1).max(),D.min(0).max())/s,3)
            m["avg_surf_dist_mm"]=round(.5*(D.min(1).mean()+D.min(0).mean())/s,3)
            dg=np.diff(gc,axis=0,append=gc[:1]); Pg=np.linalg.norm(dg,axis=1).sum()
            da=np.diff(sc,axis=0,append=sc[:1]); Pa=np.linalg.norm(da,axis=1).sum()
            m["perimeter_gt_mm"]=round(Pg/s,2); m["perimeter_auto_mm"]=round(Pa/s,2)
            m["compactness_gt"]=round(4*np.pi*gm.sum()/(Pg**2+1e-9),4)
            m["compactness_auto"]=round(4*np.pi*sm.sum()/(Pa**2+1e-9),4)
            gx0,gy0,gx1,gy1=axis_aligned_bbox(gc); ax0,ay0,ax1,ay1=axis_aligned_bbox(sc)
            m["bbox_gt_major_mm"]=round(max(gx1-gx0,gy1-gy0)/s,2)
            m["bbox_gt_minor_mm"]=round(min(gx1-gx0,gy1-gy0)/s,2)
            m["bbox_auto_major_mm"]=round(max(ax1-ax0,ay1-ay0)/s,2)
            m["bbox_auto_minor_mm"]=round(min(ax1-ax0,ay1-ay0)/s,2)
            m["aspect_ratio_gt"]=round(min(gx1-gx0,gy1-gy0)/(max(gx1-gx0,gy1-gy0)+1e-9),4)
            m["aspect_ratio_auto"]=round(min(ax1-ax0,ay1-ay0)/(max(ax1-ax0,ay1-ay0)+1e-9),4)
            m["bbox_auto_x_mm"]=round((ax1-ax0)/s,2)   # A (X): SAM bbox width along X (reuses ax* above)
            m["bbox_auto_y_mm"]=round((ay1-ay0)/s,2)   # B (Y): SAM bbox height along Y
            m["_bbox_gt"]=(gx0,gy0,gx1,gy1); m["_bbox_auto"]=(ax0,ay0,ax1,ay1)
        return m

    @pyqtSlot(result=str)
    def getMetrics(self) -> str:
        """Return the public metrics as JSON (private ``_``-prefixed keys omitted)."""
        return json.dumps({k:v for k,v in self.metrics.items() if not k.startswith("_")})

    @pyqtSlot(result=str)
    def getContours(self) -> str:
        """Return the GT/auto contours and scale as JSON for the 3D viewer.

        The result holds ``"s"`` (scale in **pixels per mm**, or ``null``) and,
        when available, ``"gt"`` / ``"auto"`` contours as ``[{x, y}]`` lists in
        **pixels**.
        """
        r={"s": self.px_per_mm}   # px/mm so the JS 3D viewer can build the mesh in mm
        if self.gt_contour is not None:
            r["gt"]=[{"x":float(x),"y":float(y)} for x,y in self.gt_contour]
        if self.auto_contour is not None:
            r["auto"]=[{"x":float(x),"y":float(y)} for x,y in self.auto_contour]
        return json.dumps(r)

    @pyqtSlot(result=str)
    def saveResults(self) -> str:
        """Write the public metrics to ``<image>_metrics.json`` next to the image.

        Returns a human-readable status string. Nothing is written when there
        are no metrics or no source-image path.
        """
        if not self.metrics or not self.img_path: return "No results to save"
        try:
            base=Path(self.img_path).parent/self.img_name
            clean={k:v for k,v in self.metrics.items() if not k.startswith("_")}
            json.dump(clean,open(f"{base}_metrics.json","w"),indent=2)
            return f"Saved: {base}_metrics.json"
        except Exception as e: return f"Save error: {e}"

    def _save_results(self):
        try: self.saveResults()
        except: pass

    @pyqtSlot(str, result=str)
    def savePerf(self, text):
        """Dump the front-end performance report to a file next to the script so
        it can be inspected without copy-pasting numbers out of the UI."""
        try:
            p = Path(__file__).parent / "perf_report.txt"
            p.write_text(text, encoding="utf-8")
            return str(p)
        except Exception as e:
            return f"error: {e}"

    @pyqtSlot(result=str)
    def saveGTMask(self):
        """Save the hand-traced ground-truth mask as a binary PNG (0/255 uint8),
        same size as the loaded image. Additive helper — does not change how the
        GT is drawn. Uses the already-computed gt_mask, or rebuilds it from the
        current control points so it works right after tracing (before auto-seg)."""
        if self.img_np is None:
            return json.dumps({"ok": False, "msg": "Load an image first"})
        mask = self.gt_mask
        if mask is None and len(self.ctrl_pts) >= 3:
            pts = np.array([[p["x"], p["y"]] if isinstance(p, dict) else p for p in self.ctrl_pts])
            crv = interpolate_spline(pts)
            if crv is not None:
                mask = contour_to_mask(crv, self.img_h, self.img_w)
        if mask is None:
            return json.dumps({"ok": False, "msg": "No GT traced — draw the wound boundary first"})
        try:
            default_dir = Path(__file__).parent / "expA" / "masks"   # proposed default only; not created
            default_path = str(default_dir / ((self.img_name or "mask") + ".png"))
            path, _ = QFileDialog.getSaveFileName(None, "Save GT mask", default_path, "PNG image (*.png)")
            if not path:
                return json.dumps({"ok": False, "msg": "Cancelled"})
            if not path.lower().endswith(".png"):
                path += ".png"
            out = (mask.astype(np.uint8) * 255)          # binary 0/255, shape (img_h, img_w)
            Image.fromarray(out, mode="L").save(path)     # PIL handles unicode paths robustly
            return json.dumps({"ok": True, "msg": f"Saved GT mask: {path}"})
        except Exception as e:
            return json.dumps({"ok": False, "msg": f"Save error: {e}"})

    @pyqtSlot(str, result=str)
    def saveSTL(self, payload_json):
        """Write the binary-STL bytes (built in JS from the exact mesh currently
        shown in the 3D viewer) to a file chosen via QFileDialog. Additive helper —
        does not rebuild or change the mesh; it just saves what JS sent."""
        try:
            data = json.loads(payload_json).get("data", "")
            if not data:
                return json.dumps({"ok": False, "msg": "No patch built yet"})
            raw = base64.b64decode(data)
            default_path = str(Path(__file__).parent / ((self.img_name or "patch") + ".stl"))
            path, _ = QFileDialog.getSaveFileName(None, "Export STL", default_path, "STL mesh (*.stl)")
            if not path:
                return json.dumps({"ok": False, "msg": "Cancelled"})
            if not path.lower().endswith(".stl"):
                path += ".stl"
            with open(path, "wb") as f:
                f.write(raw)
            return json.dumps({"ok": True, "msg": f"Saved STL: {path}"})
        except Exception as e:
            return json.dumps({"ok": False, "msg": f"STL error: {e}"})

# ── HTML ─────────────────────────────────────────────────────────────────────
HTML = (Path(__file__).parent / "ui.html").read_text(encoding="utf-8")

# ── main window ───────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Wound Annotator")
        # Resizable / maximizable window (no fixed size). The HTML layout reflows
        # responsively, and page-zoom is disabled, so enlarging the window never
        # disproportions the UI.
        self.resize(1280, 800)
        self.setMinimumSize(1024, 640)
        central = QWidget(); self.setCentralWidget(central)
        lay = QVBoxLayout(central); lay.setContentsMargins(0,0,0,0)
        self.view = QWebEngineView()
        s = self.view.settings()
        WA = QWebEngineSettings.WebAttribute if USE_QT6 else QWebEngineSettings   # scoped enum in Qt6
        s.setAttribute(WA.JavascriptEnabled, True)
        s.setAttribute(WA.LocalContentCanAccessRemoteUrls, True)
        try: s.setAttribute(WA.LocalContentCanAccessFileUrls, True)   # let the page load libs/*.js
        except Exception: pass
        # Lock page zoom factor to 1.0 (image zoom is handled inside the canvas).
        self.view.setZoomFactor(1.0)
        lay.addWidget(self.view)
        self.backend = Backend()
        self.channel = QWebChannel()
        self.channel.registerObject("backend", self.backend)
        self.view.page().setWebChannel(self.channel)
        self.view.page().setBackgroundColor(QColor("#111111"))
        # Base-URL = the local libs/ folder, so the <script src="three.min.js"> tags
        # resolve to the bundled files and the page gets a file origin (needed to
        # load them). Everything else uses data:/qrc: URLs, unaffected.
        _libs = (Path(__file__).parent / "libs").as_posix()
        if not _libs.endswith("/"): _libs += "/"
        self.view.setHtml(HTML, QUrl.fromLocalFile(_libs))

if __name__ == "__main__":
    # GPU path: keep GPU *compositing* on (--ignore-gpu-blocklist) so pan/zoom of the
    # CSS-transformed image layer stays smooth, but EXPLICITLY DISABLE GPU tile
    # rasterization (--disable-gpu-rasterization). GPU raster is ON by default in modern
    # Chromium, so merely omitting --enable-gpu-rasterization did nothing; the Quadro
    # T2000 driver was still re-rastering the zoomed image into GPU tiles and rendering
    # the not-yet-ready tiles as BLACK squares ("scacchiera nera") during zoom. With GPU
    # raster off, the image is CPU-rastered ONCE into a single texture that the GPU
    # compositor just scales on zoom: no tiles, no black squares, and a much lighter GPU
    # load while zooming. If the window comes up black, set USE_GPU=False at the top.
    _gpu = ("--ignore-gpu-blocklist --disable-gpu-rasterization "
            if USE_GPU else "--disable-gpu --disable-features=VizDisplayCompositor ")
    # Kill the checkerboard placeholders Chromium shows while it asynchronously
    # decodes large images (our base-image / overlay data-URLs) -> decode them
    # synchronously instead. Removes the "small checkerboard" artefacts (esp. while
    # a heavy SAM load competes for CPU) everywhere in the app.
    _gpu += "--disable-checker-imaging "
    # With GPU raster off, tiles are CPU-rastered; during fast zoom a frame can be
    # composited before a tile finishes -> a brief B/W checkerboard placeholder flashes.
    # More raster threads = tiles finish faster = fewer/no such micro-flashes.
    _gpu += "--num-raster-threads=4 "
    # The old Qt5/Chromium-83 compositor starves its vsync source (~10fps, proven by
    # the F8 baseline) -> drive frames from a timer there. Qt6's modern compositor
    # doesn't need (or want) this, so only apply it on the Qt5 fallback path.
    if USE_GPU and not USE_QT6:
        _gpu += "--disable-gpu-vsync --disable-frame-rate-limit "
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = _gpu + "--force-device-scale-factor=1"
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "0"
    os.environ["QT_SCALE_FACTOR"] = "1"
    if USE_QT6:
        # Qt6: high-DPI is always on; pin scaling to 1:1 so the fixed HTML layout
        # isn't enlarged (combined with QT_SCALE_FACTOR=1 above).
        try: QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        except Exception: pass
    else:
        QApplication.setAttribute(Qt.AA_DisableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, False)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec() if USE_QT6 else app.exec_())