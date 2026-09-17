"""Marker-based watershed: eroded manual region = foreground, far outside = background."""
from __future__ import annotations

import numpy as np
import cv2

from ...config import SEED_FRAC
from .base import SegmentationContext, Progress, no_progress


class WatershedBackend:
    id = "Watershed"
    label = "Watershed"
    needs_model = False

    def available(self) -> bool:
        return True

    def unavailable_reason(self) -> str:
        return ""

    def segment(self, ctx: SegmentationContext, progress: Progress = no_progress) -> np.ndarray | None:
        img = cv2.cvtColor(ctx.image, cv2.COLOR_RGB2BGR)
        gt = ctx.gt_mask.astype(np.uint8)
        k = max(7, int(SEED_FRAC * max(ctx.h, ctx.w)))
        kernel = np.ones((k, k), np.uint8)
        sure_fg = cv2.erode(gt, kernel, iterations=2)
        sure_bg = cv2.dilate(gt, kernel, iterations=3)
        markers = np.zeros(gt.shape, np.int32)
        markers[sure_bg == 0] = 1   # background
        markers[sure_fg > 0] = 2    # foreground; the rest (0) is unknown
        cv2.watershed(img, markers)
        return markers == 2
