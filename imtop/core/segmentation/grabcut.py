"""GrabCut seeded by the manual region: probable foreground inside, background far outside."""
from __future__ import annotations

import numpy as np
import cv2

from ...config import SEED_FRAC
from .base import SegmentationContext, Progress, no_progress


class GrabCutBackend:
    id = "GrabCut"
    label = "GrabCut"
    needs_model = False

    def available(self) -> bool:
        return True

    def unavailable_reason(self) -> str:
        return ""

    def segment(self, ctx: SegmentationContext, progress: Progress = no_progress) -> np.ndarray | None:
        img = cv2.cvtColor(ctx.image, cv2.COLOR_RGB2BGR)
        gt = ctx.gt_mask.astype(np.uint8)
        mask = np.full(gt.shape, cv2.GC_PR_BGD, np.uint8)
        mask[gt > 0] = cv2.GC_PR_FGD
        k = max(7, int(SEED_FRAC * max(ctx.h, ctx.w)))
        kernel = np.ones((k, k), np.uint8)
        sure_fg = cv2.erode(gt, kernel, iterations=2)
        sure_bg = cv2.dilate(gt, kernel, iterations=2)   # narrow band around the trace
        mask[sure_fg > 0] = cv2.GC_FGD
        mask[sure_bg == 0] = cv2.GC_BGD
        bgd = np.zeros((1, 65), np.float64)
        fgd = np.zeros((1, 65), np.float64)
        try:
            cv2.grabCut(img, mask, None, bgd, fgd, 5, cv2.GC_INIT_WITH_MASK)
        except cv2.error:
            return None
        out = (mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD)
        return out & (sure_bg > 0)   # never leak past the allowed band
