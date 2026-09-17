"""One picture per metric for the report: the geometry each number was
computed from, drawn on a crop of the photo around the wound.

This mirrors ``ui/js/overlays.js``: the same drawings in the same shades (the
metric-group colours of ``ui/css/tokens.css``), so the report shows what the
operator sees when a metric row is clicked in the app. No Qt in here, only
OpenCV on numpy arrays, so ``tools/`` and the tests can build a report without
a window.

Contours are ``(n, 2)`` pixel arrays, ``G`` the manual (ground truth) one and
``A`` the automatic one, exactly as the metrics module gets them.
"""
from __future__ import annotations

import math

import numpy as np
import cv2

# light / mid / dark shade of each metric group, RGB. Same values as the
# --ov-*, --ar-*, --di-*, --gm-*, --ga-* tokens in ui/css/tokens.css.
SHADES = {
    "ov": ((0xBF, 0xE3, 0xC8), (0x57, 0xB2, 0x6A), (0x2F, 0x7A, 0x46)),
    "ar": ((0xC6, 0xD8, 0xF9), (0x5B, 0x8D, 0xEF), (0x2F, 0x5E, 0xC2)),
    "di": ((0xF7, 0xC9, 0xC0), (0xE8, 0x70, 0x5B), (0xB0, 0x52, 0x4A)),
    "gm": ((0xE2, 0xCF, 0xF9), (0xB0, 0x7C, 0xF0), (0x7B, 0x44, 0xC4)),
    "ga": ((0xFA, 0xE6, 0xA8), (0xF5, 0xC5, 0x42), (0xB8, 0x90, 0x1F)),
}

# metric key -> group, the same table as MGRP in ui/js/overlays.js
METRIC_GROUP = {
    "dice": "ov", "iou": "ov",
    "area_gt_mm2": "ar", "area_auto_mm2": "ar", "area_rel_err": "ar",
    "under_cov_mm2": "ar", "over_cov_mm2": "ar", "cri_lambda3": "ar",
    "hausdorff_mm": "di", "avg_surf_dist_mm": "di",
    "perimeter_gt_mm": "gm", "compactness_gt": "gm", "bbox_gt_major_mm": "gm",
    "bbox_gt_minor_mm": "gm", "aspect_ratio_gt": "gm",
    "perimeter_auto_mm": "ga", "compactness_auto": "ga", "bbox_auto_major_mm": "ga",
    "bbox_auto_minor_mm": "ga", "aspect_ratio_auto": "ga",
    "bbox_auto_x_mm": "ga", "bbox_auto_y_mm": "ga",
}

FIGURE_MAX_SIDE = 640      # px, the long side of a figure: enough for a 6 cm print
CROP_MARGIN = 0.22         # of the wound's longer side, of air around it
HALO = (0, 0, 0)           # every stroke sits on a dark rim, so it reads on skin


# ── the crop ─────────────────────────────────────────────────────────────────
def crop_box(h: int, w: int, contours, margin: float = CROP_MARGIN) -> tuple[int, int, int, int]:
    """A 4:3 window around the contours, with some air, clamped to the image.

    Returns ``(x0, y0, x1, y1)`` in pixels. The aspect is kept where the image
    allows it, so the figures line up in the report's grid.
    """
    pts = np.vstack([c for c in contours if c is not None and len(c)])
    x0, y0 = float(pts[:, 0].min()), float(pts[:, 1].min())
    x1, y1 = float(pts[:, 0].max()), float(pts[:, 1].max())
    m = max(x1 - x0, y1 - y0) * margin + 24
    x0, x1, y0, y1 = x0 - m, x1 + m, y0 - m, y1 + m
    cw, ch = x1 - x0, y1 - y0
    if cw / max(ch, 1e-9) < 4 / 3:
        cw = ch * 4 / 3
    else:
        ch = cw * 3 / 4
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    x0, x1, y0, y1 = cx - cw / 2, cx + cw / 2, cy - ch / 2, cy + ch / 2
    if x0 < 0:
        x1 -= x0
        x0 = 0
    if y0 < 0:
        y1 -= y0
        y0 = 0
    if x1 > w:
        x0 -= x1 - w
        x1 = w
    if y1 > h:
        y0 -= y1 - h
        y1 = h
    x0, y0 = max(0.0, x0), max(0.0, y0)
    return int(x0), int(y0), int(math.ceil(x1)), int(math.ceil(y1))


class _Sheet:
    """The cropped, downscaled photo and the two contours in its coordinates."""

    def __init__(self, img: np.ndarray, gt, auto):
        h, w = img.shape[:2]
        x0, y0, x1, y1 = crop_box(h, w, [gt, auto])
        crop = img[y0:y1, x0:x1]
        ch, cw = crop.shape[:2]
        self.scale = min(1.0, FIGURE_MAX_SIDE / float(max(cw, ch, 1)))
        if self.scale < 1.0:
            crop = cv2.resize(crop, (max(1, round(cw * self.scale)), max(1, round(ch * self.scale))),
                              interpolation=cv2.INTER_AREA)
        self.base = np.ascontiguousarray(crop)
        self.origin = np.array([x0, y0], dtype=float)
        self.G = self._to_sheet(gt)
        self.A = self._to_sheet(auto)

    def _to_sheet(self, c):
        if c is None or len(c) < 3:
            return None
        return (np.asarray(c, dtype=float) - self.origin) * self.scale

    def canvas(self) -> np.ndarray:
        return self.base.copy()


# ── primitives (RGB, on a uint8 canvas) ──────────────────────────────────────
def _poly(pts) -> np.ndarray:
    return np.round(pts).astype(np.int32).reshape(-1, 1, 2)


def _mask(shape, pts) -> np.ndarray:
    m = np.zeros(shape[:2], np.uint8)
    cv2.fillPoly(m, [_poly(pts)], 1)
    return m.astype(bool)


def _fill(canvas: np.ndarray, region: np.ndarray, rgb, alpha: float) -> None:
    """Blend ``rgb`` over the pixels of ``region``."""
    if not region.any():
        return
    col = np.array(rgb, np.float32)
    canvas[region] = np.clip(canvas[region] * (1 - alpha) + col * alpha, 0, 255).astype(np.uint8)


def _line(canvas, a, b, rgb, w: float) -> None:
    cv2.line(canvas, (int(round(a[0])), int(round(a[1]))), (int(round(b[0])), int(round(b[1]))),
             rgb, max(1, int(round(w))), cv2.LINE_AA)


def _dashes(pts, on: float, off: float, closed: bool = True):
    """The dashes of a polyline as ``(a, b)`` pairs, ``on``/``off`` in pixels."""
    P = np.vstack([pts, pts[:1]]) if closed else np.asarray(pts, float)
    d = np.diff(P, axis=0)
    L = np.hypot(d[:, 0], d[:, 1])
    cum = np.concatenate([[0.0], np.cumsum(L)])
    total = float(cum[-1])

    def at(t):
        i = int(np.searchsorted(cum, t, side="right") - 1)
        i = max(0, min(i, len(L) - 1))
        f = 0.0 if L[i] < 1e-9 else (t - cum[i]) / L[i]
        return P[i] + (P[i + 1] - P[i]) * f

    out, t = [], 0.0
    while t < total:
        t2 = min(t + on, total)
        out.append((at(t), at(t2)))
        t = t2 + off
    return out


def _stroke(canvas, pts, rgb, w: float, dash=None, closed: bool = True, halo: bool = True) -> None:
    """A polyline over a dark rim, dashed when ``dash`` is ``(on, off)``."""
    if pts is None or len(pts) < 2:
        return
    if dash:
        segs = _dashes(pts, dash[0], dash[1], closed)
        if halo:
            for a, b in segs:
                _line(canvas, a, b, HALO, w + 1.2)
        for a, b in segs:
            _line(canvas, a, b, rgb, w)
        return
    P = _poly(pts)
    if halo:
        cv2.polylines(canvas, [P], closed, HALO, max(1, int(round(w + 2))), cv2.LINE_AA)
    cv2.polylines(canvas, [P], closed, rgb, max(1, int(round(w))), cv2.LINE_AA)


def _segments_by_value(canvas, pts, values, light, dark, w: float) -> None:
    """Each segment of a closed contour in a shade between light (0) and dark (1)."""
    P = np.asarray(pts, float)
    n = len(P)
    top = float(values.max()) + 1e-9
    cv2.polylines(canvas, [_poly(P)], True, HALO, max(1, int(round(w + 2))), cv2.LINE_AA)
    for i in range(n):
        t = float(values[i]) / top
        rgb = tuple(int(round(light[k] + (dark[k] - light[k]) * t)) for k in range(3))
        _line(canvas, P[i], P[(i + 1) % n], rgb, w)


def _axis(canvas, a, b, rgb, w: float) -> None:
    """A measured segment with a cap at each end."""
    _stroke(canvas, np.array([a, b]), rgb, w, closed=False)
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy) or 1.0
    nx, ny, cap = -dy / L, dx / L, 7
    for p in (a, b):
        _stroke(canvas, np.array([[p[0] - nx * cap, p[1] - ny * cap], [p[0] + nx * cap, p[1] + ny * cap]]),
                rgb, w, closed=False)


def _marker(canvas, p, r: float, rgb) -> None:
    c = (int(round(p[0])), int(round(p[1])))
    cv2.circle(canvas, c, int(round(r)) + 1, HALO, -1, cv2.LINE_AA)
    cv2.circle(canvas, c, int(round(r)), rgb, -1, cv2.LINE_AA)


def _min_dist(P, Q) -> np.ndarray:
    """Distance from every point of P to the nearest point of Q."""
    d = P[:, None, :] - Q[None, :, :]
    return np.sqrt((d * d).sum(axis=2)).min(axis=1)


def _bbox(P):
    return float(P[:, 0].min()), float(P[:, 1].min()), float(P[:, 0].max()), float(P[:, 1].max())


# ── the figures ──────────────────────────────────────────────────────────────
def draw_metric(sheet: _Sheet, key: str) -> np.ndarray:
    """The figure for one metric, the same drawing overlays.js makes on screen."""
    light, mid, dark = SHADES[METRIC_GROUP.get(key, "gm")]
    cv = sheet.canvas()
    G, A = sheet.G, sheet.A
    shape = cv.shape

    def faint(P):
        if P is not None:
            _stroke(cv, P, mid, 1.4, dash=(5, 4))

    # Overlap: IoU shows union against intersection, Dice the two areas with
    # the shared part counted twice (darker).
    if key in ("dice", "iou"):
        if G is None or A is None:
            faint(G)
            faint(A)
            return cv
        mg, ma = _mask(shape, G), _mask(shape, A)
        if key == "iou":
            _fill(cv, mg | ma, light, 0.32)
            _fill(cv, mg & ma, dark, 0.55)
        else:
            _fill(cv, mg & ~ma, mid, 0.30)
            _fill(cv, ma & ~mg, light, 0.34)
            _fill(cv, mg & ma, dark, 0.60)
        _stroke(cv, G, mid, 1.8)
        _stroke(cv, A, mid, 1.8, dash=(5, 4))
        return cv

    # Area
    if key == "area_gt_mm2":
        faint(A)
        if G is not None:
            _fill(cv, _mask(shape, G), mid, 0.34)
            _stroke(cv, G, dark, 2.4)
        return cv
    if key == "area_auto_mm2":
        faint(G)
        if A is not None:
            _fill(cv, _mask(shape, A), mid, 0.34)
            _stroke(cv, A, dark, 2.4)
        return cv
    if key == "area_rel_err":
        if G is None or A is None:
            faint(G)
            faint(A)
            return cv
        mg, ma = _mask(shape, G), _mask(shape, A)
        _fill(cv, mg & ~ma, dark, 0.50)
        _fill(cv, ma & ~mg, light, 0.55)
        _stroke(cv, G, mid, 1.8)
        _stroke(cv, A, mid, 1.8, dash=(5, 4))
        return cv

    # Coverage: W = the wound (manual), P = the patch (auto). Uncovered wound
    # is W minus P, excess on skin is P minus W; the cost index shows both,
    # with its weights as opacity.
    if key in ("under_cov_mm2", "over_cov_mm2", "cri_lambda3"):
        if G is None or A is None:
            faint(G)
            faint(A)
            return cv
        mg, ma = _mask(shape, G), _mask(shape, A)
        if key != "over_cov_mm2":
            _fill(cv, mg & ~ma, dark, 0.62 if key == "cri_lambda3" else 0.55)
        if key != "under_cov_mm2":
            _fill(cv, ma & ~mg, light, 0.28 if key == "cri_lambda3" else 0.55)
        _stroke(cv, G, mid, 1.8)
        _stroke(cv, A, mid, 1.8, dash=(5, 4))
        return cv

    # Distance: the contour is coloured by its local distance to the other one.
    if key in ("hausdorff_mm", "avg_surf_dist_mm"):
        if G is None or A is None:
            faint(G)
            faint(A)
            return cv
        dg = _min_dist(G, A)
        _segments_by_value(cv, G, dg, light, dark, 3.4)
        if key == "avg_surf_dist_mm":
            _segments_by_value(cv, A, _min_dist(A, G), light, dark, 2.2)
        else:
            _stroke(cv, A, mid, 1.5, dash=(5, 4))
            wi = int(dg.argmax())
            d = A - G[wi]
            cj = int((d * d).sum(axis=1).argmin())
            _stroke(cv, np.array([G[wi], A[cj]]), dark, 2.4, closed=False)
            _marker(cv, G[wi], 4.5, dark)
        return cv

    # Geometry: the other contour stays faint, the measured one is drawn.
    is_auto = "auto" in key
    T, O = (A, G) if is_auto else (G, A)
    faint(O)
    if T is None:
        return cv

    if key.startswith("perimeter"):
        _stroke(cv, T, mid, 3.2)
        return cv

    if key.startswith("compactness"):
        _stroke(cv, T, mid, 2.2)
        cx, cy = float(T[:, 0].mean()), float(T[:, 1].mean())
        x, y = T[:, 0], T[:, 1]
        area = 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))
        rr = math.sqrt(area / math.pi)
        ang = np.linspace(0, 2 * math.pi, 180, endpoint=False)
        circle = np.column_stack([cx + rr * np.cos(ang), cy + rr * np.sin(ang)])
        _stroke(cv, circle, dark, 2.0, dash=(7, 5))
        return cv

    # bounding box + the axis the metric refers to. X and Y are the box's own
    # sides; major and minor are the same two sides sorted by length.
    x0, y0, x1, y1 = _bbox(T)
    W, H = x1 - x0, y1 - y0
    mx, my, hor = (x0 + x1) / 2, (y0 + y1) / 2, W >= H
    _stroke(cv, np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]), mid, 1.4, dash=(5, 4))
    _stroke(cv, T, mid, 1.5)
    XA, XB = (x0, my), (x1, my)
    YA, YB = (mx, y0), (mx, y1)
    MA, MB = (XA, XB) if hor else (YA, YB)
    NA, NB = (YA, YB) if hor else (XA, XB)
    if "_x_" in key:
        _axis(cv, XA, XB, mid, 3.2)
    elif "_y_" in key:
        _axis(cv, YA, YB, mid, 3.2)
    elif "major" in key:
        _axis(cv, MA, MB, mid, 3.2)
    elif "minor" in key:
        _axis(cv, NA, NB, mid, 3.2)
    else:                                          # aspect ratio: both sides
        _axis(cv, MA, MB, dark, 3.0)
        _axis(cv, NA, NB, light, 3.0)
    return cv


def metric_figures(img: np.ndarray, gt_contour, auto_contour, keys) -> dict:
    """``{key: RGB array}`` for every key in ``keys``, all on the same crop.

    Empty when there is no photo or no contour at all: the report then simply
    has no figure for the metrics.
    """
    if img is None:
        return {}
    if (gt_contour is None or len(gt_contour) < 3) and (auto_contour is None or len(auto_contour) < 3):
        return {}
    sheet = _Sheet(img, gt_contour, auto_contour)
    return {k: draw_metric(sheet, k) for k in keys}
