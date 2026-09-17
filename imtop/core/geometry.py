"""Contours, masks and the conversions between them.

Coordinates are image **pixels** ``(x, y)`` throughout; nothing here knows about
millimetres. Converting to real-world units is the metrics module's job.
"""
from __future__ import annotations

import numpy as np
import cv2
from scipy.interpolate import splprep, splev

from ..config import CONTOUR_N, SPLINE_K, MORPH_FRAC


def as_xy_array(points) -> np.ndarray:
    """Normalise a control-point list to an ``(n, 2)`` float array.

    Accepts both shapes the UI and the auto-prompt produce, ``[{"x":…,"y":…}]``
    and ``[[x, y]]``, because mixing them silently produced ``NaN`` coordinates
    downstream. Anything unparseable raises, instead of propagating garbage.
    """
    out = []
    for p in points:
        if isinstance(p, dict):
            out.append([float(p["x"]), float(p["y"])])
        else:
            out.append([float(p[0]), float(p[1])])
    return np.asarray(out, dtype=float).reshape(-1, 2)


def to_xy_dicts(contour: np.ndarray) -> list[dict]:
    """``(n, 2)`` array -> ``[{"x": …, "y": …}]``, the shape the UI expects."""
    return [{"x": float(x), "y": float(y)} for x, y in contour]


def interpolate_spline(points, n: int = CONTOUR_N) -> np.ndarray | None:
    """Fit a smooth closed periodic B-spline through control points.

    Returns an ``(n, 2)`` contour, or ``None`` when there are fewer than three
    control points or the fit fails (duplicate/collinear points).
    """
    pts = as_xy_array(points)
    if len(pts) < 3:
        return None
    arr = np.vstack([pts, pts[0]])
    try:
        tck, _ = splprep([arr[:, 0], arr[:, 1]], s=0, per=True,
                         k=min(SPLINE_K, len(arr) - 1))
        u = np.linspace(0, 1, n, endpoint=False)
        x, y = splev(u, tck)
    except (TypeError, ValueError, RuntimeError):
        return None
    contour = np.column_stack([x, y])
    if not np.isfinite(contour).all():
        return None
    return contour


def contour_to_mask(contour: np.ndarray, h: int, w: int) -> np.ndarray:
    """Rasterise a polygon contour into a filled boolean ``(h, w)`` mask."""
    mask = np.zeros((h, w), np.uint8)
    cv2.fillPoly(mask, [contour.astype(np.int32).reshape(-1, 1, 2)], 1)
    return mask.astype(bool)


def mask_to_contour(mask: np.ndarray | None, n: int = CONTOUR_N) -> np.ndarray | None:
    """Largest external contour of a boolean mask, smoothed into a closed spline.

    Returns ``None`` when the mask is empty or the blob is too small to fit a
    spline through.
    """
    if mask is None:
        return None
    contours, _ = cv2.findContours(mask.astype(np.uint8),
                                   cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return None
    raw = max(contours, key=cv2.contourArea).squeeze()
    if raw.ndim < 2 or len(raw) < 6:
        return None
    try:
        tck, _ = splprep([raw[:, 0].astype(float), raw[:, 1].astype(float)],
                         s=max(len(raw) * 3.0, 200.0), per=True,
                         k=min(5, len(raw) - 1))
        u = np.linspace(0, 1, n, endpoint=False)
        x, y = splev(u, tck)
    except (TypeError, ValueError, RuntimeError):
        return None
    contour = np.column_stack([x, y])
    if not np.isfinite(contour).all():
        return None
    return contour


def clean_mask(mask: np.ndarray | None, h: int, w: int) -> np.ndarray | None:
    """Open+close to drop speckle and fill holes, then keep the largest blob."""
    if mask is None:
        return None
    m = mask.astype(np.uint8)
    k = max(3, int(MORPH_FRAC * max(h, w)))
    k += (k + 1) % 2  # force odd
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, kernel)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, kernel)
    n_labels, labels = cv2.connectedComponents(m)
    if n_labels > 2:
        # One bincount pass instead of one full-image scan per component: a noisy
        # background can produce thousands of speckles, and the per-component
        # scan made that quadratic (measured ~90 s on a single JPEG).
        counts = np.bincount(labels.ravel())
        counts[0] = 0
        m = (labels == int(counts.argmax())).astype(np.uint8)
    return m.astype(bool)


def largest_interior_component(labels: np.ndarray, n_labels: int) -> int | None:
    """Label of the biggest component that does **not** touch the image border.

    The auto-prompt assumes a central, isolated object. An over-exposed corner
    forms a large bright blob that touches the border and otherwise wins on size,
    which made SAM segment the background instead. Falls back to the overall
    largest component when every component touches the border.
    """
    if n_labels < 2:
        return None
    counts = np.bincount(labels.ravel(), minlength=n_labels)
    counts[0] = 0
    border = np.unique(np.concatenate(
        [labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]]))
    interior = counts.copy()
    interior[border[border < len(interior)]] = 0
    best = int(interior.argmax()) if interior.any() else int(counts.argmax())
    return best if counts[best] > 0 else None


def deepest_interior_point(mask: np.ndarray) -> tuple[int, int]:
    """The point furthest inside a filled mask (distance-transform maximum).

    Used as SAM's positive point prompt instead of the contour centroid: for a
    concave shape the centroid can fall outside the mask, which made SAM bleed
    across the concave bay.
    """
    dt = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 5)
    cy, cx = np.unravel_index(int(dt.argmax()), dt.shape)
    return int(cx), int(cy)


def axis_aligned_bbox(contour: np.ndarray):
    """``(x_min, y_min, x_max, y_max)`` of a contour, in pixels."""
    return (contour[:, 0].min(), contour[:, 1].min(),
            contour[:, 0].max(), contour[:, 1].max())


def polygon_perimeter(contour: np.ndarray) -> float:
    """Closed-polygon perimeter in pixels."""
    d = np.diff(contour, axis=0, append=contour[:1])
    return float(np.linalg.norm(d, axis=1).sum())
