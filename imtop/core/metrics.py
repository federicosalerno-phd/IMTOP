"""Agreement and geometry metrics between the manual (GT) and automatic masks.

The key names are a **published schema**: ``results_all.csv`` and the Monte
Carlo scripts read them, so they never change. The ``*_mm`` / ``*_mm2`` suffix
is historical, when no scale is set those values are in pixels. Callers get
the truth from :func:`metrics_info` and must label the numbers accordingly.
"""
from __future__ import annotations

import numpy as np
from scipy.spatial.distance import cdist

from .geometry import axis_aligned_bbox, polygon_perimeter

# Canonical key order, shared by the UI, the JSON export and the batch CSV.
METRIC_KEYS = [
    "dice", "iou", "area_gt_mm2", "area_auto_mm2", "area_rel_err",
    "cri_lambda3", "under_cov_mm2", "over_cov_mm2", "hausdorff_mm",
    "avg_surf_dist_mm", "perimeter_gt_mm", "perimeter_auto_mm",
    "compactness_gt", "compactness_auto", "bbox_gt_major_mm",
    "bbox_gt_minor_mm", "bbox_auto_major_mm", "bbox_auto_minor_mm",
    "aspect_ratio_gt", "aspect_ratio_auto", "bbox_auto_x_mm", "bbox_auto_y_mm",
]

# Which keys carry a length or an area (and therefore change unit with the
# scale). Everything else is dimensionless.
LENGTH_KEYS = frozenset(k for k in METRIC_KEYS if k.endswith("_mm"))
AREA_KEYS = frozenset(k for k in METRIC_KEYS if k.endswith("_mm2"))

# Coverage cost index weight: under-coverage (wound left exposed) counts three
# times an equal area of over-coverage (excess on healthy skin).
CRI_LAMBDA = 3


def metrics_info(px_per_mm: float | None) -> dict:
    """How to read the numbers: calibrated or not, and the unit labels."""
    calibrated = bool(px_per_mm)
    return {
        "calibrated": calibrated,
        "px_per_mm": float(px_per_mm) if calibrated else None,
        "length_unit": "mm" if calibrated else "px",
        "area_unit": "mm²" if calibrated else "px²",
    }


def compute_metrics(gt_mask: np.ndarray | None,
                    auto_mask: np.ndarray | None,
                    gt_contour: np.ndarray | None,
                    auto_contour: np.ndarray | None,
                    px_per_mm: float | None) -> dict:
    """All metrics for one GT/auto pair. Empty dict when either mask is missing.

    Keys starting with ``_`` are internal helpers (pixel bounding boxes) and are
    stripped by :func:`public_metrics` before anything leaves the process.
    """
    gm, sm, gc, sc = gt_mask, auto_mask, gt_contour, auto_contour
    if gm is None or sm is None:
        return {}
    s = px_per_mm or 1.0
    m: dict = {}
    inter = np.logical_and(gm, sm).sum()
    union = np.logical_or(gm, sm).sum()
    m["dice"] = round(2 * inter / (gm.sum() + sm.sum() + 1e-9), 4)
    m["iou"] = round(inter / (union + 1e-9), 4)
    m["area_gt_mm2"] = round(gm.sum() / s ** 2, 2)
    m["area_auto_mm2"] = round(sm.sum() / s ** 2, 2)
    m["area_rel_err"] = round(abs(m["area_gt_mm2"] - m["area_auto_mm2"])
                              / (m["area_gt_mm2"] + 1e-9), 4)
    # Asymmetric coverage. W = GT (the wound), P = auto (the patch).
    under_px = int(np.logical_and(gm, ~sm).sum())   # wound left uncovered
    over_px = int(np.logical_and(sm, ~gm).sum())    # excess on healthy skin
    area_gt_px = int(gm.sum())
    m["cri_lambda3"] = round((CRI_LAMBDA * under_px + over_px) / (area_gt_px + 1e-9), 4)
    if px_per_mm:
        m["under_cov_mm2"] = round(under_px / px_per_mm ** 2, 2)
        m["over_cov_mm2"] = round(over_px / px_per_mm ** 2, 2)
    else:
        m["under_cov_mm2"] = None
        m["over_cov_mm2"] = None
    if gc is not None and sc is not None:
        D = cdist(gc, sc)
        m["hausdorff_mm"] = round(max(D.min(1).max(), D.min(0).max()) / s, 3)
        m["avg_surf_dist_mm"] = round(0.5 * (D.min(1).mean() + D.min(0).mean()) / s, 3)
        Pg = polygon_perimeter(gc)
        Pa = polygon_perimeter(sc)
        m["perimeter_gt_mm"] = round(Pg / s, 2)
        m["perimeter_auto_mm"] = round(Pa / s, 2)
        m["compactness_gt"] = round(4 * np.pi * gm.sum() / (Pg ** 2 + 1e-9), 4)
        m["compactness_auto"] = round(4 * np.pi * sm.sum() / (Pa ** 2 + 1e-9), 4)
        gx0, gy0, gx1, gy1 = axis_aligned_bbox(gc)
        ax0, ay0, ax1, ay1 = axis_aligned_bbox(sc)
        m["bbox_gt_major_mm"] = round(max(gx1 - gx0, gy1 - gy0) / s, 2)
        m["bbox_gt_minor_mm"] = round(min(gx1 - gx0, gy1 - gy0) / s, 2)
        m["bbox_auto_major_mm"] = round(max(ax1 - ax0, ay1 - ay0) / s, 2)
        m["bbox_auto_minor_mm"] = round(min(ax1 - ax0, ay1 - ay0) / s, 2)
        m["aspect_ratio_gt"] = round(min(gx1 - gx0, gy1 - gy0)
                                     / (max(gx1 - gx0, gy1 - gy0) + 1e-9), 4)
        m["aspect_ratio_auto"] = round(min(ax1 - ax0, ay1 - ay0)
                                       / (max(ax1 - ax0, ay1 - ay0) + 1e-9), 4)
        m["bbox_auto_x_mm"] = round((ax1 - ax0) / s, 2)   # A (X): auto bbox width
        m["bbox_auto_y_mm"] = round((ay1 - ay0) / s, 2)   # B (Y): auto bbox height
        m["_bbox_gt"] = (gx0, gy0, gx1, gy1)
        m["_bbox_auto"] = (ax0, ay0, ax1, ay1)
    return m


def public_metrics(m: dict) -> dict:
    """Drop the internal ``_``-prefixed helpers."""
    return {k: v for k, v in m.items() if not k.startswith("_")}


def coverage_extras(gt_mask: np.ndarray, auto_mask: np.ndarray, metrics: dict) -> dict:
    """Scale-free coverage columns used by the batch CSV (``EXTRA_KEYS``)."""
    area_gt_px = int(gt_mask.sum())
    under_px = int(np.logical_and(gt_mask, ~auto_mask).sum())
    over_px = int(np.logical_and(auto_mask, ~gt_mask).sum())
    out = {
        "n_gt_px": area_gt_px,
        "n_auto_px": int(auto_mask.sum()),
        "under_px": under_px,
        "over_px": over_px,
        "under_cov_frac": round(under_px / (area_gt_px + 1e-9), 6),
        "over_cov_frac": round(over_px / (area_gt_px + 1e-9), 6),
    }
    if "area_rel_err" in metrics:
        out["patch_area_err_pct"] = round(100 * metrics["area_rel_err"], 4)
    return out


EXTRA_KEYS = ["n_gt_px", "n_auto_px", "under_px", "over_px",
              "under_cov_frac", "over_cov_frac", "patch_area_err_pct"]
