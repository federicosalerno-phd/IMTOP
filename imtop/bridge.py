"""The object the web UI talks to over ``QWebChannel``.

Contract with the front-end: the **signal and slot names are the API**. The
legacy single-file UI and the modular one both bind to these names, so adding a
slot is fine, renaming one is a breaking change.

Threading model, one rule, no locks:

* every slot runs on the GUI thread (QWebChannel delivers calls there);
* heavy work (SAM download/load/encode, segmentation, auto-prompt) is queued on
  a **single** daemon worker thread, so jobs never overlap and "is SAM already
  loading?" races cannot exist;
* the worker never touches Qt or the :class:`Session`. It hands a closure to
  :meth:`_post`, which runs it on the GUI thread; only there are signals emitted
  and the session mutated. Results carry the ``image_id`` they were computed
  for and are dropped if the image changed meanwhile.
"""
from __future__ import annotations

import base64
import json
import queue
import threading
import traceback
from pathlib import Path

from .qt import QObject, pyqtSignal, pyqtSlot, QFileDialog, QUEUED, Qt
from . import printing
from .config import APP_VERSION, APP_CREDIT, REPORTS_DIR, AUTOPROMPT_CTRL_PTS, ensure_dirs
from .core import imaging, report, results
from .core.autoprompt import otsu_object_contour, AutoPromptError
from .core.geometry import to_xy_dicts, contour_to_mask
from .core.imaging import IMAGE_EXTENSIONS
from .core.segmentation import (Segmenters, SegmentationContext, DEFAULT_METHOD,
                                no_progress)
from .core.session import Session


def _j(obj) -> str:
    return json.dumps(obj)


class _GuiInvoker(QObject):
    """Runs callables on the thread it was created in (the GUI thread).

    Kept as a separate object, not registered with the web channel, so the
    channel never sees a signal carrying a Python callable.
    """
    call = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.call.connect(self._run, QUEUED)

    @pyqtSlot(object)
    def _run(self, fn):
        try:
            fn()
        except Exception:
            traceback.print_exc()


class _Worker:
    """One daemon thread draining a FIFO of jobs. Daemon, so closing the window
    during a model download exits the process instead of hanging on a join."""

    def __init__(self):
        self._q: queue.Queue = queue.Queue()
        self._t = threading.Thread(target=self._loop, name="imtop-worker", daemon=True)
        self._t.start()

    def submit(self, fn) -> None:
        self._q.put(fn)

    def _loop(self) -> None:
        while True:
            fn = self._q.get()
            if fn is None:
                return
            try:
                fn()
            except Exception:
                traceback.print_exc()

    def stop(self) -> None:
        self._q.put(None)


class Backend(QObject):
    # ── signals: part of the UI contract ─────────────────────────────────
    imageReady = pyqtSignal(str)          # data URL of the loaded image
    modelReady = pyqtSignal(bool)         # SAM loaded + image encoded (or failed)
    statusUpdate = pyqtSignal(str)
    error = pyqtSignal(str)
    scaleSet = pyqtSignal(float)          # px/mm
    autoSegDone = pyqtSignal(str)         # data URL of the overlay
    samProgress = pyqtSignal(int, str)    # percent, stage
    windowMaximized = pyqtSignal(bool)    # the window's own title bar is drawn by the UI
    reportDone = pyqtSignal(bool, str)    # a PDF export finished (ok, message)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.session = Session()
        self.segmenters = Segmenters()
        self._gui = _GuiInvoker()
        self._worker = _Worker()
        self._busy = False                 # a segmentation/auto-prompt job is in flight
        self._sam_notify_queued = False    # a loading job that must report modelReady is queued
        self._prewarm_inflight = False
        self._last_dir = ""
        self.startup_image: str | None = None

    # ── plumbing ─────────────────────────────────────────────────────────
    # Set by MainWindow. The window has no native frame: the title bar is part
    # of the page, so the UI needs a way to move, resize and close it. Moving
    # and resizing go through Qt's *system* calls, which means Windows keeps
    # doing the snapping, the edge magnetism and the shadow itself.
    window = None

    def _post(self, fn) -> None:
        """Run ``fn`` on the GUI thread (from any thread)."""
        self._gui.call.emit(fn)

    def _progress_cb(self, image_id: int):
        def cb(pct: int, stage: str) -> None:
            self._post(lambda: self._emit_progress(image_id, pct, stage))
        return cb

    def _emit_progress(self, image_id: int, pct: int, stage: str) -> None:
        if image_id == self.session.image_id:
            self.samProgress.emit(int(pct), str(stage))

    def shutdown(self) -> None:
        self._worker.stop()

    # ── app info ─────────────────────────────────────────────────────────
    @pyqtSlot(result=str)
    def getAppInfo(self) -> str:
        sam = self.segmenters.sam
        return _j({
            "version": APP_VERSION,
            "credit": APP_CREDIT,
            "sam_available": sam.available,
            "sam_checkpoint_present": sam.checkpoint_present() if sam.available else False,
            "sam_device": sam.device if sam.available else None,
            "methods": self.segmenters.describe(),
            "reports_dir": str(REPORTS_DIR),
        })

    @pyqtSlot(result=str)
    def getStartupImage(self) -> str:
        """Path passed on the command line, consumed once."""
        p, self.startup_image = self.startup_image, None
        return p or ""

    # ── image ────────────────────────────────────────────────────────────
    @pyqtSlot(str)
    def loadImage(self, path: str) -> None:
        p = imaging.local_path(path)
        try:
            arr = imaging.load_image_file(p)
        except Exception as e:
            self.error.emit(f"Image error: {e}")
            return
        self._set_image(arr, Path(p).name, Path(p))

    @pyqtSlot(str)
    def loadImageData(self, payload_json: str) -> None:
        """Drag & drop: the file arrives as a data URL, so there is no path."""
        try:
            p = json.loads(payload_json)
            arr = imaging.load_image_data_url(p.get("data", ""))
        except Exception as e:
            self.error.emit(f"Image error: {e}")
            return
        self._set_image(arr, p.get("name") or "dropped_image", None)

    def _set_image(self, arr, name: str, path: Path | None) -> None:
        self.session.load_image(arr, name, path)
        self._busy = False   # any job still running belongs to the old image_id and is dropped
        self.imageReady.emit(imaging.to_data_url(arr, "JPEG", 90))

    @pyqtSlot(result=str)
    def browseFile(self) -> str:
        filt = "Images (" + " ".join("*" + e for e in IMAGE_EXTENSIONS) + ")"
        path, _ = QFileDialog.getOpenFileName(None, "Open wound image", self._last_dir, filt)
        if path:
            self._last_dir = str(Path(path).parent)
        return path or ""

    @pyqtSlot(result=str)
    def getImageSize(self) -> str:
        """Size, plus the name to display: the UI should not have to remember
        what it asked to load (and it never learns the name of an image opened
        from the command line)."""
        s = self.session
        name = s.image_path.name if s.image_path else s.image_name
        return _j({"w": s.w, "h": s.h, "name": name})

    # ── calibration ─────────────────────────────────────────────────────
    @pyqtSlot(str)
    def setCalPoints(self, pts_json: str) -> None:
        try:
            self.session.set_cal_points(json.loads(pts_json))
        except Exception as e:
            self.error.emit(f"Calibration: {e}")

    @pyqtSlot(float, result=str)
    def setScale(self, mm: float) -> str:
        ok, msg = self.session.set_scale(mm)
        if not ok:
            return _j({"ok": False, "msg": msg})
        self.scaleSet.emit(self.session.px_per_mm)
        return _j({"ok": True, "px_per_mm": round(self.session.px_per_mm, 3)})

    @pyqtSlot(result=str)
    def skipScale(self) -> str:
        self.session.skip_scale()
        return _j({"ok": True})

    # ── manual contour, undo / redo ──────────────────────────────────────
    @pyqtSlot(str, result=str)
    def pushUndo(self, payload_json: str) -> str:
        """Snapshot *before* a change: ``{"pts": [...], "closed": bool}`` or a bare list."""
        try:
            self.session.push_undo(json.loads(payload_json))
            return _j({"ok": True})
        except Exception as e:
            return _j({"ok": False, "msg": str(e)})

    @pyqtSlot(result=str)
    def undo(self) -> str:
        snap = self.session.undo()
        return _j({"ok": False}) if snap is None else _j({"ok": True, **snap.to_json()})

    @pyqtSlot(result=str)
    def redo(self) -> str:
        snap = self.session.redo()
        return _j({"ok": False}) if snap is None else _j({"ok": True, **snap.to_json()})

    @pyqtSlot()
    def resetSegmentation(self) -> None:
        self.session.reset_segmentation()

    @pyqtSlot(str)
    def setControlPoints(self, pts_json: str) -> None:
        try:
            self.session.set_control_points(json.loads(pts_json))
        except Exception as e:
            self.error.emit(f"Contour: {e}")

    @pyqtSlot(bool)
    def setContourClosed(self, closed: bool) -> None:
        self.session.contour_closed = bool(closed)

    @pyqtSlot(result=str)
    def getControlPoints(self) -> str:
        """The hand-traced points as the backend has them: ``{"pts": [...], "closed": bool}``.
        The UI pulls them after every segmentation, because the auto-prompt
        builds the trace here and the page would otherwise never see it."""
        return _j(self.session.current_snapshot().to_json())

    @pyqtSlot(result=str)
    def computeSpline(self) -> str:
        crv = self.session.spline()
        return _j([] if crv is None else to_xy_dicts(crv))

    # ── SAM lifecycle ────────────────────────────────────────────────────
    @pyqtSlot()
    def loadSAMAsync(self) -> None:
        """Load + encode with progress, then ``modelReady``."""
        self._start_sam_prepare(notify=True)

    @pyqtSlot()
    def loadSAM(self) -> None:
        """Legacy name; same as :meth:`loadSAMAsync` (nothing blocks the GUI any more)."""
        self._start_sam_prepare(notify=True)

    @pyqtSlot()
    def prewarmSAM(self) -> None:
        """Silent background encode, so the first run is near-instant."""
        self._start_sam_prepare(notify=False)

    def _start_sam_prepare(self, notify: bool) -> None:
        s, sam = self.session, self.segmenters.sam
        if not s.has_image:
            if notify:
                self.error.emit("Load an image first")
                self.modelReady.emit(False)
            return
        if not sam.available:
            if notify:
                self.error.emit(sam.unavailable_reason())
                self.modelReady.emit(False)
            return
        if sam.ready_for(s.image_id):
            if notify:
                self.samProgress.emit(100, "Ready")
                self.modelReady.emit(True)
            return
        if notify:
            if self._sam_notify_queued:
                return
            self._sam_notify_queued = True
            if self._prewarm_inflight:
                # The bar would sit at 0 until the silent job ends: let it creep.
                self.samProgress.emit(50, "Encoding image…")
        else:
            if self._prewarm_inflight:
                return
            self._prewarm_inflight = True
        img, iid = s.image, s.image_id
        progress = self._progress_cb(iid) if notify else no_progress

        def job():
            try:
                sam.prepare(img, iid, progress)
                self._post(lambda: self._sam_prepared(iid, notify, None))
            except Exception as e:
                traceback.print_exc()
                self._post(lambda: self._sam_prepared(iid, notify, f"SAM: {e}"))

        self._worker.submit(job)

    def _sam_prepared(self, image_id: int, notify: bool, err: str | None) -> None:
        if notify:
            self._sam_notify_queued = False
        else:
            self._prewarm_inflight = False
            return                      # silent; a real error surfaces on the actual run
        if image_id != self.session.image_id:
            return                      # image changed meanwhile; the UI already reset
        if err:
            self.error.emit(err)
            self.modelReady.emit(False)
        else:
            self.modelReady.emit(True)

    # ── segmentation ─────────────────────────────────────────────────────
    @pyqtSlot(str)
    def runAutoSegmentation(self, method: str = DEFAULT_METHOD) -> None:
        """Seed the chosen back-end with the hand-traced contour, off the GUI thread.
        Ends with ``autoSegDone`` (overlay) or ``error``."""
        s = self.session
        if self._busy:
            self.statusUpdate.emit("Busy, please wait…")
            return
        try:
            backend = self.segmenters.get(method or DEFAULT_METHOD)
        except KeyError as e:
            self.error.emit(str(e))
            return
        if not backend.available():
            self.error.emit(backend.unavailable_reason())
            return
        try:
            s.build_gt()
        except ValueError as e:
            self.error.emit(str(e))
            return
        ctx = SegmentationContext(s.image, s.image_id, s.gt_mask, s.gt_contour)
        self._busy = True

        def job():
            try:
                mask, contour = self.segmenters.run(backend.id, ctx)
                self._post(lambda: self._seg_done(ctx, backend.id, mask, contour, None, False))
            except Exception as e:
                traceback.print_exc()
                self._post(lambda: self._seg_done(ctx, backend.id, None, None,
                                                  f"{backend.label}: {e}", False))

        self._worker.submit(job)

    @pyqtSlot()
    def runAutoPrompt(self) -> None:
        """SAM without a manual trace (high-contrast images): Otsu finds the object,
        its contour seeds the ordinary SAM pipeline. One progress bar for everything."""
        s, sam = self.session, self.segmenters.sam
        if not s.has_image:
            self.error.emit("Load an image first")
            return
        if self._busy:
            self.statusUpdate.emit("Busy, please wait…")
            return
        if not sam.available:
            self.error.emit(sam.unavailable_reason())
            return
        self._busy = True
        img, iid = s.image, s.image_id
        progress = self._progress_cb(iid)

        def job():
            try:
                sam.prepare(img, iid, progress)
                progress(96, "Segmenting…")
                crv = otsu_object_contour(img)
                gt_mask = contour_to_mask(crv, img.shape[0], img.shape[1])
                ctx = SegmentationContext(img, iid, gt_mask, crv)
                mask, contour = self.segmenters.run(DEFAULT_METHOD, ctx)
                self._post(lambda: self._seg_done(ctx, DEFAULT_METHOD, mask, contour, None, True))
            except AutoPromptError as e:
                self._post(lambda: self._seg_failed(iid, str(e)))
            except Exception as e:
                traceback.print_exc()
                self._post(lambda: self._seg_failed(iid, f"Auto-prompt: {e}"))

        self._worker.submit(job)

    def _seg_failed(self, image_id: int, msg: str) -> None:
        self._busy = False
        if image_id == self.session.image_id:
            self.error.emit(msg)

    def _seg_done(self, ctx: SegmentationContext, method: str, mask, contour,
                  err: str | None, autoprompt: bool) -> None:
        self._busy = False
        s = self.session
        if ctx.image_id != s.image_id:
            return
        if err:
            s.apply_auto(None, None, method)
            self.error.emit(err)
            return
        if mask is None or contour is None:
            s.apply_auto(None, None, method)
            self.error.emit(f"{method} produced an empty mask, adjust the contour and retry")
            return
        s.gt_contour, s.gt_mask = ctx.gt_contour, ctx.gt_mask
        s.apply_auto(mask, contour, method)
        if autoprompt:
            # The detected object becomes the traced contour (as {x,y} dicts, so a
            # later undo cannot hand the UI raw pairs) and no manual GT remains.
            # The contour is 300 dense samples; the trace gets a few dozen of them,
            # evenly spaced, so the operator can still grab and move a point. The
            # spline through them follows the smooth contour closely.
            crv = ctx.gt_contour
            step = max(1, len(crv) // AUTOPROMPT_CTRL_PTS)
            s.ctrl_pts, s.contour_closed = to_xy_dicts(crv[::step]), True
            s.gt_contour = s.gt_mask = None
        overlay = imaging.render_overlay(s.image, s.gt_contour, s.auto_contour)
        self.autoSegDone.emit(imaging.to_data_url(overlay, "JPEG", 90))

    # ── results ──────────────────────────────────────────────────────────
    @pyqtSlot(result=str)
    def getMetrics(self) -> str:
        return _j(self.session.public_metrics())

    @pyqtSlot(result=str)
    def getMetricsInfo(self) -> str:
        """Whether the ``*_mm`` keys are really mm or pixels, and the unit labels."""
        return _j(self.session.metrics_info())

    @pyqtSlot(result=str)
    def getContours(self) -> str:
        return _j(self.session.contours_json())

    @pyqtSlot(result=str)
    def saveResults(self) -> str:
        s = self.session
        if not s.metrics or not s.has_image:
            return "No results to save"
        try:
            return f"Saved: {results.save_metrics_json(s)}"
        except Exception as e:
            return f"Save error: {e}"

    @pyqtSlot(str, result=str)
    def saveReport(self, payload_json: str = "") -> str:
        """Write the report as a PDF, printed by the app's own engine, offline.

        The dialog proposes ``<image>_report.pdf``. A name typed with an
        ``.html`` extension saves the page itself instead (the same document,
        for anyone who wants to open it in a browser).

        ``payload_json`` may carry ``{"patch3d": {...}}``: the step-6 sliders and
        the numbers they produced, which only the page knows. It is optional,
        and so is the section it fills.

        HTML is written here and answered straight away. PDF cannot be, because
        printing is asynchronous, so the answer says ``pending`` and the real
        outcome arrives on the ``reportDone`` signal.
        """
        s = self.session
        if not s.has_image:
            return _j({"ok": False, "msg": "Load an image first"})
        if not s.metrics:
            return _j({"ok": False, "msg": "Run the segmentation first"})
        try:
            payload = json.loads(payload_json) if payload_json else {}
        except Exception:
            payload = {}
        patch3d = payload.get("patch3d") or None

        default = report.default_report_path(s)
        path, selected = QFileDialog.getSaveFileName(
            None, "Save report", str(default), "PDF report (*.pdf)")
        if not path:
            return _j({"ok": False, "msg": "Cancelled"})
        if Path(path).suffix.lower() not in (".html", ".htm", ".pdf"):
            path += ".html" if "html" in (selected or "").lower() else ".pdf"

        try:
            if path.lower().endswith(".pdf"):
                html = report.build_report_html(s, patch3d)
                out = Path(path)
                printing.html_to_pdf(html, out, lambda ok, msg: self._report_printed(out, ok, msg))
                return _j({"ok": True, "pending": True, "msg": "Building the PDF…"})
            written = report.save_report_html(s, path, patch3d)
            return _j({"ok": True, "msg": f"Saved report: {written}"})
        except Exception as e:
            traceback.print_exc()
            return _j({"ok": False, "msg": f"Report error: {e}"})

    def _report_printed(self, out: Path, ok: bool, msg: str) -> None:
        self.reportDone.emit(bool(ok), f"Saved report: {out}" if ok else f"PDF error: {msg}")

    @pyqtSlot(str, result=str)
    def savePerf(self, text: str) -> str:
        try:
            ensure_dirs()
            p = REPORTS_DIR / "perf_report.txt"
            p.write_text(text, encoding="utf-8")
            return str(p)
        except Exception as e:
            return f"error: {e}"

    @pyqtSlot(result=str)
    def saveGTMask(self) -> str:
        """Hand-traced mask as a binary PNG, image-sized. Defaults to a ``masks/``
        folder so a PNG source image is never overwritten by its own mask."""
        s = self.session
        if not s.has_image:
            return _j({"ok": False, "msg": "Load an image first"})
        mask = s.gt_mask_or_build()
        if mask is None:
            return _j({"ok": False, "msg": "No GT traced, draw the wound boundary first"})
        default = results.output_dir(s) / "masks" / f"{s.image_name or 'mask'}.png"
        path, _ = QFileDialog.getSaveFileName(None, "Save GT mask", str(default), "PNG image (*.png)")
        if not path:
            return _j({"ok": False, "msg": "Cancelled"})
        if not path.lower().endswith(".png"):
            path += ".png"
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(imaging.mask_to_png_bytes(mask))
            return _j({"ok": True, "msg": f"Saved GT mask: {path}"})
        except Exception as e:
            return _j({"ok": False, "msg": f"Save error: {e}"})

    @pyqtSlot(str, result=str)
    def saveSTL(self, payload_json: str) -> str:
        """Write the binary STL the viewer built (sent base64) to a chosen file."""
        s = self.session
        try:
            data = json.loads(payload_json).get("data", "")
            if not data:
                return _j({"ok": False, "msg": "No patch built yet"})
            raw = base64.b64decode(data)
            default = results.output_path(s, "_patch.stl") if s.has_image else REPORTS_DIR / "patch.stl"
            path, _ = QFileDialog.getSaveFileName(None, "Export STL", str(default), "STL mesh (*.stl)")
            if not path:
                return _j({"ok": False, "msg": "Cancelled"})
            if not path.lower().endswith(".stl"):
                path += ".stl"
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(raw)
            return _j({"ok": True, "msg": f"Saved STL: {path}"})
        except Exception as e:
            return _j({"ok": False, "msg": f"STL error: {e}"})

    # ── window chrome (the title bar is drawn by the page) ───────────────
    @pyqtSlot()
    def winMinimize(self) -> None:
        if self.window is not None:
            self.window.showMinimized()

    @pyqtSlot()
    def winMaximizeToggle(self) -> None:
        if self.window is None:
            return
        if self.window.isMaximized():
            self.window.showNormal()
        else:
            self.window.showMaximized()

    @pyqtSlot()
    def winClose(self) -> None:
        if self.window is not None:
            self.window.close()

    @pyqtSlot()
    def winDrag(self) -> None:
        """Hand the drag to the window manager: snapping keeps working."""
        if self.window is None:
            return
        handle = self.window.windowHandle()
        if handle is not None:
            handle.startSystemMove()

    @pyqtSlot(str)
    def winResize(self, edge: str) -> None:
        """``edge`` is one of n, s, e, w, ne, nw, se, sw."""
        if self.window is None:
            return
        handle = self.window.windowHandle()
        if handle is None:
            return
        edges = Qt.Edge(0)
        if "n" in edge:
            edges |= Qt.Edge.TopEdge
        if "s" in edge:
            edges |= Qt.Edge.BottomEdge
        if "w" in edge:
            edges |= Qt.Edge.LeftEdge
        if "e" in edge:
            edges |= Qt.Edge.RightEdge
        handle.startSystemResize(edges)

    @pyqtSlot(result=bool)
    def winIsMaximized(self) -> bool:
        return bool(self.window is not None and self.window.isMaximized())
