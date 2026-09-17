"""Segmentation back-ends, looked up by the id string the UI sends.

Adding a back-end = one new module exposing a class with the
:class:`SegmentationBackend` shape, plus one line in :func:`Segmenters.__init__`.
"""
from __future__ import annotations

import numpy as np

from ..geometry import clean_mask, mask_to_contour
from .base import SegmentationContext, SegmentationBackend, Progress, no_progress
from .sam import SamEngine, SamBackend
from .grabcut import GrabCutBackend
from .watershed import WatershedBackend

DEFAULT_METHOD = "SAM (ViT-B)"

__all__ = ["Segmenters", "SegmentationContext", "SegmentationBackend",
           "SamEngine", "Progress", "no_progress", "DEFAULT_METHOD"]


class Segmenters:
    """The registry. One shared :class:`SamEngine` so weights load once."""

    def __init__(self, sam: SamEngine | None = None):
        self.sam = sam or SamEngine()
        self._backends: dict[str, SegmentationBackend] = {}
        for b in (SamBackend(self.sam), GrabCutBackend(), WatershedBackend()):
            self._backends[b.id] = b

    @property
    def ids(self) -> list[str]:
        return list(self._backends)

    def get(self, method: str | None) -> SegmentationBackend:
        key = (method or DEFAULT_METHOD).strip()
        if key not in self._backends:
            # Old callers sent "SAM" / "sam" and similar; be forgiving on prefix.
            for k in self._backends:
                if k.lower().startswith(key.lower()):
                    key = k
                    break
            else:
                raise KeyError(f"Unknown segmentation method: {method!r}")
        return self._backends[key]

    def describe(self) -> list[dict]:
        """For the UI's method list: id, label, availability and why not."""
        return [{"id": b.id, "label": b.label, "available": b.available(),
                 "needs_model": b.needs_model, "reason": b.unavailable_reason()}
                for b in self._backends.values()]

    def run(self, method: str | None, ctx: SegmentationContext,
            progress: Progress = no_progress) -> tuple[np.ndarray | None, np.ndarray | None]:
        """Segment, clean, extract the contour. Returns ``(mask, contour)``.

        ``mask`` is ``None`` when the back-end produced nothing usable;
        ``contour`` alone can be ``None`` when the blob is too small to fit a
        spline (the overlap/area metrics are still meaningful then)."""
        backend = self.get(method)
        if not backend.available():
            raise RuntimeError(backend.unavailable_reason() or f"{backend.label} is not available")
        raw = backend.segment(ctx, progress)
        mask = clean_mask(raw, ctx.h, ctx.w)
        if mask is None or not mask.any():
            return None, None
        return mask, mask_to_contour(mask)
