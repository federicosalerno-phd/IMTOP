"""The annotation session: one image and everything derived from it.

Plain Python, no Qt, no threads. The bridge owns the threading and applies
results here on the GUI thread; scripts can drive a ``Session`` directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .geometry import (as_xy_array, to_xy_dicts, interpolate_spline,
                       contour_to_mask)
from .metrics import compute_metrics, public_metrics, metrics_info

UNDO_DEPTH = 200


@dataclass
class Snapshot:
    """One undo step of the hand-traced contour: the points and whether it was closed."""
    pts: list[dict]
    closed: bool = False

    @classmethod
    def parse(cls, payload) -> "Snapshot":
        """Accept ``{"pts": [...], "closed": bool}`` or a bare point list."""
        if isinstance(payload, dict):
            pts, closed = payload.get("pts", []), bool(payload.get("closed", False))
        else:
            pts, closed = payload, False
        return cls(to_xy_dicts(as_xy_array(pts)), closed)

    def to_json(self) -> dict:
        return {"pts": self.pts, "closed": self.closed}


@dataclass
class Session:
    image: np.ndarray | None = None
    image_name: str = ""
    image_path: Path | None = None       # None for drag-dropped images (no file on disk)
    image_id: int = 0                    # bumps on every load; tags async results as stale

    cal_pts: list[list[float]] = field(default_factory=list)
    cal_mm: float | None = None
    px_per_mm: float | None = None

    ctrl_pts: list[dict] = field(default_factory=list)
    contour_closed: bool = False
    undo_stack: list[Snapshot] = field(default_factory=list)
    redo_stack: list[Snapshot] = field(default_factory=list)

    gt_contour: np.ndarray | None = None
    gt_mask: np.ndarray | None = None
    auto_contour: np.ndarray | None = None
    auto_mask: np.ndarray | None = None
    method: str = ""
    metrics: dict = field(default_factory=dict)

    # ── image ────────────────────────────────────────────────────────────
    @property
    def h(self) -> int:
        return 0 if self.image is None else int(self.image.shape[0])

    @property
    def w(self) -> int:
        return 0 if self.image is None else int(self.image.shape[1])

    @property
    def has_image(self) -> bool:
        return self.image is not None

    def load_image(self, arr: np.ndarray, name: str, path: Path | None) -> None:
        self.image = arr
        self.image_name = Path(name).stem if name else "image"
        self.image_path = path
        self.image_id += 1
        self.reset_pipeline()

    def reset_pipeline(self) -> None:
        """Forget calibration, contours, masks and metrics (new image)."""
        self.cal_pts, self.cal_mm, self.px_per_mm = [], None, None
        self.reset_segmentation()
        self.auto_contour = self.auto_mask = None
        self.method, self.metrics = "", {}

    # ── calibration ─────────────────────────────────────────────────────
    def set_cal_points(self, pts) -> None:
        self.cal_pts = as_xy_array(pts).tolist()

    def set_scale(self, mm: float) -> tuple[bool, str]:
        """Scale from the two calibration points. Returns ``(ok, message)``."""
        if len(self.cal_pts) < 2:
            return False, "Need 2 points"
        if not mm or mm <= 0:
            return False, "Length must be positive"
        p0, p1 = self.cal_pts[:2]
        d = float(np.hypot(p1[0] - p0[0], p1[1] - p0[1]))
        if d < 1:
            return False, "Points too close"
        self.cal_mm = float(mm)
        self.px_per_mm = d / float(mm)
        return True, f"{self.px_per_mm:.3f} px/mm"

    def skip_scale(self) -> None:
        self.px_per_mm = None
        self.cal_mm = None

    # ── manual contour + undo/redo ───────────────────────────────────────
    def current_snapshot(self) -> Snapshot:
        return Snapshot(list(self.ctrl_pts), self.contour_closed)

    def set_control_points(self, pts, closed: bool | None = None) -> None:
        self.ctrl_pts = to_xy_dicts(as_xy_array(pts))
        if closed is not None:
            self.contour_closed = bool(closed)

    def push_undo(self, payload) -> None:
        """Record the state *before* a change (the UI calls this first)."""
        self.undo_stack.append(Snapshot.parse(payload))
        del self.undo_stack[:-UNDO_DEPTH]
        self.redo_stack.clear()

    def undo(self) -> Snapshot | None:
        if not self.undo_stack:
            return None
        self.redo_stack.append(self.current_snapshot())
        snap = self.undo_stack.pop()
        self.ctrl_pts, self.contour_closed = list(snap.pts), snap.closed
        return snap

    def redo(self) -> Snapshot | None:
        if not self.redo_stack:
            return None
        self.undo_stack.append(self.current_snapshot())
        snap = self.redo_stack.pop()
        self.ctrl_pts, self.contour_closed = list(snap.pts), snap.closed
        return snap

    def reset_segmentation(self) -> None:
        self.ctrl_pts, self.contour_closed = [], False
        self.undo_stack, self.redo_stack = [], []
        self.gt_contour = self.gt_mask = None

    def spline(self) -> np.ndarray | None:
        return interpolate_spline(self.ctrl_pts) if len(self.ctrl_pts) >= 3 else None

    def build_gt(self) -> np.ndarray:
        """Rasterise the hand-traced contour into the GT mask. Raises if invalid."""
        if self.image is None:
            raise ValueError("Load an image first")
        crv = self.spline()
        if crv is None:
            raise ValueError("Invalid contour, place at least 3 points")
        self.gt_contour = crv
        self.gt_mask = contour_to_mask(crv, self.h, self.w)
        return crv

    def gt_mask_or_build(self) -> np.ndarray | None:
        """The GT mask, rebuilding it from the points if it was never rasterised."""
        if self.gt_mask is not None:
            return self.gt_mask
        crv = self.spline()
        return None if crv is None else contour_to_mask(crv, self.h, self.w)

    # ── automatic result ─────────────────────────────────────────────────
    def apply_auto(self, mask: np.ndarray | None, contour: np.ndarray | None,
                   method: str) -> None:
        self.auto_mask, self.auto_contour, self.method = mask, contour, method
        self.metrics = compute_metrics(self.gt_mask, mask, self.gt_contour,
                                       contour, self.px_per_mm) if mask is not None else {}

    def public_metrics(self) -> dict:
        return public_metrics(self.metrics)

    def metrics_info(self) -> dict:
        return metrics_info(self.px_per_mm)

    def contours_json(self) -> dict:
        """Contours + scale for the 3D viewer and the canvas overlays."""
        r: dict = {"s": self.px_per_mm}
        if self.gt_contour is not None:
            r["gt"] = to_xy_dicts(self.gt_contour)
        if self.auto_contour is not None:
            r["auto"] = to_xy_dicts(self.auto_contour)
        return r
